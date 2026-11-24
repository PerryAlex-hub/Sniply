"""The main application window.

This is the only module that touches both the Qt UI and the capture
engine. It owns the CaptureController (see capture/worker.py) and
connects its signals -- which arrive on the UI thread via Qt's queued
cross-thread delivery -- to ordinary widget updates. No other module
should import QWidget-family classes and also import CaptureWorker;
keeping that boundary one-directional is what keeps detection/
capture testable without a display.
"""

import time
import webbrowser
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from sniply.capture.region import CaptureAnchor, Region
from sniply.capture.worker import CaptureController
from sniply.config.defaults import AppConfig
from sniply.detection.pipeline import DetectionPipeline
from sniply.paths import SESSIONS_DIR
from sniply.export.image_export import export_images
from sniply.export.pdf_export import EmptyExportError, export_pdf
from sniply.storage.session import SessionStore
from sniply.ui.components import (
    Card,
    EmptyState,
    StatTile,
    StatusPill,
    horizontal_divider,
    wordmark,
)
from sniply.ui.floating_bar import FloatingControlBar
from sniply.ui.gallery import GalleryWidget
from sniply.ui.icons import camera_icon, crosshair_icon, gear_icon, standard_icon
from sniply.ui.viewer import ScreenshotViewerDialog
from sniply.ui.overlay import RegionOverlay
from sniply.ui.session_dialog import NewSessionDialog, OpenSessionDialog
from sniply.ui.settings_dialog import SettingsDialog
from sniply.update_checker import UpdateInfo, start_update_check
from sniply.__version__ import __version__

_ICON_SIZE = QSize(16, 16)

# Below this window width the two panes stop fitting side by side and
# are stacked instead (see MainWindow._apply_responsive_layout). It is
# the sum of both panes' minimum widths plus the splitter handle and
# the window's own margins -- i.e. the width at which a horizontal
# split genuinely stops working, not a round number.
STACK_PANES_BELOW_WIDTH = 660

# Where raw session data (screenshots + session.db) lives -- the
# app's own storage, not something the user picks. Re-exported from
# sniply.paths, which is the single definition of every location
# Sniply writes to, rather than this module and the entry point each
# deriving their own and drifting apart.
APP_DATA_ROOT = SESSIONS_DIR

# Where exported PDFs/images default to -- a deliberate, separate
# choice the user makes only when they're ready to hand off a
# finished deck, not at session-creation time.
DEFAULT_EXPORT_DIR = Path.home() / "Downloads"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sniply")
        self.resize(1100, 740)
        # Small enough to sit docked beside a lecture window: below
        # STACK_PANES_BELOW_WIDTH the panes stack rather than squeeze,
        # so a narrow window stays readable instead of truncating
        # every control. The width floor is what the top bar actually
        # needs at its natural size -- going under it would only buy a
        # horizontal scrollbar, which is a worse answer than a window
        # that declines to get narrower.
        self.setMinimumSize(480, 420)

        self.config = AppConfig()
        self.store: SessionStore | None = None
        self.session_id: int | None = None
        self.session_name: str | None = None
        self.save_dir: Path | None = None
        self.region: Region | None = None
        # Which window (if any) owns the selected region, resolved
        # once at selection time (see RegionOverlay.mouseReleaseEvent)
        # -- this, not whatever's in front when Start Capture is later
        # clicked, is the correct basis for both the occlusion guard
        # and (when a window was resolved) direct window capture.
        self._anchor = CaptureAnchor()
        self.controller: CaptureController | None = None
        # Created once and reused for every region selection -- see
        # RegionOverlay's docstring for why (avoids re-creating a
        # native window, and its first-paint flash, on every click).
        self._overlay = RegionOverlay()
        self._overlay.region_selected.connect(self._on_region_selected)
        self._overlay.selection_cancelled.connect(self._on_selection_cancelled)

        # Shown instead of this main window while a capture session is
        # actively running (see _enter_compact_mode) -- a small,
        # draggable, always-on-top bar with just Pause/Resume/Stop/
        # Manual Capture/Expand, so the user never needs the full
        # window back on screen (and the app never has to repeatedly
        # hide/show it) just to glance at or watch a lecture.
        self._floating_bar = FloatingControlBar()
        self._floating_bar.pause_requested.connect(self._on_pause)
        self._floating_bar.resume_requested.connect(self._on_resume)
        self._floating_bar.reselect_requested.connect(self._on_select_area)
        self._floating_bar.stop_requested.connect(self._on_stop)
        self._floating_bar.manual_capture_requested.connect(self._on_manual_capture)
        self._floating_bar.expand_requested.connect(self._exit_compact_mode)

        self.saved_count = 0
        self.duplicate_count = 0
        self._paused = False
        self._session_start: float | None = None

        self._duration_timer = QTimer(self)
        self._duration_timer.setInterval(1000)
        self._duration_timer.timeout.connect(self._update_duration_label)

        self._pending_update: UpdateInfo | None = None

        self._build_ui()
        self._set_capture_controls_enabled(has_region=False, is_capturing=False)

        # Non-blocking: runs on a background thread (see
        # update_checker.py) so a slow/unreachable GitHub never delays
        # opening the app. _update_check_thread must be kept alive as
        # an attribute for the duration of the check, or Python may
        # garbage-collect the QThread mid-request.
        self._update_check_thread = start_update_check(self._on_update_available)

    # ---- UI construction ----------------------------------------

    def _build_ui(self) -> None:
        """Lays out the window.

        The shape of the interface follows what the user is actually
        doing at each moment, which the old single-column-of-
        groupboxes layout did not:

        - A **top bar** holds identity and the things that are true
          for the whole app (which session is open, settings), so they
          stop competing for space with the controls that change
          constantly.
        - The **live preview is the hero**, not a 160px thumbnail
          wedged between two button stacks. It is the only thing that
          answers "is this capturing the right thing?", which is the
          question this app lives or dies by -- and the question its
          capture bug made urgent. It carries the lock-state pill in
          its own header, where the answer belongs.
        - **Capture controls are rows**, with the primary action
          weighted and Pause/Resume collapsed into one toggle rather
          than two buttons of which one is always disabled.
        - **Counters became stat tiles**: mid-lecture the user glances
          for a number, so the number gets the size and the words
          shrink to captions.
        - The **gallery owns the right pane** and explains what will
          appear there, instead of an empty box that reads as broken.
        """
        root_widget = QWidget()
        self.setCentralWidget(root_widget)
        shell = QVBoxLayout(root_widget)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        shell.addWidget(self._build_top_bar())

        # Everything below the top bar scrolls: if the window is
        # opened or dragged smaller than the content needs, it scrolls
        # rather than clipping controls or squeezing the layout into
        # something unusable.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        shell.addWidget(scroll, stretch=1)

        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(self._build_get_started_banner())
        root.addWidget(self._build_update_banner())

        # Two panes: setting up and running a capture on the left,
        # reviewing and exporting results on the right. A splitter
        # rather than fixed widths so each pane shrinks to its own
        # real minimum instead of a hardcoded pixel width fighting the
        # window's actual size -- and so the user can set the balance
        # they want.
        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(12)
        root.addWidget(self._splitter, stretch=1)

        left_pane = QWidget()
        left_column = QVBoxLayout(left_pane)
        left_column.setContentsMargins(0, 0, 0, 0)
        left_column.setSpacing(12)
        left_pane.setMinimumWidth(300)
        self._splitter.addWidget(left_pane)

        right_pane = QWidget()
        right_column = QVBoxLayout(right_pane)
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.setSpacing(12)
        right_pane.setMinimumWidth(280)
        self._splitter.addWidget(right_pane)

        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 4)
        self._splitter.setSizes([440, 580])

        left_column.addWidget(self._build_preview_card(), stretch=1)
        left_column.addWidget(self._build_capture_card())
        left_column.addWidget(self._build_stats_row())
        left_column.addStretch()

        right_column.addWidget(self._build_gallery_card(), stretch=1)
        right_column.addWidget(self._build_export_card())

        self._update_get_started_banner()
        self._refresh_gallery_view(0)
        self._apply_responsive_layout(self.width())

    # ---- UI pieces ----------------------------------------------

    def _build_top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 12, 10)
        layout.setSpacing(12)

        layout.addWidget(wordmark())

        self.session_label = QLabel("No session open")
        self.session_label.setObjectName("secondary")
        layout.addWidget(self.session_label)
        layout.addStretch()

        self.new_session_button = QPushButton("New Session")
        self.new_session_button.setObjectName("primaryButton")
        self.new_session_button.setIcon(
            standard_icon(self, QStyle.StandardPixmap.SP_FileDialogNewFolder)
        )
        self.new_session_button.clicked.connect(self._on_new_session)

        self.open_session_button = QPushButton("Open")
        self.open_session_button.setObjectName("subtleButton")
        self.open_session_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_DirOpenIcon))
        self.open_session_button.clicked.connect(self._on_open_session)

        self.settings_button = QPushButton()
        self.settings_button.setObjectName("iconButton")
        self.settings_button.setIcon(gear_icon())
        self.settings_button.setToolTip("Settings")
        self.settings_button.clicked.connect(self._on_open_settings)

        for btn in (self.new_session_button, self.open_session_button, self.settings_button):
            btn.setIconSize(_ICON_SIZE)
            layout.addWidget(btn)
        return bar

    def _build_get_started_banner(self) -> QWidget:
        # Shown only when there's no active session -- the point is to
        # make "you need a session before capture will work"
        # impossible to miss, rather than Start Capture quietly
        # refusing to do anything with no explanation.
        self.get_started_banner = QFrame()
        self.get_started_banner.setObjectName("banner")
        layout = QHBoxLayout(self.get_started_banner)
        layout.setContentsMargins(14, 12, 14, 12)
        label = QLabel(
            "Start with <b>New Session</b>, then select the slide area. "
            "Screenshots can’t be saved without a session."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        return self.get_started_banner

    def _build_update_banner(self) -> QWidget:
        # Shown only when a background check (see update_checker.py)
        # finds a newer release than this build -- hidden by default,
        # since most launches won't have an update waiting.
        self.update_banner = QFrame()
        self.update_banner.setObjectName("banner")
        self.update_banner.setVisible(False)
        layout = QHBoxLayout(self.update_banner)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(10)

        self.update_banner_label = QLabel("")
        self.update_banner_label.setWordWrap(True)
        layout.addWidget(self.update_banner_label, stretch=1)

        self.update_download_button = QPushButton("Download")
        self.update_download_button.setObjectName("primaryButton")
        self.update_download_button.clicked.connect(self._on_download_update_clicked)
        layout.addWidget(self.update_download_button)

        self.update_dismiss_button = QPushButton("Dismiss")
        self.update_dismiss_button.setObjectName("subtleButton")
        self.update_dismiss_button.clicked.connect(lambda: self.update_banner.setVisible(False))
        layout.addWidget(self.update_dismiss_button)
        return self.update_banner

    def _build_preview_card(self) -> QWidget:
        card = Card("Live preview")

        # The lock-state pill sits in the preview's own header rather
        # than in a separate status block, because it is a statement
        # *about this image*: whether what you're looking at is
        # actually the area you selected (see
        # capture/anchor_tracker.py). Anywhere else and the user has
        # to correlate two widgets to answer one question.
        self.status_pill = StatusPill("Idle", "idle")
        card.header_row.addWidget(self.status_pill)

        self.preview_label = QLabel("No capture area selected")
        self.preview_label.setObjectName("previewSurface")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(190)
        self.preview_label.setWordWrap(True)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        card.body.addWidget(self.preview_label, stretch=1)

        self.region_label = QLabel("Select the slide area to see a preview here.")
        self.region_label.setObjectName("muted")
        self.region_label.setWordWrap(True)
        card.body.addWidget(self.region_label)
        return card

    def _build_capture_card(self) -> QWidget:
        card = Card("Capture")

        self.select_area_button = QPushButton("Select Area")
        self.select_area_button.setIcon(crosshair_icon())
        self.select_area_button.clicked.connect(self._on_select_area)

        self.reselect_button = QPushButton("Reselect Area")
        self.reselect_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_BrowserReload))
        self.reselect_button.setToolTip("Point at the slide area again")
        self.reselect_button.clicked.connect(self._on_select_area)

        self.start_button = QPushButton("Start Capture")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay))
        self.start_button.clicked.connect(self._on_start)

        # One toggle rather than a Pause button and a Resume button
        # permanently side by side with one of them always disabled:
        # they are the same control in two states, and showing both
        # spent a row of the layout saying so.
        self.pause_resume_button = QPushButton("Pause")
        self.pause_resume_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPause))
        self.pause_resume_button.clicked.connect(self._on_toggle_pause)

        self.manual_capture_button = QPushButton("Capture Now")
        self.manual_capture_button.setIcon(camera_icon())
        self.manual_capture_button.setToolTip(
            "Save the current frame, skipping duplicate detection"
        )
        self.manual_capture_button.clicked.connect(self._on_manual_capture)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.clicked.connect(self._on_stop)

        setup_row = QHBoxLayout()
        setup_row.setSpacing(8)
        for btn in (self.select_area_button, self.reselect_button):
            btn.setIconSize(_ICON_SIZE)
            setup_row.addWidget(btn)
        setup_row.addStretch()
        self.start_button.setIconSize(_ICON_SIZE)
        setup_row.addWidget(self.start_button)
        card.body.addLayout(setup_row)

        card.body.addWidget(horizontal_divider())

        running_row = QHBoxLayout()
        running_row.setSpacing(8)
        for btn in (self.pause_resume_button, self.manual_capture_button, self.stop_button):
            btn.setIconSize(_ICON_SIZE)
            running_row.addWidget(btn)
        running_row.addStretch()
        card.body.addLayout(running_row)
        return card

    def _build_stats_row(self) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.saved_tile = StatTile("Saved", "0")
        self.duplicate_tile = StatTile("Duplicates", "0")
        self.duration_tile = StatTile("Elapsed", "00:00:00")
        for tile in (self.saved_tile, self.duplicate_tile, self.duration_tile):
            row.addWidget(tile, stretch=1)
        return holder

    def _build_gallery_card(self) -> QWidget:
        card = Card("Screenshots")

        self.gallery_count_label = QLabel("")
        self.gallery_count_label.setObjectName("muted")
        card.header_row.addWidget(self.gallery_count_label)

        self.gallery = GalleryWidget()
        self.gallery.deletion_requested.connect(self._on_delete_screenshots)
        self.gallery.reorder_requested.connect(self._on_reorder)
        self.gallery.selection_changed.connect(self._on_selection_changed)
        self.gallery.open_requested.connect(self._on_open_screenshot)
        self.gallery.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.gallery.setMinimumHeight(220)

        self._gallery_empty = EmptyState(
            "No screenshots yet",
            "Captured slides appear here. Double-click one to view it full size, "
            "drag to reorder, and untick any you don’t want in the export.",
        )

        # Stacked rather than hidden/shown: a QStackedWidget keeps the
        # pane's height stable, so the layout doesn't jump the moment
        # the first screenshot lands mid-lecture.
        self._gallery_stack = QStackedWidget()
        self._gallery_stack.addWidget(self._gallery_empty)
        self._gallery_stack.addWidget(self.gallery)
        card.body.addWidget(self._gallery_stack, stretch=1)
        return card

    def _build_export_card(self) -> QWidget:
        card = Card("Export")

        self.export_pdf_button = QPushButton("Export as PDF")
        self.export_pdf_button.setObjectName("primaryButton")
        self.export_pdf_button.setIcon(
            standard_icon(self, QStyle.StandardPixmap.SP_DialogSaveButton)
        )
        self.export_pdf_button.clicked.connect(self._on_export_pdf)

        self.export_images_button = QPushButton("Export as Images")
        self.export_images_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_DirIcon))
        self.export_images_button.clicked.connect(self._on_export_images)

        row = QHBoxLayout()
        row.setSpacing(8)
        for btn in (self.export_pdf_button, self.export_images_button):
            btn.setIconSize(_ICON_SIZE)
            row.addWidget(btn)
        row.addStretch()
        card.body.addLayout(row)
        return card

    # ---- responsive layout --------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())

    def _apply_responsive_layout(self, width: int) -> None:
        """Stacks the two panes vertically on a narrow window.

        Side by side, each pane needs ~300px before its own contents
        start truncating, so below roughly double that the horizontal
        split stops being a layout and becomes two unreadable columns
        -- which is what this window looked like on a small laptop
        screen or dragged narrow. Stacking keeps every control at full
        width and turns the shortfall into vertical scrolling, which
        the enclosing QScrollArea already handles.
        """
        if not hasattr(self, "_splitter"):
            return
        wanted = Qt.Horizontal if width >= STACK_PANES_BELOW_WIDTH else Qt.Vertical
        if self._splitter.orientation() == wanted:
            return
        self._splitter.setOrientation(wanted)
        self._splitter.setSizes([440, 580] if wanted is Qt.Horizontal else [460, 420])

    # ---- state -> controls --------------------------------------

    def _set_capture_controls_enabled(
        self, has_region: bool, is_capturing: bool, paused: bool = False
    ) -> None:
        has_session = self.session_id is not None
        self.start_button.setEnabled(has_region and has_session and not is_capturing)
        self.pause_resume_button.setEnabled(is_capturing)
        self.stop_button.setEnabled(is_capturing)
        self.manual_capture_button.setEnabled(is_capturing)
        self._set_paused_label(paused)

        # Only one of these two is ever meaningful, so only one is
        # ever shown. Reselect stays ENABLED during capture: it is the
        # fix for a capture area that has drifted or been lost, and
        # disabling it mid-session (as the old layout did) left the
        # user no way to recover except stopping the session.
        self.select_area_button.setVisible(not has_region)
        self.reselect_button.setVisible(has_region)
        self.select_area_button.setEnabled(True)
        self.reselect_button.setEnabled(True)

        # Explain, rather than just silently disabling, why Start
        # can't be clicked yet -- the original complaint was that
        # someone could select an area, click Start, and have nothing
        # happen with no indication why.
        if is_capturing:
            self.start_button.setToolTip("Capture is already running.")
        elif not has_session:
            self.start_button.setToolTip("Create or open a session first (New Session, top right).")
        elif not has_region:
            self.start_button.setToolTip("Select the slide area first.")
        else:
            self.start_button.setToolTip("Start capturing the selected area.")

    def _set_paused_label(self, paused: bool) -> None:
        self.pause_resume_button.setText("Resume" if paused else "Pause")
        self.pause_resume_button.setIcon(
            standard_icon(
                self,
                QStyle.StandardPixmap.SP_MediaPlay
                if paused
                else QStyle.StandardPixmap.SP_MediaPause,
            )
        )
        self.pause_resume_button.setIconSize(_ICON_SIZE)
        self._paused = paused

    def _set_status(self, text: str, state: str = "idle") -> None:
        """The one place the status pill and the floating bar's label
        are kept in step, so a status can never be showing in the main
        window but not on the bar the user is actually looking at."""
        self.status_pill.set_status(text, state)
        self._floating_bar.set_status(text, state)

    def _update_get_started_banner(self) -> None:
        self.get_started_banner.setVisible(self.session_id is None)

    def _refresh_gallery_view(self, count: int) -> None:
        self.gallery_count_label.setText(f"{count} saved" if count else "")
        self._gallery_stack.setCurrentWidget(self.gallery if count else self._gallery_empty)

    # ---- Update checking --------------------------------------------

    def _on_update_available(self, info: UpdateInfo) -> None:
        self._pending_update = info
        self.update_banner_label.setText(
            f"A new version of Sniply is available (v{info.version}, you have v{__version__})."
        )
        self.update_banner.setVisible(True)

    def _on_download_update_clicked(self) -> None:
        # Opens the GitHub release page rather than silently
        # downloading and replacing the running executable -- that's
        # a deliberate, separate decision (self-replacing a running
        # binary) that shouldn't happen without the user explicitly
        # choosing to go further than "let me know when there's
        # something new."
        if self._pending_update is not None:
            webbrowser.open(self._pending_update.release_url)

    # ---- Session management ---------------------------------------

    def _on_new_session(self) -> None:
        APP_DATA_ROOT.mkdir(parents=True, exist_ok=True)
        dialog = NewSessionDialog(self)
        # QDialog.exec() returns DialogCode.Accepted (1) / Rejected (0);
        # comparing truthiness avoids PySide6's scoped-enum access
        # quirks (dialog.Accepted isn't valid -- it's QDialog.DialogCode.Accepted).
        if not dialog.exec():
            return

        name = dialog.session_name()
        save_dir = APP_DATA_ROOT / name
        if save_dir.exists():
            # Folder name derived from the session name collides with
            # an existing one (e.g. two sessions both named "Lecture")
            # -- disambiguate rather than silently reusing/overwriting
            # someone else's session data.
            save_dir = APP_DATA_ROOT / f"{name}_{time.strftime('%Y%m%d_%H%M%S')}"

        self.store = SessionStore(save_dir / "session.db")
        self.session_id = self.store.create_session(name, save_dir)
        self.session_name = name
        self.save_dir = save_dir
        self.saved_count = 0
        self.duplicate_count = 0
        self._refresh_counters()
        self.session_label.setText(name)
        self.gallery.set_screenshots([])
        self._refresh_gallery_view(0)
        self._set_capture_controls_enabled(has_region=self.region is not None, is_capturing=False)
        self._update_get_started_banner()

    def _discover_sessions(self) -> list[tuple[int, str, str]]:
        """Scan APP_DATA_ROOT for session folders (each has its own
        session.db, one row per session -- see storage/session.py)
        and collect every session across all of them, so "Open
        Session" can offer a single pick-list instead of making the
        user browse the filesystem for a folder they never chose."""
        APP_DATA_ROOT.mkdir(parents=True, exist_ok=True)
        found: list[tuple[int, str, str]] = []
        for entry in APP_DATA_ROOT.iterdir():
            db_path = entry / "session.db"
            if not db_path.exists():
                continue
            store = SessionStore(db_path)
            found.extend(store.list_sessions())
            store.close()
        return found

    def _on_open_session(self) -> None:
        sessions = self._discover_sessions()
        if not sessions:
            QMessageBox.information(self, "No Sessions", "No previous sessions were found.")
            return

        dialog = OpenSessionDialog(sessions, self)
        if not dialog.exec():
            return
        chosen_id = dialog.selected_session_id()
        chosen = next((s for s in sessions if s[0] == chosen_id), None)
        if chosen is None:
            return

        _, _, save_dir = chosen
        db_path = Path(save_dir) / "session.db"
        self.store = SessionStore(db_path)
        rows = self.store.list_sessions()
        row = next((r for r in rows if r[0] == chosen_id), None)
        if row is None:
            QMessageBox.warning(self, "Empty Session", "That session has no recorded data.")
            return

        session_id, name, save_dir = row
        self.session_id = session_id
        self.session_name = name
        self.save_dir = Path(save_dir)
        self.session_label.setText(name)

        shots = self.store.list_screenshots(session_id)
        self.gallery.set_screenshots(shots)
        self._refresh_gallery_view(len(shots))
        self.saved_count = len(shots)
        self.duplicate_count = 0
        self._refresh_counters()
        self._set_capture_controls_enabled(has_region=self.region is not None, is_capturing=False)
        self._update_get_started_banner()

    def _on_open_settings(self) -> None:
        dialog = SettingsDialog(self.config.detection, self)
        if dialog.exec():
            self.config.detection = dialog.updated_config()

    # ---- Region selection ------------------------------------------

    def _on_select_area(self) -> None:
        """Hide the main window first so it isn't sitting on top of
        whatever the user is trying to select (this is what Snipping
        Tool does too) -- then give Windows a moment to actually
        repaint the now-revealed desktop before the overlay grabs
        focus, or the overlay can flash in before the window beneath
        it has redrawn.
        """
        self.hide()
        QTimer.singleShot(350, self._show_overlay)

    def _show_overlay(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        # self._overlay is created once in __init__ and reused here
        # (see RegionOverlay's docstring) -- constructing a fresh
        # instance on every click was the main source of the white
        # flash, since each one forced the OS to create a brand-new
        # native window surface from scratch.
        self._overlay.prepare_for_screen(screen)
        # show_revealed(), not show(): it shows the window fully
        # transparent and fades it in a frame later, which papers over
        # any remaining first-paint flash regardless of its root cause
        # (see RegionOverlay.show_revealed). Also not showFullScreen():
        # the overlay's geometry is already set to the exact target
        # screen rect (see prepare_for_screen), and showFullScreen()
        # would additionally trigger Windows' native maximize-transition
        # animation on top of all this.
        self._overlay.show_revealed()
        self._overlay.activateWindow()

    def _on_region_selected(self, region: Region, anchor: CaptureAnchor) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.region = region
        self._anchor = anchor
        tracked = anchor.hwnd != 0 and anchor.reference_window is not None
        self.region_label.setText(
            f"{region.width} \u00d7 {region.height} px \u00b7 "
            + (
                "locked to the window's content, so it follows the slide if the "
                "layout shifts"
                if tracked
                else "fixed screen area \u2014 capture pauses while another window covers it"
            )
        )
        self._set_status("Area selected", "info")
        self._set_capture_controls_enabled(has_region=True, is_capturing=False)
        self._show_region_preview(region)

    def _on_selection_cancelled(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self._set_status("Selection cancelled", "idle")

    def _show_region_preview(self, region: Region) -> None:
        try:
            import mss

            with mss.mss() as sct:
                shot = sct.grab(region.as_mss_dict())
            frame = np.array(shot)  # BGRA
            self._set_preview_image(frame, is_bgra=True)
        except Exception:
            self.preview_label.setText("Region selected (preview unavailable)")

    # ---- Capture lifecycle -------------------------------------------

    def _on_start(self) -> None:
        # Defensive: the Start button is normally disabled until both
        # conditions hold (see _set_capture_controls_enabled), but if
        # this is ever reached anyway, say exactly what's missing
        # rather than silently doing nothing -- that silent no-op was
        # the actual complaint this guards against.
        if self.session_id is None or self.store is None:
            QMessageBox.information(
                self, "No Session",
                "Create or open a session first (see the Session section above) "
                "before starting a capture.",
            )
            return
        if self.region is None:
            QMessageBox.information(self, "No Capture Area", "Select a capture area first.")
            return

        pipeline = DetectionPipeline(self.config.detection)
        existing = self.store.list_screenshots(self.session_id)
        if existing:
            import cv2

            frames = [cv2.imread(s.file_path) for s in existing]
            pipeline.seed_from_frames([f for f in frames if f is not None])

        self.controller = CaptureController(
            self.region,
            pipeline,
            self.store.db_path,
            self.session_id,
            # Raw auto-saved screenshots live in a "screenshots" subfolder
            # rather than the session root, so the root stays clean for
            # the database and whatever you choose to export there --
            # otherwise exporting a PDF into the session folder mixes it
            # in visually with 100+ raw per-tick capture files.
            self.save_dir / "screenshots",
            self.config.detection.capture_interval_ms,
            self._anchor,
        )
        self.controller.worker.screenshot_saved.connect(self._on_screenshot_saved)
        self.controller.worker.duplicate_ignored.connect(self._on_duplicate_ignored)
        self.controller.worker.status_changed.connect(self._on_status_changed)
        self.controller.worker.error.connect(self._on_capture_error)
        self.controller.worker.preview_frame.connect(self._on_preview_frame)
        self.controller.worker.anchor_status_changed.connect(self._on_anchor_status_changed)
        self.controller.start()

        self._session_start = time.time()
        self._duration_timer.start()
        self._set_capture_controls_enabled(has_region=True, is_capturing=True, paused=False)
        self._enter_compact_mode()

    def _on_toggle_pause(self) -> None:
        """The single Pause/Resume control's handler -- it dispatches
        on current state rather than there being two buttons."""
        if self._paused:
            self._on_resume()
        else:
            self._on_pause()

    def _on_pause(self) -> None:
        if self.controller:
            self.controller.pause()
        self._set_capture_controls_enabled(has_region=True, is_capturing=True, paused=True)
        self._floating_bar.set_paused(True)
        self._set_status("Paused", "warn")

    def _on_resume(self) -> None:
        if self.controller:
            self.controller.resume()
        self._set_capture_controls_enabled(has_region=True, is_capturing=True, paused=False)
        self._floating_bar.set_paused(False)
        self._set_status("Capturing", "live")

    def _on_stop(self) -> None:
        if self.controller:
            self.controller.stop()
            self.controller = None
        self._duration_timer.stop()
        self._set_capture_controls_enabled(has_region=True, is_capturing=False)
        self._set_status("Stopped", "idle")
        self._exit_compact_mode()

    def _on_manual_capture(self) -> None:
        if self.controller:
            self.controller.manual_capture()

    def _enter_compact_mode(self) -> None:
        """Swap the full window for the small floating control bar
        once capture is actively running -- see FloatingControlBar's
        docstring for why this exists instead of just leaving the big
        window up."""
        self.hide()
        self._floating_bar.set_paused(False)
        self._floating_bar.show()

    def _exit_compact_mode(self) -> None:
        """Bring the full window back (gallery, export, settings...)
        without necessarily stopping capture -- the Expand button on
        the floating bar does this, as does Stop."""
        self._floating_bar.hide()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        if self.controller:
            self.controller.stop()
        self._floating_bar.hide()
        event.accept()

    # ---- Worker signal handlers --------------------------------------

    def _on_screenshot_saved(self, file_path: str) -> None:
        self.saved_count += 1
        self._refresh_counters()
        if self.session_id is not None and self.store is not None:
            shots = self.store.list_screenshots(self.session_id)
            self.gallery.set_screenshots(shots)
            self._refresh_gallery_view(len(shots))

    def _on_duplicate_ignored(self) -> None:
        self.duplicate_count += 1
        self._refresh_counters()

    #: Worker status strings -> pill state. The worker reports what
    #: happened in its own words (it has no business knowing about
    #: colours); this is where that becomes something visual. Matched
    #: on a prefix so a status can carry detail after it without
    #: needing a new entry here.
    _STATUS_STATES = (
        ("Lost", "error"),
        ("Re-locked", "live"),
        ("Waiting", "warn"),
        ("Paused", "warn"),
        ("Stopped", "idle"),
        ("Capturing", "live"),
    )

    def _on_status_changed(self, status: str) -> None:
        state = next(
            (s for prefix, s in self._STATUS_STATES if status.startswith(prefix)), "info"
        )
        self._set_status(status, state)

    def _on_capture_error(self, message: str) -> None:
        self._set_status(message, "error")
        # Errors get the full sentence somewhere it can't be truncated
        # by the pill's width, since they're the ones that tell the
        # user what to actually do about it.
        self.region_label.setText(message)

    def _on_anchor_status_changed(self, status: str) -> None:
        """Mirrors the capture-area tracking state onto the preview.

        This is the indicator the whole re-anchoring mechanism exists
        to make honest: "lost" means Sniply has deliberately stopped
        saving because it can no longer tell where the selected area
        is, rather than quietly banking screenshots of whatever moved
        in underneath. Silence there was the actual failure.
        """
        if status == "lost":
            # Clear the pixmap BEFORE setting the text: a QLabel holds
            # either a pixmap or text, and whichever is set last wins,
            # so doing it the other way round leaves the explanation
            # invisible behind an empty pixmap -- a blank black box
            # exactly where the user most needs to be told what
            # happened.
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(
                "Lost track of the selected area.\n\n"
                "Nothing is being saved. Click Reselect Area to point at the slide again."
            )
            self.region_label.setText(
                "The window's layout changed and the selected content can no longer be "
                "found inside it."
            )
        elif status == "untracked":
            self.region_label.setText(
                "Capturing a fixed area of this window \u2014 no landmark nearby to "
                "track it by, so a layout change may need a reselect."
            )

    def _on_preview_frame(self, frame: np.ndarray) -> None:
        self._set_preview_image(frame, is_bgra=False)

    def _set_preview_image(self, frame: np.ndarray, is_bgra: bool) -> None:
        h, w = frame.shape[:2]
        fmt = QImage.Format_RGBA8888 if is_bgra else QImage.Format_BGR888
        # mss frames are BGRA; cv2/worker frames are BGR. Convert BGRA
        # to a format Qt understands directly without extra copies
        # where possible.
        if is_bgra:
            rgba = frame[:, :, [2, 1, 0, 3]].copy()
            image = QImage(rgba.data, w, h, rgba.strides[0], QImage.Format_RGBA8888)
        else:
            image = QImage(frame.data, w, h, frame.strides[0], QImage.Format_BGR888)
        pixmap = QPixmap.fromImage(image).scaled(
            self.preview_label.width(), self.preview_label.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.preview_label.setPixmap(pixmap)

    # ---- Gallery actions ----------------------------------------------

    def _on_delete_screenshots(self, screenshot_ids: list) -> None:
        if self.store is None or not screenshot_ids:
            return
        if len(screenshot_ids) > 1:
            reply = QMessageBox.question(
                self,
                "Delete Screenshots",
                f"Delete {len(screenshot_ids)} selected screenshots? This cannot be undone.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        for screenshot_id in screenshot_ids:
            self.store.delete_screenshot(screenshot_id)
        shots = self.store.list_screenshots(self.session_id)
        self.gallery.set_screenshots(shots)
        self._refresh_gallery_view(len(shots))

    def _on_reorder(self, ordered_ids: list) -> None:
        if self.store is not None and self.session_id is not None:
            self.store.reorder(self.session_id, ordered_ids)

    def _on_selection_changed(self, screenshot_id: int, selected: bool) -> None:
        if self.store is not None:
            self.store.set_selected_for_export(screenshot_id, selected)

    def _on_open_screenshot(self, file_path: str) -> None:
        dialog = ScreenshotViewerDialog(file_path, self)
        dialog.exec()

    # ---- Export ----------------------------------------------------------

    def _selected_paths_in_order(self) -> list[Path]:
        if self.store is None or self.session_id is None:
            return []
        shots_by_id = {s.id: s for s in self.store.list_screenshots(self.session_id)}
        ids = self.gallery.selected_ids()
        return [Path(shots_by_id[i].file_path) for i in ids if i in shots_by_id]

    def _on_export_pdf(self) -> None:
        paths = self._selected_paths_in_order()
        if not paths:
            QMessageBox.warning(self, "Nothing to Export", "No screenshots are selected for export.")
            return
        DEFAULT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        default_name = f"{self.session_name or 'Sniply'}.pdf"
        default_path = DEFAULT_EXPORT_DIR / default_name
        output, _ = QFileDialog.getSaveFileName(self, "Export PDF", str(default_path), "PDF Files (*.pdf)")
        if not output:
            return
        try:
            export_pdf(paths, Path(output))
        except EmptyExportError:
            QMessageBox.warning(self, "Nothing to Export", "No screenshots are selected for export.")
            return
        QMessageBox.information(self, "Export Complete", f"Exported {len(paths)} screenshot(s) to:\n{output}")

    def _on_export_images(self) -> None:
        paths = self._selected_paths_in_order()
        if not paths:
            QMessageBox.warning(self, "Nothing to Export", "No screenshots are selected for export.")
            return
        DEFAULT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        output_dir = QFileDialog.getExistingDirectory(self, "Export Images To", str(DEFAULT_EXPORT_DIR))
        if not output_dir:
            return
        written = export_images(paths, Path(output_dir))
        QMessageBox.information(self, "Export Complete", f"Exported {len(written)} screenshot(s) to:\n{output_dir}")

    # ---- Misc ----------------------------------------------------------

    def _refresh_counters(self) -> None:
        self.saved_tile.set_value(self.saved_count)
        self.duplicate_tile.set_value(self.duplicate_count)
        self._floating_bar.set_saved_count(self.saved_count, self.duplicate_count)

    def _update_duration_label(self) -> None:
        if self._session_start is None:
            return
        elapsed = int(time.time() - self._session_start)
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        self.duration_tile.set_value(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
