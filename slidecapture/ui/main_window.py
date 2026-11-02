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
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from slidecapture.capture.region import InvalidRegionError, Region
from slidecapture.capture.worker import CaptureController
from slidecapture.config.defaults import AppConfig
from slidecapture.detection.pipeline import DetectionPipeline
from slidecapture.export.image_export import export_images
from slidecapture.export.pdf_export import EmptyExportError, export_pdf
from slidecapture.storage.session import SessionStore
from slidecapture.ui.floating_bar import FloatingControlBar
from slidecapture.ui.gallery import GalleryWidget
from slidecapture.ui.viewer import ScreenshotViewerDialog
from slidecapture.ui.overlay import RegionOverlay
from slidecapture.ui.session_dialog import NewSessionDialog, OpenSessionDialog
from slidecapture.ui.settings_dialog import SettingsDialog

# Where raw session data (screenshots + session.db) lives -- the
# app's own storage, not something the user picks. This follows the
# standard Windows convention for per-user application data rather
# than cluttering the user's home folder directly.
APP_DATA_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SlideCapture" / "Sessions"

# Where exported PDFs/images default to -- a deliberate, separate
# choice the user makes only when they're ready to hand off a
# finished deck, not at session-creation time.
DEFAULT_EXPORT_DIR = Path.home() / "Downloads"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SlideCapture")
        self.resize(900, 650)

        self.config = AppConfig()
        self.store: SessionStore | None = None
        self.session_id: int | None = None
        self.session_name: str | None = None
        self.save_dir: Path | None = None
        self.region: Region | None = None
        # The process (PID) that actually owned the window at the
        # capture region when it was selected (see
        # RegionOverlay.mouseReleaseEvent) -- this, not whatever's in
        # front when Start Capture is later clicked, is the correct
        # baseline for the occlusion guard.
        self._target_pid_hint = 0
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
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Session row
        session_row = QHBoxLayout()
        self.new_session_button = QPushButton("New Session")
        self.open_session_button = QPushButton("Open Session")
        self.settings_button = QPushButton("Settings")
        self.session_label = QLabel("No session open")
        self.new_session_button.clicked.connect(self._on_new_session)
        self.open_session_button.clicked.connect(self._on_open_session)
        self.settings_button.clicked.connect(self._on_open_settings)
        session_row.addWidget(self.new_session_button)
        session_row.addWidget(self.open_session_button)
        session_row.addWidget(self.settings_button)
        session_row.addStretch()
        session_row.addWidget(self.session_label)
        root.addLayout(session_row)

        # Preview
        self.preview_label = QLabel("No capture area selected")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(240)
        self.preview_label.setStyleSheet("background-color: #222; color: #aaa;")
        root.addWidget(self.preview_label)

        # Capture controls
        controls_row = QHBoxLayout()
        self.select_area_button = QPushButton("Select Capture Area")
        self.reselect_button = QPushButton("Reselect Area")
        self.start_button = QPushButton("Start Capture")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop Capture")
        self.manual_capture_button = QPushButton("Manual Capture")

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
            controls_row.addWidget(btn)
        root.addLayout(controls_row)

        # Status / counters
        status_row = QHBoxLayout()
        self.status_label = QLabel("Status: Idle")
        self.saved_label = QLabel("Saved: 0")
        self.duplicate_label = QLabel("Duplicates ignored: 0")
        self.duration_label = QLabel("Duration: 00:00:00")
        for lbl in (self.status_label, self.saved_label, self.duplicate_label, self.duration_label):
            status_row.addWidget(lbl)
        status_row.addStretch()
        root.addLayout(status_row)

        # Gallery
        self.gallery = GalleryWidget()
        self.gallery.deletion_requested.connect(self._on_delete_screenshot)
        self.gallery.reorder_requested.connect(self._on_reorder)
        self.gallery.selection_changed.connect(self._on_selection_changed)
        self.gallery.open_requested.connect(self._on_open_screenshot)
        root.addWidget(self.gallery)

        # Export row
        export_row = QHBoxLayout()
        self.export_pdf_button = QPushButton("Export Selected as PDF")
        self.export_images_button = QPushButton("Export Selected as Images")
        self.export_pdf_button.clicked.connect(self._on_export_pdf)
        self.export_images_button.clicked.connect(self._on_export_images)
        export_row.addWidget(self.export_pdf_button)
        export_row.addWidget(self.export_images_button)
        export_row.addStretch()
        root.addLayout(export_row)

    def _set_capture_controls_enabled(self, has_region: bool, is_capturing: bool, paused: bool = False) -> None:
        has_session = self.session_id is not None
        self.start_button.setEnabled(has_region and has_session and not is_capturing)
        self.reselect_button.setEnabled(not is_capturing)
        self.pause_button.setEnabled(is_capturing and not paused)
        self.resume_button.setEnabled(is_capturing and paused)
        self.stop_button.setEnabled(is_capturing)
        self.manual_capture_button.setEnabled(is_capturing)

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

    def _on_region_selected(self, region: Region, target_pid_hint: int) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.region = region
        self._target_pid_hint = target_pid_hint
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
        if self.region is None or self.session_id is None or self.store is None:
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
            self._target_pid_hint,
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

    def _on_delete_screenshot(self, screenshot_id: int) -> None:
        if self.store is not None:
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
        default_name = f"{self.session_name or 'SlideCapture'}.pdf"
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
