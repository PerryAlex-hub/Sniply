"""Default configuration values for Sniply.

These are starting points established from the project spec; they're
exposed as plain module-level constants so tests and the settings UI
can both import and override them without a framework dependency.
"""

from dataclasses import dataclass


@dataclass
class DetectionConfig:
    # How wide to downscale frames before any comparison math.
    # Comparisons run on this size; the ORIGINAL full-res frame is what
    # actually gets saved to disk.
    compare_width: int = 320

    # Capture tick interval, in milliseconds.
    capture_interval_ms: int = 1000

    # Fast grayscale mean-abs-difference (0-255 scale) above which two
    # consecutive frames are considered "changed" rather than "boring".
    fast_diff_threshold: float = 4.0

    # Number of consecutive "boring" ticks (i.e. frame-to-frame diff
    # below fast_diff_threshold) required before a candidate frame is
    # considered stable/settled and eligible to be saved.
    stability_count: int = 2

    # Perceptual hash (phash) Hamming distance thresholds, comparing a
    # candidate frame against every previously saved screenshot.
    # <= phash_duplicate_max  -> confidently a duplicate, discard.
    # >= phash_new_min        -> confidently new content, save.
    # in between              -> ambiguous, fall back to SSIM.
    phash_duplicate_max: int = 5
    phash_new_min: int = 12

    # SSIM similarity (0-1) above which an ambiguous-band frame is
    # still treated as a duplicate.
    ssim_duplicate_threshold: float = 0.90


@dataclass
class AppConfig:
    detection: DetectionConfig = None
    default_save_location: str = ""

    def __post_init__(self):
        if self.detection is None:
            self.detection = DetectionConfig()
