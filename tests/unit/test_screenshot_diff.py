"""Tests for ScreenshotDiffVerifier change detection.

Regression target: a click whose only effect is a small but real UI change
(e.g. a "Cart: 0" -> "Cart: 1" counter badge) was reported as "no visible
effect" because the change covers far less than the 1%-of-pixels threshold.
That false negative drove destructive re-click loops (cart filled with 3-5
duplicate items). Detection must catch small strong clusters while staying
robust to scattered noise.
"""

import numpy as np

from automation_agent.orchestrator.screenshot_diff import ScreenshotDiffVerifier


class _ArrayCapturer:
    """Capturer whose capture_screen() returns queued frames."""

    def __init__(self, frames):
        self._frames = list(frames)

    def capture_screen(self):
        return self._frames.pop(0)


def _blank(w=1024, h=768, value=255):
    return np.full((h, w, 3), value, dtype=np.uint8)


class TestSmallStrongChangeDetected:
    def test_counter_badge_increment_counts_as_change(self):
        before = _blank()
        after = before.copy()
        # ~12x18 dark glyph appears top-right (a counter digit) — ~216 px,
        # 0.027% of the screen, far below the 1% fraction threshold.
        after[20:38, 980:992] = 0

        diff = ScreenshotDiffVerifier(_ArrayCapturer([before, after]))
        diff.capture_before()
        assert diff.screen_changed() is True

    def test_scattered_noise_below_floor_is_not_a_change(self):
        before = _blank()
        after = before.copy()
        # 20 isolated mildly-different pixels (JPEG/antialiasing-style noise):
        # below both the fraction threshold and the strong-cluster floor.
        rng = np.random.RandomState(0)
        ys = rng.randint(0, 768, size=20)
        xs = rng.randint(0, 1024, size=20)
        after[ys, xs] = 235  # only 20 units off, under the strong-diff cutoff

        diff = ScreenshotDiffVerifier(_ArrayCapturer([before, after]))
        diff.capture_before()
        assert diff.screen_changed() is False


class TestExistingBehaviorPreserved:
    def test_identical_frames_no_change(self):
        before = _blank()
        diff = ScreenshotDiffVerifier(_ArrayCapturer([before, before.copy()]))
        diff.capture_before()
        assert diff.screen_changed() is False

    def test_large_change_still_detected(self):
        before = _blank()
        after = _blank(value=0)  # whole screen flips
        diff = ScreenshotDiffVerifier(_ArrayCapturer([before, after]))
        diff.capture_before()
        assert diff.screen_changed() is True

    def test_no_before_returns_true_safe_default(self):
        diff = ScreenshotDiffVerifier(_ArrayCapturer([_blank()]))
        assert diff.screen_changed() is True

    def test_region_detects_small_strong_change(self):
        before = _blank()
        after = before.copy()
        after[300:316, 500:512] = 0  # small glyph inside the region
        diff = ScreenshotDiffVerifier(_ArrayCapturer([before, after]))
        diff.capture_before()
        assert diff.region_changed(506, 308, radius=100) is True
