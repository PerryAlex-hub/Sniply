"""Helpers for building synthetic "slide" frames in tests, so the
detection pipeline can be exercised deterministically without ever
needing a real screen capture or a Zoom call.
"""

import cv2
import numpy as np


def make_slide(lines: list[str], size=(480, 640), noise_seed: int | None = None) -> np.ndarray:
    """Render a white "slide" frame with the given lines of black text.
    Optionally add a tiny bit of deterministic noise to simulate codec
    / capture artifacts between otherwise-identical frames.
    """
    h, w = size
    frame = np.full((h, w, 3), 255, dtype=np.uint8)
    y = 80
    for line in lines:
        cv2.putText(frame, line, (30, y), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3)
        y += 90

    if noise_seed is not None:
        rng = np.random.default_rng(noise_seed)
        noise = rng.integers(-2, 3, size=frame.shape, dtype=np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    return frame


def make_transition_frame(x_offset: int, size=(480, 640)) -> np.ndarray:
    """A solid black bar at a given horizontal offset, used to
    simulate an in-progress slide transition animation (wipe/fade):
    each offset produces a frame that's substantially different from
    the previous one, so a sequence of these should never settle into
    a stable candidate.
    """
    h, w = size
    frame = np.full((h, w, 3), 255, dtype=np.uint8)
    cv2.rectangle(frame, (x_offset, 0), (x_offset + 150, h), (0, 0, 0), -1)
    return frame


def move_cursor_dot(frame: np.ndarray, x: int, y: int) -> np.ndarray:
    """Return a copy of `frame` with a small dark dot drawn at (x, y),
    simulating a mouse cursor sitting somewhere in the capture region.
    """
    out = frame.copy()
    cv2.circle(out, (x, y), 4, (20, 20, 20), -1)
    return out
