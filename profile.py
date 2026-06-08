"""
Speed/position-vs-time profiles for the bench - the "draw a curve and play it" part.

A Profile is a list of (time_s, value) breakpoints, linearly interpolated. `kind`
says whether `value` is a velocity (rev/s) or a position (rev). The GUI plays a
profile by sampling `value_at(t)` every tick and pushing it to the drive as a
velocity (VELOCITY mode) or position (POSITION mode) setpoint.

This is deliberately the same idea as the Xylosome curve editor, so the bench's
profile player is a direct prototype of the eventual motion backend.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class ProfileKind(Enum):
    VELOCITY = "velocity"   # values are rev/s
    POSITION = "position"   # values are rev (absolute)


@dataclass
class Profile:
    kind: ProfileKind = ProfileKind.VELOCITY
    # breakpoints sorted by time; (t_seconds, value_user_units)
    points: list[tuple[float, float]] = field(default_factory=lambda: [(0.0, 0.0)])

    @property
    def duration(self) -> float:
        return self.points[-1][0] if self.points else 0.0

    def value_at(self, t: float) -> float:
        """Linearly interpolate the profile value at time t (seconds)."""
        pts = self.points
        if not pts:
            return 0.0
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t0 <= t <= t1:
                if t1 == t0:
                    return v1
                frac = (t - t0) / (t1 - t0)
                return v0 + frac * (v1 - v0)
        return pts[-1][1]

    # --- handy presets ---------------------------------------------------
    @staticmethod
    def trapezoid(peak_rev_s: float, ramp_s: float, hold_s: float) -> "Profile":
        """A symmetric speed ramp: 0 -> peak (ramp), hold, peak -> 0 (ramp)."""
        return Profile(
            kind=ProfileKind.VELOCITY,
            points=[
                (0.0, 0.0),
                (ramp_s, peak_rev_s),
                (ramp_s + hold_s, peak_rev_s),
                (2 * ramp_s + hold_s, 0.0),
            ],
        )

    @staticmethod
    def sine_sweep(peak_rev_s: float, period_s: float, cycles: int = 1,
                   steps_per_cycle: int = 24) -> "Profile":
        """A smooth sinusoidal speed sweep (nice for visualising response)."""
        import math
        pts: list[tuple[float, float]] = []
        total_steps = cycles * steps_per_cycle
        for i in range(total_steps + 1):
            t = period_s * i / steps_per_cycle
            v = peak_rev_s * math.sin(2 * math.pi * (i / steps_per_cycle))
            pts.append((t, v))
        return Profile(kind=ProfileKind.VELOCITY, points=pts)


class Smoother:
    """
    Trajectory shaper: turns a *desired* velocity (rev/s) into a *feasible* one a
    real servo can actually follow, by limiting acceleration and (optionally) jerk.

    A drawn curve has sharp corners (step changes in acceleration = infinite jerk),
    which a motor can't follow — it would lag, vibrate, and may fault. Feed the
    desired velocity through `step()` each cycle and you get a command stream that
    respects the machine's limits, in sim and on the real drive alike.

    - max_accel > 0, max_jerk == 0  -> acceleration clamp (trapezoidal velocity)
    - max_accel > 0, max_jerk > 0   -> S-curve (jerk-limited, rounded corners)
    - max_accel == 0                -> pass-through (no shaping)
    """

    def __init__(self, max_accel: float = 0.0, max_jerk: float = 0.0):
        self.max_accel = float(max_accel)   # rev/s^2
        self.max_jerk = float(max_jerk)     # rev/s^3
        self.v = 0.0                        # current commanded velocity
        self.a = 0.0                        # current commanded acceleration

    def reset(self, v: float = 0.0) -> None:
        self.v = v
        self.a = 0.0

    def step(self, v_des: float, dt: float) -> float:
        if dt <= 0:
            return self.v
        if self.max_accel <= 0:                      # no shaping
            self.v, self.a = v_des, 0.0
            return self.v

        if self.max_jerk <= 0:                        # acceleration clamp
            step = self.max_accel * dt
            dv = max(-step, min(step, v_des - self.v))
            self.v += dv
            self.a = dv / dt
            return self.v

        # jerk-limited S-curve: cap accel so it can still ramp to 0 by v_des,
        # then slew the acceleration toward that cap at the jerk limit.
        err = v_des - self.v
        a_cap = min(self.max_accel, math.sqrt(2.0 * self.max_jerk * abs(err))) if err else 0.0
        a_des = math.copysign(a_cap, err)
        da = self.max_jerk * dt
        self.a += max(-da, min(da, a_des - self.a))
        self.a = max(-self.max_accel, min(self.max_accel, self.a))
        self.v += self.a * dt
        return self.v


class ProfileRunner:
    """
    Plays a Profile against a MotionDrive. Tick it with the elapsed wall time;
    it pushes the right setpoint and reports when finished.
    """

    def __init__(self, drive, profile: Profile, counts_per_rev: int,
                 smoother: "Smoother | None" = None):
        self.drive = drive
        self.profile = profile
        self.counts_per_rev = counts_per_rev
        self.smoother = smoother
        self._t0: float | None = None
        self._last: float | None = None
        self._playing = False

    @property
    def playing(self) -> bool:
        return self._playing

    def start(self, now: float) -> None:
        self._t0 = now
        self._last = now
        self._playing = True
        if self.smoother:
            self.smoother.reset(0.0)

    def stop(self) -> None:
        self._playing = False
        self.drive.stop()

    def tick(self, now: float) -> float:
        """Push the current setpoint; return elapsed time. Auto-stops at the end."""
        if not self._playing or self._t0 is None:
            return 0.0
        t = now - self._t0
        dt = (now - self._last) if self._last is not None else 0.0
        self._last = now
        value = self.profile.value_at(t)
        if self.profile.kind == ProfileKind.VELOCITY:
            if self.smoother:
                value = self.smoother.step(value, dt)
            self.drive.command_velocity(value * self.counts_per_rev)
        else:
            self.drive.command_position(value * self.counts_per_rev)
        if t >= self.profile.duration:
            self.stop()
        return t
