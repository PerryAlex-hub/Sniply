from slidecapture.config.defaults import DetectionConfig
from slidecapture.detection.stability import StabilityTracker
from tests.fixtures.synthetic import make_slide, make_transition_frame, move_cursor_dot


def _tracker():
    cfg = DetectionConfig()
    return StabilityTracker(
        fast_diff_threshold=cfg.fast_diff_threshold,
        stability_count=cfg.stability_count,
        compare_width=cfg.compare_width,
    )


def test_first_frame_never_emits():
    tracker = _tracker()
    frame = make_slide(["Slide One"])
    assert tracker.update(frame) is None


def test_identical_frames_emit_once_after_required_streak():
    tracker = _tracker()
    frame = make_slide(["Slide One"])

    results = [tracker.update(frame) for _ in range(5)]
    emitted = [r for r in results if r is not None]

    assert len(emitted) == 1


def test_changing_frames_never_settle():
    """A sequence that never repeats (simulating a slide transition
    animation, here a bar sweeping left to right) should never
    produce a stable candidate."""
    tracker = _tracker()
    results = []
    for x in range(0, 480, 80):
        frame = make_transition_frame(x)
        results.append(tracker.update(frame))

    assert all(r is None for r in results)


def test_settles_again_after_a_real_change():
    tracker = _tracker()
    slide_a = make_slide(["Slide A", "Opening remarks", "Course overview"])
    slide_b = make_slide(
        ["Slide B", "Midterm review session", "Practice problems walkthrough", "Q and A at the end"]
    )

    first_round = [tracker.update(slide_a) for _ in range(3)]
    second_round = [tracker.update(slide_b) for _ in range(3)]

    assert sum(r is not None for r in first_round) == 1
    assert sum(r is not None for r in second_round) == 1


def test_cursor_jitter_alone_does_not_prevent_stability():
    """Tiny localized movement (cursor) shouldn't be enough to push
    the mean frame diff above threshold for a mostly-static slide."""
    tracker = _tracker()
    base = make_slide(["Slide With Cursor"])

    frames = [move_cursor_dot(base, 100 + i, 200) for i in range(4)]
    results = [tracker.update(f) for f in frames]

    assert any(r is not None for r in results)
