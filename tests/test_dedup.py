from slidecapture.config.defaults import DetectionConfig
from slidecapture.detection.pipeline import Decision, DetectionPipeline
from tests.fixtures.synthetic import make_slide


def _run_until_settled(pipeline: DetectionPipeline, frame, max_ticks=5):
    """Feed the same frame repeatedly until the pipeline returns a
    non-PENDING decision, mimicking a slide sitting on screen."""
    result = None
    for _ in range(max_ticks):
        result = pipeline.process_frame(frame)
        if result.decision is not Decision.PENDING:
            return result
    return result


def test_first_slide_is_saved():
    pipeline = DetectionPipeline(DetectionConfig())
    result = _run_until_settled(pipeline, make_slide(["Slide One"]))
    assert result.decision is Decision.SAVE


def test_distinct_slides_are_both_saved():
    pipeline = DetectionPipeline(DetectionConfig())
    r1 = _run_until_settled(pipeline, make_slide(["Slide One"]))
    r2 = _run_until_settled(pipeline, make_slide(["Something Completely Different", "Second line of content"]))

    assert r1.decision is Decision.SAVE
    assert r2.decision is Decision.SAVE


def test_returning_to_an_earlier_slide_is_a_duplicate():
    """This is the key behavior from the spec: a lecturer returning to
    a previous slide must not produce a second save, because dedup
    compares against the full saved history, not just the last frame.
    """
    pipeline = DetectionPipeline(DetectionConfig())
    slide_a = make_slide(["Slide A", "Opening remarks", "Course overview"])
    slide_b = make_slide(
        ["Slide B", "Midterm review session", "Practice problems walkthrough", "Q and A at the end"]
    )

    r1 = _run_until_settled(pipeline, slide_a)
    r2 = _run_until_settled(pipeline, slide_b)
    r3 = _run_until_settled(pipeline, slide_a)  # lecturer goes back

    assert r1.decision is Decision.SAVE
    assert r2.decision is Decision.SAVE
    assert r3.decision is Decision.DUPLICATE


def test_similar_layout_but_different_text_is_not_discarded():
    """Two slides sharing a template/layout but with different text
    content must both be saved -- phash alone could conflate them,
    which is exactly why SSIM exists as a tiebreaker."""
    pipeline = DetectionPipeline(DetectionConfig())
    slide_a = make_slide(["Chapter 3", "Introduction to Loops", "For, while, and do-while"])
    slide_b = make_slide(
        ["Chapter 3", "Advanced Recursion Patterns", "Trees, graphs, backtracking", "Memoization strategies"]
    )

    r1 = _run_until_settled(pipeline, slide_a)
    r2 = _run_until_settled(pipeline, slide_b)

    assert r1.decision is Decision.SAVE
    assert r2.decision is Decision.SAVE


def test_seed_from_frames_recognizes_prior_session_slides():
    """A resumed session should still dedup against screenshots that
    were saved before the app was closed."""
    slide_a = make_slide(["Resumed Slide"])

    pipeline = DetectionPipeline(DetectionConfig())
    pipeline.seed_from_frames([slide_a])

    result = _run_until_settled(pipeline, slide_a)
    assert result.decision is Decision.DUPLICATE
