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

from sniply.capture.anchor_tracker import AnchorStatus, AnchorTracker
from sniply.capture.occlusion import process_at_region_center
from sniply.capture.region import CaptureAnchor, Region
from sniply.capture.window_capture import capture_window, window_exists
from sniply.detection.pipeline import Decision, DetectionPipeline
from sniply.storage.screenshot_store import save_screenshot
from sniply.storage.session import SessionStore

logger = logging.getLogger(__name__)

# After this many consecutive grab failures (e.g. the selected monitor
# was disconnected, or the captured window was closed), auto-pause
# instead of silently retrying forever.
MAX_CONSECUTIVE_ERRORS = 3


class CaptureGrabError(Exception):
    """Raised by _grab_frame for window-capture failures (window
    closed, resized smaller than the selected area, etc.) -- handled
    identically to mss.ScreenShotError by _tick/manual_capture."""


class AnchorLostError(Exception):
    """The window was captured fine, but the selected content could no
    longer be found inside it (see capture/anchor_tracker.py).

    Deliberately NOT a CaptureGrabError: a grab failure means
    something is broken and escalates to an auto-pause after a few
    ticks, whereas this is a normal, recoverable condition -- the
    layout moved in a way the tracker can't resolve yet, and the
    content may well reappear on the next tick (a transient
    animation, a menu, a window mid-resize). The right response is to
    skip this tick's save and say so, not to tear the session down.

    What matters is that it is never swallowed into a save. Before
    the tracker existed, this situation produced a confident
    screenshot of whatever happened to sit at the stale offset --
    which is exactly how Zoom's chat panel got filed as a slide.
    """


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
    # Emitted only when the tracking state actually changes, not every
    # tick: this drives a user-visible indicator, and a label that
    # rewrites itself once a second is noise rather than information.
    anchor_status_changed = Signal(str)  # one of AnchorStatus's names, lowercased

    def __init__(
        self,
        region: Region,
        pipeline: DetectionPipeline,
        db_path: Path,
        session_id: int,
        save_dir: Path,
        interval_ms: int,
        anchor: CaptureAnchor = CaptureAnchor(),
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

        # This MUST come from outside (resolved by the UI at
        # region-selection time, the one moment Sniply's own windows
        # are both out of the way) rather than being re-resolved here
        # at start()/resume() time: by definition, whoever calls
        # start()/resume() just clicked a button in Sniply's own
        # window, so "whatever's in front right now" would always
        # resolve to Sniply itself, not the user's actual
        # presentation -- that was the bug that made the occlusion
        # guard treat the real target as an intruder the instant the
        # user switched to it.
        self._target_pid = anchor.pid
        # When non-zero, frames come from capturing this window's
        # content directly (see window_capture.py) rather than reading
        # screen pixels -- this is what keeps capture working even
        # when another window is later drawn on top of it on screen,
        # since PrintWindow asks the target window to render its own
        # content rather than reading whatever's currently composited
        # at those screen coordinates. The occlusion guard below (and
        # its "something's covering it, pause" behavior) is only
        # meaningful for the mss/screen-region fallback used when no
        # window could be resolved at selection time.
        self._target_hwnd = anchor.hwnd
        self._offset_x = anchor.offset_x
        self._offset_y = anchor.offset_y
        # Those offsets describe the window's layout at selection time
        # only. The tracker (built in start(), from the reference frame
        # the overlay captured) re-locates the selected content when
        # the window reflows around it -- opening Zoom's chat panel,
        # switching Zoom views, the presenter resizing their share.
        # Without it the crop stayed at a stale offset and happily
        # saved whatever moved in underneath, which is the bug this
        # whole mechanism exists to kill.
        self._anchor_reference = anchor.reference_window
        self._tracker: AnchorTracker | None = None
        self._anchor_status: AnchorStatus | None = None
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
        self._build_tracker()
        self._timer = QTimer()
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        if self._target_hwnd == 0 and self._target_pid == 0:
            # No hint was available (e.g. resuming a session without a
            # fresh region selection) -- best effort, with the known
            # caveat that this will incorrectly anchor on Sniply's
            # own process since that's what's in front right now.
            self._target_pid = process_at_region_center(self._region)
        logger.debug(
            "worker started: region=%s interval_ms=%d target_pid=%s target_hwnd=%s mode=%s",
            self._region, self._interval_ms, self._target_pid, self._target_hwnd,
            "window-capture" if self._target_hwnd else "screen-region",
        )
        self.status_changed.emit("Capturing")

    def _build_tracker(self) -> None:
        """Creates the drift tracker, if this session can have one.

        Only window-capture mode needs it. In screen-region mode the
        user picked a rectangle of the *screen* and that is exactly
        what gets grabbed every tick, so there is no coordinate space
        that can silently shift out from under the selection.
        """
        if self._target_hwnd == 0 or self._anchor_reference is None:
            return
        try:
            tracker = AnchorTracker(
                self._anchor_reference,
                self._offset_x,
                self._offset_y,
                self._region.width,
                self._region.height,
            )
        except Exception:  # defensive: never let tracker setup block capture
            logger.exception("anchor tracker setup failed; continuing with a fixed offset")
            return
        self._tracker = tracker
        # The reference frame is only needed to build the template;
        # holding a full-window RGB frame for the whole session after
        # that is pure memory for nothing.
        self._anchor_reference = None

    @Slot()
    def pause(self) -> None:
        self._paused = True
        self.status_changed.emit("Paused")

    @Slot()
    def resume(self) -> None:
        self._paused = False
        self._consecutive_errors = 0
        # Deliberately NOT re-resolving _target_pid or touching
        # _occluded here: clicking Resume means Sniply's own
        # window is what's currently in front, so re-resolving now
        # would re-anchor on Sniply itself instead of the user's
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
        except AnchorLostError:
            # Deliberately refused rather than honoured. An explicit
            # request is a strong signal about *when* to capture, not
            # about *what* is at a stale offset -- saving the chat
            # panel because the user pressed the button is still
            # saving the chat panel.
            self.error.emit(
                "Can't tell where the selected area is right now -- the window's layout "
                "changed. Click Reselect Area to point at it again."
            )
            return
        except (mss.ScreenShotError, CaptureGrabError) as exc:
            self.error.emit(f"Manual capture failed: {exc}")
            return
        self._save(frame)

    def _tick(self) -> None:
        if self._paused:
            logger.debug("tick: paused, skipping")
            return

        try:
            frame = self._grab_frame()
        except AnchorLostError as exc:
            # Not counted toward _consecutive_errors: this is a
            # recoverable layout condition, not a failing capture
            # path, and escalating it to an auto-pause would punish
            # the user for a transient animation. The tracker keeps
            # searching every tick and re-locks on its own when the
            # content reappears. Nothing is saved meanwhile, which is
            # the whole point.
            logger.info("tick: %s", exc)
            return
        except (mss.ScreenShotError, CaptureGrabError) as exc:
            self._consecutive_errors += 1
            logger.warning("tick: grab failed: %s", exc)
            self.error.emit(f"Capture failed: {exc}")
            if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                self.pause()
                reason = (
                    "the captured window was closed or is no longer available"
                    if self._target_hwnd
                    else "monitor disconnected or locked?"
                )
                self.error.emit(
                    f"Capture region is unavailable ({reason}); "
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

        Only meaningful for the mss/screen-region fallback. When a
        target window was resolved (self._target_hwnd != 0), frames
        come from capturing that window's content directly (see
        _grab_frame/window_capture.py), which already sees straight
        through anything drawn on top of it on screen -- there is
        nothing to be "occluded" by in that mode, so this always
        reports not-occluded and the whole covered-by-another-window
        pause behavior simply doesn't apply.

        Compares by owning process rather than exact window handle so
        that an app swapping to a different top-level window for the
        same content -- notably PowerPoint, which creates a brand new
        window when you switch from the editor into Slideshow (F5) --
        isn't mistaken for a different application entirely.

        Sniply's own process is never treated as an occluder:
        the floating control bar (ui/floating_bar.py) is meant to be
        draggable anywhere, including directly over the capture
        region, and is excluded from the actual screen capture via
        SetWindowDisplayAffinity -- so mss genuinely still sees the
        real target underneath it. Treating our own bar as "occluding"
        would incorrectly pause capture every time it's dragged there,
        even though nothing about what's actually being captured
        changed.
        """
        if self._target_hwnd != 0:
            return False  # window-capture mode: nothing can occlude it

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
        if self._target_hwnd:
            return self._grab_from_window()
        shot = self._sct.grab(self._region.as_mss_dict())
        bgra = np.array(shot)
        return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)

    def _grab_from_window(self) -> np.ndarray:
        """Captures the target window's own content directly (see
        window_capture.py) and returns just the sub-rectangle the user
        selected -- found afresh in this frame rather than assumed to
        still be where it was.

        Two separate problems are being solved here, and conflating
        them is what made the old version wrong:

        - *Something else is on top of the window on screen.*
          PrintWindow handles this by asking the target to render
          itself instead of reading composited screen pixels.
        - *The window rearranged its own insides.* PrintWindow does
          nothing for this, because the window is perfectly visible --
          the slide just isn't where it used to be. Opening Zoom's
          chat panel shrinks the shared-screen area within an
          identically sized window, and cropping the old rectangle
          then returns the chat. That's what the tracker fixes.
        """
        if not window_exists(self._target_hwnd):
            raise CaptureGrabError("the captured window no longer exists")

        full = capture_window(self._target_hwnd)
        if full is None:
            raise CaptureGrabError("failed to capture the target window's content")

        if self._tracker is None:
            return self._crop_fixed(full)

        result = self._tracker.track(full)
        self._note_anchor_status(result.status)
        if result.status is AnchorStatus.LOST:
            raise AnchorLostError(
                "the selected area is no longer where it was in the window "
                "(did the layout change?) -- not saving a possibly wrong capture"
            )

        win_h, win_w = full.shape[:2]
        x0, y0, w, h = result.offset_x, result.offset_y, result.width, result.height
        if x0 < 0 or y0 < 0 or x0 + w > win_w or y0 + h > win_h:
            raise CaptureGrabError(
                "the captured window moved/resized in a way that invalidated the selection"
            )

        cropped = full[y0:y0 + h, x0:x0 + w]
        if (w, h) != (self._region.width, self._region.height):
            # The content area was rescaled by the reflow (a panel
            # opening typically shrinks it). Normalise back to the
            # originally selected pixel size so every frame the
            # detection pipeline and the gallery see has consistent
            # dimensions -- otherwise a mid-session reflow would make
            # every subsequent screenshot a different size from the
            # ones before it, and the dedup comparisons across that
            # boundary meaningless.
            interp = cv2.INTER_AREA if w > self._region.width else cv2.INTER_CUBIC
            cropped = cv2.resize(
                cropped, (self._region.width, self._region.height), interpolation=interp
            )
        return cropped

    def _crop_fixed(self, full: np.ndarray) -> np.ndarray:
        """Fallback crop at the selection-time offset, used when no
        tracker could be built -- the selection covers essentially the
        whole window (nothing to drift relative to), its surroundings
        are a flat colour field with no landmark to recognise, or
        PrintWindow refused at selection time so there was no
        reference frame. Best-effort by construction.
        """
        x0, y0 = self._offset_x, self._offset_y
        w, h = self._region.width, self._region.height
        if x0 < 0 or y0 < 0:
            raise CaptureGrabError(
                "the captured window moved/resized in a way that invalidated the selection"
            )
        cropped = full[y0:y0 + h, x0:x0 + w]
        if cropped.shape[0] != h or cropped.shape[1] != w:
            raise CaptureGrabError(
                "the captured window is now smaller than the originally selected area"
            )
        return cropped

    def _note_anchor_status(self, status: AnchorStatus) -> None:
        """Reports tracking state to the UI on change only, and makes
        a re-anchor behave like the content just appeared.

        The force-evaluate matters: after a re-anchor, the detection
        pipeline's "previous frame" is a crop from the *old* position,
        so the normal settle-over-N-ticks path would compare across
        two unrelated rectangles. Treating the new position as freshly
        revealed re-runs the same immediate-evaluate path used when
        the user switches back to a covered window, which is exactly
        the right semantics -- this is the first frame of this
        content as it now sits.
        """
        if status is self._anchor_status:
            return
        self._anchor_status = status
        self.anchor_status_changed.emit(status.name.lower())
        if status is AnchorStatus.REANCHORED:
            self._just_became_visible = True
            self.status_changed.emit("Re-locked onto the selected area")
        elif status is AnchorStatus.LOST:
            self.status_changed.emit("Lost the selected area — not saving")
        elif status is AnchorStatus.LOCKED:
            self.status_changed.emit("Capturing")


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
        anchor: CaptureAnchor = CaptureAnchor(),
    ):
        super().__init__()
        self.thread = QThread()
        self.worker = CaptureWorker(
            region, pipeline, db_path, session_id, save_dir, interval_ms, anchor
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
