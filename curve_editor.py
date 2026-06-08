"""
Speed-vs-time curve editor - the Xylosome-style drawable motion curve.

A canvas of draggable nodes defining velocity (rev/s) over time (s):
  * drag a node           - reshape the curve
  * double-click empty     - add a node
  * right-click / double-click a node - delete it (min 2 kept)

Emits `changed` whenever the curve is edited; `profile()` hands back a Profile
the ProfileRunner can play. Works in user units (rev/s, s); the runner converts
to encoder counts. Same data model as profile.Profile, so presets load straight in.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

import theme
from profile import Profile, ProfileKind


class CurveEditor(QtWidgets.QWidget):
    changed = QtCore.Signal()

    HANDLE = 5     # node half-size, px
    HIT = 13       # click hit radius, px

    def __init__(self, span: float = 3.0):
        super().__init__()
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.span = float(span)
        # default: a gentle trapezoid
        self.nodes: list[list[float]] = [
            [0.0, 0.0],
            [span * 0.2, 8.0],
            [span * 0.8, 8.0],
            [span, 0.0],
        ]
        self._vmax = 10.0
        self._drag: int | None = None
        self._recompute_vmax()

    # -- geometry ---------------------------------------------------------
    def _plot(self) -> QtCore.QRectF:
        ml, mr, mt, mb = 44, 12, 20, 24
        return QtCore.QRectF(ml, mt,
                             max(1.0, self.width() - ml - mr),
                             max(1.0, self.height() - mt - mb))

    def _to_px(self, t: float, v: float) -> QtCore.QPointF:
        r = self._plot()
        x = r.left() + (t / self.span) * r.width() if self.span > 0 else r.left()
        y = r.center().y() - (v / self._vmax) * (r.height() / 2) if self._vmax > 0 else r.center().y()
        return QtCore.QPointF(x, y)

    def _to_data(self, px: float, py: float) -> tuple[float, float]:
        r = self._plot()
        t = (px - r.left()) / r.width() * self.span if r.width() > 0 else 0.0
        v = (r.center().y() - py) / (r.height() / 2) * self._vmax if r.height() > 0 else 0.0
        return max(0.0, min(self.span, t)), max(-self._vmax, min(self._vmax, v))

    def _recompute_vmax(self):
        peak = max((abs(v) for _, v in self.nodes), default=1.0)
        self._vmax = max(2.0, peak * 1.2)

    # -- public API -------------------------------------------------------
    def set_span(self, span: float):
        self.span = max(0.2, float(span))
        for n in self.nodes:
            n[0] = max(0.0, min(self.span, n[0]))
        self.nodes.sort(key=lambda n: n[0])
        self.update()
        self.changed.emit()

    def load_preset(self, profile: Profile):
        self.nodes = [[float(t), float(v)] for t, v in profile.points] or [[0.0, 0.0]]
        self.span = max(0.2, self.nodes[-1][0])
        self._recompute_vmax()
        self.update()
        self.changed.emit()

    def profile(self) -> Profile:
        pts = sorted(self.nodes, key=lambda n: n[0])
        return Profile(kind=ProfileKind.VELOCITY, points=[(t, v) for t, v in pts])

    # -- mouse ------------------------------------------------------------
    def _node_at(self, x: float, y: float):
        for i, (t, v) in enumerate(self.nodes):
            p = self._to_px(t, v)
            if abs(p.x() - x) + abs(p.y() - y) <= self.HIT:
                return i
        return None

    def mousePressEvent(self, e):
        x, y = e.position().x(), e.position().y()
        i = self._node_at(x, y)
        if e.button() == QtCore.Qt.RightButton:
            if i is not None and len(self.nodes) > 2:
                del self.nodes[i]
                self._recompute_vmax()
                self.update()
                self.changed.emit()
            return
        if e.button() == QtCore.Qt.LeftButton and i is not None:
            self._drag = i

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        t, v = self._to_data(e.position().x(), e.position().y())
        lo = self.nodes[self._drag - 1][0] + 1e-3 if self._drag > 0 else 0.0
        hi = self.nodes[self._drag + 1][0] - 1e-3 if self._drag < len(self.nodes) - 1 else self.span
        t = max(lo, min(hi, t))
        self.nodes[self._drag] = [t, v]
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self._recompute_vmax()
            self.update()

    def mouseDoubleClickEvent(self, e):
        x, y = e.position().x(), e.position().y()
        i = self._node_at(x, y)
        if i is not None:
            if len(self.nodes) > 2:
                del self.nodes[i]
        else:
            t, v = self._to_data(x, y)
            self.nodes.append([t, v])
            self.nodes.sort(key=lambda n: n[0])
        self._recompute_vmax()
        self.update()
        self.changed.emit()

    # -- paint ------------------------------------------------------------
    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(theme.BG))
        r = self._plot()

        # grid
        p.setPen(QtGui.QPen(QtGui.QColor(theme.GRID), 1))
        for f in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = r.top() + f * r.height()
            p.drawLine(QtCore.QPointF(r.left(), y), QtCore.QPointF(r.right(), y))
            x = r.left() + f * r.width()
            p.drawLine(QtCore.QPointF(x, r.top()), QtCore.QPointF(x, r.bottom()))

        # zero line
        p.setPen(QtGui.QPen(QtGui.QColor(theme.ZERO), 1))
        zc = r.center().y()
        p.drawLine(QtCore.QPointF(r.left(), zc), QtCore.QPointF(r.right(), zc))

        # curve
        path = QtGui.QPainterPath()
        for i, (t, v) in enumerate(sorted(self.nodes, key=lambda n: n[0])):
            pt = self._to_px(t, v)
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        p.setPen(QtGui.QPen(QtGui.QColor(theme.SIGNAL), 2))
        p.drawPath(path)

        # nodes
        h = self.HANDLE
        for t, v in self.nodes:
            pt = self._to_px(t, v)
            p.setBrush(QtGui.QColor(theme.BG))
            p.setPen(QtGui.QPen(QtGui.QColor(theme.SIGNAL), 2))
            p.drawRect(QtCore.QRectF(pt.x() - h, pt.y() - h, 2 * h, 2 * h))

        # labels
        p.setPen(QtGui.QColor(theme.TEXT_DIM))
        p.setFont(theme.font(8))
        p.drawText(4, int(r.top()) + 9, f"+{self._vmax:.0f}")
        p.drawText(4, int(r.bottom()), f"-{self._vmax:.0f}")
        p.drawText(int(r.right()) - 34, int(r.bottom()) + 17, f"{self.span:.1f} s")
        p.drawText(int(r.left()), 13,
                   "SPEED CURVE  rev/s vs s   —  drag nodes · double-click add · right-click delete")
        p.end()
