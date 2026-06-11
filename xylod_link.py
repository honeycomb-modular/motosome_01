"""
xylod_link.py - MOTOSOME backend for the Xylosome motion daemon (xylod).

Third backend next to SimDrive / SoemDrive: instead of owning the bus, it
connects to xylod's TCP port (newline-JSON, see xylosome-hmi/beckhoff/PROTOCOL.md)
and mirrors the live axis state - position, velocity, drive state, homed.

The headline use: while the Pi pendant executes a scan, MOTOSOME's scope shows
what the motor is actually doing, live, on the Beckhoff desktop. Because xylod
broadcasts to every client, this is pure eavesdropping - the Pi never knows.

Bench commands work too: jog / move / home / enable are translated to xylod
protocol commands, so the same GUI can drive the scan axis through the daemon
(sim or real EtherCAT, whatever xylod was started with).

Units: xylod speaks output-side degrees. One bench "rev" = one output
revolution: counts = deg / 360 * counts_per_rev.
"""

from __future__ import annotations

import json
import socket
import threading
import time

from drive import MotionDrive, DriveLimits, DriveState, DriveStatus, Mode


class XylodDrive(MotionDrive):
    """Live link to xylod (default localhost:5510 - same machine as the daemon)."""

    def __init__(self, limits: DriveLimits | None = None,
                 host: str = "127.0.0.1", port: int = 5510):
        super().__init__(limits)
        self.host, self.port = host, port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        # mirrored daemon state
        self._state = DriveState.DISCONNECTED
        self._mode = Mode.POSITION
        self._pos_deg = 0.0
        self._vel_degs = 0.0
        self._homed = False
        self._fault = ""
        self._xstate = "offline"      # xylod state string (running/settle/...)
        self._pass = -1
        # Xylosome scan context (from status pushes + pass events)
        self._progress = 0.0
        self._line_hz = 0.0
        self._filter_slot = -1
        self._estop_ok = True
        self._pass_filter = ""        # "R"/"G"/"B"/"C" from the pass_start event
        self._seq_passes = 0          # passes completed in the last finished sequence
        # last value WE commanded (for the scope's target trace)
        self._target_vel = 0.0
        self._target_pos = 0.0

    # ---- unit helpers ----------------------------------------------------
    def _deg2counts(self, deg: float) -> float:
        return deg / 360.0 * self.limits.counts_per_rev

    def _counts2deg(self, counts: float) -> float:
        return counts * 360.0 / self.limits.counts_per_rev

    # ---- lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        s = socket.create_connection((self.host, self.port), timeout=3.0)
        s.settimeout(0.5)
        self._sock = s
        self._send({"cmd": "hello", "client": "motosome"})
        self._send({"cmd": "status"})
        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None
        with self._lock:
            self._state = DriveState.DISCONNECTED
            self._xstate = "offline"

    # ---- protocol ----------------------------------------------------------
    def _send(self, obj: dict) -> None:
        if not self._sock:
            return
        try:
            self._sock.sendall((json.dumps(obj) + "\n").encode())
        except OSError:
            pass  # reader thread notices the dead socket

    def _reader(self) -> None:
        buf = b""
        while self._running and self._sock:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                chunk = b""
            if not chunk:                       # daemon gone - mark and retry
                with self._lock:
                    self._state = DriveState.DISCONNECTED
                    self._xstate = "offline"
                time.sleep(2.0)
                try:
                    s = socket.create_connection((self.host, self.port), timeout=2.0)
                    s.settimeout(0.5)
                    self._sock = s
                    self._send({"cmd": "hello", "client": "motosome"})
                except OSError:
                    continue
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    try:
                        self._handle(json.loads(line))
                    except (ValueError, KeyError):
                        pass

    def _handle(self, m: dict) -> None:
        ev = m.get("ev")
        if ev == "status":
            with self._lock:
                self._pos_deg = float(m.get("posDeg", 0.0))
                self._vel_degs = float(m.get("velDegS", 0.0))
                self._homed = bool(m.get("homed", False))
                self._xstate = m.get("state", "?")
                self._pass = int(m.get("pass", -1))
                self._progress = float(m.get("progress", 0.0))
                self._line_hz = float(m.get("lineHz", 0.0))
                self._filter_slot = int(m.get("filterSlot", -1))
                self._estop_ok = bool(m.get("estopOk", True))
                if self._xstate in ("fault", "estop") or not self._estop_ok:
                    self._state = DriveState.FAULT
                    self._fault = self._xstate
                else:
                    self._state = (DriveState.ENABLED if m.get("enabled")
                                   else DriveState.DISABLED)
                    self._fault = ""
                if self._xstate in ("running", "paused", "settle", "filter"):
                    self._mode = Mode.PROFILE      # a scan sequence is executing
                elif self._xstate == "jogging":
                    self._mode = Mode.VELOCITY
                else:
                    self._mode = Mode.POSITION
        elif ev == "pass_start":
            with self._lock:
                self._pass = int(m.get("pass", -1))
                self._pass_filter = m.get("filter", "")
        elif ev == "seq_done":
            with self._lock:
                self._seq_passes = int(m.get("passes", 0))
                self._pass_filter = ""
        elif ev == "fault":
            with self._lock:
                self._state = DriveState.FAULT
                self._fault = m.get("text", "fault")

    # ---- Xylosome scan context (read by the bench when this backend runs) -----
    def xylo_status(self) -> dict:
        with self._lock:
            return {
                "xstate": self._xstate,
                "pass": self._pass,
                "filter": self._pass_filter,
                "progress": self._progress,
                "line_hz": self._line_hz,
                "filter_slot": self._filter_slot,
                "estop_ok": self._estop_ok,
                "last_seq_passes": self._seq_passes,
            }

    # ---- commanding (translated to xylod protocol) ---------------------------
    def enable(self) -> None:
        self._send({"cmd": "enable"})

    def disable(self) -> None:
        self._send({"cmd": "disable"})

    def reset_fault(self) -> None:
        self._send({"cmd": "fault_reset"})

    def set_mode(self, mode: Mode) -> None:
        pass  # xylod picks the mode per command

    def command_velocity(self, counts_per_s: float) -> None:
        with self._lock:
            self._target_vel = counts_per_s
        self._send({"cmd": "jog", "velDegS": self._counts2deg(counts_per_s)})

    def command_position(self, counts: float) -> None:
        with self._lock:
            self._target_pos = counts
        self._send({"cmd": "moveTo", "posDeg": self._counts2deg(counts),
                    "velDegS": 20.0})

    def stop(self) -> None:
        with self._lock:
            self._target_vel = 0.0
        self._send({"cmd": "stop"})

    def home(self) -> None:
        self._send({"cmd": "home"})

    # ---- feedback -------------------------------------------------------------
    def status(self) -> DriveStatus:
        with self._lock:
            # During a daemon-driven scan the bench isn't the commander — a flat
            # zero "target" trace on the scope would be a lie. Mirror actual so
            # the traces overlap (reads as: axis following its own plan).
            scan = self._xstate in ("running", "paused", "settle", "filter", "moving")
            tvel = self._deg2counts(self._vel_degs) if scan else self._target_vel
            return DriveStatus(
                state=self._state,
                mode=self._mode,
                actual_position=self._deg2counts(self._pos_deg),
                actual_velocity=self._deg2counts(self._vel_degs),
                target_position=self._target_pos,
                target_velocity=tvel,
                homed=self._homed,
                fault_text=self._fault,
            )
