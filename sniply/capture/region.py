"""The capture region: a rectangle in physical screen pixels.

Deliberately expressed in the same coordinate space mss uses (and NOT
Qt's logical/DPI-scaled coordinates) -- see worker.py and
ui/overlay.py for why that distinction matters on scaled displays.
"""

from dataclasses import dataclass

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
    """

    pid: int = 0
    hwnd: int = 0
    offset_x: int = 0
    offset_y: int = 0
