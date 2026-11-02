"""The full detection/dedup pipeline, independent of any UI or screen
capture library. It is fed numpy frames and returns a decision -- this
is what lets it be unit-tested with prerecorded image sequences
instead of a live Zoom call.

Stage order, cheapest first:
  1. StabilityTracker -- is this frame even worth evaluating?
  2. phash vs. every previously saved screenshot -- cheap, catches
     the easy "definitely same" / "definitely different" cases.
  3. SSIM vs. the closest phash match -- only for the ambiguous band,
     since it's the most expensive stage.
"""

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np

from slidecapture.config.defaults import DetectionConfig
from slidecapture.detection.frame_diff import downscale_grayscale
from slidecapture.detection.phash import compute_phash, hamming_distance
from slidecapture.detection.ssim import compute_ssim_gray
from slidecapture.detection.stability import StabilityTracker


class Decision(Enum):
    PENDING = auto()    # no stable candidate yet, nothing to decide
    DUPLICATE = auto()  # stable candidate, but matches an earlier save
    SAVE = auto()        # stable candidate, new content


@dataclass
class PipelineResult:
    decision: Decision
    frame: np.ndarray | None = None  # populated only when decision == SAVE


class _SavedEntry:
    """What the pipeline remembers about each saved screenshot: just
    enough to compare against (hash + small grayscale copy), never the
    full-resolution image -- that keeps memory bounded over a
    multi-hour lecture even with hundreds of saved slides.
    """

    __slots__ = ("phash", "gray")

    def __init__(self, phash, gray: np.ndarray):
        self.phash = phash
        self.gray = gray


class DetectionPipeline:
    def __init__(self, config: DetectionConfig):
        self.config = config
        self._stability = StabilityTracker(
            fast_diff_threshold=config.fast_diff_threshold,
            stability_count=config.stability_count,
            compare_width=config.compare_width,
        )
        self._saved: list[_SavedEntry] = []

    def reset(self) -> None:
        """Clear in-memory state, e.g. when the capture region or
        session changes. Does not touch anything on disk."""
        self._stability.reset()
        self._saved = []

    def seed_from_frames(self, frames: list[np.ndarray]) -> None:
        """Repopulate dedup history from a reopened session's existing
        screenshots, so a resumed session still recognizes slides it
        already saved before the app was closed."""
        for frame in frames:
            self._register(frame)

    def process_frame(self, frame: np.ndarray) -> PipelineResult:
        candidate = self._stability.update(frame)
        if candidate is None:
            return PipelineResult(Decision.PENDING)

        if not self._saved:
            self._register(candidate)
            return PipelineResult(Decision.SAVE, candidate)

        cand_hash = compute_phash(candidate)
        distances = [hamming_distance(cand_hash, entry.phash) for entry in self._saved]
        min_dist = min(distances)
        closest = self._saved[distances.index(min_dist)]

        if min_dist <= self.config.phash_duplicate_max:
            return PipelineResult(Decision.DUPLICATE)

        if min_dist >= self.config.phash_new_min:
            self._register(candidate, precomputed_hash=cand_hash)
            return PipelineResult(Decision.SAVE, candidate)

        # Ambiguous band: let SSIM break the tie against the closest match.
        cand_gray = downscale_grayscale(candidate, self.config.compare_width)
        similarity = compute_ssim_gray(cand_gray, closest.gray)
        if similarity >= self.config.ssim_duplicate_threshold:
            return PipelineResult(Decision.DUPLICATE)

        self._register(candidate, precomputed_hash=cand_hash, precomputed_gray=cand_gray)
        return PipelineResult(Decision.SAVE, candidate)

    def _register(self, frame: np.ndarray, precomputed_hash=None, precomputed_gray=None) -> None:
        phash = precomputed_hash if precomputed_hash is not None else compute_phash(frame)
        gray = (
            precomputed_gray
            if precomputed_gray is not None
            else downscale_grayscale(frame, self.config.compare_width)
        )
        self._saved.append(_SavedEntry(phash, gray))
