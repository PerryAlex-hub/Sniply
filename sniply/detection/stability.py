"""Stability tracking: decide when a run of captured frames has
"settled" into something worth evaluating as a save candidate.

This is what prevents Sniply from saving a screenshot mid-fade
during a slide transition. The rule is simple and deliberately dumb:
a candidate is only accepted once N consecutive ticks were each
"boring" relative to the one before (frame-to-frame diff below
threshold). A transition animation keeps producing frames that differ
from their predecessor, so it never accumulates N boring ticks until
motion actually stops.
"""

import logging

import numpy as np

from sniply.detection.frame_diff import mean_abs_diff

logger = logging.getLogger(__name__)


class StabilityTracker:
    def __init__(self, fast_diff_threshold: float, stability_count: int, compare_width: int):
        self._threshold = fast_diff_threshold
        self._required = stability_count
        self._compare_width = compare_width
        self._last_frame: np.ndarray | None = None
        self._boring_streak = 0
        self._already_emitted = False

    def reset(self) -> None:
        """Forget history, e.g. after a candidate has been consumed
        or the region/session changes."""
        self._last_frame = None
        self._boring_streak = 0
        self._already_emitted = False

    def set_baseline(self, frame: np.ndarray) -> None:
        """Treat `frame` as the current reference point without going
        through the normal settle-over-N-ticks wait -- used when the
        capture target just became visible again (e.g. the user
        switched back to the presentation after being away): there's
        no reason to make the stability tracker re-learn that this
        frame is "boring" relative to itself over several ticks before
        future comparisons can proceed normally. `_already_emitted` is
        set True so the very next ordinary tick (which will compare
        against this same frame) doesn't immediately re-emit it as a
        second "new" candidate.
        """
        self._last_frame = frame
        self._boring_streak = 1
        self._already_emitted = True

    def update(self, frame: np.ndarray) -> np.ndarray | None:
        """Feed one new captured frame in. Returns the frame itself if
        it is a freshly-settled stable candidate ready for dedup
        evaluation, otherwise None.

        "Freshly settled" means: this is the first tick of an
        already-boring streak that just reached `_required` -- we
        don't re-emit the same settled frame every tick after that,
        since nothing changed.
        """
        if self._last_frame is None:
            self._last_frame = frame
            self._boring_streak = 1
            self._already_emitted = False
            return None

        diff = mean_abs_diff(frame, self._last_frame, self._compare_width)
        self._last_frame = frame

        if diff <= self._threshold:
            self._boring_streak += 1
        else:
            self._boring_streak = 1
            self._already_emitted = False

        logger.debug(
            "stability: diff=%.4f threshold=%.4f streak=%d/%d",
            diff, self._threshold, self._boring_streak, self._required,
        )

        if self._boring_streak >= self._required and not self._already_emitted:
            self._already_emitted = True
            logger.debug("stability: candidate settled")
            return frame

        return None
