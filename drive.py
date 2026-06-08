"""
Motion drive abstraction for the EtherCAT servo test bench.

Everything the GUI talks to goes through `MotionDrive`. Two backends implement it:

  * `SimDrive`  - a simulated motor (no hardware). Integrates commanded motion so
                  the whole app runs and is testable today.
  * `SoemDrive` - the real EtherCAT/CiA402 backend (skeleton). Filled in and tested
                  once a StepperOnline EtherCAT servo is on the bus.

Because the GUI only ever sees `MotionDrive`, swapping sim <-> hardware is a one-line
change. Internally everything is in encoder *counts*; the GUI converts to user units
(revolutions) via `counts_per_rev`.
"""

from __future__ import annotations

import math
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class DriveState(Enum):
    DISCONNECTED = "disconnected"
    DISABLED = "disabled"      # connected, power stage off
    ENABLED = "enabled"        # power stage on, holding/moving
    FAULT = "fault"


class Mode(Enum):
    """CiA402 modes of operation we care about for a bench."""
    VELOCITY = "csv"   # Cyclic Synchronous Velocity  (jog / run-at-speed)
    POSITION = "csp"   # Cyclic Synchronous Position   (go-to / homing)
    PROFILE = "profile"  # play a speed/position-vs-time curve (built on CSV/CSP)


@dataclass
class DriveStatus:
    """A snapshot the GUI polls for display. All positions/velocities in counts."""
    state: DriveState = DriveState.DISCONNECTED
    mode: Mode = Mode.VELOCITY
    actual_position: float = 0.0      # counts
    actual_velocity: float = 0.0      # counts/s
    target_position: float = 0.0      # counts
    target_velocity: float = 0.0      # counts/s
    homed: bool = False
    fault_text: str = ""


@dataclass
class DriveLimits:
    counts_per_rev: int = 131072      # 17-bit single-turn; override per drive
    max_velocity: float = 131072 * 30  # counts/s  (~30 rev/s)
    acceleration: float = 131072 * 60  # counts/s^2 (~60 rev/s^2)


class MotionDrive(ABC):
    """Interface every backend implements. The GUI only knows about this."""

    def __init__(self, limits: DriveLimits | None = None):
        self.limits = limits or DriveLimits()

    # --- lifecycle -------------------------------------------------------
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def enable(self) -> None: ...

    @abstractmethod
    def disable(self) -> None: ...

    @abstractmethod
    def reset_fault(self) -> None: ...

    # --- commanding ------------------------------------------------------
    @abstractmethod
    def set_mode(self, mode: Mode) -> None: ...

    @abstractmethod
    def command_velocity(self, counts_per_s: float) -> None:
        """Run at a target velocity (switches to VELOCITY mode)."""

    @abstractmethod
    def command_position(self, counts: float) -> None:
        """Move to an absolute target position (switches to POSITION mode)."""

    @abstractmethod
    def stop(self) -> None:
        """Decelerate to a halt and hold."""

    @abstractmethod
    def home(self) -> None:
        """Run a homing routine; sets homed=True when reference is found."""

    # --- feedback --------------------------------------------------------
    @abstractmethod
    def status(self) -> DriveStatus: ...

    # convenience: user-unit conversions ---------------------------------
    def rev(self, counts: float) -> float:
        return counts / self.limits.counts_per_rev

    def counts(self, rev: float) -> float:
        return rev * self.limits.counts_per_rev


# ----------------------------------------------------------------------------
# Simulated backend
# ----------------------------------------------------------------------------
class SimDrive(MotionDrive):
    """
    A believable single-axis servo simulator.

    Velocity tracks its setpoint at the acceleration limit; in POSITION mode it
    runs a trapezoidal-ish approach (v = sqrt(2*a*remaining), clamped to max_v).
    Call `update(dt)` periodically (the GUI does this from a timer).
    """

    def __init__(self, limits: DriveLimits | None = None):
        super().__init__(limits)
        self._state = DriveState.DISCONNECTED
        self._mode = Mode.VELOCITY
        self._pos = 0.0
        self._vel = 0.0
        self._target_vel = 0.0
        self._target_pos = 0.0
        self._homed = False
        self._homing = False
        self._fault = ""

    # lifecycle
    def connect(self) -> None:
        self._state = DriveState.DISABLED

    def disconnect(self) -> None:
        self._state = DriveState.DISCONNECTED
        self._vel = 0.0

    def enable(self) -> None:
        if self._state in (DriveState.DISABLED, DriveState.ENABLED):
            self._state = DriveState.ENABLED

    def disable(self) -> None:
        if self._state == DriveState.ENABLED:
            self._state = DriveState.DISABLED
        self._target_vel = 0.0

    def reset_fault(self) -> None:
        if self._state == DriveState.FAULT:
            self._state = DriveState.DISABLED
        self._fault = ""

    # commanding
    def set_mode(self, mode: Mode) -> None:
        self._mode = mode

    def command_velocity(self, counts_per_s: float) -> None:
        self._mode = Mode.VELOCITY
        self._homing = False
        self._target_vel = self._clamp_vel(counts_per_s)

    def command_position(self, counts: float) -> None:
        self._mode = Mode.POSITION
        self._homing = False
        self._target_pos = counts

    def stop(self) -> None:
        self._homing = False
        if self._mode == Mode.VELOCITY:
            self._target_vel = 0.0
        else:
            # hold current position
            self._target_pos = self._pos
            self._mode = Mode.POSITION

    def home(self) -> None:
        # Simple sim homing: drive to absolute zero, then mark homed.
        self._mode = Mode.POSITION
        self._homing = True
        self._homed = False
        self._target_pos = 0.0

    # physics step
    def update(self, dt: float) -> None:
        if dt <= 0:
            return
        if self._state != DriveState.ENABLED:
            # power off -> coast to a quick stop
            self._vel = self._approach(self._vel, 0.0, self.limits.acceleration, dt)
            self._pos += self._vel * dt
            return

        a = self.limits.acceleration
        if self._mode == Mode.POSITION:
            error = self._target_pos - self._pos
            # velocity that still allows stopping exactly on target
            v_cmd = math.copysign(math.sqrt(2 * a * abs(error)), error)
            v_cmd = self._clamp_vel(v_cmd)
            self._vel = self._approach(self._vel, v_cmd, a, dt)
            self._pos += self._vel * dt
            # snap & settle when basically there
            if abs(error) < 2.0 and abs(self._vel) < a * dt:
                self._pos = self._target_pos
                self._vel = 0.0
                if self._homing:
                    self._homing = False
                    self._homed = True
        else:  # VELOCITY
            self._vel = self._approach(self._vel, self._target_vel, a, dt)
            self._pos += self._vel * dt

    # helpers
    def _clamp_vel(self, v: float) -> float:
        mv = self.limits.max_velocity
        return max(-mv, min(mv, v))

    @staticmethod
    def _approach(current: float, target: float, rate: float, dt: float) -> float:
        step = rate * dt
        if current < target:
            return min(current + step, target)
        return max(current - step, target)

    def status(self) -> DriveStatus:
        return DriveStatus(
            state=self._state,
            mode=self._mode,
            actual_position=self._pos,
            actual_velocity=self._vel,
            target_position=self._target_pos,
            target_velocity=self._target_vel,
            homed=self._homed,
            fault_text=self._fault,
        )


# ----------------------------------------------------------------------------
# Real EtherCAT / CiA402 backend  (skeleton - filled in with hardware)
# ----------------------------------------------------------------------------
class SoemDrive(MotionDrive):
    """
    SOEM-based CiA402 master for a StepperOnline EtherCAT servo.

    This is intentionally a SKELETON: the structure, threading model and CiA402
    state machine are laid out, but the PDO mapping and object indices are marked
    TODO because they come from the specific drive's ESI/manual. With the drive on
    the bus, we (1) confirm `slaveinfo` sees it, (2) fill in the PDO map, (3) test
    each method against real hardware - the GUI above never changes.

    Requires:  pip install pysoem   (and run with CAP_NET_RAW / sudo, on enp4s0)
    """

    # CiA402 object dictionary (standard indices) ----------------------------
    OD_CONTROLWORD = 0x6040
    OD_STATUSWORD = 0x6041
    OD_MODES_OF_OPERATION = 0x6060
    OD_TARGET_VELOCITY = 0x60FF   # CSV
    OD_TARGET_POSITION = 0x607A   # CSP
    OD_ACTUAL_POSITION = 0x6064
    OD_ACTUAL_VELOCITY = 0x606C

    # CiA402 controlword command sequence to reach "Operation enabled"
    CW_SHUTDOWN = 0x0006
    CW_SWITCH_ON = 0x0007
    CW_ENABLE_OPERATION = 0x000F
    CW_FAULT_RESET = 0x0080

    def __init__(self, ifname: str = "enp4s0", limits: DriveLimits | None = None):
        super().__init__(limits)
        self.ifname = ifname
        self._master = None
        self._slave = None
        self._cycle_thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._status = DriveStatus()

    def connect(self) -> None:
        # import pysoem  # deferred until hardware is present
        # self._master = pysoem.Master(); self._master.open(self.ifname)
        # if self._master.config_init() <= 0: raise RuntimeError("no slaves found")
        # self._slave = self._master.slaves[0]
        # TODO: map PDOs (controlword, statusword, target/actual pos+vel) per ESI
        # self._master.config_map(); self._master.state = pysoem.OP_STATE; ...
        # self._start_cycle_thread()
        raise NotImplementedError(
            "SoemDrive is a skeleton. Connect the StepperOnline drive, run "
            "slaveinfo, then fill in PDO mapping + the cyclic loop."
        )

    def disconnect(self) -> None:
        self._running = False
        if self._cycle_thread:
            self._cycle_thread.join(timeout=1.0)
        # if self._master: self._master.close()

    def _cyclic_loop(self) -> None:
        """Runs at the bus cycle (e.g. 1 kHz). Exchange PDOs, run state machine."""
        while self._running:
            # self._master.send_processdata(); self._master.receive_processdata(2000)
            # read statusword/actual pos+vel from input PDOs, write controlword +
            # target from a shared command, advance CiA402 state machine.
            time.sleep(0.001)

    # The commanding/feedback methods set shared fields the cyclic loop consumes.
    def enable(self) -> None: raise NotImplementedError
    def disable(self) -> None: raise NotImplementedError
    def reset_fault(self) -> None: raise NotImplementedError
    def set_mode(self, mode: Mode) -> None: raise NotImplementedError
    def command_velocity(self, counts_per_s: float) -> None: raise NotImplementedError
    def command_position(self, counts: float) -> None: raise NotImplementedError
    def stop(self) -> None: raise NotImplementedError
    def home(self) -> None: raise NotImplementedError

    def status(self) -> DriveStatus:
        with self._lock:
            return self._status
