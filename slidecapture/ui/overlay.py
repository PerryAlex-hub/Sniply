"""A translucent, Snipping-Tool-style full-screen overlay used to let
the user drag out the capture region with the mouse.

Coordinate handling note: Qt widget geometry is expressed in LOGICAL
pixels (DIPs), which get scaled by Windows display scaling (100%,
125%, 150%, ...) to physical pixels. mss, however, captures in
PHYSICAL pixels. If we naively used Qt's logical coordinates as the
capture region, the selection would drift on any scaled display. So
every point here gets multiplied by the screen's devicePixelRatio
before being turned into a Region.

Limitation (documented rather than silently wrong): this assumes a
uniform DPI scale across the virtual desktop. Windows supports
per-monitor DPI, which could in principle introduce drift on setups
mixing differently-scaled monitors -- acceptable for the MVP, callable
out as a known limitation rather than something that's pretended to
be solved.
"""

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QScreen
from PySide6.QtWidgets import QWidget

from slidecapture.capture.region import InvalidRegionError, Region


class RegionOverlay(QWidget):
    region_selected = Signal(Region)
    selection_cancelled = Signal()

    def __init__(self, screen: QScreen):
        super().__init__()
        self._screen = screen
        self._dpr = screen.devicePixelRatio()
        self._origin_logical = screen.geometry().topLeft()

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(screen.geometry())

        self._start: QPoint | None = None
        self._end: QPoint | None = None

    def _to_physical(self, local_point: QPoint) -> QPoint:
        global_logical = local_point + self._origin_logical
        return QPoint(round(global_logical.x() * self._dpr), round(global_logical.y() * self._dpr))

    def _current_rect_local(self) -> QRect | None:
        if self._start is None or self._end is None:
            return None
        return QRect(self._start, self._end).normalized()

    def mousePressEvent(self, event) -> None:
        self._start = event.position().toPoint()
        self._end = self._start
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._start is not None:
            self._end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        rect = self._current_rect_local()
        self._start = self._end = None
        if rect is None or rect.width() < 2 or rect.height() < 2:
            self.selection_cancelled.emit()
            self.close()
            return

        top_left_physical = self._to_physical(rect.topLeft())
        bottom_right_physical = self._to_physical(rect.bottomRight())

        try:
            region = Region.from_points(
                top_left_physical.x(), top_left_physical.y(),
                bottom_right_physical.x(), bottom_right_physical.y(),
            )
        except InvalidRegionError:
            self.selection_cancelled.emit()
            self.close()
            return

        self.region_selected.emit(region)
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.selection_cancelled.emit()
            self.close()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        rect = self._current_rect_local()
        if rect is not None:
            # Punch a clear "hole" over the selection so the user can
            # see the actual content they're about to capture.
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, Qt.transparent)
            painter.restore()

            pen = QPen(QColor(0, 150, 255), 2)
            painter.setPen(pen)
            painter.drawRect(rect)
