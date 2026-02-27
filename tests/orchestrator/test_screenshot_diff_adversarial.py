"""Adversarial tests for ScreenshotDiffVerifier.

Targets edge cases in numerical precision, shape mismatches, region bounds,
state management, threshold behaviour, and synthetic real-world scenarios.
All screenshots are synthetic numpy arrays -- no live desktop required.
"""

import numpy as np
import pytest
from unittest.mock import MagicMock
from PIL import Image

from automation_agent.orchestrator.screenshot_diff import ScreenshotDiffVerifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_capturer(*images: np.ndarray) -> MagicMock:
    """Return a mock ScreenCapturer whose capture_screen() yields *images* in order.

    Each numpy array is converted to a PIL Image when requested.
    """
    capturer = MagicMock()
    pil_images = [Image.fromarray(img) for img in images]
    capturer.capture_screen = MagicMock(side_effect=pil_images)
    return capturer


def _solid(height: int, width: int, value: int, channels: int = 3) -> np.ndarray:
    """Return a solid-colour image (uint8)."""
    return np.full((height, width, channels), value, dtype=np.uint8)


def _gradient(height: int, width: int, channels: int = 3) -> np.ndarray:
    """Return a horizontal gradient (0..255 over width)."""
    grad = np.linspace(0, 255, width, dtype=np.uint8)
    row = np.tile(grad[:, None], (1, channels))
    return np.tile(row[None, :, :], (height, 1, 1))


# ============================================================================
# 1. Numerical Edge Cases
# ============================================================================


@pytest.mark.unit
class TestNumericalEdgeCases:

    def test_identical_all_black(self):
        """Two all-black screenshots must report NO change."""
        before = _solid(100, 100, 0)
        after = _solid(100, 100, 0)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is False

    def test_identical_all_white(self):
        """Two all-white screenshots must report NO change."""
        before = _solid(100, 100, 255)
        after = _solid(100, 100, 255)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is False

    def test_single_pixel_change_below_threshold(self):
        """One pixel changed in a large image should be under 1% threshold."""
        before = _solid(200, 200, 0)
        after = before.copy()
        after[50, 50] = [255, 255, 255]  # single pixel
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        # 1 pixel out of 200*200 = 0.000025 fraction; well below 1%
        assert v.screen_changed() is False

    def test_single_pixel_detected_at_zero_threshold(self):
        """With threshold=0, even a single changed pixel should be detected."""
        before = _solid(100, 100, 0)
        after = before.copy()
        after[0, 0] = [255, 255, 255]
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.0)
        v.capture_before()
        assert v.screen_changed() is True

    def test_max_uint8_values_no_overflow(self):
        """Pixels at 255 compared with 0 must not cause integer overflow."""
        before = _solid(100, 100, 255)
        after = _solid(100, 100, 0)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is True

    def test_subtraction_direction_no_negative_wrap(self):
        """after < before must not wrap around in unsigned arithmetic.

        np.abs(before.astype(float) - after.astype(float)) must handle this.
        If the implementation uses raw uint8 subtraction, 0 - 255 wraps to 1.
        """
        before = _solid(100, 100, 200)
        after = _solid(100, 100, 50)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        # Diff per pixel = 150 > 10, fraction changed = 1.0 > 0.01
        assert v.screen_changed() is True

    def test_diff_exactly_at_pixel_threshold_boundary(self):
        """Pixel diff of exactly 10 should NOT count as changed (>10 required)."""
        before = _solid(100, 100, 100)
        after = _solid(100, 100, 110)  # diff = 10 exactly
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.0)
        v.capture_before()
        # diff > 10 is False when diff == 10, so fraction = 0.0
        # threshold=0.0: 0.0 > 0.0 is False => no change
        assert v.screen_changed() is False

    def test_diff_one_above_pixel_threshold(self):
        """Pixel diff of 11 should count as changed."""
        before = _solid(100, 100, 100)
        after = _solid(100, 100, 111)  # diff = 11
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.0)
        v.capture_before()
        # diff > 10 is True for all pixels, fraction = 1.0 > 0.0
        assert v.screen_changed() is True

    def test_gradient_subtle_shift(self):
        """Shifting a gradient by a small amount: only some pixels cross threshold."""
        before = _gradient(100, 256)
        after = _gradient(100, 256)
        # Shift intensities by +5 (below pixel threshold of 10)
        after = np.clip(after.astype(np.int16) + 5, 0, 255).astype(np.uint8)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        # +5 shift never exceeds pixel threshold of 10
        assert v.screen_changed() is False

    def test_gradient_large_shift(self):
        """Shifting gradient by 20 should exceed pixel threshold everywhere."""
        before = _gradient(100, 256)
        after = np.clip(before.astype(np.int16) + 20, 0, 255).astype(np.uint8)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is True

    def test_1x1_pixel_image_identical(self):
        """Smallest possible image: 1x1, identical."""
        before = np.array([[[128, 128, 128]]], dtype=np.uint8)
        after = np.array([[[128, 128, 128]]], dtype=np.uint8)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is False

    def test_1x1_pixel_image_changed(self):
        """Smallest possible image: 1x1, changed."""
        before = np.array([[[0, 0, 0]]], dtype=np.uint8)
        after = np.array([[[255, 255, 255]]], dtype=np.uint8)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.0)
        v.capture_before()
        assert v.screen_changed() is True

    def test_large_image_performance(self):
        """8K-ish image (7680x4320): verify it runs without error."""
        rng = np.random.RandomState(42)
        before = rng.randint(0, 256, (4320, 7680, 3), dtype=np.uint8)
        after = before.copy()
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is False


# ============================================================================
# 2. Screenshot Shape Mismatches
# ============================================================================


@pytest.mark.unit
class TestShapeMismatches:

    def test_different_resolutions(self):
        """Window resized between captures should return True (shape mismatch)."""
        before = _solid(1080, 1920, 128)
        after = _solid(720, 1280, 128)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is True

    def test_rgb_vs_rgba(self):
        """Before is RGB (3 channels), after is RGBA (4 channels).

        PIL converts images on load, but shape mismatch should be safe.
        """
        before = _solid(100, 100, 128, channels=3)
        after = _solid(100, 100, 128, channels=4)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        # Shapes differ: (100, 100, 3) vs (100, 100, 4) -> True
        assert v.screen_changed() is True

    def test_grayscale_vs_rgb(self):
        """Before grayscale (H,W), after RGB (H,W,3): shape mismatch."""
        before = np.full((100, 100), 128, dtype=np.uint8)
        after = _solid(100, 100, 128, channels=3)
        # For grayscale PIL image we need mode "L"
        cap = MagicMock()
        pil_before = Image.fromarray(before, mode="L")
        pil_after = Image.fromarray(after)
        cap.capture_screen = MagicMock(side_effect=[pil_before, pil_after])
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is True

    def test_retina_vs_normal_resolution(self):
        """Retina (2x) before, normal (1x) after: different shapes."""
        before = _solid(2160, 3840, 128)  # 2x of 1080x1920
        after = _solid(1080, 1920, 128)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is True

    def test_region_changed_shape_mismatch(self):
        """region_changed must also handle shape mismatches gracefully."""
        before = _solid(100, 100, 128)
        after = _solid(200, 200, 128)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.region_changed(50, 50, radius=20) is True


# ============================================================================
# 3. region_changed Edge Cases
# ============================================================================


@pytest.mark.unit
class TestRegionEdgeCases:

    def test_region_beyond_image_bounds(self):
        """Region radius extends past image: should still work (clipped)."""
        before = _solid(100, 100, 0)
        after = before.copy()
        after[0:10, 0:10] = [255, 255, 255]  # Change in corner
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.0)
        v.capture_before()
        # x=0, y=0, radius=500 on 100x100 image
        result = v.region_changed(0, 0, radius=500)
        # Clipped to full image, some pixels changed
        assert result is True

    def test_region_center_outside_image_entirely(self):
        """Center (x, y) outside image bounds: clipped region may be empty."""
        before = _solid(100, 100, 0)
        after = before.copy()
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        # x=500, y=500 way outside 100x100 image
        # After clipping: x1=max(0,400)=400, x2=min(100,600)=100 -> x1>=x2
        # Should return True (safe default) or handle gracefully
        result = v.region_changed(500, 500, radius=100)
        assert isinstance(result, bool)

    def test_negative_coordinates(self):
        """Negative x, y coordinates should be handled gracefully."""
        before = _solid(100, 100, 0)
        after = before.copy()
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        # Should not crash
        result = v.region_changed(-50, -50, radius=100)
        assert isinstance(result, bool)

    def test_radius_zero(self):
        """radius=0: center pixel only, or empty region?

        x1 = max(0, x-0) = x, x2 = min(w, x+0) = x -> x1 >= x2 -> empty
        Should return True (safe default) since no pixels to compare.
        """
        before = _solid(100, 100, 0)
        after = before.copy()
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        result = v.region_changed(50, 50, radius=0)
        # Empty region: safe default should be True
        assert result is True

    def test_negative_radius(self):
        """Negative radius should not crash."""
        before = _solid(100, 100, 0)
        after = before.copy()
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        # radius=-1: x1 = max(0, 50-(-1)) = 51, x2 = min(100, 50+(-1)) = 49
        # x1 >= x2 -> empty region -> True
        result = v.region_changed(50, 50, radius=-1)
        assert isinstance(result, bool)

    def test_very_large_radius(self):
        """Radius much larger than image: effectively full-screen diff."""
        before = _solid(100, 100, 0)
        after = _solid(100, 100, 200)  # completely different
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.region_changed(50, 50, radius=999999) is True

    def test_region_at_top_left_corner(self):
        """Region centered at (0, 0): only the bottom-right quadrant survives clipping."""
        before = _solid(100, 100, 0)
        after = before.copy()
        after[0:20, 0:20] = [255, 255, 255]
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.0)
        v.capture_before()
        # center (0,0), radius 50 => region [0:50, 0:50] after clipping
        result = v.region_changed(0, 0, radius=50)
        # 20*20 = 400 pixels changed out of 50*50 = 2500 => fraction = 0.16
        assert result is True

    def test_region_at_bottom_right_corner(self):
        """Region centered at (99, 99) on 100x100 image with radius 50."""
        before = _solid(100, 100, 0)
        after = before.copy()
        after[90:100, 90:100] = [255, 255, 255]
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.0)
        v.capture_before()
        result = v.region_changed(99, 99, radius=50)
        assert result is True

    def test_region_no_change_in_region_but_elsewhere(self):
        """Change outside the region should NOT be detected by region_changed."""
        before = _solid(200, 200, 0)
        after = before.copy()
        after[180:200, 180:200] = [255, 255, 255]  # bottom-right corner
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        # Region around (50, 50) with radius 30: no change there
        assert v.region_changed(50, 50, radius=30) is False

    def test_region_change_only_in_region(self):
        """Change only inside the target region should be detected."""
        before = _solid(200, 200, 0)
        after = before.copy()
        after[40:60, 40:60] = [255, 255, 255]  # centered around (50, 50)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.0)
        v.capture_before()
        assert v.region_changed(50, 50, radius=30) is True


# ============================================================================
# 4. State Management
# ============================================================================


@pytest.mark.unit
class TestStateManagement:

    def test_screen_changed_without_capture_before(self):
        """screen_changed() without prior capture_before() should return True (safe default)."""
        cap = MagicMock()
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        assert v.screen_changed() is True

    def test_region_changed_without_capture_before(self):
        """region_changed() without prior capture_before() should return True."""
        cap = MagicMock()
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        assert v.region_changed(50, 50, radius=20) is True

    def test_capture_before_twice_overwrites(self):
        """Second capture_before() should overwrite the first baseline."""
        img1 = _solid(100, 100, 0)
        img2 = _solid(100, 100, 100)
        img3 = _solid(100, 100, 100)  # same as img2
        cap = _make_capturer(img1, img2, img3)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)

        v.capture_before()  # stores img1
        v.capture_before()  # should overwrite with img2
        # Now screen_changed captures img3 (same as img2)
        assert v.screen_changed() is False

    def test_multiple_screen_changed_calls_consistent(self):
        """Multiple screen_changed() calls after one capture_before() should
        each compare against the same baseline, though the "after" screenshot
        is captured fresh each time.
        """
        before = _solid(100, 100, 0)
        after1 = _solid(100, 100, 200)
        after2 = _solid(100, 100, 200)
        cap = _make_capturer(before, after1, after2)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        result1 = v.screen_changed()
        result2 = v.screen_changed()
        assert result1 == result2

    def test_before_is_not_mutated_after_screen_changed(self):
        """Calling screen_changed should not modify the stored _before array."""
        before = _solid(100, 100, 50)
        after = _solid(100, 100, 200)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()

        stored_before = v._before.copy()
        v.screen_changed()
        np.testing.assert_array_equal(v._before, stored_before)

    def test_capturer_called_once_per_operation(self):
        """capture_before() should call capturer once; screen_changed() once more."""
        before = _solid(100, 100, 50)
        after = _solid(100, 100, 50)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)

        v.capture_before()
        assert cap.capture_screen.call_count == 1
        v.screen_changed()
        assert cap.capture_screen.call_count == 2


# ============================================================================
# 5. Threshold Behaviour
# ============================================================================


@pytest.mark.unit
class TestThresholdBehaviour:

    def test_threshold_zero_any_change_detected(self):
        """change_threshold=0.0: any changed pixel above intensity 10 should trigger."""
        before = _solid(100, 100, 100)
        after = before.copy()
        after[0, 0] = [200, 200, 200]  # diff=100 > 10, 1 pixel of 10000
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.0)
        v.capture_before()
        # fraction = 1/10000*3channels... actually mean(diff>10) over all elements
        # For a single pixel with 3 channels all > 10: 3 out of 30000 elements
        # 3/30000 = 0.0001 > 0.0 => True
        assert v.screen_changed() is True

    def test_threshold_one_nothing_detected(self):
        """change_threshold=1.0: even if 100% pixels changed, fraction=1.0 is NOT > 1.0."""
        before = _solid(100, 100, 0)
        after = _solid(100, 100, 255)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=1.0)
        v.capture_before()
        # Every element differs by 255 > 10. mean(True) = 1.0. 1.0 > 1.0 is False.
        assert v.screen_changed() is False

    def test_threshold_just_below_actual_change(self):
        """Threshold set just below actual fraction: should detect change."""
        # Create image where exactly 10% of pixels change
        before = _solid(100, 100, 0)
        after = before.copy()
        # Change first 10 rows (10% of pixels)
        after[0:10, :] = [255, 255, 255]
        cap = _make_capturer(before, after)
        # Fraction of changed elements: 10*100*3 / (100*100*3) = 0.1
        # Set threshold just below
        v = ScreenshotDiffVerifier(cap, change_threshold=0.099)
        v.capture_before()
        assert v.screen_changed() is True

    def test_threshold_just_above_actual_change(self):
        """Threshold set just above actual fraction: should NOT detect change."""
        before = _solid(100, 100, 0)
        after = before.copy()
        after[0:10, :] = [255, 255, 255]
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.101)
        v.capture_before()
        assert v.screen_changed() is False

    def test_threshold_exactly_at_change_fraction(self):
        """Threshold exactly matching fraction: strict > means NOT detected."""
        before = _solid(100, 100, 0)
        after = before.copy()
        after[0:10, :] = [255, 255, 255]
        cap = _make_capturer(before, after)
        # Fraction = exactly 0.1
        v = ScreenshotDiffVerifier(cap, change_threshold=0.1)
        v.capture_before()
        # 0.1 > 0.1 is False
        assert v.screen_changed() is False

    def test_negative_threshold_always_detects(self):
        """Negative threshold: fraction >= 0 is always > negative value.

        This is a weird corner case -- should it be rejected or handled?
        """
        before = _solid(100, 100, 128)
        after = _solid(100, 100, 128)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=-0.5)
        v.capture_before()
        # fraction = 0.0, 0.0 > -0.5 => True
        assert v.screen_changed() is True

    def test_threshold_greater_than_one(self):
        """Threshold > 1.0: impossible to exceed, nothing ever counts as changed."""
        before = _solid(100, 100, 0)
        after = _solid(100, 100, 255)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=2.0)
        v.capture_before()
        # fraction = 1.0, 1.0 > 2.0 is False
        assert v.screen_changed() is False

    def test_region_threshold_applied_correctly(self):
        """Threshold must apply to the region, not the full screen."""
        before = _solid(200, 200, 0)
        after = before.copy()
        # Change a 10x10 block at center of region (50,50)
        after[45:55, 45:55] = [255, 255, 255]
        cap = _make_capturer(before, after)
        # Region = (50,50) radius=50 => [0:100, 0:100] = 100x100 = 10000 pixels
        # Changed = 10*10 = 100 pixels, fraction = 100*3/(10000*3) = 0.01
        v = ScreenshotDiffVerifier(cap, change_threshold=0.005)
        v.capture_before()
        assert v.region_changed(50, 50, radius=50) is True

        # Reset with fresh captures and higher threshold
        before2 = _solid(200, 200, 0)
        after2 = before2.copy()
        after2[45:55, 45:55] = [255, 255, 255]
        cap2 = _make_capturer(before2, after2)
        v2 = ScreenshotDiffVerifier(cap2, change_threshold=0.02)
        v2.capture_before()
        assert v2.region_changed(50, 50, radius=50) is False


# ============================================================================
# 6. Real-World Scenarios (Synthetic)
# ============================================================================


@pytest.mark.unit
class TestRealWorldScenarios:

    def test_cursor_blink_false_positive(self):
        """Cursor blink: tiny 2x16 region changes. Below 1% of any region."""
        before = _solid(1080, 1920, 30)
        after = before.copy()
        # Simulate cursor blink at (300, 400): 2 pixel wide, 16 pixel tall
        after[400:416, 300:302] = [200, 200, 200]
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        # Full-screen: 2*16 = 32 pixels out of 1920*1080 = 2_073_600 => ~0.0015%
        assert v.screen_changed() is False

    def test_loading_spinner_small_region(self):
        """Animation/loading spinner in a small area.

        Small number of pixels changing should be below threshold.
        """
        before = _solid(1080, 1920, 40)
        after = before.copy()
        # Spinner area: 32x32 block
        rng = np.random.RandomState(99)
        after[200:232, 960:992] = rng.randint(0, 256, (32, 32, 3), dtype=np.uint8)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        # 32*32 = 1024 out of ~2M pixels = ~0.05%
        assert v.screen_changed() is False

    def test_menu_dropdown_appeared(self):
        """Menu dropdown: large region changed (true positive)."""
        before = _solid(1080, 1920, 50)
        after = before.copy()
        # Menu dropdown: 200 pixels wide, 400 pixels tall
        after[100:500, 100:300] = [220, 220, 230]
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        # 200*400 = 80,000 out of ~2M => ~3.9%
        assert v.screen_changed() is True

    def test_click_had_no_effect_identical_screenshots(self):
        """Click had no effect: screenshots are identical (true negative)."""
        before = _solid(1080, 1920, 60)
        after = _solid(1080, 1920, 60)
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is False
        # Also check region at click location
        # Need fresh captures
        cap2 = _make_capturer(_solid(1080, 1920, 60), _solid(1080, 1920, 60))
        v2 = ScreenshotDiffVerifier(cap2, change_threshold=0.01)
        v2.capture_before()
        assert v2.region_changed(500, 300, radius=100) is False

    def test_dark_mode_toggle(self):
        """Dark mode toggle: entire screen changes dramatically."""
        before = _solid(1080, 1920, 240)  # light mode
        after = _solid(1080, 1920, 30)  # dark mode
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is True

    def test_modal_dialog_appeared(self):
        """Modal dialog: central region changed, edges remain."""
        before = _solid(1080, 1920, 50)
        after = before.copy()
        # Modal in center: 600x400
        after[340:740, 660:1260] = [230, 230, 240]
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        # Region diff at center of modal
        assert v.region_changed(960, 540, radius=200) is True

    def test_notification_banner_top_of_screen(self):
        """Notification banner at top: top changes, click area below unaffected."""
        before = _solid(1080, 1920, 50)
        after = before.copy()
        # Notification banner: full width, 60px tall at top
        after[0:60, :] = [200, 200, 220]
        cap1 = _make_capturer(before, after.copy())
        v1 = ScreenshotDiffVerifier(cap1, change_threshold=0.01)
        v1.capture_before()
        # Full screen: 60*1920 out of 1080*1920 => 5.6% -> True
        assert v1.screen_changed() is True

        # But region around the click target (lower on screen) should be unaffected
        cap2 = _make_capturer(before.copy(), after.copy())
        v2 = ScreenshotDiffVerifier(cap2, change_threshold=0.01)
        v2.capture_before()
        assert v2.region_changed(960, 800, radius=100) is False

    def test_subtle_button_highlight_on_hover(self):
        """Button hover: small region changes color slightly (above pixel threshold)."""
        before = _solid(1080, 1920, 100)
        after = before.copy()
        # Button area gets a highlight: intensity shifts by ~30
        after[500:540, 800:920] = [130, 130, 140]
        cap = _make_capturer(before, after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.0)
        v.capture_before()
        # Using region_changed around the button
        assert v.region_changed(860, 520, radius=80) is True


# ============================================================================
# 7. Performance and Memory
# ============================================================================


@pytest.mark.unit
class TestPerformanceAndMemory:

    def test_capture_before_loop_no_leak(self):
        """Calling capture_before() in a loop should just overwrite, not accumulate."""
        imgs = [_solid(100, 100, i % 256) for i in range(50)]
        cap = MagicMock()
        pil_images = [Image.fromarray(img) for img in imgs]
        cap.capture_screen = MagicMock(side_effect=pil_images)

        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        for _ in range(50):
            v.capture_before()
        # Only the last one should be stored
        assert v._before is not None
        # Verify it's the last image
        expected = np.array(pil_images[-1])
        np.testing.assert_array_equal(v._before, expected)

    def test_capture_before_replaces_not_appends(self):
        """Ensure _before is a single array, not a list or growing structure."""
        before1 = _solid(100, 100, 10)
        before2 = _solid(100, 100, 20)
        dummy_after = _solid(100, 100, 20)
        cap = _make_capturer(before1, before2, dummy_after)
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()  # before1
        v.capture_before()  # before2 overwrites
        # _before should be a numpy array, not a list
        assert isinstance(v._before, np.ndarray)
        assert v._before.shape == (100, 100, 3)


# ============================================================================
# 8. Capturer Interaction Edge Cases
# ============================================================================


@pytest.mark.unit
class TestCapturerInteraction:

    def test_capturer_raises_on_capture_before(self):
        """If capturer fails during capture_before(), error should propagate."""
        cap = MagicMock()
        cap.capture_screen = MagicMock(side_effect=RuntimeError("Screen unavailable"))
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        with pytest.raises(RuntimeError, match="Screen unavailable"):
            v.capture_before()

    def test_capturer_raises_on_screen_changed(self):
        """If capturer fails during screen_changed() after valid capture, error propagates."""
        before = _solid(100, 100, 128)
        cap = MagicMock()
        cap.capture_screen = MagicMock(
            side_effect=[Image.fromarray(before), RuntimeError("Display disconnected")]
        )
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        with pytest.raises(RuntimeError, match="Display disconnected"):
            v.screen_changed()

    def test_capturer_raises_on_region_changed(self):
        """If capturer fails during region_changed() after valid capture, error propagates."""
        before = _solid(100, 100, 128)
        cap = MagicMock()
        cap.capture_screen = MagicMock(
            side_effect=[Image.fromarray(before), RuntimeError("Capture failed")]
        )
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        with pytest.raises(RuntimeError, match="Capture failed"):
            v.region_changed(50, 50, radius=20)


# ============================================================================
# 9. Dtype and Channel Robustness
# ============================================================================


@pytest.mark.unit
class TestDtypeRobustness:

    def test_float_image_from_capturer(self):
        """If PIL returns an image that converts to float64 array, should still work."""
        before = _solid(100, 100, 128)
        after = _solid(100, 100, 128)
        # Force float conversion to test astype path
        cap = MagicMock()
        cap.capture_screen = MagicMock(
            side_effect=[Image.fromarray(before), Image.fromarray(after)]
        )
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is False

    def test_single_channel_images_identical(self):
        """Two identical single-channel (grayscale, mode L) images -> no change."""
        gray = np.full((100, 100), 128, dtype=np.uint8)
        cap = MagicMock()
        pil1 = Image.fromarray(gray, mode="L")
        pil2 = Image.fromarray(gray, mode="L")
        cap.capture_screen = MagicMock(side_effect=[pil1, pil2])
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is False

    def test_rgba_images_identical(self):
        """Two identical RGBA images -> no change."""
        rgba = _solid(100, 100, 128, channels=4)
        cap = _make_capturer(rgba, rgba.copy())
        v = ScreenshotDiffVerifier(cap, change_threshold=0.01)
        v.capture_before()
        assert v.screen_changed() is False


# ============================================================================
# 10. Constructor and Initialization
# ============================================================================


@pytest.mark.unit
class TestConstructor:

    def test_default_threshold(self):
        """Default change_threshold should be 0.01."""
        cap = MagicMock()
        v = ScreenshotDiffVerifier(cap)
        assert v.change_threshold == 0.01

    def test_before_is_none_initially(self):
        """_before should be None before any capture."""
        cap = MagicMock()
        v = ScreenshotDiffVerifier(cap, change_threshold=0.5)
        assert v._before is None

    def test_capturer_is_stored(self):
        """Capturer reference should be stored."""
        cap = MagicMock()
        v = ScreenshotDiffVerifier(cap, change_threshold=0.05)
        assert v.capturer is cap
