"""Tests for ScreenshotDiffVerifier.

All tests use synthetic numpy arrays -- no real screenshots are captured.
"""

import numpy as np
import pytest
from unittest.mock import MagicMock
from PIL import Image

from automation_agent.orchestrator.screenshot_diff import ScreenshotDiffVerifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_capturer(images: list[Image.Image]) -> MagicMock:
    """Create a mock ScreenCapturer that returns PIL images in order."""
    capturer = MagicMock()
    capturer.capture_screen = MagicMock(side_effect=images)
    return capturer


def _solid_image(width: int, height: int, value: int = 128) -> Image.Image:
    """Create a solid-color RGB PIL Image."""
    arr = np.full((height, width, 3), value, dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


def _image_from_array(arr: np.ndarray) -> Image.Image:
    """Create a PIL Image from a numpy array."""
    return Image.fromarray(arr.astype(np.uint8), "RGB")


# ---------------------------------------------------------------------------
# Tests: screen_changed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScreenChanged:
    """Tests for full-screen change detection."""

    def test_identical_screenshots_returns_false(self):
        """Identical before/after screenshots -> no change detected."""
        img = _solid_image(100, 100, 128)
        capturer = _make_capturer([img, img])
        verifier = ScreenshotDiffVerifier(capturer)

        verifier.capture_before()
        assert verifier.screen_changed() is False

    def test_completely_different_screenshots_returns_true(self):
        """Completely different before/after screenshots -> change detected."""
        before = _solid_image(100, 100, 0)
        after = _solid_image(100, 100, 255)
        capturer = _make_capturer([before, after])
        verifier = ScreenshotDiffVerifier(capturer)

        verifier.capture_before()
        assert verifier.screen_changed() is True

    def test_small_change_above_threshold_returns_true(self):
        """A small region of significant pixel change above threshold."""
        before_arr = np.full((100, 100, 3), 128, dtype=np.uint8)
        after_arr = before_arr.copy()
        # Change 5% of pixels by more than 10 intensity units
        after_arr[:5, :, :] = 200

        capturer = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=0.01)

        verifier.capture_before()
        assert verifier.screen_changed() is True

    def test_small_change_below_threshold_returns_false(self):
        """Tiny pixel changes below threshold -> not detected."""
        before_arr = np.full((100, 100, 3), 128, dtype=np.uint8)
        after_arr = before_arr.copy()
        # Change 5 intensity units (below the 10-unit threshold)
        after_arr[0, 0, :] = 133

        capturer = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=0.01)

        verifier.capture_before()
        assert verifier.screen_changed() is False

    def test_no_capture_before_returns_true(self):
        """No before screenshot captured -> returns True (safe default)."""
        capturer = _make_capturer([])
        verifier = ScreenshotDiffVerifier(capturer)

        assert verifier.screen_changed() is True

    def test_different_shapes_returns_true(self):
        """Different image dimensions -> returns True (safe default)."""
        before = _solid_image(100, 100, 128)
        after = _solid_image(200, 150, 128)
        capturer = _make_capturer([before, after])
        verifier = ScreenshotDiffVerifier(capturer)

        verifier.capture_before()
        assert verifier.screen_changed() is True

    def test_low_threshold_more_sensitive(self):
        """Lower threshold detects smaller changes."""
        before_arr = np.full((1000, 1000, 3), 128, dtype=np.uint8)
        after_arr = before_arr.copy()
        # Change 0.5% of pixels (5 rows out of 1000)
        after_arr[:5, :, :] = 200

        before_img = _image_from_array(before_arr)
        after_img = _image_from_array(after_arr)

        # High threshold: not detected
        capturer_high = _make_capturer([before_img, after_img])
        verifier_high = ScreenshotDiffVerifier(capturer_high, change_threshold=0.01)
        verifier_high.capture_before()
        result_high = verifier_high.screen_changed()

        # Low threshold: detected
        capturer_low = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        verifier_low = ScreenshotDiffVerifier(capturer_low, change_threshold=0.001)
        verifier_low.capture_before()
        result_low = verifier_low.screen_changed()

        assert result_low is True
        assert result_high is False

    def test_intensity_change_exactly_at_boundary(self):
        """Pixel change of exactly 10 units is NOT considered changed (> 10 required)."""
        before_arr = np.full((100, 100, 3), 100, dtype=np.uint8)
        after_arr = np.full((100, 100, 3), 110, dtype=np.uint8)

        capturer = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=0.01)

        verifier.capture_before()
        # diff == 10, not > 10, so no pixel is "changed"
        assert verifier.screen_changed() is False

    def test_intensity_change_just_above_boundary(self):
        """Pixel change of 11 units IS considered changed."""
        before_arr = np.full((100, 100, 3), 100, dtype=np.uint8)
        after_arr = np.full((100, 100, 3), 111, dtype=np.uint8)

        capturer = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=0.01)

        verifier.capture_before()
        assert verifier.screen_changed() is True


# ---------------------------------------------------------------------------
# Tests: region_changed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegionChanged:
    """Tests for region-specific change detection."""

    def test_region_detects_change_in_target_area(self):
        """Change in the target region is detected."""
        before_arr = np.full((200, 200, 3), 128, dtype=np.uint8)
        after_arr = before_arr.copy()
        # Change pixels in a 50x50 block centered at (100, 100)
        after_arr[75:125, 75:125, :] = 255

        capturer = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=0.01)

        verifier.capture_before()
        assert verifier.region_changed(100, 100, radius=50) is True

    def test_region_ignores_change_outside_target(self):
        """Change outside the target region is NOT detected."""
        before_arr = np.full((200, 200, 3), 128, dtype=np.uint8)
        after_arr = before_arr.copy()
        # Change far-away corner (0,0)
        after_arr[0:10, 0:10, :] = 255

        capturer = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=0.01)

        verifier.capture_before()
        # Check region at (150, 150) which is far from the change
        assert verifier.region_changed(150, 150, radius=30) is False

    def test_region_no_capture_before_returns_true(self):
        """No before screenshot -> region_changed returns True (safe default)."""
        capturer = _make_capturer([])
        verifier = ScreenshotDiffVerifier(capturer)

        assert verifier.region_changed(50, 50) is True

    def test_region_different_shapes_returns_true(self):
        """Different image dimensions -> region_changed returns True."""
        before = _solid_image(100, 100, 128)
        after = _solid_image(200, 150, 128)
        capturer = _make_capturer([before, after])
        verifier = ScreenshotDiffVerifier(capturer)

        verifier.capture_before()
        assert verifier.region_changed(50, 50) is True

    def test_region_at_top_left_edge(self):
        """Region near top-left corner clips correctly."""
        before_arr = np.full((100, 100, 3), 128, dtype=np.uint8)
        after_arr = before_arr.copy()
        # Change the top-left corner
        after_arr[0:20, 0:20, :] = 255

        capturer = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=0.01)

        verifier.capture_before()
        assert verifier.region_changed(0, 0, radius=30) is True

    def test_region_at_bottom_right_edge(self):
        """Region near bottom-right corner clips correctly."""
        before_arr = np.full((100, 100, 3), 128, dtype=np.uint8)
        after_arr = before_arr.copy()
        # Change the bottom-right corner
        after_arr[80:100, 80:100, :] = 255

        capturer = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=0.01)

        verifier.capture_before()
        assert verifier.region_changed(99, 99, radius=30) is True

    def test_region_entirely_outside_image_returns_true(self):
        """Region coordinates entirely outside the image -> returns True."""
        before = _solid_image(100, 100, 128)
        after = _solid_image(100, 100, 128)
        capturer = _make_capturer([before, after])
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=0.01)

        verifier.capture_before()
        # Coordinates far outside the image
        assert verifier.region_changed(500, 500, radius=10) is True

    def test_region_with_negative_coordinates_clips(self):
        """Negative coordinates are clipped to 0."""
        before_arr = np.full((100, 100, 3), 128, dtype=np.uint8)
        after_arr = before_arr.copy()
        after_arr[0:20, 0:20, :] = 255

        capturer = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=0.01)

        verifier.capture_before()
        assert verifier.region_changed(-10, -10, radius=30) is True

    def test_region_identical_returns_false(self):
        """Identical before/after in the target region -> no change."""
        img = _solid_image(200, 200, 128)
        capturer = _make_capturer([img, img])
        verifier = ScreenshotDiffVerifier(capturer)

        verifier.capture_before()
        assert verifier.region_changed(100, 100, radius=50) is False

    def test_region_small_radius(self):
        """Very small radius still works correctly."""
        before_arr = np.full((100, 100, 3), 128, dtype=np.uint8)
        after_arr = before_arr.copy()
        # Change a single pixel at (50, 50)
        after_arr[50, 50, :] = 255

        capturer = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        # Use very low threshold since we're only changing 1 pixel in a small region
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=0.001)

        verifier.capture_before()
        assert verifier.region_changed(50, 50, radius=5) is True


# ---------------------------------------------------------------------------
# Tests: capture_before
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCaptureBefore:
    """Tests for the capture_before method."""

    def test_capture_before_stores_screenshot(self):
        """capture_before stores the screenshot internally."""
        img = _solid_image(100, 100, 128)
        capturer = _make_capturer([img])
        verifier = ScreenshotDiffVerifier(capturer)

        verifier.capture_before()
        assert verifier._before is not None
        assert verifier._before.shape == (100, 100, 3)

    def test_capture_before_replaces_previous(self):
        """Calling capture_before again replaces the stored screenshot."""
        img1 = _solid_image(100, 100, 0)
        img2 = _solid_image(100, 100, 255)
        capturer = _make_capturer([img1, img2])
        verifier = ScreenshotDiffVerifier(capturer)

        verifier.capture_before()
        first = verifier._before.copy()
        verifier.capture_before()
        second = verifier._before

        assert not np.array_equal(first, second)

    def test_capture_before_calls_capturer(self):
        """capture_before calls capturer.capture_screen()."""
        img = _solid_image(100, 100, 128)
        capturer = _make_capturer([img])
        verifier = ScreenshotDiffVerifier(capturer)

        verifier.capture_before()
        capturer.capture_screen.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: threshold behavior
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestThreshold:
    """Tests for threshold parameter behavior."""

    def test_zero_threshold_detects_any_change(self):
        """Threshold 0.0 detects even a single pixel change."""
        before_arr = np.full((100, 100, 3), 128, dtype=np.uint8)
        after_arr = before_arr.copy()
        after_arr[0, 0, :] = 200  # single pixel, > 10 intensity diff

        capturer = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=0.0)

        verifier.capture_before()
        assert verifier.screen_changed() is True

    def test_threshold_one_requires_all_pixels_changed(self):
        """Threshold 1.0 requires every pixel to change."""
        before_arr = np.full((100, 100, 3), 128, dtype=np.uint8)
        after_arr = before_arr.copy()
        # Change 50% of pixels
        after_arr[:50, :, :] = 255

        capturer = _make_capturer([
            _image_from_array(before_arr),
            _image_from_array(after_arr),
        ])
        verifier = ScreenshotDiffVerifier(capturer, change_threshold=1.0)

        verifier.capture_before()
        # Only 50% changed, threshold is 100%
        assert verifier.screen_changed() is False

    def test_default_threshold_is_one_percent(self):
        """Default threshold is 0.01 (1%)."""
        capturer = MagicMock()
        verifier = ScreenshotDiffVerifier(capturer)
        assert verifier.change_threshold == 0.01
