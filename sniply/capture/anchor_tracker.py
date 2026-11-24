"""Keeps the capture crop locked onto the content the user actually
selected, even when the target window reflows its own layout.

The bug this exists to fix
--------------------------
Window-capture mode (see window_capture.py) resolves the selection
once, at selection time, into a fixed offset inside the window's
rect, and then crops that same rectangle on every tick forever. That
is correct only as long as the window's *internal* layout never
changes. In practice it changes constantly:

- Opening Zoom's chat/participants panel shrinks and shifts the
  shared-screen area inside an identically-sized window, so the old
  offset now lands on the chat panel -- and Sniply dutifully saved
  screenshots of the chat as if they were slides.
- Zoom switching between speaker/gallery view, a toolbar appearing or
  auto-hiding, the presenter resizing their shared window, or the
  local window being resized all move the slide area the same way.

So the selection has to be treated as "this *content*", not "these
window coordinates", and re-located on the fly.

How it works
------------
At selection time we remember the **ring of pixels immediately
surrounding** the selection, not the selection's own interior. The
interior is the slide, which by design changes on every slide -- it
is the worst possible landmark. The surrounding ring is the app's own
chrome (panel edges, letterbox bars, toolbars, window background),
which stays put relative to the content area and moves *with* it when
the layout reflows. That ring is the fingerprint we hunt for.

Each tick:

1. **Verify first, cheaply.** Score the ring at the offset we used
   last tick. Still a good match? Nothing moved; reuse it. This is
   the overwhelmingly common case and costs one small comparison.
2. **Otherwise, relocate.** Search the whole window for the ring,
   coarse-to-fine and across a sweep of scales (a reflow usually
   *resizes* the content area as well as moving it), then refine the
   winner at full resolution.
3. **Refuse to guess.** A match is accepted only if it is both close
   enough (low per-pixel error) *and* distinctive -- clearly better
   than the best competing match elsewhere in the window. A near-
   uniform ring matches thousands of places equally well, and in that
   situation a confident answer would be a coin flip dressed up as a
   measurement. When nothing passes, we report LOST, and the caller's
   job is to stop saving and say so rather than to bank whatever
   pixels happened to be at a stale offset. Saving the wrong thing
   silently is the failure mode that made the app untrustworthy; a
   visible "I've lost the slide area" is strictly better.

Matching uses squared-difference (TM_SQDIFF) rather than a
correlation measure on purpose: this is not a perceptual
"does this look similar" problem. It is the same application
rendering the same chrome pixels a second later, so exact pixel
agreement is both available and the sharpest discriminator there is.
Its weakness -- no brightness invariance -- does not apply here, and
its other weakness, ambiguity on low-texture rings, is handled
explicitly by the distinctiveness test rather than hidden.

No Qt, no Win32: this is pure numpy/OpenCV against in-memory frames,
so it is testable with synthetic windows (see
tests/test_anchor_tracker.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# How far outside the selection to reach for the landmark ring, as a
# fraction of the selection's smaller side, clamped to this pixel
# range. Too thin and there isn't enough chrome to identify; too
# thick and the ring starts swallowing neighbouring content that
# itself moves independently.
RING_FRACTION = 0.08
RING_MIN_PX = 10
RING_MAX_PX = 48

# The ring must make up at least this fraction of the template's area
# after clamping to the window, or there isn't enough landmark to
# track with (e.g. the user selected almost the entire window, which
# leaves no surrounding chrome -- and also cannot drift).
MIN_RING_AREA_FRACTION = 0.10

# The ring's own pixel standard deviation (0-255). Below this it is
# effectively a flat colour field, which matches equally well almost
# anywhere; tracking it would produce confident nonsense.
MIN_RING_STD = 6.0

# Confidence is 1 - RMSE/255 over the ring's pixels, so 0.92 means an
# average error of ~20 grey levels per pixel. Verification at the
# known position is allowed to be looser than a blind relocate: we
# already have strong positional evidence there, and a slide change
# can bleed a little into the ring (anti-aliasing, a scrollbar, a
# progress bar baked into the deck).
VERIFY_MIN_CONFIDENCE = 0.88
RELOCATE_MIN_CONFIDENCE = 0.93

# The coarse pass gets a deliberately looser gate than the full-
# resolution decision above. Its job is only to *nominate* positions,
# and its scores are depressed by an artifact rather than by a worse
# match: the window and the template are downscaled independently, so
# fine detail (window chrome is full of 1px lines and text) lands on
# slightly different sampling grids in each. A perfect, pixel-exact
# match can score ~0.91 coarsely and 1.00 at full resolution. Judging
# nominees by the final threshold would therefore reject correct
# matches for being blurry, so acceptance is decided only after the
# refine step, on real pixels.
COARSE_MIN_CONFIDENCE = 0.80

# How many of the best coarse nominees get refined at full
# resolution. More than one because the same sampling artifact can
# reorder near-tied scales coarsely; the full-resolution score is the
# one that actually decides, so give it a few candidates to rank.
REFINE_CANDIDATES = 3

# A relocate must beat the best *competing* match elsewhere in the
# window by this much confidence to count as distinctive.
MIN_DISTINCTIVENESS = 0.015

# Ticks to wait before attempting another full search after one came
# up empty. Bounds how much CPU is spent hunting for content that
# genuinely isn't there, while still recovering within a few seconds
# once it comes back.
RELOCATE_COOLDOWN_TICKS = 2

# Scales (relative to the original selection) tried when relocating,
# since a reflow typically resizes the content area as well as moving
# it. Ordered so the most likely candidate is scored first.
RELOCATE_SCALES = (1.0, 0.95, 1.05, 0.9, 1.1, 0.8, 1.25, 0.7, 1.4)

# Coarse search runs on a downscaled window for speed; this is the
# target width it is reduced to before the full-window sweep.
COARSE_SEARCH_WIDTH = 720


class AnchorStatus(Enum):
    """Where this tick's crop came from, and how much to trust it."""

    #: The ring still matched where it was last tick. Nothing moved.
    LOCKED = auto()
    #: The layout moved and the ring was found somewhere new. The
    #: crop is trustworthy, but the caller should treat the content
    #: as freshly revealed (re-evaluate immediately) since the
    #: detection pipeline's frame history refers to the old position.
    REANCHORED = auto()
    #: There is no usable landmark ring (the selection covers
    #: essentially the whole window, or its surroundings are a flat
    #: colour field). The fixed offset is used as-is -- which is
    #: correct for a selection that has nothing to drift relative to,
    #: and best-effort otherwise.
    UNTRACKED = auto()
    #: The ring could not be found with confidence. The crop is NOT
    #: trustworthy and must not be saved.
    LOST = auto()


@dataclass(frozen=True)
class _Template:
    """A prepared landmark ring, ready to hand to cv2.matchTemplate.

    `inset_x`/`inset_y` are where the selection's top-left sits inside
    this template, which is what converts a match position back into a
    crop offset. They are the ring's margins, and 9-slice scaling
    leaves margins untouched, so they do not vary with content scale.
    """

    image: np.ndarray
    mask: np.ndarray
    ring_pixels: float
    inset_x: int
    inset_y: int


@dataclass(frozen=True)
class TrackResult:
    status: AnchorStatus
    offset_x: int
    offset_y: int
    width: int
    height: int
    confidence: float

    @property
    def usable(self) -> bool:
        """Whether the caller may crop and save using this result."""
        return self.status is not AnchorStatus.LOST


def _to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame.astype(np.float32)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)


def _confidence_from_sqdiff(sqdiff: float, ring_pixels: float) -> float:
    """Turns an OpenCV TM_SQDIFF score into a 0-1 confidence.

    TM_SQDIFF is the summed squared per-pixel difference over the
    masked (ring) pixels only. Dividing by the ring's pixel count and
    taking the root gives an RMSE in grey levels, which scaled
    against the 0-255 range reads as a plain "how closely do these
    pixels agree" number -- far easier to reason about, threshold and
    log than a raw sum whose magnitude depends on template size.
    """
    if ring_pixels <= 0:
        return 0.0
    rmse = float(np.sqrt(max(sqdiff, 0.0) / ring_pixels))
    return max(0.0, 1.0 - rmse / 255.0)


class AnchorTracker:
    """Tracks one selected content rectangle inside one window.

    Construct it from the window frame as it looked when the user made
    the selection -- that frame is the ground truth for what they
    pointed at, and it is the only moment we can be sure the layout
    matched their intent.
    """

    def __init__(
        self,
        reference_window: np.ndarray,
        offset_x: int,
        offset_y: int,
        width: int,
        height: int,
    ):
        self.width = int(width)
        self.height = int(height)
        self._offset_x = int(offset_x)
        self._offset_y = int(offset_y)
        self._scale = 1.0
        self._relocate_cooldown = 0

        win_h, win_w = reference_window.shape[:2]
        self._ref_window_size = (win_w, win_h)

        self._template: np.ndarray | None = None
        self._ring_pixels = 0.0
        # The ring's thickness on each side, which is also where the
        # selection sits inside the template. Tracked per side because
        # a selection near a window edge gets its box clamped and ends
        # up with an asymmetric ring rather than none at all.
        self._ml = 0
        self._mt = 0
        self._mr = 0
        self._mb = 0

        self._build_template(reference_window)

    # ---- setup ---------------------------------------------------

    def _build_template(self, window: np.ndarray) -> None:
        """Cuts out the selection plus its surrounding ring, and the
        mask that keeps only the ring. Returns quietly without a
        template when there isn't a usable landmark -- that is a
        legitimate state (see AnchorStatus.UNTRACKED), not an error.
        """
        win_h, win_w = window.shape[:2]
        margin = int(round(min(self.width, self.height) * RING_FRACTION))
        margin = max(RING_MIN_PX, min(RING_MAX_PX, margin))

        # Clamp the inflated box to the window. A selection near an
        # edge keeps whatever ring it can get on the other sides
        # rather than being dropped entirely.
        left = max(0, self._offset_x - margin)
        top = max(0, self._offset_y - margin)
        right = min(win_w, self._offset_x + self.width + margin)
        bottom = min(win_h, self._offset_y + self.height + margin)

        if right - left <= self.width or bottom - top <= self.height:
            logger.info("anchor: no room for a landmark ring; tracking disabled")
            return

        template = _to_gray(window[top:bottom, left:right])
        ml = self._offset_x - left
        mt = self._offset_y - top
        mr = right - (self._offset_x + self.width)
        mb = bottom - (self._offset_y + self.height)

        mask = np.ones(template.shape, dtype=np.uint8)
        mask[mt:mt + self.height, ml:ml + self.width] = 0
        ring_pixels = float(mask.sum())
        if ring_pixels < MIN_RING_AREA_FRACTION * mask.size:
            logger.info(
                "anchor: landmark ring is only %.1f%% of the template; tracking disabled",
                100.0 * ring_pixels / mask.size,
            )
            return

        ring_std = float(template[mask.astype(bool)].std())
        if ring_std < MIN_RING_STD:
            logger.info(
                "anchor: surroundings are a near-flat colour field (std=%.2f); tracking disabled",
                ring_std,
            )
            return

        self._template = template
        self._ring_pixels = ring_pixels
        self._ml, self._mt, self._mr, self._mb = ml, mt, mr, mb
        logger.info(
            "anchor: tracking enabled (template=%dx%d ring=%.0fpx std=%.2f margins=%d/%d/%d/%d)",
            template.shape[1], template.shape[0], ring_pixels, ring_std, ml, mt, mr, mb,
        )

    @property
    def tracking(self) -> bool:
        return self._template is not None

    # ---- per-tick ------------------------------------------------

    def track(self, window: np.ndarray) -> TrackResult:
        """Locates the selected content in this frame of the window."""
        if not self.tracking:
            return TrackResult(
                AnchorStatus.UNTRACKED,
                self._offset_x, self._offset_y, self.width, self.height, 1.0,
            )

        scaled_w = max(1, int(round(self.width * self._scale)))
        scaled_h = max(1, int(round(self.height * self._scale)))

        held = self._score_at(window, self._offset_x, self._offset_y, self._scale)
        if held >= VERIFY_MIN_CONFIDENCE:
            self._relocate_cooldown = 0
            return TrackResult(
                AnchorStatus.LOCKED,
                self._offset_x, self._offset_y, scaled_w, scaled_h, held,
            )

        lost = TrackResult(
            AnchorStatus.LOST,
            self._offset_x, self._offset_y, scaled_w, scaled_h, held,
        )

        if self._relocate_cooldown > 0:
            # Already searched recently and found nothing. A full
            # sweep costs a large fraction of a capture tick, and
            # repeating it every tick while the content stays missing
            # (the presenter stopped sharing, the window is minimised)
            # would spin the CPU for the whole time the user is
            # waiting. The cheap verify above still runs every tick,
            # so a return to the same place is picked up immediately;
            # only the expensive hunt for a *new* place is rationed.
            self._relocate_cooldown -= 1
            return lost

        logger.info("anchor: lost lock at held position (confidence=%.4f); relocating", held)
        found = self._relocate(window)
        if found is None:
            self._relocate_cooldown = RELOCATE_COOLDOWN_TICKS
            return lost

        offset_x, offset_y, scale, confidence = found
        self._offset_x, self._offset_y, self._scale = offset_x, offset_y, scale
        self._relocate_cooldown = 0
        logger.info(
            "anchor: re-anchored to (%d,%d) scale=%.3f confidence=%.4f",
            offset_x, offset_y, scale, confidence,
        )
        return TrackResult(
            AnchorStatus.REANCHORED,
            offset_x, offset_y,
            max(1, int(round(self.width * scale))),
            max(1, int(round(self.height * scale))),
            confidence,
        )

    def _scaled_template(self, content_scale: float) -> "_Template | None":
        """Builds the landmark ring for a content area resized by
        `content_scale`, using 9-slice (border-image) scaling rather
        than scaling the whole template uniformly.

        This distinction is the difference between the resize case
        working and not working. When Zoom's shared-screen area
        shrinks to make room for the chat panel, the chrome around it
        does *not* shrink with it: a toolbar is still the same number
        of pixels tall, a 1px panel divider is still 1px, the window
        background is unchanged. Only the content box's own width and
        height change. So the ring is rebuilt the way UI toolkits
        stretch a 9-patch: the four corners are copied at their
        original size, the top and bottom strips stretch horizontally
        only, the left and right strips stretch vertically only, and
        the middle -- the slide -- is masked out and never scored.

        Scaling the template uniformly instead (the obvious first
        implementation) asks the chrome to have shrunk too, which it
        hasn't, so a genuine match scores badly and the tracker gives
        up on exactly the reflow it exists to survive.
        """
        assert self._template is not None
        ref = self._template
        ml, mt, mr, mb = self._ml, self._mt, self._mr, self._mb

        new_w = max(1, int(round(self.width * content_scale)))
        new_h = max(1, int(round(self.height * content_scale)))
        out_w, out_h = ml + new_w + mr, mt + new_h + mb
        if out_w < 8 or out_h < 8:
            return None

        if (new_w, new_h) == (self.width, self.height):
            template = ref
        else:
            template = np.zeros((out_h, out_w), dtype=np.float32)
            # Source and destination column/row bands. The middle band
            # is the only one that changes size.
            src_cols = ((0, ml), (ml, ml + self.width), (ml + self.width, ml + self.width + mr))
            dst_cols = ((0, ml), (ml, ml + new_w), (ml + new_w, out_w))
            src_rows = ((0, mt), (mt, mt + self.height), (mt + self.height, mt + self.height + mb))
            dst_rows = ((0, mt), (mt, mt + new_h), (mt + new_h, out_h))

            for (sr0, sr1), (dr0, dr1) in zip(src_rows, dst_rows):
                for (sc0, sc1), (dc0, dc1) in zip(src_cols, dst_cols):
                    if dr1 <= dr0 or dc1 <= dc0 or sr1 <= sr0 or sc1 <= sc0:
                        continue  # a side with no margin (selection flush to the window edge)
                    patch = ref[sr0:sr1, sc0:sc1]
                    if (dr1 - dr0, dc1 - dc0) != patch.shape:
                        patch = cv2.resize(
                            patch, (dc1 - dc0, dr1 - dr0),
                            interpolation=cv2.INTER_AREA
                            if (dc1 - dc0) * (dr1 - dr0) < patch.size else cv2.INTER_LINEAR,
                        )
                    template[dr0:dr1, dc0:dc1] = patch

        # Built analytically rather than resized: the mask is pure
        # geometry, and resampling it would produce fractional edge
        # weights that quietly let masked-out slide pixels into the
        # score.
        mask = np.ones((out_h, out_w), dtype=np.float32)
        mask[mt:mt + new_h, ml:ml + new_w] = 0.0
        ring_pixels = float(mask.sum())
        if ring_pixels <= 0:
            return None
        return _Template(template, mask, ring_pixels, ml, mt)

    @staticmethod
    def _downscaled(tpl: "_Template", factor: float) -> "_Template | None":
        """Uniformly shrinks a prepared template for the coarse pass.

        Uniform here is correct, unlike in _scaled_template: this
        mirrors the search image being uniformly downscaled, not any
        change in the window's own layout.
        """
        if factor >= 1.0:
            return tpl
        h, w = tpl.image.shape
        new_w, new_h = int(round(w * factor)), int(round(h * factor))
        if new_w < 8 or new_h < 8:
            return None
        image = cv2.resize(tpl.image, (new_w, new_h), interpolation=cv2.INTER_AREA)
        mask = cv2.resize(tpl.mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
        ring_pixels = float(mask.sum())
        if ring_pixels <= 0:
            return None
        return _Template(
            image, mask, ring_pixels,
            int(round(tpl.inset_x * factor)), int(round(tpl.inset_y * factor)),
        )

    def _score_at(self, window: np.ndarray, offset_x: int, offset_y: int, scale: float) -> float:
        """Confidence of the ring sitting exactly at a given offset."""
        tpl = self._scaled_template(scale)
        if tpl is None:
            return 0.0

        left = offset_x - tpl.inset_x
        top = offset_y - tpl.inset_y
        t_h, t_w = tpl.image.shape
        win_h, win_w = window.shape[:2]
        if left < 0 or top < 0 or left + t_w > win_w or top + t_h > win_h:
            return 0.0

        patch = _to_gray(window[top:top + t_h, left:left + t_w])
        diff = (patch - tpl.image) * tpl.mask
        return _confidence_from_sqdiff(float((diff * diff).sum()), tpl.ring_pixels)

    def _relocate(self, window: np.ndarray) -> tuple[int, int, float, float] | None:
        """Searches the whole window for the ring.

        Returns (offset_x, offset_y, scale, confidence), or None when
        no candidate is both confident and distinctive enough to act
        on.

        Same-size first, then the scale sweep. Plenty of reflows move
        the content area without resizing it (a sidebar opening in a
        window that's already wide enough, a toolbar appearing, the
        view shifting), and searching one scale is roughly a ninth of
        the work of searching all of them -- the difference between
        comfortably inside a capture tick and overrunning it on a
        large display. The sweep still runs whenever the single-scale
        answer isn't good enough, so nothing is given up but time in
        the easy case.
        """
        gray = _to_gray(window)
        win_h, win_w = gray.shape
        factor = min(1.0, COARSE_SEARCH_WIDTH / float(win_w))
        coarse = (
            cv2.resize(gray, (int(win_w * factor), int(win_h * factor)), interpolation=cv2.INTER_AREA)
            if factor < 1.0 else gray
        )

        same_size = self._search(gray, coarse, factor, (1.0,))
        if same_size is not None:
            return same_size
        rest = tuple(s for s in RELOCATE_SCALES if s != 1.0)
        return self._search(gray, coarse, factor, rest)

    def _search(
        self,
        gray: np.ndarray,
        coarse: np.ndarray,
        factor: float,
        scales: tuple[float, ...],
    ) -> tuple[int, int, float, float] | None:
        """One coarse-to-fine sweep over the given content scales.

        Every scale is swept on the downscaled window, then only the
        few best coarse nominees are refined at full resolution -- a
        full-resolution sweep of nine scales on a 4K window every time
        a panel opens would cost more than the capture interval
        itself.
        """
        # (coarse confidence, scale, x, y, distinctiveness margin)
        nominees: list[tuple[float, float, int, int, float]] = []

        for scale in scales:
            at_scale = self._scaled_template(scale)
            if at_scale is None:
                continue
            tpl = self._downscaled(at_scale, factor)
            if tpl is None:
                continue
            if tpl.image.shape[0] > coarse.shape[0] or tpl.image.shape[1] > coarse.shape[1]:
                continue

            scores = cv2.matchTemplate(coarse, tpl.image, cv2.TM_SQDIFF, mask=tpl.mask)
            # TM_SQDIFF: lower is better, so the best match is the
            # global minimum of the response map.
            min_val, _, min_loc, _ = cv2.minMaxLoc(scores)
            confidence = _confidence_from_sqdiff(min_val, tpl.ring_pixels)
            if confidence < COARSE_MIN_CONFIDENCE:
                continue

            competitor = self._best_competitor(scores, min_loc, tpl.image.shape, tpl.ring_pixels)
            nominees.append((confidence, scale, min_loc[0], min_loc[1], confidence - competitor))

        if not nominees:
            logger.debug("anchor: no candidate position passed the coarse search at %s", scales)
            return None

        nominees.sort(key=lambda n: n[0], reverse=True)

        best: tuple[int, int, float, float, float] | None = None  # x, y, scale, conf, margin
        for _, scale, coarse_x, coarse_y, margin in nominees[:REFINE_CANDIDATES]:
            # Map the coarse hit back to full resolution and refine
            # it: a position found on a downscaled copy is only
            # accurate to within 1/factor pixels, and the score there
            # is depressed by resampling (see COARSE_MIN_CONFIDENCE).
            approx_left = int(round(coarse_x / factor))
            approx_top = int(round(coarse_y / factor))
            refined = self._refine(gray, approx_left, approx_top, scale, int(round(2 / factor)) + 2)
            if refined is None:
                continue
            left, top, confidence = refined
            if best is None or confidence > best[3]:
                best = (left, top, scale, confidence, margin)

        if best is None:
            return None

        left, top, scale, confidence, margin = best
        if confidence < RELOCATE_MIN_CONFIDENCE:
            logger.debug("anchor: best candidate only %.4f confident; not relocating", confidence)
            return None
        if margin < MIN_DISTINCTIVENESS:
            logger.info(
                "anchor: best candidate (%.4f) beats the next best elsewhere by only %.4f; "
                "the surroundings are too repetitive to relocate safely",
                confidence, margin,
            )
            return None

        return left + self._ml, top + self._mt, scale, confidence

    @staticmethod
    def _best_competitor(
        scores: np.ndarray,
        best_loc: tuple[int, int],
        template_shape: tuple[int, int],
        ring_pixels: float,
    ) -> float:
        """Confidence of the best match that is *not* the winner.

        Everything within half a template of the winner is excluded:
        the response map always has a broad basin around a true match
        (shifting by one pixel barely changes the score), and counting
        that basin as a rival would make every genuine, unambiguous
        match look indistinct.
        """
        t_h, t_w = template_shape
        masked = scores.copy()
        x0 = max(0, best_loc[0] - t_w // 2)
        y0 = max(0, best_loc[1] - t_h // 2)
        masked[y0:best_loc[1] + t_h // 2 + 1, x0:best_loc[0] + t_w // 2 + 1] = np.inf

        finite = masked[np.isfinite(masked)]
        if finite.size == 0:
            return 0.0
        return _confidence_from_sqdiff(float(finite.min()), ring_pixels)

    def _refine(
        self,
        gray: np.ndarray,
        approx_left: int,
        approx_top: int,
        scale: float,
        radius: int,
    ) -> tuple[int, int, float] | None:
        """Full-resolution search in a small box around a coarse hit."""
        tpl = self._scaled_template(scale)
        if tpl is None:
            return None
        t_h, t_w = tpl.image.shape
        win_h, win_w = gray.shape

        left0 = max(0, approx_left - radius)
        top0 = max(0, approx_top - radius)
        left1 = min(win_w, approx_left + t_w + radius)
        top1 = min(win_h, approx_top + t_h + radius)
        search = gray[top0:top1, left0:left1]
        if search.shape[0] < t_h or search.shape[1] < t_w:
            return None

        scores = cv2.matchTemplate(search, tpl.image, cv2.TM_SQDIFF, mask=tpl.mask)
        min_val, _, min_loc, _ = cv2.minMaxLoc(scores)
        return (
            left0 + min_loc[0],
            top0 + min_loc[1],
            _confidence_from_sqdiff(min_val, tpl.ring_pixels),
        )
