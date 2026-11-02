"""Detects whether the selected capture region is currently covered
by a different APPLICATION than whatever was there when capture
started.

Why this matters: SlideCapture captures a fixed rectangle of SCREEN
pixels, not "the PowerPoint/Zoom window" specifically -- the app has
no notion of which application owns that content (see region.py and
the project spec: region selection is manual, by design). If you
Alt-Tab away to Chrome and Chrome's window happens to cover the same
screen coordinates, the capture loop previously had no way to notice
and would dutifully keep grabbing and dedup-checking whatever pixels
were there -- meaning it could save screenshots of your browser
instead of the lecture while you're not looking at the capture area.

This identifies the OWNING PROCESS of whatever window currently
occupies the region's center point (via WindowFromPoint +
GetWindowThreadProcessId, through ctypes, no extra dependency), and
compares that process against the one that was there when capture
started/the region was selected.

Deliberately process-based rather than window-handle-based: many
presentation apps swap to an entirely different top-level window for
the same logical content. PowerPoint in particular creates a brand
new native window when you switch from the editor into Slideshow
(F5) -- a window-handle comparison would see that as "a different
window" forever and refuse to ever capture the actual presentation.
Comparing by process instead is tolerant of that (still the same
POWERPNT.EXE), while still correctly catching a genuine switch to an
unrelated application.

Windows-only, matching the rest of this project's platform scope.

Known limitation: this is a center-point heuristic, not a true
"is my target window visible" check -- if a different, unrelated
window's corner happens to sit exactly on that pixel the result could
be momentarily wrong. Good enough to catch the common case (switching
applications entirely) without new dependencies.
"""

import ctypes
from ctypes import wintypes

from slidecapture.capture.region import Region

_user32 = ctypes.windll.user32

# ctypes defaults an undeclared function's return type to a 32-bit
# c_int. HWND is a pointer and 64 bits wide on 64-bit Windows, so
# without this explicit prototype, the call below would silently
# truncate every returned handle to its low 32 bits.
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def process_at_region_center(region: Region) -> int:
    """Returns the process id (PID) owning whatever window currently
    occupies the center of `region`, or 0 if there's none (e.g. bare
    desktop) or the lookup fails."""
    point = wintypes.POINT(
        region.left + region.width // 2,
        region.top + region.height // 2,
    )
    hwnd = _user32.WindowFromPoint(point)
    if not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


# Marks a window as visible to the human user on the real display but
# invisible to any screen-capture API, including the GDI-based BitBlt
# that mss uses -- the exact Windows feature video-conferencing apps
# use to show floating controls that never show up in a shared screen.
# Available since Windows 10 2004 (May 2020 Update).
_WDA_EXCLUDEFROMCAPTURE = 0x00000011

_user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]


def exclude_window_from_capture(hwnd: int) -> bool:
    """Call once a window's native handle exists (i.e. after it has
    been shown at least once). Returns False (without raising) on
    Windows versions that don't support this -- it's a nice-to-have
    for SlideCapture's own floating control bar, not essential
    functionality that should crash the app if unavailable.
    """
    try:
        return bool(_user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), _WDA_EXCLUDEFROMCAPTURE))
    except OSError:
        return False
