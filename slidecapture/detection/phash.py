"""Perceptual hashing for cross-screenshot duplicate detection.

Unlike frame_diff (which only compares consecutive ticks), phash is
used to compare a stable candidate against EVERY previously saved
screenshot, so returning to an earlier slide is recognized correctly.
Perceptual hashes are small (64 bits here) and tolerant of minor
compression/resize noise, which makes comparing against a whole
history cheap and robust.
"""

import imagehash
import numpy as np
from PIL import Image


def compute_phash(frame: np.ndarray) -> imagehash.ImageHash:
    """Compute a perceptual hash for a BGR numpy frame."""
    rgb = frame[:, :, ::-1]  # BGR -> RGB
    pil_image = Image.fromarray(rgb)
    return imagehash.phash(pil_image)


def hamming_distance(a: imagehash.ImageHash, b: imagehash.ImageHash) -> int:
    """Number of differing bits between two phashes. 0 = identical,
    higher = more different. imagehash supports subtraction directly."""
    return a - b
