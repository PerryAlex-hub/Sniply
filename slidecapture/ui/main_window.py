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
from slidecapture.ui.gallery import GalleryWidget
from slidecapture.ui.overlay import RegionOverlay
from slidecapture.ui.session_dialog import NewSessionDialog, OpenSessionDialog
from slidecapture.ui.settings_dialog import SettingsDialog

DEFAULT_SESSIONS_ROOT = Path.home() / "SlideCapture Sessions"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SlideCapture")
        self.resize(900, 650)

        self.config = AppConfig()
        self.store: SessionStore | None = None
        self.session_id: int | None = None
        self.save_dir: Path | None = None
        self.region: Region | None = None
        self.controller: CaptureController | None = None
        self._overlay: RegionOverlay | None = None

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
        DEFAULT_SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
        dialog = NewSessionDialog(DEFAULT_SESSIONS_ROOT, self)
        if dialog.exec() != dialog.Accepted:
            return

        name = dialog.session_name()
        save_dir = dialog.save_dir() / name
        self.store = SessionStore(save_dir / "session.db")
        self.session_id = self.store.create_session(name, save_dir)
        self.save_dir = save_dir
        self.saved_count = 0
        self.duplicate_count = 0
        self._refresh_counters()
        self.session_label.setText(f"Session: {name}")
        self.gallery.set_screenshots([])
        self._set_capture_controls_enabled(has_region=self.region is not None, is_capturing=False)

    def _on_open_session(self) -> None:
        DEFAULT_SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
        # Session metadata is per-session-folder SQLite; to list sessions
        # we need an existing db. Ask the user to pick the session folder.
        chosen = QFileDialog.getExistingDirectory(self, "Choose Session Folder", str(DEFAULT_SESSIONS_ROOT))
        if not chosen:
            return
        db_path = Path(chosen) / "session.db"
        if not db_path.exists():
            QMessageBox.warning(self, "Not a Session Folder", "No session database found in that folder.")
            return

        self.store = SessionStore(db_path)
        sessions = self.store.list_sessions()
        if not sessions:
            QMessageBox.warning(self, "Empty Session", "That session has no recorded data.")
            return

        session_id, name, save_dir = sessions[0]
        self.session_id = session_id
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
        if dialog.exec() == dialog.Accepted:
            self.config.detection = dialog.updated_config()

    # ---- Region selection ------------------------------------------

    def _on_select_area(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self._overlay = RegionOverlay(screen)
        self._overlay.region_selected.connect(self._on_region_selected)
        self._overlay.selection_cancelled.connect(
            lambda: self.status_label.setText("Status: Selection cancelled")
        )
        self._overlay.showFullScreen()

    def _on_region_selected(self, region: Region) -> None:
        self.region = region
        self.status_label.setText(f"Status: Region selected ({region.width}x{region.height})")
        self._set_capture_controls_enabled(has_region=True, is_capturing=False)
        self._show_region_preview(region)

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
            self.store,
            self.session_id,
            self.save_dir,
            self.config.detection.capture_interval_ms,
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

    def _on_pause(self) -> None:
        if self.controller:
            self.controller.pause()
        self._set_capture_controls_enabled(has_region=True, is_capturing=True, paused=True)

    def _on_resume(self) -> None:
        if self.controller:
            self.controller.resume()
        self._set_capture_controls_enabled(has_region=True, is_capturing=True, paused=False)

    def _on_stop(self) -> None:
        if self.controller:
            self.controller.stop()
            self.controller = None
        self._duration_timer.stop()
        self._set_capture_controls_enabled(has_region=True, is_capturing=False)
        self.status_label.setText("Status: Stopped")

    def _on_manual_capture(self) -> None:
        if self.controller:
            self.controller.manual_capture()

    def closeEvent(self, event) -> None:
        if self.controller:
            self.controller.stop()
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

    def _on_capture_error(self, message: str) -> None:
        self.status_label.setText(f"Status: {message}")

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
        output, _ = QFileDialog.getSaveFileName(self, "Export PDF", str(self.save_dir or Path.home()), "PDF Files (*.pdf)")
        if not output:
            return
        try:
            export_pdf(paths, Path(output))
        except EmptyExportError:
            QMessageBox.warning(self, "Nothing to Export", "No screenshots are selected for export.")

    def _on_export_images(self) -> None:
        paths = self._selected_paths_in_order()
        if not paths:
            QMessageBox.warning(self, "Nothing to Export", "No screenshots are selected for export.")
            return
        output_dir = QFileDialog.getExistingDirectory(self, "Export Images To", str(self.save_dir or Path.home()))
        if not output_dir:
            return
        export_images(paths, Path(output_dir))

    # ---- Misc ----------------------------------------------------------

    def _refresh_counters(self) -> None:
        self.saved_label.setText(f"Saved: {self.saved_count}")
        self.duplicate_label.setText(f"Duplicates ignored: {self.duplicate_count}")

    def _update_duration_label(self) -> None:
        if self._session_start is None:
            return
        elapsed = int(time.time() - self._session_start)
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        self.duration_label.setText(f"Duration: {hours:02d}:{minutes:02d}:{seconds:02d}")
