"""A small, draggable, always-on-top control bar shown WHILE capture
is actively running, so the user never needs the full application
window back on screen to pause/resume/stop or take a manual shot --
and Sniply never has to hide/show the large main window during
an active session, which was a source of visible flicker on top of
just being disruptive.

Excluded from screen capture via SetWindowDisplayAffinity (see
capture/occlusion.py) -- it can be dragged anywhere on screen,
including directly over the capture region itself, without ever
appearing in a saved screenshot. The exclusion is also why the
capture engine's occlusion guard special-cases Sniply's own
process (see worker.py): this bar visually sitting over the target
doesn't actually affect what mss captures underneath it.
"""

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QStyle, QWidget

from sniply.capture.occlusion import exclude_window_from_capture
from sniply.ui.components import StatusPill
from sniply.ui.icons import camera_icon, crosshair_icon, expand_icon, standard_icon
from sniply.ui.theme import TOKENS

_ICON_SIZE = QSize(15, 15)


class FloatingControlBar(QWidget):
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    manual_capture_requested = Signal()
    reselect_requested = Signal()
    expand_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        # Deliberately NOT translucent (no WA_TranslucentBackground /
        # WA_NoSystemBackground, unlike the selection overlay). Those
        # attributes hand painting responsibility entirely to Qt's
        # stylesheet/child-widget rendering with no guaranteed fallback
        # fill; the overlay gets away with it because it has its own
        # explicit paintEvent() that always fills something. This bar
        # doesn't, and on at least one real system the result was a
        # window that rendered nothing at all -- genuinely invisible,
        # not just excluded from capture. A plain opaque background,
        # guaranteed to paint via Qt's normal widget painting, is worth
        # more here than rounded-corner translucency.
        self.setAutoFillBackground(True)
        self.setObjectName("floatingBar")
        self.setStyleSheet(
            f"#floatingBar {{ background-color: {TOKENS['SURFACE']}; "
            f"  border: 1px solid {TOKENS['BORDER_STRONG']}; border-radius: 10px; }}"
            f"QPushButton {{ color: {TOKENS['TEXT']}; background-color: {TOKENS['SURFACE_RAISED']}; "
            f"  border: 1px solid {TOKENS['BORDER_STRONG']}; padding: 6px 11px; "
            f"  border-radius: 7px; font-size: 12px; font-weight: 500; }}"
            f"QPushButton:hover {{ background-color: #2a2a33; }}"
            f"QPushButton#stopBtn {{ background-color: transparent; color: {TOKENS['DANGER']}; "
            f"  border-color: #4d2626; }}"
            f"QPushButton#stopBtn:hover {{ background-color: {TOKENS['DANGER_DIM']}; "
            f"  border-color: {TOKENS['DANGER']}; }}"
        )

        self._drag_offset: QPoint | None = None

        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stopBtn")
        self.manual_button = QPushButton("Capture Now")
        # Reselect is on the bar, not only in the main window,
        # because losing the capture area happens WHILE the main
        # window is hidden behind a lecture -- which is precisely when
        # the fix needs to be one click away rather than behind
        # Expand. Icon-only to keep the bar narrow.
        self.reselect_button = QPushButton()
        self.reselect_button.setIcon(crosshair_icon())
        self.reselect_button.setToolTip("Reselect the slide area")
        # The same pill as the main window's, so "what is it doing
        # right now" looks identical in both places.
        self.status_pill = StatusPill("Capturing", "live")
        self.expand_button = QPushButton()
        self.expand_button.setIcon(expand_icon())
        self.expand_button.setToolTip("Show full window")

        self.pause_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPause))
        self.resume_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay))
        self.stop_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaStop))
        self.manual_button.setIcon(camera_icon())
        for btn in (
            self.pause_button, self.resume_button, self.stop_button,
            self.manual_button, self.reselect_button, self.expand_button,
        ):
            btn.setIconSize(_ICON_SIZE)

        self.pause_button.clicked.connect(self.pause_requested)
        self.resume_button.clicked.connect(self.resume_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        self.manual_button.clicked.connect(self.manual_capture_requested)
        self.reselect_button.clicked.connect(self.reselect_requested)
        self.expand_button.clicked.connect(self.expand_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(6)
        for widget in (
            self.status_pill,
            self.pause_button,
            self.resume_button,
            self.manual_button,
            self.reselect_button,
            self.stop_button,
            self.expand_button,
        ):
            layout.addWidget(widget)

        self.set_paused(False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Harmless to call on every show -- idempotent, and the native
        # handle only exists once the widget has actually been shown
        # at least once, so this can't be done any earlier than here.
        exclude_window_from_capture(int(self.winId()))

    def set_paused(self, paused: bool) -> None:
        self.pause_button.setVisible(not paused)
        self.resume_button.setVisible(paused)

    def set_status(self, text: str, state: str = "idle") -> None:
        """`state` is one of theme.PILL_COLOURS' keys. The bar is
        often the only part of Sniply on screen during a lecture, so
        anything worth saying in the main window has to be sayable
        here too -- including that capture has stopped saving because
        it lost the selected area."""
        self.status_pill.set_status(text, state)
        self.adjustSize()

    def set_saved_count(self, saved: int, duplicates: int) -> None:
        self.status_pill.setToolTip(
            f"{self.status_pill.text()}\n{saved} saved · {duplicates} duplicates ignored"
        )

    # ---- dragging: a frameless window has no native title bar to
    # drag by, so the whole bar area itself serves as the drag handle.

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
