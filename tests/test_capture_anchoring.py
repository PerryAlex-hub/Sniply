"""Tests the capture worker's use of the capture-area tracker.

test_anchor_tracker.py proves the tracker can follow and can refuse.
This file proves the worker *acts* on that, which is the part that
actually protects the user: a refusal has to become "no screenshot
and a visible warning", not a screenshot of whatever was at the stale
offset. That translation is where the original bug lived, so it gets
its own test rather than being assumed.

The Win32 calls are replaced with a fake window, so this runs
anywhere -- the point here is the worker's decision-making, not
PrintWindow.
"""

import numpy as np
import pytest

from sniply.capture.region import CaptureAnchor, Region
from sniply.capture.worker import AnchorLostError, CaptureGrabError, CaptureWorker

from tests.test_anchor_tracker import ORIGINAL, _slide, _texture, _window

pytest.importorskip("PySide6")

LEFT, TOP, WIDTH, HEIGHT = ORIGINAL
HWND = 4242


@pytest.fixture
def fake_window(monkeypatch):
    """Swaps the Win32 window capture for a frame this test controls.

    `fake_window.frame` is what the next grab will see, so a test can
    stage a reflow simply by assigning a differently-laid-out window.
    """

    class Fake:
        frame = _window(ORIGINAL, _slide(WIDTH, HEIGHT, seed=1))

    fake = Fake()
    monkeypatch.setattr("sniply.capture.worker.window_exists", lambda hwnd: True)
    monkeypatch.setattr("sniply.capture.worker.capture_window", lambda hwnd: fake.frame)
    return fake


def _worker(reference, tmp_path):
    worker = CaptureWorker(
        region=Region(left=0, top=0, width=WIDTH, height=HEIGHT),
        pipeline=None,
        db_path=tmp_path / "session.db",
        session_id=1,
        save_dir=tmp_path,
        interval_ms=1000,
        anchor=CaptureAnchor(
            pid=1, hwnd=HWND, offset_x=LEFT, offset_y=TOP, reference_window=reference
        ),
    )
    worker._build_tracker()
    return worker


def test_a_reflow_returns_the_selected_content_not_the_stale_rectangle(fake_window, tmp_path):
    """The bug, end to end.

    A panel opens, the content area slides across inside the same
    window, and the grab must come back with the slide -- not with
    whatever now occupies the coordinates the selection was made at.
    """
    reference = fake_window.frame
    worker = _worker(reference, tmp_path)
    assert worker._tracker is not None and worker._tracker.tracking

    moved_to = (120, 200, WIDTH, HEIGHT)
    slide = _slide(WIDTH, HEIGHT, seed=99)
    fake_window.frame = _window(moved_to, slide)

    grabbed = worker._grab_from_window()

    assert grabbed.shape == (HEIGHT, WIDTH, 3)
    expected = fake_window.frame[
        moved_to[1]:moved_to[1] + HEIGHT, moved_to[0]:moved_to[0] + WIDTH
    ]
    assert np.array_equal(grabbed, expected)

    # And emphatically not the old coordinates, which now hold chrome.
    stale = fake_window.frame[TOP:TOP + HEIGHT, LEFT:LEFT + WIDTH]
    assert not np.array_equal(grabbed, stale)


def test_a_reflow_that_resizes_still_returns_the_original_pixel_size(fake_window, tmp_path):
    """Downstream code -- dedup comparisons, the gallery, the PDF --
    assumes every frame in a session is the same size. A reflow that
    shrinks the content area must therefore be normalised back, not
    passed through at its new size."""
    worker = _worker(fake_window.frame, tmp_path)

    fake_window.frame = _window((180, 240, 512, 320), _slide(512, 320, seed=99))
    grabbed = worker._grab_from_window()

    assert grabbed.shape == (HEIGHT, WIDTH, 3)


def test_losing_the_area_raises_rather_than_returning_wrong_pixels(fake_window, tmp_path):
    """The safety property. When the tracker can't find the content,
    the worker must refuse -- an exception the tick handler turns into
    "skip this save and tell the user". Returning the stale crop here
    is precisely how the chat panel got filed as a slide.
    """
    worker = _worker(fake_window.frame, tmp_path)

    fake_window.frame = _texture(1280, 800, seed=404)

    with pytest.raises(AnchorLostError):
        worker._grab_from_window()


def test_anchor_loss_is_not_treated_as_a_capture_failure(fake_window, tmp_path):
    """AnchorLostError must not be a CaptureGrabError.

    Grab failures escalate: a few in a row auto-pause the session.
    Losing the area is recoverable and often transient (an animation,
    a window mid-resize), and the tracker keeps looking every tick --
    so escalating it would tear down a session that was about to
    recover on its own.
    """
    assert not issubclass(AnchorLostError, CaptureGrabError)


def test_an_untrackable_selection_falls_back_to_the_fixed_offset(tmp_path, monkeypatch):
    """Selecting (nearly) the whole window leaves no surrounding
    landmark -- and also nothing that can drift. Capture must still
    work, using the offset as given."""
    plain = np.full((800, 1280, 3), 30, dtype=np.uint8)
    plain[TOP:TOP + HEIGHT, LEFT:LEFT + WIDTH] = _slide(WIDTH, HEIGHT, seed=1)

    monkeypatch.setattr("sniply.capture.worker.window_exists", lambda hwnd: True)
    monkeypatch.setattr("sniply.capture.worker.capture_window", lambda hwnd: plain)

    worker = _worker(plain, tmp_path)
    assert worker._tracker is not None
    assert not worker._tracker.tracking

    grabbed = worker._grab_from_window()
    assert np.array_equal(grabbed, plain[TOP:TOP + HEIGHT, LEFT:LEFT + WIDTH])


def test_no_reference_frame_means_no_tracker_but_still_captures(tmp_path, monkeypatch):
    """PrintWindow can refuse at selection time (some hardware-
    accelerated surfaces). Capture then behaves exactly as it did
    before tracking existed, rather than failing to start."""
    frame = _window(ORIGINAL, _slide(WIDTH, HEIGHT, seed=1))
    monkeypatch.setattr("sniply.capture.worker.window_exists", lambda hwnd: True)
    monkeypatch.setattr("sniply.capture.worker.capture_window", lambda hwnd: frame)

    worker = _worker(None, tmp_path)
    assert worker._tracker is None

    grabbed = worker._grab_from_window()
    assert grabbed.shape == (HEIGHT, WIDTH, 3)
