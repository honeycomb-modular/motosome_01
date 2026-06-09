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
# Real EtherCAT / CiA402 backend  (SOEM)
# ----------------------------------------------------------------------------
# CiA402 modes of operation (object 0x6060)
_MODE_CSP = 8   # cyclic synchronous position
_MODE_CSV = 9   # cyclic synchronous velocity
_MODE_HM = 6    # homing

# CiA402 controlword commands
_CW_SHUTDOWN = 0x0006
_CW_SWITCH_ON = 0x0007
_CW_ENABLE_OP = 0x000F
_CW_FAULT_RESET = 0x0080


@dataclass
class CiA402Pdo:
    """Byte offsets (little-endian) within the process image.

    Defaults match the explicit mapping set in SoemDrive._setup_pdos():
      RxPDO 0x1600: controlword(16) modes(8) target_pos(32) target_vel(32)  -> 11 B
      TxPDO 0x1A00: statusword(16) modes_disp(8) actual_pos(32) actual_vel(32) -> 11 B
    If the A6-EC uses fixed/other PDOs, set these to what `soem_scan.py` reports.
    """
    out_controlword: int = 0
    out_mode: int = 2
    out_target_pos: int = 3
    out_target_vel: int = 7
    out_len: int = 11
    in_statusword: int = 0
    in_mode_disp: int = 2
    in_actual_pos: int = 3
    in_actual_vel: int = 7
    in_len: int = 11


class SoemDrive(MotionDrive):
    """SOEM EtherCAT master + CiA402 servo backbone (the A6-EC scan axis).

    Opens the bus, maps a standard CiA402 PDO set, brings the slave to OP, and
    runs a cyclic thread that drives the CiA402 state machine and exchanges
    target/actual values. The GUI talks to it through the same MotionDrive
    interface as the simulator — so selecting "EtherCAT" in the bench and hitting
    Enable / jog / Play drives the real motor with zero GUI changes.

    Bring-up (when the drive arrives):
      1. pip install --user pysoem ; run the bench with CAP_NET_RAW (or sudo) on enp4s0
      2. python3 soem_scan.py enp4s0   -> confirm the drive's vendor/product + PDO sizes
      3. if its process image differs from CiA402Pdo's defaults, adjust the offsets
         (and _setup_pdos if the drive allows PDO remapping; some use fixed PDOs)
      4. calibrate the counts<->drive-units factor if 0x60FF/0x6064 aren't in counts

    NOTE: written against the standard CiA402 profile but UNTESTED on hardware yet —
    expect a short calibration pass on first connect.
    """

    def __init__(self, ifname: str = "enp4s0", limits: DriveLimits | None = None,
                 cycle_us: int = 1000, pdo: CiA402Pdo | None = None):
        super().__init__(limits)
        self.ifname = ifname
        self.cycle_us = cycle_us
        self.pdo = pdo or CiA402Pdo()
        self._pysoem = None
        self._master = None
        self._slave = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        # shared command state (GUI thread -> cyclic thread)
        self._mode = Mode.VELOCITY
        self._want_enable = False
        self._reset_req = False
        self._target_vel = 0.0   # counts/s
        self._target_pos = 0.0   # counts
        self._homing = False
        # shared feedback (cyclic thread -> GUI thread)
        self._state = DriveState.DISCONNECTED
        self._pos = 0.0
        self._vel = 0.0
        self._homed = False
        self._fault = ""

    # ---- lifecycle ------------------------------------------------------
    def connect(self) -> None:
        try:
            import pysoem
        except ImportError as e:
            raise RuntimeError("pysoem not installed — run: pip install --user pysoem") from e
        self._pysoem = pysoem
        m = pysoem.Master()
        m.open(self.ifname)
        if m.config_init() <= 0:
            m.close()
            raise RuntimeError(f"no EtherCAT slaves found on {self.ifname}")
        self._slave = m.slaves[0]
        self._slave.config_func = self._setup_pdos
        m.config_map()
        m.config_dc()
        self._master = m
        if m.state_check(pysoem.SAFEOP_STATE, 50000) != pysoem.SAFEOP_STATE:
            self._fail("slave did not reach SAFE_OP")
        # prime process data, then request OP
        self._slave.output = bytes(self.pdo.out_len)
        m.send_processdata(); m.receive_processdata(2000)
        m.state = pysoem.OP_STATE
        m.write_state()
        for _ in range(200):
            m.send_processdata(); m.receive_processdata(2000)
            if m.state_check(pysoem.OP_STATE, 5000) == pysoem.OP_STATE:
                break
        if m.state_check(pysoem.OP_STATE, 5000) != pysoem.OP_STATE:
            self._fail("slave did not reach OP")
        with self._lock:
            self._state = DriveState.DISABLED
        self._running = True
        self._thread = threading.Thread(target=self._cyclic, daemon=True)
        self._thread.start()

    def _fail(self, msg: str) -> None:
        try:
            if self._master:
                self._master.close()
        finally:
            self._master = None
        raise RuntimeError(msg)

    def disconnect(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._master:
            try:
                self._master.state = self._pysoem.INIT_STATE
                self._master.write_state()
            except Exception:
                pass
            self._master.close()
            self._master = None
        with self._lock:
            self._state = DriveState.DISCONNECTED

    # ---- PDO mapping (invoked by config_map while in PRE_OP) ------------
    def _setup_pdos(self, slave_pos: int) -> int:
        import struct
        s = self._slave

        def w(idx, sub, val, fmt):
            s.sdo_write(idx, sub, struct.pack(fmt, val))

        try:
            # RxPDO 0x1600: CW(0x6040,16) MODE(0x6060,8) TPOS(0x607A,32) TVEL(0x60FF,32)
            w(0x1C12, 0, 0, "B")
            w(0x1600, 0, 0, "B")
            w(0x1600, 1, 0x60400010, "<I")
            w(0x1600, 2, 0x60600008, "<I")
            w(0x1600, 3, 0x607A0020, "<I")
            w(0x1600, 4, 0x60FF0020, "<I")
            w(0x1600, 0, 4, "B")
            w(0x1C12, 1, 0x1600, "<H")
            w(0x1C12, 0, 1, "B")
            # TxPDO 0x1A00: SW(0x6041,16) MODEd(0x6061,8) APOS(0x6064,32) AVEL(0x606C,32)
            w(0x1C13, 0, 0, "B")
            w(0x1A00, 0, 0, "B")
            w(0x1A00, 1, 0x60410010, "<I")
            w(0x1A00, 2, 0x60610008, "<I")
            w(0x1A00, 3, 0x60640020, "<I")
            w(0x1A00, 4, 0x606C0020, "<I")
            w(0x1A00, 0, 4, "B")
            w(0x1C13, 1, 0x1A00, "<H")
            w(0x1C13, 0, 1, "B")
        except Exception as e:  # drive may use fixed PDOs
            print(f"[SoemDrive] PDO remap skipped ({e}); using drive default PDOs — "
                  f"verify offsets with soem_scan.py")
        return 0

    # ---- commanding (GUI thread) ---------------------------------------
    def enable(self) -> None:
        with self._lock:
            self._want_enable = True

    def disable(self) -> None:
        with self._lock:
            self._want_enable = False

    def reset_fault(self) -> None:
        with self._lock:
            self._reset_req = True

    def set_mode(self, mode: Mode) -> None:
        with self._lock:
            self._mode = mode

    def command_velocity(self, counts_per_s: float) -> None:
        mv = self.limits.max_velocity
        with self._lock:
            self._mode = Mode.VELOCITY
            self._homing = False
            self._target_vel = max(-mv, min(mv, counts_per_s))

    def command_position(self, counts: float) -> None:
        with self._lock:
            self._mode = Mode.POSITION
            self._homing = False
            self._target_pos = counts

    def stop(self) -> None:
        with self._lock:
            self._homing = False
            if self._mode == Mode.VELOCITY:
                self._target_vel = 0.0
            else:
                self._target_pos = self._pos

    def home(self) -> None:
        # Backbone homing: drive to absolute zero in CSP, flag homed when settled.
        # (True CiA402 homing = mode 6 + the drive's configured homing method/switch.)
        with self._lock:
            self._mode = Mode.POSITION
            self._homing = True
            self._homed = False
            self._target_pos = 0.0

    # ---- cyclic real-time loop -----------------------------------------
    def _cyclic(self) -> None:
        import struct
        p = self.pdo
        period = self.cycle_us / 1_000_000.0
        out = bytearray(p.out_len)
        next_t = time.perf_counter()
        while self._running:
            self._master.send_processdata()
            self._master.receive_processdata(2000)
            data = bytes(self._slave.input)

            sw = struct.unpack_from("<H", data, p.in_statusword)[0] if len(data) >= p.in_len else 0
            apos = struct.unpack_from("<i", data, p.in_actual_pos)[0] if len(data) >= p.in_len else 0
            avel = struct.unpack_from("<i", data, p.in_actual_vel)[0] if len(data) >= p.in_len else 0

            with self._lock:
                mode, want = self._mode, self._want_enable
                tvel, tpos = self._target_vel, self._target_pos
                do_reset = self._reset_req
                self._reset_req = False
                homing = self._homing

            cw, dstate = self._step_state(sw, want, do_reset)

            mode_byte = _MODE_CSV if mode == Mode.VELOCITY else _MODE_CSP
            struct.pack_into("<H", out, p.out_controlword, cw)
            struct.pack_into("B", out, p.out_mode, mode_byte)
            struct.pack_into("<i", out, p.out_target_pos, int(tpos))
            struct.pack_into("<i", out, p.out_target_vel,
                             int(tvel) if mode == Mode.VELOCITY else 0)
            self._slave.output = bytes(out)

            homed_now = homing and abs(apos) < 5 and abs(avel) < 5
            with self._lock:
                self._pos, self._vel = float(apos), float(avel)
                self._state = dstate
                self._fault = "drive fault" if dstate == DriveState.FAULT else ""
                if homed_now:
                    self._homed = True
                    self._homing = False

            next_t += period
            dt = next_t - time.perf_counter()
            if dt > 0:
                time.sleep(dt)
            else:
                next_t = time.perf_counter()

    @staticmethod
    def _walk_to_enabled(sw: int) -> tuple[int, "DriveState"]:
        if (sw & 0x4F) == 0x40:      # switch-on disabled
            return _CW_SHUTDOWN, DriveState.DISABLED
        if (sw & 0x6F) == 0x21:      # ready to switch on
            return _CW_SWITCH_ON, DriveState.DISABLED
        if (sw & 0x6F) == 0x23:      # switched on
            return _CW_ENABLE_OP, DriveState.DISABLED
        if (sw & 0x6F) == 0x27:      # operation enabled
            return _CW_ENABLE_OP, DriveState.ENABLED
        return _CW_SHUTDOWN, DriveState.DISABLED

    def _step_state(self, sw: int, want_enable: bool, do_reset: bool):
        """CiA402 state machine -> (controlword, DriveState)."""
        if (sw & 0x4F) == 0x08:                      # fault
            if do_reset:
                return _CW_FAULT_RESET, DriveState.FAULT   # one-cycle rising-edge pulse
            return _CW_SHUTDOWN, DriveState.FAULT
        if not want_enable:
            return _CW_SHUTDOWN, DriveState.DISABLED
        return self._walk_to_enabled(sw)

    # ---- feedback (GUI thread) -----------------------------------------
    def status(self) -> DriveStatus:
        with self._lock:
            return DriveStatus(
                state=self._state, mode=self._mode,
                actual_position=self._pos, actual_velocity=self._vel,
                target_position=self._target_pos, target_velocity=self._target_vel,
                homed=self._homed, fault_text=self._fault,
            )
