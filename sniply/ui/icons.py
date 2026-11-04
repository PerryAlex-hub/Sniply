"""Icons for the app's buttons.

Most actions map reasonably well onto Qt's built-in cross-platform
icon set (QStyle standard icons) -- used via standard_icon() below, no
asset files or extra dependencies needed. A handful of actions have no
good standard-icon match (gear, crosshair, camera, expand-diagonal);
those are drawn directly with QPainter as small flat vector icons in
the theme's neutral foreground color, kept visually consistent with
each other rather than mixing in another icon style.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QWidget

_SIZE = 18
_COLOR = "#e4e4e7"


def standard_icon(widget: QWidget, standard_pixmap) -> QIcon:
    """A built-in icon from the active Qt style, e.g.
    standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay)."""
    return widget.style().standardIcon(standard_pixmap)


def _blank_pixmap() -> QPixmap:
    pm = QPixmap(_SIZE, _SIZE)
    pm.fill(Qt.transparent)
    return pm


def _painter(pm: QPixmap) -> QPainter:
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(_COLOR)
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    return p


def gear_icon() -> QIcon:
    """Settings."""
    pm = _blank_pixmap()
    p = _painter(pm)
    center = QPointF(_SIZE / 2, _SIZE / 2)
    outer_r, inner_r, tooth_r = 7.0, 3.2, 8.6
    path = QPainterPath()
    teeth = 8
    import math

    for i in range(teeth * 2):
        angle = (math.pi * 2 / (teeth * 2)) * i
        r = tooth_r if i % 2 == 0 else outer_r
        pt = QPointF(center.x() + r * math.cos(angle), center.y() + r * math.sin(angle))
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    p.drawPath(path)
    p.drawEllipse(center, inner_r, inner_r)
    p.end()
    return QIcon(pm)


def crosshair_icon() -> QIcon:
    """Select capture area: a target/crosshair, circle with four
    outward tick marks -- a conventional "pick a region" glyph."""
    pm = _blank_pixmap()
    p = _painter(pm)
    center = QPointF(_SIZE / 2, _SIZE / 2)
    radius = 5.5
    p.drawEllipse(center, radius, radius)
    tick = 3.0
    gap = 1.0
    mid = _SIZE / 2
    p.drawLine(QPointF(mid, 0.5), QPointF(mid, 0.5 + tick - gap))
    p.drawLine(QPointF(mid, _SIZE - 0.5 - (tick - gap)), QPointF(mid, _SIZE - 0.5))
    p.drawLine(QPointF(0.5, mid), QPointF(0.5 + tick - gap, mid))
    p.drawLine(QPointF(_SIZE - 0.5 - (tick - gap), mid), QPointF(_SIZE - 0.5, mid))
    p.end()
    return QIcon(pm)


def camera_icon() -> QIcon:
    """Manual capture."""
    pm = _blank_pixmap()
    p = _painter(pm)
    body = QRectF(2, 6, 14, 10)
    p.drawRoundedRect(body, 1.5, 1.5)
    p.drawLine(QPointF(6, 6), QPointF(7.5, 4))
    p.drawLine(QPointF(7.5, 4), QPointF(10.5, 4))
    p.drawLine(QPointF(10.5, 4), QPointF(12, 6))
    p.drawEllipse(QPointF(9, 11), 3.2, 3.2)
    p.end()
    return QIcon(pm)


def expand_icon() -> QIcon:
    """Restore the full window from the floating control bar."""
    pm = _blank_pixmap()
    p = _painter(pm)
    # Two small diagonal arrows pointing away from the center.
    p.drawLine(QPointF(3, 9), QPointF(9, 3))
    p.drawLine(QPointF(9, 3), QPointF(9, 6.5))
    p.drawLine(QPointF(9, 3), QPointF(5.5, 3))
    p.drawLine(QPointF(15, 9), QPointF(9, 15))
    p.drawLine(QPointF(9, 15), QPointF(9, 11.5))
    p.drawLine(QPointF(9, 15), QPointF(12.5, 15))
    p.end()
    return QIcon(pm)
