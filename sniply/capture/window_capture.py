"""Captures a specific window's content directly, via the Win32
PrintWindow API, rather than reading screen pixels at a fixed
location.

Why this exists: the normal capture path (mss) reads whatever is
actually composited on screen at a region -- if you switch to your
code editor and it visually covers Zoom, mss necessarily sees the
editor, because that's genuinely all that's on screen at those
pixels. PrintWindow instead asks the TARGET WINDOW to render its own
content into an off-screen bitmap directly, bypassing whatever is
drawn on top of it on screen. This is the same technique screen-
share/recording tools use for "share a specific window" (as opposed
to "share my screen"), and it's what makes it possible to keep
capturing a lecture while freely multitasking on top of it.

Windows-only, via ctypes (no extra dependency, consistent with the
rest of this package).

Known limitations:
- A window that is fully minimized, or that renders via a graphics
  path PrintWindow can't reach (some hardware-accelerated / exclusive
  fullscreen surfaces), may return a blank or stale frame. Covered-
  by-another-window is the case this specifically solves; minimized
  is a separate, harder problem not addressed here.
- The window must still exist; capture_window returns None if the
  handle is no longer valid, which the caller treats like any other
  grab failure.
"""

import ctypes
from ctypes import wintypes

import numpy as np

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

# Tells PrintWindow to render the window's full content, including
# parts drawn via DirectX/Direct2D/modern GDI paths -- without this
# flag, PrintWindow on many apps (especially Electron/Chromium-based
# ones, which Zoom partly is) produces a blank white capture instead
# of the actual content. Available since Windows 8.1.
_PW_RENDERFULLCONTENT = 0x00000002
_BI_RGB = 0
_DIB_RGB_COLORS = 0

_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.PrintWindow.restype = wintypes.BOOL
_user32.GetWindowDC.argtypes = [wintypes.HWND]
_user32.GetWindowDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL

_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


_gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
]
_gdi32.GetDIBits.restype = ctypes.c_int


def window_exists(hwnd: int) -> bool:
    return bool(_user32.IsWindow(wintypes.HWND(hwnd)))


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Returns (left, top, right, bottom) in physical screen pixels,
    or None if the window handle is no longer valid."""
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def capture_window(hwnd: int) -> np.ndarray | None:
    """Returns the window's current content as a BGR numpy array (its
    full window rect, not cropped to any sub-region -- see
    worker.py, which crops to the user's selected sub-rectangle
    afterward), or None if the window is gone or the capture failed.
    """
    rect = get_window_rect(hwnd)
    if rect is None:
        return None
    left, top, right, bottom = rect
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None

    window_dc = _user32.GetWindowDC(wintypes.HWND(hwnd))
    if not window_dc:
        return None

    mem_dc = _gdi32.CreateCompatibleDC(window_dc)
    bitmap = _gdi32.CreateCompatibleBitmap(window_dc, width, height)
    old_obj = _gdi32.SelectObject(mem_dc, bitmap)

    try:
        ok = _user32.PrintWindow(wintypes.HWND(hwnd), mem_dc, _PW_RENDERFULLCONTENT)
        if not ok:
            return None

        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # negative: top-down DIB, matches normal image row order
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = _BI_RGB

        buffer = (ctypes.c_ubyte * (width * height * 4))()
        lines_copied = _gdi32.GetDIBits(
            mem_dc, bitmap, 0, height, buffer, ctypes.byref(info), _DIB_RGB_COLORS
        )
        if lines_copied == 0:
            return None

        arr = np.frombuffer(buffer, dtype=np.uint8).reshape((height, width, 4))
        return np.ascontiguousarray(arr[:, :, :3])  # GDI gives BGRX; drop the padding channel
    finally:
        _gdi32.SelectObject(mem_dc, old_obj)
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(wintypes.HWND(hwnd), window_dc)
