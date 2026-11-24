"""The capture region: a rectangle in physical screen pixels.

Deliberately expressed in the same coordinate space mss uses (and NOT
Qt's logical/DPI-scaled coordinates) -- see worker.py and
ui/overlay.py for why that distinction matters on scaled displays.
"""

from dataclasses import dataclass, field
from typing import Any

MIN_REGION_SIZE = 20  # pixels; below this a selection is rejected as unusable


class InvalidRegionError(ValueError):
    pass


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self):
        if self.width < MIN_REGION_SIZE or self.height < MIN_REGION_SIZE:
            raise InvalidRegionError(
                f"Selected region is too small ({self.width}x{self.height}); "
                f"minimum is {MIN_REGION_SIZE}x{MIN_REGION_SIZE}."
            )

    @classmethod
    def from_points(cls, x1: int, y1: int, x2: int, y2: int) -> "Region":
        """Build a Region from two arbitrary corner points, normalizing
        drag direction (the overlay lets users drag in any direction)."""
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        return cls(left=left, top=top, width=right - left, height=bottom - top)

    def as_mss_dict(self) -> dict:
        """mss expects a dict with these exact keys."""
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class CaptureAnchor:
    """Where and how to capture a selected Region, resolved once at
    selection time (see ui/overlay.py's mouseReleaseEvent).

    If `hwnd` is non-zero, the worker captures that window's content
    directly (see capture/window_capture.py) and crops to
    (offset_x, offset_y, region.width, region.height) relative to the
    window's own top-left corner, rather than reading screen pixels --
    this keeps capturing correctly even if another window is later
    drawn on top of it on screen. `pid` is kept as a fallback
    occlusion-guard baseline for when hwnd can't be resolved (e.g. the
    selection was over bare desktop).

    `reference_window` is that window's full content as it looked at
    the instant of selection. The offsets above are only valid for
    that exact layout: opening Zoom's chat panel, switching Zoom
    views, or the presenter resizing their shared window all move the
    slide area *inside* an otherwise unchanged window, after which the
    stored offsets point at the wrong thing (which is how chat panels
    ended up saved as slides). capture/anchor_tracker.py uses this
    reference frame to re-locate the selected content on the fly, so
    carrying it costs one frame of memory and buys a crop that follows
    the content rather than the coordinates.

    It is captured here, at selection time, rather than on the
    worker's first tick, because that is the only moment the layout is
    known to match what the user pointed at -- they may well open the
    chat panel between selecting the area and clicking Start Capture.
    """

    pid: int = 0
    hwnd: int = 0
    offset_x: int = 0
    offset_y: int = 0
    # `compare=False`: this is a numpy array, and numpy's elementwise
    # `==` would make the generated __eq__ return an array instead of
    # a bool (and frozen=True's __hash__ would raise outright).
    # Typed as Any rather than np.ndarray to keep this module -- the
    # one every other capture module imports -- free of a numpy
    # import it has no other use for.
    reference_window: Any = field(default=None, compare=False, repr=False)
