"""The background capture engine.

This is the one place real-time screen grabbing happens, and it must
never touch Qt widgets directly -- see the module docstring in
ui/main_window.py for why. CaptureWorker is a plain QObject that gets
moved onto its own QThread; all communication back to the UI goes
through Qt signals, which Qt automatically marshals across the thread
boundary (queued connections) so slots run safely on the receiving
thread.
"""

from pathlib import Path

import cv2
import mss
import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from slidecapture.capture.region import Region
from slidecapture.detection.pipeline import Decision, DetectionPipeline
from slidecapture.storage.screenshot_store import save_screenshot
from slidecapture.storage.session import SessionStore

# After this many consecutive grab failures (e.g. the selected monitor
# was disconnected), auto-pause instead of silently retrying forever.
MAX_CONSECUTIVE_ERRORS = 3


class CaptureWorker(QObject):
    screenshot_saved = Signal(str)       # file path of the newly saved screenshot
    duplicate_ignored = Signal()
    status_changed = Signal(str)
    error = Signal(str)
    # Signal(object) rather than Signal(np.ndarray): this crosses a
    # thread boundary (queued connection), and np.ndarray isn't a
    # registered Qt metatype. `object` tells PySide6 to box the
    # Python value generically, which works for any Python type
    # including numpy arrays.
    preview_frame = Signal(object)        # latest raw frame, for the live preview widget

    def __init__(
        self,
        region: Region,
        pipeline: DetectionPipeline,
        store: SessionStore,
        session_id: int,
        save_dir: Path,
        interval_ms: int,
    ):
        super().__init__()
        self._region = region
        self._pipeline = pipeline
        self._store = store
        self._session_id = session_id
        self._save_dir = save_dir
        self._interval_ms = interval_ms

        self._sct: mss.MSS | None = None
        self._timer: QTimer | None = None
        self._paused = False
        self._consecutive_errors = 0

    @Slot()
    def start(self) -> None:
        """Called via a queued connection once this object has been
        moved to its own QThread -- creating the QTimer here (rather
        than in __init__) ensures the timer lives on that thread's
        event loop, not whichever thread constructed CaptureWorker.
        """
        self._sct = mss.MSS()
        self._timer = QTimer()
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.status_changed.emit("Capturing")

    @Slot()
    def pause(self) -> None:
        self._paused = True
        self.status_changed.emit("Paused")

    @Slot()
    def resume(self) -> None:
        self._paused = False
        self._consecutive_errors = 0
        self.status_changed.emit("Capturing")

    @Slot()
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        if self._sct is not None:
            self._sct.close()
        self.status_changed.emit("Stopped")

    @Slot()
    def manual_capture(self) -> None:
        """User explicitly asked for a screenshot right now: grab and
        save unconditionally, bypassing the dedup pipeline, since an
        explicit request is a strong enough signal on its own."""
        try:
            frame = self._grab_frame()
        except mss.ScreenShotError as exc:
            self.error.emit(f"Manual capture failed: {exc}")
            return
        self._save(frame)

    def _tick(self) -> None:
        if self._paused:
            return

        try:
            frame = self._grab_frame()
        except mss.ScreenShotError as exc:
            self._consecutive_errors += 1
            self.error.emit(f"Capture failed: {exc}")
            if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                self.pause()
                self.error.emit(
                    "Capture region is unavailable (monitor disconnected or locked?); "
                    "pausing automatically. Reselect the area and resume."
                )
            return
        self._consecutive_errors = 0

        self.preview_frame.emit(frame)

        try:
            result = self._pipeline.process_frame(frame)
        except Exception as exc:  # defensive: never let a detection bug kill the timer loop
            self.error.emit(f"Detection error: {exc}")
            return

        if result.decision is Decision.SAVE:
            self._save(result.frame)
        elif result.decision is Decision.DUPLICATE:
            self.duplicate_ignored.emit()

    def _save(self, frame: np.ndarray) -> None:
        try:
            path = save_screenshot(self._store, self._session_id, self._save_dir, frame)
        except OSError as exc:
            self.error.emit(f"Could not save screenshot (storage unavailable?): {exc}")
            return
        self.screenshot_saved.emit(str(path))

    def _grab_frame(self) -> np.ndarray:
        shot = self._sct.grab(self._region.as_mss_dict())
        bgra = np.array(shot)
        return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)


class CaptureController(QObject):
    """Owns the QThread that CaptureWorker runs on.

    The UI thread must never call CaptureWorker's methods directly --
    the worker object lives on a different thread, and a plain Python
    method call runs wherever the caller is, not wherever the object
    "lives". That would read/write the worker's timer and counters
    from two threads at once with no locking: a real race condition.

    Instead this controller exposes its own `*_requested` signals,
    connected to the worker's slots. Because the controller and the
    worker have different thread affinities, Qt automatically upgrades
    those connections to QueuedConnection: emitting the signal just
    posts an event onto the worker thread's queue, and the slot runs
    there, in order, next time that thread's event loop is free.
    """

    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    manual_capture_requested = Signal()

    def __init__(
        self,
        region: Region,
        pipeline: DetectionPipeline,
        store: SessionStore,
        session_id: int,
        save_dir: Path,
        interval_ms: int,
    ):
        super().__init__()
        self.thread = QThread()
        self.worker = CaptureWorker(region, pipeline, store, session_id, save_dir, interval_ms)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.start)
        self.pause_requested.connect(self.worker.pause)
        self.resume_requested.connect(self.worker.resume)
        self.stop_requested.connect(self.worker.stop)
        self.manual_capture_requested.connect(self.worker.manual_capture)

    def start(self) -> None:
        self.thread.start()

    def pause(self) -> None:
        self.pause_requested.emit()

    def resume(self) -> None:
        self.resume_requested.emit()

    def manual_capture(self) -> None:
        self.manual_capture_requested.emit()

    def stop(self) -> None:
        """Synchronously stop and tear down the worker thread. Blocks
        briefly (thread.wait()) until the worker's event loop drains,
        which is fine here since it's only called from UI actions
        (Stop button, app close) that are expected to pause anyway."""
        self.stop_requested.emit()
        self.thread.quit()
        self.thread.wait()
