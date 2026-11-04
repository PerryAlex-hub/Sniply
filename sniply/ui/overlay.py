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

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QScreen
from PySide6.QtWidgets import QWidget

from sniply.capture.occlusion import process_at_region_center
from sniply.capture.region import InvalidRegionError, Region


class RegionOverlay(QWidget):
    """Created ONCE and reused for every "Select Capture Area" click
    (see ui/main_window.py), rather than constructed fresh each time.

    Constructing a brand-new top-level QWidget on every click means
    the OS has to create a brand-new native window surface every
    time, and that surface briefly renders opaque white before Qt's
    first custom paintEvent runs -- a visible flash on every single
    invocation. Reusing one instance pays that cost once, at app
    startup while the widget is still hidden, where nobody sees it.
    """

    # The int is a "target process" hint -- the PID owning the window
    # at the region's center the instant this overlay (and whatever
    # was behind it, i.e. the still-hidden main window) is out of the
    # way. See mouseReleaseEvent for why this exact moment matters.
    region_selected = Signal(Region, int)
    selection_cancelled = Signal()

    def __init__(self):
        super().__init__()
        self._screen: QScreen | None = None
        self._dpr = 1.0
        self._origin_logical = QPoint(0, 0)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Without these, Windows paints the window's native backing
        # surface as opaque white before Qt's first paintEvent ever
        # runs, producing a flash on first appearance even though the
        # widget is meant to be translucent from frame one.
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.setCursor(Qt.CrossCursor)

        self._start: QPoint | None = None
        self._end: QPoint | None = None

    def prepare_for_screen(self, screen: QScreen) -> None:
        """Reconfigure this reused overlay for a specific screen
        (which monitor to cover) right before showing it again, and
        reset any selection state left over from the previous use.

        Starts fully transparent (windowOpacity 0) rather than
        visible: the WA_NoSystemBackground/WA_TranslucentBackground
        attributes above reduce but don't always fully eliminate a
        brief opaque-white first frame on some Windows/driver
        combinations, since that first frame can render before the
        compositor has actually applied blending for a freshly shown
        window. windowOpacity is enforced by the compositor itself
        rather than by Qt's own paint, so forcing it to 0 here
        guarantees nothing is visible yet no matter what that first
        frame contains -- see show_revealed() for the other half.
        """
        self._screen = screen
        self._dpr = screen.devicePixelRatio()
        self._origin_logical = screen.geometry().topLeft()
        self._start = None
        self._end = None
        self.setGeometry(screen.geometry())
        self.setWindowOpacity(0.0)

    def show_revealed(self) -> None:
        """Show the overlay invisibly, then fade it in ~one frame
        later, once the compositor has actually processed a real
        paint. Call this instead of show() directly. A plain 0ms
        singleShot isn't reliably enough delay for that first real
        paint to land first on every system; ~16ms (one frame at
        60Hz) is cheap and gives it room.
        """
        self.show()
        QTimer.singleShot(16, lambda: self.setWindowOpacity(1.0))

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
            self.hide()
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
            self.hide()
            return

        # Hide BEFORE resolving the target process, and BEFORE emitting
        # the signal: the main window is still hidden at this instant
        # too (see ui/main_window.py's _on_select_area), so this is
        # the one moment where WindowFromPoint can actually see past
        # both of Sniply's own windows to the real content
        # underneath (e.g. PowerPoint). Resolving this later -- at
        # Start Capture click time, as an earlier version did -- always
        # found Sniply's own process instead, since clicking that
        # button necessarily means Sniply is what's focused.
        self.hide()
        target_pid = process_at_region_center(region)
        self.region_selected.emit(region, target_pid)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.selection_cancelled.emit()
            self.hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        rect = self._current_rect_local()
        if rect is None:
            painter.setPen(QColor(255, 255, 255))
            font = painter.font()
            font.setPointSize(14)
            painter.setFont(font)
            hint_rect = QRect(0, 40, self.width(), 40)
            painter.drawText(hint_rect, Qt.AlignHCenter, "Click and drag to select the slide area  —  Esc to cancel")

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
