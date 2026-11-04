"""Structural similarity (SSIM) comparison for ambiguous cases.

phash is fast but coarse: two slides that share a similar layout
(e.g. a title + bullet template) can land close together in hash
space even though their content differs. SSIM looks at local
structural patterns (luminance, contrast, structure) and is a much
stronger -- if slower -- signal, so it's only used as a tiebreaker
when phash distance falls in the ambiguous band.
"""

import numpy as np
from skimage.metrics import structural_similarity

from sniply.detection.frame_diff import downscale_grayscale


def compute_ssim_gray(gray_a: np.ndarray, gray_b: np.ndarray) -> float:
    """SSIM between two already-downscaled grayscale images. Kept
    separate from compute_ssim so the pipeline can cache each saved
    screenshot's small grayscale copy once instead of re-downscaling
    full-resolution frames on every comparison.
    """
    if gray_a.shape != gray_b.shape:
        import cv2

        gray_b = cv2.resize(gray_b, (gray_a.shape[1], gray_a.shape[0]))
    return float(structural_similarity(gray_a, gray_b))


def compute_ssim(frame_a: np.ndarray, frame_b: np.ndarray, compare_width: int) -> float:
    """Returns a similarity score in [-1, 1]; 1.0 means identical.
    In practice for screenshots of real content this ranges roughly
    0..1, with values near 1 indicating the same slide.
    """
    gray_a = downscale_grayscale(frame_a, compare_width)
    gray_b = downscale_grayscale(frame_b, compare_width)
    return compute_ssim_gray(gray_a, gray_b)
