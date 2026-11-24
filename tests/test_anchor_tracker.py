"""Tests for the capture-area drift tracker.

These are written against synthetic "windows" rather than a live
screen, matching the rest of the suite (see tests/fixtures) -- the
tracker is pure numpy/OpenCV by design precisely so this is possible.

The model each test builds is a deliberate miniature of the real
situation that broke: a window containing a *panel* (a textured bezel
with content inside it) sitting on a plain background. The bezel
stands in for Zoom's chrome around a shared screen -- letterbox bars,
the floating toolbar, panel edges. What makes it the right model is
that it moves and scales *with* the content, exactly as real chrome
does when a chat panel opens, which is what makes it a usable
landmark when the content itself (the slide) has changed completely.
"""

import numpy as np
import pytest

from sniply.capture.anchor_tracker import AnchorStatus, AnchorTracker

WINDOW_W, WINDOW_H = 1280, 800
BEZEL = 44  # comfortably thicker than the tracker's ring margin


def _texture(width: int, height: int, seed: int) -> np.ndarray:
    """A deterministic, high-variance BGR pattern.

    Chrome has to be *textured* for the tracker to accept it as a
    landmark -- a flat colour field matches equally well everywhere,
    and the tracker refuses to track one on purpose (see
    test_flat_surroundings_are_not_tracked). Real window chrome has
    toolbars, icons and text, so texture is the realistic case.
    """
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)


def _slide(width: int, height: int, seed: int) -> np.ndarray:
    """Stand-in for slide content: large flat blocks, like real slides."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(60, 240, size=(6, 8, 3), dtype=np.uint8)
    return np.repeat(np.repeat(coarse, height // 6 + 1, axis=0), width // 8 + 1, axis=1)[
        :height, :width
    ]


def _window(content_rect, slide, bg=38):
    """Builds a window modelled on a real screen-share panel.

    `content_rect` is (left, top, width, height) of the content area.
    Around it, deliberately NOT a uniformly stretched texture: that
    would just be the tracker's own 9-slice scaling model played back
    to it, and a test that assumes the implementation's model proves
    nothing about reality.

    Instead this is how chrome actually reflows:

    - a border line hugging the content, which translates with it and
      stretches along its length,
    - fixed-size "button" blocks pinned to the corners and to the ends
      of the bottom toolbar, which keep their pixel size at every
      content size and just end up further apart or closer together,
    - flat filler between them, which is what absorbs the size change.

    So when the content is resized, the ring genuinely does not
    transform the way *either* a uniform scale or a pure 9-slice
    stretch predicts -- the corners and the border agree, the middles
    of the strips do not. Relocating through that is the real task.
    """
    import cv2

    left, top, width, height = content_rect
    window = np.full((WINDOW_H, WINDOW_W, 3), bg, dtype=np.uint8)

    b_left, b_top = left - BEZEL, top - BEZEL
    b_w, b_h = width + 2 * BEZEL, height + 2 * BEZEL
    assert b_left >= 0 and b_top >= 0
    assert b_left + b_w <= WINDOW_W and b_top + b_h <= WINDOW_H

    # Panel background, then a bright border hugging the content.
    window[b_top:b_top + b_h, b_left:b_left + b_w] = (58, 54, 52)
    cv2.rectangle(
        window, (left - 3, top - 3), (left + width + 2, top + height + 2), (210, 205, 200), 3
    )

    # Fixed-size chrome blocks pinned to the panel's own corners and
    # to each end of the bottom toolbar -- same pixels at every
    # content size, which is exactly what real buttons and icons do.
    chip = _texture(30, 26, seed=21)
    for cx, cy in (
        (b_left + 4, b_top + 6),
        (b_left + b_w - 34, b_top + 6),
        (b_left + 4, b_top + b_h - 32),
        (b_left + b_w - 34, b_top + b_h - 32),
        (left + 8, top + height + 8),
        (left + width - 38, top + height + 8),
    ):
        window[cy:cy + 26, cx:cx + 30] = chip

    fitted = cv2.resize(slide, (width, height), interpolation=cv2.INTER_LINEAR)
    window[top:top + height, left:left + width] = fitted
    return window


ORIGINAL = (300, 200, 640, 400)  # left, top, width, height


@pytest.fixture
def tracker():
    reference = _window(ORIGINAL, _slide(640, 400, seed=1))
    left, top, width, height = ORIGINAL
    return AnchorTracker(reference, left, top, width, height)


def test_a_textured_panel_is_trackable(tracker):
    assert tracker.tracking


def test_unmoved_window_stays_locked_even_when_the_slide_changes(tracker):
    """The single most important case.

    The selection's own interior is the slide, and it changes on every
    slide -- which is exactly why the tracker fingerprints the ring
    *around* the selection instead. If a new slide could knock the
    tracker off its lock, every slide change would trigger a pointless
    relocate, and the LOST path would start firing during normal use.
    """
    later = _window(ORIGINAL, _slide(640, 400, seed=99))
    result = tracker.track(later)

    assert result.status is AnchorStatus.LOCKED
    assert (result.offset_x, result.offset_y) == (ORIGINAL[0], ORIGINAL[1])
    assert (result.width, result.height) == (ORIGINAL[2], ORIGINAL[3])


def test_content_shifted_by_a_panel_opening_is_relocated(tracker):
    """A chat panel opens: the window is the same size, but the
    content area slides left to make room. This is the bug -- the old
    fixed-offset crop would return the chat panel's pixels."""
    moved = (120, 200, 640, 400)
    later = _window(moved, _slide(640, 400, seed=99))

    result = tracker.track(later)

    assert result.status is AnchorStatus.REANCHORED
    assert result.offset_x == pytest.approx(moved[0], abs=2)
    assert result.offset_y == pytest.approx(moved[1], abs=2)


def test_content_shrunk_by_a_panel_opening_is_relocated_and_rescaled(tracker):
    """The more common Zoom behaviour: opening the panel both shifts
    and shrinks the shared-screen area."""
    resized = (180, 240, 512, 320)  # 0.8x the original
    later = _window(resized, _slide(512, 320, seed=99))

    result = tracker.track(later)

    assert result.status is AnchorStatus.REANCHORED
    assert result.offset_x == pytest.approx(resized[0], abs=6)
    assert result.offset_y == pytest.approx(resized[1], abs=6)
    assert result.width == pytest.approx(resized[2], rel=0.08)
    assert result.height == pytest.approx(resized[3], rel=0.08)


def test_relocating_sticks_for_subsequent_ticks(tracker):
    """After a relocate, the next tick should be a cheap LOCKED hit at
    the new position rather than another full-window search."""
    moved = (120, 200, 640, 400)
    assert tracker.track(_window(moved, _slide(640, 400, seed=99))).status is AnchorStatus.REANCHORED

    again = tracker.track(_window(moved, _slide(640, 400, seed=123)))
    assert again.status is AnchorStatus.LOCKED
    assert again.offset_x == pytest.approx(moved[0], abs=2)


def test_vanished_content_is_reported_lost_not_guessed(tracker):
    """When the panel isn't there at all -- the presenter stopped
    sharing, the app switched to a completely different screen -- the
    tracker must say so. Reporting a confident position here is what
    produced screenshots of the wrong thing; the worker turns LOST
    into "skip this tick and tell the user" (see
    capture/worker.py's AnchorLostError).
    """
    unrelated = np.zeros((WINDOW_H, WINDOW_W, 3), dtype=np.uint8)
    unrelated[:] = _texture(WINDOW_W, WINDOW_H, seed=404)

    result = tracker.track(unrelated)

    assert result.status is AnchorStatus.LOST
    assert not result.usable


def test_flat_surroundings_are_not_tracked():
    """A selection whose surroundings are a uniform colour has no
    landmark to recognise. The tracker declines rather than matching
    the first of thousands of equally good positions -- and the worker
    falls back to the fixed offset, which is no worse than the old
    behaviour and never pretends to be better.
    """
    left, top, width, height = ORIGINAL
    window = np.full((WINDOW_H, WINDOW_W, 3), 30, dtype=np.uint8)
    window[top:top + height, left:left + width] = _slide(width, height, seed=1)

    tracker = AnchorTracker(window, left, top, width, height)

    assert not tracker.tracking
    result = tracker.track(window)
    assert result.status is AnchorStatus.UNTRACKED
    assert result.usable
    assert (result.offset_x, result.offset_y) == (left, top)


def test_full_window_selection_is_not_tracked():
    """Selecting (nearly) the whole window leaves no surrounding ring
    -- and also nothing that can drift, since the selection and the
    window are the same thing."""
    window = _texture(WINDOW_W, WINDOW_H, seed=5)
    tracker = AnchorTracker(window, 0, 0, WINDOW_W, WINDOW_H)

    assert not tracker.tracking
    assert tracker.track(window).status is AnchorStatus.UNTRACKED


def test_repetitive_surroundings_are_refused_rather_than_guessed():
    """A ring that repeats across the window (a tiled background) has
    many equally good matches. The distinctiveness test is what keeps
    the tracker from picking one of them and reporting high
    confidence in a position it has no real evidence for.
    """
    import cv2

    tile = _texture(40, 40, seed=11)
    tiled = np.tile(tile, (WINDOW_H // 40 + 1, WINDOW_W // 40 + 1, 1))[:WINDOW_H, :WINDOW_W]
    left, top, width, height = ORIGINAL
    reference = tiled.copy()
    reference[top:top + height, left:left + width] = _slide(width, height, seed=1)

    tracker = AnchorTracker(reference, left, top, width, height)
    if not tracker.tracking:
        pytest.skip("tiled ring was rejected at setup, which is also a correct refusal")

    # Content moved by an exact multiple of the tile period, so the
    # ring at the old position looks just as good as at the new one.
    moved = tiled.copy()
    moved[top:top + height, left - 120:left - 120 + width] = _slide(width, height, seed=99)

    result = tracker.track(moved)
    assert result.status in (AnchorStatus.LOCKED, AnchorStatus.LOST)
    if result.status is AnchorStatus.LOCKED:
        # Held its old position rather than jumping to an
        # indistinguishable alternative -- also a refusal to guess.
        assert (result.offset_x, result.offset_y) == (left, top)


def test_a_failed_search_is_not_repeated_every_tick(tracker, monkeypatch):
    """A full-window search costs a large fraction of a capture tick.
    While the content genuinely isn't there -- the presenter stopped
    sharing, the window got minimised -- repeating it every tick would
    burn CPU for however long the user waits. The cheap verify still
    runs every tick, so recovery to the same position is immediate;
    only the expensive hunt is rationed.
    """
    import sniply.capture.anchor_tracker as mod

    searches = 0
    real_relocate = mod.AnchorTracker._relocate

    def counting_relocate(self, window):
        nonlocal searches
        searches += 1
        return real_relocate(self, window)

    monkeypatch.setattr(mod.AnchorTracker, "_relocate", counting_relocate)

    gone = _texture(WINDOW_W, WINDOW_H, seed=404)
    for _ in range(6):
        assert tracker.track(gone).status is AnchorStatus.LOST

    assert searches == 2, f"expected a search every {mod.RELOCATE_COOLDOWN_TICKS + 1} ticks, got {searches}"


def test_content_returning_to_its_old_place_relocks_immediately(tracker):
    """The cheap verify runs on every tick regardless of cooldown, so
    the common recovery -- the panel closes again and the content goes
    back where it was -- costs nothing and is picked up at once."""
    gone = _texture(WINDOW_W, WINDOW_H, seed=404)
    assert tracker.track(gone).status is AnchorStatus.LOST
    assert tracker.track(gone).status is AnchorStatus.LOST  # now in cooldown

    back = _window(ORIGINAL, _slide(640, 400, seed=77))
    assert tracker.track(back).status is AnchorStatus.LOCKED
