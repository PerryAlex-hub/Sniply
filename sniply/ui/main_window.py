"""The main application window.

This is the only module that touches both the Qt UI and the capture
engine. It owns the CaptureController (see capture/worker.py) and
connects its signals -- which arrive on the UI thread via Qt's queued
cross-thread delivery -- to ordinary widget updates. No other module
should import QWidget-family classes and also import CaptureWorker;
keeping that boundary one-directional is what keeps detection/
capture testable without a display.
"""

import os
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from sniply.capture.region import CaptureAnchor, InvalidRegionError, Region
from sniply.capture.worker import CaptureController
from sniply.config.defaults import AppConfig
from sniply.detection.pipeline import DetectionPipeline
from sniply.export.image_export import export_images
from sniply.export.pdf_export import EmptyExportError, export_pdf
from sniply.storage.session import SessionStore
from sniply.ui.floating_bar import FloatingControlBar
from sniply.ui.gallery import GalleryWidget
from sniply.ui.icons import camera_icon, crosshair_icon, gear_icon, standard_icon
from sniply.ui.viewer import ScreenshotViewerDialog
from sniply.ui.overlay import RegionOverlay
from sniply.ui.session_dialog import NewSessionDialog, OpenSessionDialog
from sniply.ui.settings_dialog import SettingsDialog

_ICON_SIZE = QSize(18, 18)

# Where raw session data (screenshots + session.db) lives -- the
# app's own storage, not something the user picks. This follows the
# standard Windows convention for per-user application data rather
# than cluttering the user's home folder directly.
APP_DATA_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Sniply" / "Sessions"

# Where exported PDFs/images default to -- a deliberate, separate
# choice the user makes only when they're ready to hand off a
# finished deck, not at session-creation time.
DEFAULT_EXPORT_DIR = Path.home() / "Downloads"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sniply")
        self.resize(1040, 720)
        self.setMinimumSize(640, 420)

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
        self._floating_bar.stop_requested.connect(self._on_stop)
        self._floating_bar.manual_capture_requested.connect(self._on_manual_capture)
        self._floating_bar.expand_requested.connect(self._exit_compact_mode)

        self.saved_count = 0
        self.duplicate_count = 0
        self._session_start: float | None = None

        self._duration_timer = QTimer(self)
        self._duration_timer.setInterval(1000)
        self._duration_timer.timeout.connect(self._update_duration_label)

        self._build_ui()
        self._set_capture_controls_enabled(has_region=False, is_capturing=False)

    # ---- UI construction ----------------------------------------

    def _build_ui(self) -> None:
        # Everything lives inside a QScrollArea rather than directly on
        # the window: if the window gets resized (or opened on) smaller
        # than the content needs, it scrolls instead of clipping
        # buttons or squeezing the layout into an unusable mess.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # Content is built to wrap/shrink to whatever width it's given
        # (word-wrapped labels, a flowing thumbnail grid, a splitter
        # with real minimum sizes rather than fixed ones), so this
        # should rarely if ever need to actually appear -- kept as
        # "as needed" rather than disabled outright so an extreme
        # window size degrades into a scrollbar instead of silently
        # clipped, inaccessible content.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setCentralWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        heading = QLabel("Sniply")
        heading.setObjectName("heading")
        root.addWidget(heading)

        # Shown only when there's no active session -- the whole point
        # is to make "you need a session before capture will work"
        # impossible to miss, rather than Start Capture just quietly
        # refusing to do anything with no explanation.
        self.get_started_banner = QLabel(
            "Get started: click “New Session” below before selecting "
            "a capture area -- screenshots can't be saved without one."
        )
        self.get_started_banner.setObjectName("banner")
        self.get_started_banner.setWordWrap(True)
        root.addWidget(self.get_started_banner)

        # Two-pane layout below the heading/banner: a left pane for
        # session/preview/capture controls (everything involved in
        # setting up and running a capture), and a right pane for
        # reviewing and exporting results (the gallery). Keeping these
        # visually and spatially separate reflects how they're actually
        # used -- set up once, then work mostly in the gallery -- rather
        # than one long undifferentiated stack of controls.
        #
        # A QSplitter rather than a fixed-width layout: the user can
        # drag the divider to whatever balance suits their screen, and
        # -- more importantly -- a splitter lets each pane shrink down
        # to its own real minimum size instead of a hardcoded pixel
        # width fighting the window's actual size.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, stretch=1)

        left_column = QVBoxLayout()
        left_column.setSpacing(12)
        left_pane = QWidget()
        left_pane.setLayout(left_column)
        left_pane.setMinimumWidth(260)
        splitter.addWidget(left_pane)

        right_column = QVBoxLayout()
        right_column.setSpacing(12)
        right_pane = QWidget()
        right_pane.setLayout(right_column)
        right_pane.setMinimumWidth(300)
        splitter.addWidget(right_pane)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 700])

        # ---- Session ----
        # Stacked vertically, like the Capture section below, rather
        # than packed into one horizontal row: this pane is only
        # 320-380px wide, and three labeled buttons side by side at
        # that width just truncated into unreadable text ("New Sess...",
        # "Setting...").
        session_box = QGroupBox("Session")
        session_layout = QVBoxLayout(session_box)
        self.new_session_button = QPushButton("New Session")
        self.open_session_button = QPushButton("Open Session")
        self.settings_button = QPushButton("Settings")
        self.new_session_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_FileDialogNewFolder))
        self.open_session_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_DirOpenIcon))
        self.settings_button.setIcon(gear_icon())
        self.new_session_button.setObjectName("primaryButton")
        self.new_session_button.clicked.connect(self._on_new_session)
        self.open_session_button.clicked.connect(self._on_open_session)
        self.settings_button.clicked.connect(self._on_open_settings)
        for btn in (self.new_session_button, self.open_session_button, self.settings_button):
            btn.setIconSize(_ICON_SIZE)
            session_layout.addWidget(btn)
        self.session_label = QLabel("No session open")
        self.session_label.setObjectName("muted")
        self.session_label.setWordWrap(True)
        session_layout.addWidget(self.session_label)
        left_column.addWidget(session_box)

        # ---- Preview ----
        preview_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_box)
        self.preview_label = QLabel("No capture area selected")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(160)
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet(
            "background-color: #141417; color: #8a8a90; border-radius: 6px;"
        )
        preview_layout.addWidget(self.preview_label)
        left_column.addWidget(preview_box)

        # ---- Capture controls ----
        capture_box = QGroupBox("Capture")
        capture_layout = QVBoxLayout(capture_box)
        self.select_area_button = QPushButton("Select Capture Area")
        self.reselect_button = QPushButton("Reselect Area")
        self.start_button = QPushButton("Start Capture")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop Capture")
        self.manual_capture_button = QPushButton("Manual Capture")

        self.select_area_button.setIcon(crosshair_icon())
        self.reselect_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_BrowserReload))
        self.start_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay))
        self.pause_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPause))
        self.resume_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay))
        self.stop_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaStop))
        self.manual_capture_button.setIcon(camera_icon())
        self.start_button.setObjectName("primaryButton")
        self.stop_button.setObjectName("dangerButton")

        self.select_area_button.clicked.connect(self._on_select_area)
        self.reselect_button.clicked.connect(self._on_select_area)
        self.start_button.clicked.connect(self._on_start)
        self.pause_button.clicked.connect(self._on_pause)
        self.resume_button.clicked.connect(self._on_resume)
        self.stop_button.clicked.connect(self._on_stop)
        self.manual_capture_button.clicked.connect(self._on_manual_capture)

        for btn in (
            self.select_area_button,
            self.reselect_button,
            self.start_button,
            self.pause_button,
            self.resume_button,
            self.stop_button,
            self.manual_capture_button,
        ):
            btn.setIconSize(_ICON_SIZE)
            capture_layout.addWidget(btn)
        left_column.addWidget(capture_box)

        # ---- Status ----
        status_box = QGroupBox("Status")
        status_layout = QVBoxLayout(status_box)
        self.status_label = QLabel("Status: Idle")
        self.saved_label = QLabel("Saved: 0")
        self.duplicate_label = QLabel("Duplicates ignored: 0")
        self.duration_label = QLabel("Duration: 00:00:00")
        for lbl in (self.status_label, self.saved_label, self.duplicate_label, self.duration_label):
            lbl.setObjectName("muted")
            status_layout.addWidget(lbl)
        left_column.addWidget(status_box)
        left_column.addStretch()

        # ---- Gallery ----
        self.gallery = GalleryWidget()
        self.gallery.deletion_requested.connect(self._on_delete_screenshots)
        self.gallery.reorder_requested.connect(self._on_reorder)
        self.gallery.selection_changed.connect(self._on_selection_changed)
        self.gallery.open_requested.connect(self._on_open_screenshot)
        self.gallery.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.gallery.setMinimumHeight(260)
        right_column.addWidget(self.gallery, stretch=1)

        # ---- Export ----
        export_box = QGroupBox("Export")
        export_row = QHBoxLayout(export_box)
        self.export_pdf_button = QPushButton("Export as PDF")
        self.export_images_button = QPushButton("Export as Images")
        self.export_pdf_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_DialogSaveButton))
        self.export_images_button.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_DirIcon))
        self.export_pdf_button.setIconSize(_ICON_SIZE)
        self.export_images_button.setIconSize(_ICON_SIZE)
        self.export_pdf_button.clicked.connect(self._on_export_pdf)
        self.export_images_button.clicked.connect(self._on_export_images)
        export_row.addWidget(self.export_pdf_button)
        export_row.addWidget(self.export_images_button)
        export_row.addStretch()
        right_column.addWidget(export_box)

        self._update_get_started_banner()

    def _set_capture_controls_enabled(self, has_region: bool, is_capturing: bool, paused: bool = False) -> None:
        has_session = self.session_id is not None
        self.start_button.setEnabled(has_region and has_session and not is_capturing)
        self.reselect_button.setEnabled(not is_capturing)
        self.pause_button.setEnabled(is_capturing and not paused)
        self.resume_button.setEnabled(is_capturing and paused)
        self.stop_button.setEnabled(is_capturing)
        self.manual_capture_button.setEnabled(is_capturing)

        # Explain, rather than just silently disabling, why Start can't
        # be clicked yet -- this was the actual complaint: someone could
        # select an area, click Start, and nothing would happen with no
        # indication why, if they'd skipped creating a session first.
        if is_capturing:
            self.start_button.setToolTip("Capture is already running.")
        elif not has_session:
            self.start_button.setToolTip("Create or open a session first (see the Session section above).")
        elif not has_region:
            self.start_button.setToolTip("Select a capture area first.")
        else:
            self.start_button.setToolTip("Start capturing the selected area.")

    def _update_get_started_banner(self) -> None:
        self.get_started_banner.setVisible(self.session_id is None)

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
        self.session_label.setText(f"Session: {name}")
        self.gallery.set_screenshots([])
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
        self.session_label.setText(f"Session: {name}")

        shots = self.store.list_screenshots(session_id)
        self.gallery.set_screenshots(shots)
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
        self.status_label.setText(f"Status: Region selected ({region.width}x{region.height})")
        self._set_capture_controls_enabled(has_region=True, is_capturing=False)
        self._show_region_preview(region)

    def _on_selection_cancelled(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.status_label.setText("Status: Selection cancelled")

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
        self.controller.start()

        self._session_start = time.time()
        self._duration_timer.start()
        self._set_capture_controls_enabled(has_region=True, is_capturing=True, paused=False)
        self._enter_compact_mode()

    def _on_pause(self) -> None:
        if self.controller:
            self.controller.pause()
        self._set_capture_controls_enabled(has_region=True, is_capturing=True, paused=True)
        self._floating_bar.set_paused(True)

    def _on_resume(self) -> None:
        if self.controller:
            self.controller.resume()
        self._set_capture_controls_enabled(has_region=True, is_capturing=True, paused=False)
        self._floating_bar.set_paused(False)

    def _on_stop(self) -> None:
        if self.controller:
            self.controller.stop()
            self.controller = None
        self._duration_timer.stop()
        self._set_capture_controls_enabled(has_region=True, is_capturing=False)
        self.status_label.setText("Status: Stopped")
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
            self.gallery.set_screenshots(self.store.list_screenshots(self.session_id))

    def _on_duplicate_ignored(self) -> None:
        self.duplicate_count += 1
        self._refresh_counters()

    def _on_status_changed(self, status: str) -> None:
        self.status_label.setText(f"Status: {status}")
        self._floating_bar.set_status_text(status)

    def _on_capture_error(self, message: str) -> None:
        self.status_label.setText(f"Status: {message}")
        self._floating_bar.set_status_text(message)

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
        self.gallery.set_screenshots(self.store.list_screenshots(self.session_id))

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
        self.saved_label.setText(f"Saved: {self.saved_count}")
        self.duplicate_label.setText(f"Duplicates ignored: {self.duplicate_count}")
        self._floating_bar.status_label.setToolTip(
            f"Saved: {self.saved_count}  •  Duplicates ignored: {self.duplicate_count}"
        )

    def _update_duration_label(self) -> None:
        if self._session_start is None:
            return
        elapsed = int(time.time() - self._session_start)
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        self.duration_label.setText(f"Duration: {hours:02d}:{minutes:02d}:{seconds:02d}")
