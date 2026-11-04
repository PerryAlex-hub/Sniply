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
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStyle, QWidget

from sniply.capture.occlusion import exclude_window_from_capture
from sniply.ui.icons import camera_icon, expand_icon, standard_icon

_ICON_SIZE = QSize(15, 15)


class FloatingControlBar(QWidget):
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    manual_capture_requested = Signal()
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
            "#floatingBar { background-color: rgb(24, 24, 27); border: 1px solid rgb(63, 63, 70); }"
            "QPushButton { color: white; background-color: rgb(49, 49, 56); "
            "  border: none; padding: 6px 10px; border-radius: 4px; font-size: 12px; }"
            "QPushButton:hover { background-color: rgb(63, 63, 70); }"
            "QPushButton#stopBtn { background-color: rgb(127, 29, 29); }"
            "QPushButton#stopBtn:hover { background-color: rgb(153, 27, 27); }"
            "QLabel { color: #cccccc; padding: 0 8px; font-size: 12px; }"
        )

        self._drag_offset: QPoint | None = None

        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stopBtn")
        self.manual_button = QPushButton("Capture Now")
        self.status_label = QLabel("Capturing")
        self.expand_button = QPushButton()
        self.expand_button.setIcon(expand_icon())
        self.expand_button.setToolTip("Show full window")

        self.pause_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPause))
        self.resume_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay))
        self.stop_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaStop))
        self.manual_button.setIcon(camera_icon())
        for btn in (self.pause_button, self.resume_button, self.stop_button, self.manual_button, self.expand_button):
            btn.setIconSize(_ICON_SIZE)

        self.pause_button.clicked.connect(self.pause_requested)
        self.resume_button.clicked.connect(self.resume_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        self.manual_button.clicked.connect(self.manual_capture_requested)
        self.expand_button.clicked.connect(self.expand_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)
        for widget in (
            self.status_label,
            self.pause_button,
            self.resume_button,
            self.stop_button,
            self.manual_button,
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

    def set_status_text(self, text: str) -> None:
        self.status_label.setText(text)

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
