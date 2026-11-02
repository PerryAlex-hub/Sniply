"""The background capture engine.

This is the one place real-time screen grabbing happens, and it must
never touch Qt widgets directly -- see the module docstring in
ui/main_window.py for why. CaptureWorker is a plain QObject that gets
moved onto its own QThread; all communication back to the UI goes
through Qt signals, which Qt automatically marshals across the thread
boundary (queued connections) so slots run safely on the receiving
thread.
"""

import logging
import os
from pathlib import Path

import cv2
import mss
import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from slidecapture.capture.occlusion import process_at_region_center
from slidecapture.capture.region import Region
from slidecapture.detection.pipeline import Decision, DetectionPipeline
from slidecapture.storage.screenshot_store import save_screenshot
from slidecapture.storage.session import SessionStore

logger = logging.getLogger(__name__)

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
        db_path: Path,
        session_id: int,
        save_dir: Path,
        interval_ms: int,
        initial_target_pid: int = 0,
    ):
        super().__init__()
        self._region = region
        self._pipeline = pipeline
        self._db_path = db_path
        self._session_id = session_id
        self._save_dir = save_dir
        self._interval_ms = interval_ms

        self._store: SessionStore | None = None
        self._sct: mss.MSS | None = None
        self._timer: QTimer | None = None
        self._paused = False
        self._consecutive_errors = 0

        # The process (PID) owning whatever's at the capture region --
        # see occlusion.py. This MUST come from outside (resolved by
        # the UI at region-selection time, the one moment SlideCapture's
        # own windows are both out of the way) rather than being
        # re-resolved here at start()/resume() time: by definition,
        # whoever calls start()/resume() just clicked a button in
        # SlideCapture's own window, so "whatever's in front right
        # now" would always resolve to SlideCapture itself, not the
        # user's actual presentation -- that was the bug that made the
        # occlusion guard treat the real target as an intruder the
        # instant the user switched to it.
        self._target_pid = initial_target_pid
        self._occluded = False
        # True for exactly one tick right after the target becomes
        # visible (first tick ever, or the first tick after an
        # occluded period ends) -- see _tick() and
        # DetectionPipeline.force_evaluate for why that tick gets
        # special treatment.
        self._just_became_visible = True

    @Slot()
    def start(self) -> None:
        """Called via a queued connection once this object has been
        moved to its own QThread -- creating the QTimer (and the
        SQLite connection) here, rather than in __init__, ensures
        they're owned by this thread's event loop, not whichever
        thread constructed CaptureWorker.

        The SessionStore is opened fresh here rather than reusing the
        UI thread's store instance: sqlite3 connections are tied to
        the thread that created them and raise
        "SQLite objects created in a thread can only be used in that
        same thread" if used from elsewhere. Both connections point
        at the same database file, which SQLite is fine with.
        """
        self._store = SessionStore(self._db_path)
        self._sct = mss.MSS()
        self._timer = QTimer()
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        if self._target_pid == 0:
            # No hint was available (e.g. resuming a session without a
            # fresh region selection) -- best effort, with the known
            # caveat that this will incorrectly anchor on SlideCapture's
            # own process since that's what's in front right now.
            self._target_pid = process_at_region_center(self._region)
        logger.debug(
            "worker started: region=%s interval_ms=%d target_pid=%s",
            self._region, self._interval_ms, self._target_pid,
        )
        self.status_changed.emit("Capturing")

    @Slot()
    def pause(self) -> None:
        self._paused = True
        self.status_changed.emit("Paused")

    @Slot()
    def resume(self) -> None:
        self._paused = False
        self._consecutive_errors = 0
        # Deliberately NOT re-resolving _target_pid or touching
        # _occluded here: clicking Resume means SlideCapture's own
        # window is what's currently in front, so re-resolving now
        # would re-anchor on SlideCapture itself instead of the user's
        # actual presentation -- exactly the bug described in
        # __init__. The normal per-tick occlusion check below picks
        # back up correctly against the target established at
        # region-selection time, including firing the
        # "just became visible" immediate-evaluate path once the user
        # actually switches back to it.
        self.status_changed.emit("Capturing")

    @Slot()
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        if self._sct is not None:
            self._sct.close()
        if self._store is not None:
            self._store.close()
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
            logger.debug("tick: paused, skipping")
            return

        try:
            frame = self._grab_frame()
        except mss.ScreenShotError as exc:
            self._consecutive_errors += 1
            logger.warning("tick: grab failed: %s", exc)
            self.error.emit(f"Capture failed: {exc}")
            if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                self.pause()
                self.error.emit(
                    "Capture region is unavailable (monitor disconnected or locked?); "
                    "pausing automatically. Reselect the area and resume."
                )
            return
        self._consecutive_errors = 0

        logger.debug("tick: grabbed frame shape=%s mean=%.2f", frame.shape, float(frame.mean()))
        self.preview_frame.emit(frame)

        if self._is_region_occluded():
            return

        try:
            if self._just_became_visible:
                # First tick ever, or the first tick after the target
                # was occluded -- evaluate immediately rather than
                # waiting for the usual settle-over-N-ticks delay. This
                # closes a real gap: without it, whatever slide is on
                # screen the instant you switch back to the target
                # never gets captured unless it happens to stay on
                # screen for a couple more full ticks before you move
                # on, which in practice meant the very first slide (or
                # whatever's showing right when you return from
                # switching apps) was routinely missed.
                self._just_became_visible = False
                result = self._pipeline.force_evaluate(frame)
            else:
                result = self._pipeline.process_frame(frame)
        except Exception as exc:  # defensive: never let a detection bug kill the timer loop
            logger.exception("tick: detection pipeline raised")
            self.error.emit(f"Detection error: {exc}")
            return

        logger.debug("tick: decision=%s", result.decision)
        if result.decision is Decision.SAVE:
            self._save(result.frame)
        elif result.decision is Decision.DUPLICATE:
            self.duplicate_ignored.emit()

    def _is_region_occluded(self) -> bool:
        """True if a different PROCESS now owns whatever covers the
        capture region's center than the one present when capture
        started/resumed -- e.g. the user Alt-Tabbed away to Chrome.
        While occluded, the tick returns early without touching the
        detection pipeline at all, so the stability tracker's notion
        of "last real frame" stays anchored to the last frame of
        actual target content, not whatever briefly covered it.

        Compares by owning process rather than exact window handle so
        that an app swapping to a different top-level window for the
        same content -- notably PowerPoint, which creates a brand new
        window when you switch from the editor into Slideshow (F5) --
        isn't mistaken for a different application entirely.

        SlideCapture's own process is never treated as an occluder:
        the floating control bar (ui/floating_bar.py) is meant to be
        draggable anywhere, including directly over the capture
        region, and is excluded from the actual screen capture via
        SetWindowDisplayAffinity -- so mss genuinely still sees the
        real target underneath it. Treating our own bar as "occluding"
        would incorrectly pause capture every time it's dragged there,
        even though nothing about what's actually being captured
        changed.
        """
        if self._target_pid == 0:
            return False  # couldn't resolve a target process; fail open rather than block capture

        current = process_at_region_center(self._region)
        if current == os.getpid():
            return self._occluded  # our own UI; ignore, keep whatever state we already had

        occluded_now = current != 0 and current != self._target_pid
        logger.debug(
            "occlusion check: target_pid=%s current_pid=%s occluded=%s",
            self._target_pid, current, occluded_now,
        )

        if occluded_now and not self._occluded:
            self._occluded = True
            logger.info("tick: capture area covered by a different application; pausing saves until it returns")
            self.status_changed.emit("Waiting (capture area covered by another window)")
        elif not occluded_now and self._occluded:
            self._occluded = False
            self._just_became_visible = True
            logger.info("tick: capture area visible again")
            self.status_changed.emit("Capturing")

        return occluded_now

    def _save(self, frame: np.ndarray) -> None:
        try:
            path = save_screenshot(self._store, self._session_id, self._save_dir, frame)
        except OSError as exc:
            logger.exception("save failed")
            self.error.emit(f"Could not save screenshot (storage unavailable?): {exc}")
            return
        logger.info("saved screenshot: %s", path)
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
        db_path: Path,
        session_id: int,
        save_dir: Path,
        interval_ms: int,
        initial_target_pid: int = 0,
    ):
        super().__init__()
        self.thread = QThread()
        self.worker = CaptureWorker(
            region, pipeline, db_path, session_id, save_dir, interval_ms, initial_target_pid
        )
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
