#!/usr/bin/env python3
"""
EtherCAT servo test bench - GUI.

A clean motion playground: connect, enable, jog, run at speed, move to position,
home, and play a speed-vs-time profile - with a live scope of velocity & position.

Runs today against a simulated motor (no hardware). When the StepperOnline
EtherCAT drive is on the bus, switch the backend to "EtherCAT" and the same
controls drive the real motor.

Run:  python3 bench.py
Deps: PySide6   (pip install --user PySide6   or   sudo apt install python3-pyside6)
"""

from __future__ import annotations

import time
from collections import deque

from PySide6 import QtCore, QtGui, QtWidgets

from drive import SimDrive, SoemDrive, DriveLimits, DriveState, Mode
from profile import Profile, ProfileRunner, Smoother
from curve_editor import CurveEditor
import theme

# Update rate of the GUI loop / sim integration
TICK_MS = 20
SCOPE_SECONDS = 6.0  # how much history the scope shows


class Scope(QtWidgets.QWidget):
    """A tiny rolling strip-chart (velocity + target), drawn by hand - no extra deps."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(160)
        n = int(SCOPE_SECONDS * 1000 / TICK_MS)
        self.actual = deque([0.0] * n, maxlen=n)
        self.target = deque([0.0] * n, maxlen=n)
        self.vrange = 1.0  # rev/s, auto-grown

    def push(self, actual: float, target: float):
        self.actual.append(actual)
        self.target.append(target)
        peak = max(1.0, max(abs(v) for v in self.actual), max(abs(v) for v in self.target))
        # smooth auto-range
        self.vrange = self.vrange * 0.95 + peak * 0.05
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QtGui.QColor(theme.BG))
        mid = h / 2
        rng = max(self.vrange * 1.2, 0.5)

        # grid + zero line
        p.setPen(QtGui.QPen(QtGui.QColor(theme.GRID), 1))
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = frac * h
            p.drawLine(0, int(y), w, int(y))
        p.setPen(QtGui.QPen(QtGui.QColor(theme.ZERO), 1))
        p.drawLine(0, int(mid), w, int(mid))

        def draw(series, color):
            p.setPen(QtGui.QPen(QtGui.QColor(color), 2))
            n = len(series)
            if n < 2:
                return
            path = QtGui.QPainterPath()
            for i, v in enumerate(series):
                x = w * i / (n - 1)
                y = mid - (v / rng) * (h * 0.45)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.drawPath(path)

        draw(self.target, theme.TEXT_DIM)   # setpoint
        draw(self.actual, theme.SIGNAL)     # actual velocity

        p.setPen(QtGui.QColor(theme.TEXT_DIM))
        p.setFont(theme.font(8))
        p.drawText(8, 16, f"VELOCITY   ±{rng:.1f} rev/s    —  actual / target")
        p.end()


class BenchWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MOTOSOME · servo bench")
        self.limits = DriveLimits(counts_per_rev=10000)  # adjust per real drive later
        self.drive = SimDrive(self.limits)
        self.runner: ProfileRunner | None = None
        self._last_t = time.monotonic()

        self._build_ui()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(10)
        outer.addWidget(self._make_header())

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(10)
        outer.addLayout(body, 1)

        controls = QtWidgets.QVBoxLayout()
        body.addLayout(controls, 0)

        right = QtWidgets.QVBoxLayout()
        body.addLayout(right, 1)

        # the editable speed curve (created early so the Span control can bind to it)
        self.curve = CurveEditor(span=3.0)

        # --- connection ---
        conn = QtWidgets.QGroupBox("Connection")
        cl = QtWidgets.QGridLayout(conn)
        self.backend_box = QtWidgets.QComboBox()
        self.backend_box.addItems(["Simulated", "EtherCAT (enp4s0)"])
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.clicked.connect(self._on_connect)
        cl.addWidget(QtWidgets.QLabel("Backend"), 0, 0)
        cl.addWidget(self.backend_box, 0, 1)
        cl.addWidget(self.connect_btn, 1, 0, 1, 2)
        controls.addWidget(conn)

        # --- drive power ---
        power = QtWidgets.QGroupBox("Drive")
        pl = QtWidgets.QHBoxLayout(power)
        self.enable_btn = QtWidgets.QPushButton("Enable")
        self.enable_btn.clicked.connect(lambda: self.drive.enable())
        self.disable_btn = QtWidgets.QPushButton("Disable")
        self.disable_btn.clicked.connect(lambda: self.drive.disable())
        self.fault_btn = QtWidgets.QPushButton("Reset Fault")
        self.fault_btn.clicked.connect(lambda: self.drive.reset_fault())
        pl.addWidget(self.enable_btn)
        pl.addWidget(self.disable_btn)
        pl.addWidget(self.fault_btn)
        controls.addWidget(power)

        # --- jog ---
        jog = QtWidgets.QGroupBox("Jog (hold)")
        jl = QtWidgets.QGridLayout(jog)
        self.jog_speed = self._spin(5.0, 0.0, 50.0, " rev/s")
        jog_minus = QtWidgets.QPushButton("◀  Jog −")
        jog_plus = QtWidgets.QPushButton("Jog +  ▶")
        jog_minus.pressed.connect(lambda: self.drive.command_velocity(-self._counts(self.jog_speed.value())))
        jog_minus.released.connect(lambda: self.drive.stop())
        jog_plus.pressed.connect(lambda: self.drive.command_velocity(self._counts(self.jog_speed.value())))
        jog_plus.released.connect(lambda: self.drive.stop())
        jl.addWidget(QtWidgets.QLabel("Jog speed"), 0, 0)
        jl.addWidget(self.jog_speed, 0, 1)
        jl.addWidget(jog_minus, 1, 0)
        jl.addWidget(jog_plus, 1, 1)
        controls.addWidget(jog)

        # --- run at velocity ---
        vel = QtWidgets.QGroupBox("Run at speed")
        vlay = QtWidgets.QGridLayout(vel)
        self.vel_target = self._spin(10.0, -50.0, 50.0, " rev/s")
        run_btn = QtWidgets.QPushButton("Run")
        run_btn.clicked.connect(lambda: self.drive.command_velocity(self._counts(self.vel_target.value())))
        stop_btn = QtWidgets.QPushButton("Stop")
        stop_btn.clicked.connect(lambda: self.drive.stop())
        stop_btn.setProperty("role", "action")
        vlay.addWidget(QtWidgets.QLabel("Target"), 0, 0)
        vlay.addWidget(self.vel_target, 0, 1)
        vlay.addWidget(run_btn, 1, 0)
        vlay.addWidget(stop_btn, 1, 1)
        controls.addWidget(vel)

        # --- move to position ---
        pos = QtWidgets.QGroupBox("Move to position")
        play = QtWidgets.QGridLayout(pos)
        self.pos_target = self._spin(10.0, -10000.0, 10000.0, " rev")
        move_btn = QtWidgets.QPushButton("Move")
        move_btn.clicked.connect(lambda: self.drive.command_position(self._counts(self.pos_target.value())))
        home_btn = QtWidgets.QPushButton("Home")
        home_btn.clicked.connect(lambda: self.drive.home())
        play.addWidget(QtWidgets.QLabel("Target"), 0, 0)
        play.addWidget(self.pos_target, 0, 1)
        play.addWidget(move_btn, 1, 0)
        play.addWidget(home_btn, 1, 1)
        controls.addWidget(pos)

        # --- speed curve ---
        prof = QtWidgets.QGroupBox("Speed curve")
        prl = QtWidgets.QGridLayout(prof)
        self.prof_span = self._spin(3.0, 0.5, 120.0, " s")
        self.prof_span.valueChanged.connect(lambda val: self.curve.set_span(val))
        self.prof_kind = QtWidgets.QComboBox()
        self.prof_kind.addItems(["Trapezoid", "Sine sweep"])
        self.prof_peak = self._spin(8.0, 0.0, 50.0, " rev/s")
        self.prof_ramp = self._spin(0.4, 0.0, 10.0, " s")
        self.prof_hold = self._spin(1.0, 0.0, 60.0, " s")
        load_btn = QtWidgets.QPushButton("Load preset → editor")
        load_btn.clicked.connect(self._on_load_preset)
        self.play_btn = QtWidgets.QPushButton("▶ Play curve")
        self.play_btn.clicked.connect(self._on_play)
        self.play_btn.setProperty("role", "action")
        pstop = QtWidgets.QPushButton("■ Stop")
        pstop.clicked.connect(self._on_profile_stop)
        pstop.setProperty("role", "action")
        prl.addWidget(QtWidgets.QLabel("Span"), 0, 0); prl.addWidget(self.prof_span, 0, 1)
        prl.addWidget(QtWidgets.QLabel("Preset"), 1, 0); prl.addWidget(self.prof_kind, 1, 1)
        prl.addWidget(QtWidgets.QLabel("Peak"), 2, 0); prl.addWidget(self.prof_peak, 2, 1)
        prl.addWidget(QtWidgets.QLabel("Ramp"), 3, 0); prl.addWidget(self.prof_ramp, 3, 1)
        prl.addWidget(QtWidgets.QLabel("Hold"), 4, 0); prl.addWidget(self.prof_hold, 4, 1)
        prl.addWidget(load_btn, 5, 0, 1, 2)
        prl.addWidget(self.play_btn, 6, 0); prl.addWidget(pstop, 6, 1)
        controls.addWidget(prof)

        # --- smoothing (make the drawn curve physically followable) ---
        sm = QtWidgets.QGroupBox("Smoothing")
        sml = QtWidgets.QGridLayout(sm)
        self.sm_accel = self._spin(25.0, 0.0, 2000.0, " rev/s²")
        self.sm_jerk = self._spin(0.0, 0.0, 20000.0, " rev/s³")
        hint = QtWidgets.QLabel("jerk 0 = accel-clamp · jerk > 0 = S-curve")
        hint.setStyleSheet(f"color:{theme.TEXT_DIM};")
        hint.setFont(theme.font(8))
        sml.addWidget(QtWidgets.QLabel("Max accel"), 0, 0); sml.addWidget(self.sm_accel, 0, 1)
        sml.addWidget(QtWidgets.QLabel("Max jerk"), 1, 0); sml.addWidget(self.sm_jerk, 1, 1)
        sml.addWidget(hint, 2, 0, 1, 2)
        controls.addWidget(sm)

        controls.addStretch(1)

        # --- right: editable curve (draw) over the live scope (watch it run) ---
        right.addWidget(self.curve, 1)
        self.scope = Scope()
        right.addWidget(self.scope, 1)

        read = QtWidgets.QGroupBox("Status")
        rl = QtWidgets.QGridLayout(read)
        self.lbl_state = QtWidgets.QLabel("—")
        self.lbl_mode = QtWidgets.QLabel("—")
        self.lbl_pos = QtWidgets.QLabel("—")
        self.lbl_vel = QtWidgets.QLabel("—")
        self.lbl_homed = QtWidgets.QLabel("—")
        for i, (name, lbl) in enumerate([
            ("State", self.lbl_state), ("Mode", self.lbl_mode),
            ("Position", self.lbl_pos), ("Velocity", self.lbl_vel),
            ("Homed", self.lbl_homed),
        ]):
            rl.addWidget(QtWidgets.QLabel(name), i, 0)
            f = lbl.font(); f.setPointSize(f.pointSize() + 2); f.setBold(True); lbl.setFont(f)
            rl.addWidget(lbl, i, 1)
        right.addWidget(read, 0)

        self.resize(1000, 760)

    def _make_header(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(2, 2, 2, 0)

        h.addWidget(self._make_logo(height=26), 0)
        h.addStretch(1)

        title = QtWidgets.QLabel("MOTOSOME")
        title.setFont(theme.font(12, bold=True))
        title.setStyleSheet(f"color:{theme.TEXT};")
        sub = QtWidgets.QLabel("servo bench")
        sub.setFont(theme.font(9))
        sub.setStyleSheet(f"color:{theme.TEXT_DIM};")
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(title, 0, QtCore.Qt.AlignRight)
        col.addWidget(sub, 0, QtCore.Qt.AlignRight)
        h.addLayout(col, 0)

        line = QtWidgets.QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{theme.BORDER};")

        wrap = QtWidgets.QWidget()
        wl = QtWidgets.QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(8)
        wl.addWidget(bar)
        wl.addWidget(line)
        return wrap

    def _make_logo(self, height: int = 26) -> QtWidgets.QWidget:
        try:
            from PySide6.QtSvgWidgets import QSvgWidget
            w = QSvgWidget(theme.LOGO_PATH)
            size = w.renderer().defaultSize()
            if size.height() > 0:
                w.setFixedSize(int(height * size.width() / size.height()), height)
            return w
        except Exception:
            lbl = QtWidgets.QLabel("⬡ honeycomb modular")
            lbl.setFont(theme.font(11, bold=True))
            lbl.setStyleSheet(f"color:{theme.TEXT};")
            return lbl

    def _spin(self, val, lo, hi, suffix):
        s = QtWidgets.QDoubleSpinBox()
        s.setRange(lo, hi); s.setDecimals(2); s.setValue(val); s.setSuffix(suffix)
        s.setSingleStep(0.5)
        return s

    def _counts(self, rev: float) -> float:
        return rev * self.limits.counts_per_rev

    # --------------------------------------------------------------- slots
    def _on_connect(self, checked):
        if checked:
            if self.backend_box.currentIndex() == 1:
                self.drive = SoemDrive("enp4s0", self.limits)
            else:
                self.drive = SimDrive(self.limits)
            try:
                self.drive.connect()
                self.connect_btn.setText("Disconnect")
            except NotImplementedError as e:
                self.connect_btn.setChecked(False)
                QtWidgets.QMessageBox.information(
                    self, "Hardware backend not wired yet", str(e))
                self.drive = SimDrive(self.limits)
        else:
            self.drive.disconnect()
            self.connect_btn.setText("Connect")

    def _preset_profile(self) -> Profile:
        if self.prof_kind.currentText() == "Trapezoid":
            return Profile.trapezoid(self.prof_peak.value(),
                                     self.prof_ramp.value(),
                                     self.prof_hold.value())
        return Profile.sine_sweep(self.prof_peak.value(),
                                  period_s=max(0.2, 2 * self.prof_ramp.value() + self.prof_hold.value()))

    def _on_load_preset(self):
        self.curve.load_preset(self._preset_profile())
        self.prof_span.blockSignals(True)
        self.prof_span.setValue(self.curve.span)
        self.prof_span.blockSignals(False)

    def _on_play(self):
        smoother = Smoother(self.sm_accel.value(), self.sm_jerk.value())
        self.runner = ProfileRunner(self.drive, self.curve.profile(),
                                    self.limits.counts_per_rev, smoother=smoother)
        self.runner.start(time.monotonic())

    def _on_profile_stop(self):
        if self.runner:
            self.runner.stop()
            self.runner = None

    # ---------------------------------------------------------------- loop
    def _tick(self):
        now = time.monotonic()
        dt = now - self._last_t
        self._last_t = now

        if self.runner and self.runner.playing:
            self.runner.tick(now)
        elif self.runner and not self.runner.playing:
            self.runner = None

        # integrate the simulator (real backend runs its own cyclic loop)
        if isinstance(self.drive, SimDrive):
            self.drive.update(dt)

        s = self.drive.status()
        vel_rev = s.actual_velocity / self.limits.counts_per_rev
        tgt_rev = s.target_velocity / self.limits.counts_per_rev
        self.scope.push(vel_rev, tgt_rev)

        self.lbl_state.setText(s.state.value)
        self.lbl_mode.setText(s.mode.value)
        self.lbl_pos.setText(f"{s.actual_position / self.limits.counts_per_rev:+.3f} rev")
        self.lbl_vel.setText(f"{vel_rev:+.3f} rev/s")
        self.lbl_homed.setText("yes" if s.homed else "no")
        color = {DriveState.ENABLED: theme.OK, DriveState.FAULT: theme.FAULT,
                 DriveState.DISABLED: theme.TEXT_DIM, DriveState.DISCONNECTED: theme.IDLE}
        self.lbl_state.setStyleSheet(f"color:{color.get(s.state, theme.TEXT)}")


def main():
    app = QtWidgets.QApplication([])
    theme.apply(app)
    win = BenchWindow()
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
