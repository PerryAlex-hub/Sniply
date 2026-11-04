"""Fast frame-to-frame difference check.

This is the cheapest stage of the pipeline and runs on every captured
tick, so it has to be fast: downscale, grayscale, mean absolute
difference. It answers one question only -- "did anything change
since the last tick?" -- not whether a change is worth saving.
"""

import cv2
import numpy as np


def downscale_grayscale(frame: np.ndarray, width: int) -> np.ndarray:
    """Resize a BGR frame to `width` (preserving aspect ratio) and
    convert to grayscale. Downscaling first keeps every later stage
    (diff, phash, SSIM) cheap regardless of the user's selected region
    size -- a 4K capture region costs the same as a tiny one here.
    """
    h, w = frame.shape[:2]
    scale = width / w
    new_size = (width, max(1, int(h * scale)))
    small = cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def mean_abs_diff(frame_a: np.ndarray, frame_b: np.ndarray, compare_width: int) -> float:
    """Mean absolute pixel difference between two frames, on a 0-255
    scale, after downscaling+grayscaling both. Lower = more similar.
    """
    gray_a = downscale_grayscale(frame_a, compare_width)
    gray_b = downscale_grayscale(frame_b, compare_width)
    if gray_a.shape != gray_b.shape:
        gray_b = cv2.resize(gray_b, (gray_a.shape[1], gray_a.shape[0]))
    diff = cv2.absdiff(gray_a, gray_b)
    return float(np.mean(diff))
