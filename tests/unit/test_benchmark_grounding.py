"""Exhaustive unit tests for scripts/benchmark_grounding.py.

All HTTP calls are mocked. No network access required.
Tests are written against the function signatures documented in
docs/grounding-benchmark-spec.md.
"""

import importlib
import io
import json
import math
import os
import re
import sys
import types
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the benchmark script as a module
# ---------------------------------------------------------------------------

SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "scripts", "benchmark_grounding.py"
)


@pytest.fixture(scope="session")
def bg():
    """Import benchmark_grounding.py as a module, once per test session."""
    spec = importlib.util.spec_from_file_location("benchmark_grounding", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. point_in_bbox  (9 tests)
# ---------------------------------------------------------------------------


class TestPointInBbox:
    """Tests for point_in_bbox(x, y, bbox) -> bool."""

    def test_point_inside_bbox(self, bg):
        """Point clearly inside the bbox returns True."""
        assert bg.point_in_bbox(0.5, 0.5, [0.2, 0.2, 0.8, 0.8]) is True

    def test_point_on_left_edge(self, bg):
        """Point exactly on left edge is inclusive -> True."""
        assert bg.point_in_bbox(0.2, 0.5, [0.2, 0.2, 0.8, 0.8]) is True

    def test_point_on_right_edge(self, bg):
        """Point exactly on right edge is inclusive -> True."""
        assert bg.point_in_bbox(0.8, 0.5, [0.2, 0.2, 0.8, 0.8]) is True

    def test_point_on_top_edge(self, bg):
        """Point exactly on top edge is inclusive -> True."""
        assert bg.point_in_bbox(0.5, 0.2, [0.2, 0.2, 0.8, 0.8]) is True

    def test_point_on_bottom_edge(self, bg):
        """Point exactly on bottom edge is inclusive -> True."""
        assert bg.point_in_bbox(0.5, 0.8, [0.2, 0.2, 0.8, 0.8]) is True

    def test_point_outside_left(self, bg):
        """Point to the left of bbox -> False."""
        assert bg.point_in_bbox(0.1, 0.5, [0.2, 0.2, 0.8, 0.8]) is False

    def test_point_outside_right(self, bg):
        """Point to the right of bbox -> False."""
        assert bg.point_in_bbox(0.9, 0.5, [0.2, 0.2, 0.8, 0.8]) is False

    def test_point_above_bbox(self, bg):
        """Point above the bbox -> False."""
        assert bg.point_in_bbox(0.5, 0.1, [0.2, 0.2, 0.8, 0.8]) is False

    def test_point_below_bbox(self, bg):
        """Point below the bbox -> False."""
        assert bg.point_in_bbox(0.5, 0.9, [0.2, 0.2, 0.8, 0.8]) is False

    def test_point_on_corner(self, bg):
        """Point on exact corner (top-left) is inclusive -> True."""
        assert bg.point_in_bbox(0.2, 0.2, [0.2, 0.2, 0.8, 0.8]) is True

    def test_point_on_bottom_right_corner(self, bg):
        """Point on exact corner (bottom-right) is inclusive -> True."""
        assert bg.point_in_bbox(0.8, 0.8, [0.2, 0.2, 0.8, 0.8]) is True

    def test_zero_area_bbox_point_on_it(self, bg):
        """Point exactly on a zero-area bbox (single point) -> True."""
        assert bg.point_in_bbox(0.5, 0.5, [0.5, 0.5, 0.5, 0.5]) is True

    def test_zero_area_bbox_point_off(self, bg):
        """Point not on a zero-area bbox -> False."""
        assert bg.point_in_bbox(0.6, 0.5, [0.5, 0.5, 0.5, 0.5]) is False


# ---------------------------------------------------------------------------
# 2. compute_accuracy  (4 tests)
# ---------------------------------------------------------------------------


def _make_backend_result(bg, hit: bool, distance_px: float = 0.0):
    """Helper: create a minimal BackendResult with given hit value."""
    return bg.BackendResult(
        sample_file_name="test.png",
        instruction="click button",
        backend_name="test-backend",
        predicted_x=0.5 if hit else None,
        predicted_y=0.5 if hit else None,
        ground_truth_bbox=[0.2, 0.2, 0.8, 0.8],
        hit=hit,
        distance_px=distance_px,
        latency_s=1.0,
        raw_response="FOUND: x=0.5, y=0.5" if hit else "NOT_FOUND",
        error=None,
    )


class TestComputeAccuracy:
    """Tests for compute_accuracy(results) -> float."""

    def test_empty_list(self, bg):
        """Empty list -> 0.0."""
        assert bg.compute_accuracy([]) == 0.0

    def test_all_hits(self, bg):
        """All hits -> 1.0."""
        results = [_make_backend_result(bg, hit=True) for _ in range(5)]
        assert bg.compute_accuracy(results) == 1.0

    def test_all_misses(self, bg):
        """All misses -> 0.0."""
        results = [_make_backend_result(bg, hit=False) for _ in range(5)]
        assert bg.compute_accuracy(results) == 0.0

    def test_mixed_results(self, bg):
        """3 hits out of 10 -> 0.3."""
        results = [_make_backend_result(bg, hit=True) for _ in range(3)]
        results += [_make_backend_result(bg, hit=False) for _ in range(7)]
        assert bg.compute_accuracy(results) == pytest.approx(0.3)

    def test_single_hit(self, bg):
        """Single hit -> 1.0."""
        results = [_make_backend_result(bg, hit=True)]
        assert bg.compute_accuracy(results) == 1.0

    def test_single_miss(self, bg):
        """Single miss -> 0.0."""
        results = [_make_backend_result(bg, hit=False)]
        assert bg.compute_accuracy(results) == 0.0


# ---------------------------------------------------------------------------
# 3. compute_mean_distance  (4 tests)
# ---------------------------------------------------------------------------


class TestComputeMeanDistance:
    """Tests for compute_mean_distance(results) -> float."""

    def test_empty_list(self, bg):
        """Empty list -> 0.0."""
        assert bg.compute_mean_distance([]) == 0.0

    def test_known_distances(self, bg):
        """Known distances: verify arithmetic mean."""
        r1 = _make_backend_result(bg, hit=True, distance_px=10.0)
        r2 = _make_backend_result(bg, hit=True, distance_px=20.0)
        r3 = _make_backend_result(bg, hit=False, distance_px=30.0)
        assert bg.compute_mean_distance([r1, r2, r3]) == pytest.approx(20.0)

    def test_none_prediction_uses_diagonal_penalty(self, bg):
        """None prediction should have distance_px = full image diagonal.

        For image 960x540: diagonal = sqrt(960^2 + 540^2) = sqrt(921600+291600) = sqrt(1213200).
        """
        diagonal = math.sqrt(960**2 + 540**2)
        r = _make_backend_result(bg, hit=False, distance_px=diagonal)
        assert bg.compute_mean_distance([r]) == pytest.approx(diagonal)

    def test_single_distance(self, bg):
        """Single result -> that distance."""
        r = _make_backend_result(bg, hit=True, distance_px=42.5)
        assert bg.compute_mean_distance([r]) == pytest.approx(42.5)


# ---------------------------------------------------------------------------
# 4. compute_distance_px  (5 tests)
# ---------------------------------------------------------------------------


class TestComputeDistancePx:
    """Tests for compute_distance_px function."""

    def test_perfect_prediction(self, bg):
        """Prediction at exact bbox center -> distance 0."""
        bbox = [0.4, 0.4, 0.6, 0.6]  # center at (0.5, 0.5)
        dist = bg.compute_distance_px(0.5, 0.5, bbox, 1000, 1000)
        assert dist == pytest.approx(0.0)

    def test_known_distance(self, bg):
        """Known offset: pred (0.6, 0.5), center (0.5, 0.5), image 1000x1000.
        dx = (0.6-0.5)*1000 = 100, dy = 0 -> dist = 100.
        """
        bbox = [0.4, 0.4, 0.6, 0.6]
        dist = bg.compute_distance_px(0.6, 0.5, bbox, 1000, 1000)
        assert dist == pytest.approx(100.0)

    def test_none_prediction_returns_diagonal(self, bg):
        """None prediction returns full image diagonal."""
        bbox = [0.0, 0.0, 1.0, 1.0]
        dist = bg.compute_distance_px(None, None, bbox, 960, 540)
        expected = math.sqrt(960**2 + 540**2)
        assert dist == pytest.approx(expected)

    def test_none_x_only(self, bg):
        """If only pred_x is None, still returns diagonal."""
        bbox = [0.0, 0.0, 1.0, 1.0]
        dist = bg.compute_distance_px(None, 0.5, bbox, 960, 540)
        expected = math.sqrt(960**2 + 540**2)
        assert dist == pytest.approx(expected)

    def test_none_y_only(self, bg):
        """If only pred_y is None, still returns diagonal."""
        bbox = [0.0, 0.0, 1.0, 1.0]
        dist = bg.compute_distance_px(0.5, None, bbox, 960, 540)
        expected = math.sqrt(960**2 + 540**2)
        assert dist == pytest.approx(expected)

    def test_asymmetric_image(self, bg):
        """Rectangular image, pred off-center.
        bbox center = (0.5, 0.5), pred = (0.6, 0.7), image 1920x1080.
        dx = (0.6-0.5)*1920 = 192, dy = (0.7-0.5)*1080 = 216.
        dist = sqrt(192^2 + 216^2) = sqrt(36864 + 46656) = sqrt(83520).
        """
        bbox = [0.4, 0.4, 0.6, 0.6]
        dist = bg.compute_distance_px(0.6, 0.7, bbox, 1920, 1080)
        expected = math.sqrt(192**2 + 216**2)
        assert dist == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 5. estimate_cost  (5 tests)
# ---------------------------------------------------------------------------


class TestEstimateCost:
    """Tests for estimate_cost(backend_name, n_samples, ...) -> float."""

    def test_local_backend_free(self, bg):
        """Local backend (qwen2.5-vl-ollama) -> 0.0."""
        assert bg.estimate_cost("qwen2.5-vl-ollama", 100) == 0.0

    def test_local_llamacpp_free(self, bg):
        """Local llama.cpp backend -> 0.0."""
        assert bg.estimate_cost("qwen2.5-vl-llamacpp", 100) == 0.0

    def test_claude_sonnet_50_samples(self, bg):
        """claude-sonnet, 50 samples, default tokens.

        input_cost = (50 * 1600 / 1_000_000) * 3.00 = 0.24
        output_cost = (50 * 30 / 1_000_000) * 15.00 = 0.0225
        total = 0.2625 -> rounded to 4 decimals = 0.2625
        """
        cost = bg.estimate_cost("claude-sonnet", 50)
        assert cost == pytest.approx(0.2625, abs=0.001)

    def test_n_zero(self, bg):
        """n=0 -> 0.0 for any backend."""
        assert bg.estimate_cost("claude-sonnet", 0) == 0.0

    def test_unknown_backend(self, bg):
        """Unknown backend name -> 0.0 (not an error)."""
        assert bg.estimate_cost("nonexistent-backend-xyz", 50) == 0.0


# ---------------------------------------------------------------------------
# 6. normalize_prediction  (7 tests)
# ---------------------------------------------------------------------------


class TestNormalizePrediction:
    """Tests for normalize_prediction(raw_x, raw_y, model, w, h) -> (x, y)."""

    def test_molmo_passthrough(self, bg):
        """molmo model: 0-100 values divided by 100."""
        x, y = bg.normalize_prediction(50.0, 70.0, "molmo", 960, 540)
        assert x == pytest.approx(0.5)
        assert y == pytest.approx(0.7)

    def test_molmo_clamped_high(self, bg):
        """molmo model: values > 100 are clamped to 1.0."""
        x, y = bg.normalize_prediction(150.0, 200.0, "molmo", 960, 540)
        assert x == pytest.approx(1.0)
        assert y == pytest.approx(1.0)

    def test_molmo_clamped_low(self, bg):
        """molmo model: values < 0.0 are clamped to 0.0."""
        x, y = bg.normalize_prediction(-5.0, -10.0, "molmo", 960, 540)
        assert x == pytest.approx(0.0)
        assert y == pytest.approx(0.0)

    def test_qwen_500_750(self, bg):
        """qwen2.5-vl model: 500/1000=0.5, 750/1000=0.75."""
        x, y = bg.normalize_prediction(500, 750, "qwen2.5-vl", 960, 540)
        assert x == pytest.approx(0.5)
        assert y == pytest.approx(0.75)

    def test_qwen_1000_1000(self, bg):
        """qwen2.5-vl model: 1000/1000=1.0."""
        x, y = bg.normalize_prediction(1000, 1000, "qwen2.5-vl", 960, 540)
        assert x == pytest.approx(1.0)
        assert y == pytest.approx(1.0)

    def test_qwen_zero(self, bg):
        """qwen2.5-vl model: 0/1000=0.0."""
        x, y = bg.normalize_prediction(0, 0, "qwen2.5-vl", 960, 540)
        assert x == pytest.approx(0.0)
        assert y == pytest.approx(0.0)

    def test_claude_pixel_coords(self, bg):
        """claude-sonnet-4-20250514: pixel (480, 270) / image (960, 540) -> (0.5, 0.5)."""
        x, y = bg.normalize_prediction(
            480, 270, "claude-sonnet-4-20250514", 960, 540
        )
        assert x == pytest.approx(0.5)
        assert y == pytest.approx(0.5)

    def test_claude_pixel_full_image(self, bg):
        """claude-sonnet-4-20250514: pixel at full extent -> (1.0, 1.0)."""
        x, y = bg.normalize_prediction(
            960, 540, "claude-sonnet-4-20250514", 960, 540
        )
        assert x == pytest.approx(1.0)
        assert y == pytest.approx(1.0)

    def test_unknown_model_raises(self, bg):
        """Unknown model -> ValueError."""
        with pytest.raises(ValueError, match="Unknown coordinate space"):
            bg.normalize_prediction(0.5, 0.5, "unknown-model-xyz", 960, 540)

    def test_qwen_clamped_above_1000(self, bg):
        """qwen2.5-vl model: value > 1000 is clamped to 1.0."""
        x, y = bg.normalize_prediction(1200, 1500, "qwen2.5-vl", 960, 540)
        assert x == pytest.approx(1.0)
        assert y == pytest.approx(1.0)

    def test_qwen_prefix_match(self, bg):
        """qwen2.5-vl:7b should also work via prefix matching."""
        x, y = bg.normalize_prediction(500, 500, "qwen2.5-vl:7b", 960, 540)
        assert x == pytest.approx(0.5)
        assert y == pytest.approx(0.5)

    def test_molmo2_normalize(self, bg):
        """Molmo2 model: 500/1000=0.5, 750/1000=0.75 (same scale as Qwen)."""
        x, y = bg.normalize_prediction(
            500.0, 750.0, "mlx-community/Molmo2-8B-5bit", 960, 540
        )
        assert x == pytest.approx(0.5)
        assert y == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# 7. _parse_coordinates  (9 tests)
# ---------------------------------------------------------------------------


class TestParseCoordinates:
    """Tests for _parse_coordinates(response) -> Optional[Tuple[float, float]]."""

    def test_found_integer_coords(self, bg):
        """Standard FOUND with integer coords."""
        result = bg._parse_coordinates("FOUND: x=100, y=200")
        assert result == (100.0, 200.0)

    def test_not_found(self, bg):
        """NOT_FOUND -> None."""
        result = bg._parse_coordinates("NOT_FOUND")
        assert result is None

    def test_not_found_case_insensitive(self, bg):
        """not_found (lowercase) -> None."""
        result = bg._parse_coordinates("not_found")
        assert result is None

    def test_found_float_coords(self, bg):
        """FOUND with float coords."""
        result = bg._parse_coordinates("FOUND: x=500.5, y=250.25")
        assert result == (500.5, 250.25)

    def test_garbage_text(self, bg):
        """Garbage text -> None."""
        result = bg._parse_coordinates("garbage text that means nothing")
        assert result is None

    def test_empty_string(self, bg):
        """Empty string -> None."""
        result = bg._parse_coordinates("")
        assert result is None

    def test_leading_trailing_whitespace(self, bg):
        """Leading/trailing whitespace is handled."""
        result = bg._parse_coordinates("  FOUND: x=100, y=200  ")
        assert result == (100.0, 200.0)

    def test_found_case_insensitive_zero(self, bg):
        """found: x=0, y=0 (lowercase) -> (0.0, 0.0)."""
        result = bg._parse_coordinates("found: x=0, y=0")
        assert result == (0.0, 0.0)

    def test_not_found_with_extra_text(self, bg):
        """NOT_FOUND followed by extra text still parses as None."""
        result = bg._parse_coordinates("NOT_FOUND - element not visible on screen")
        assert result is None

    def test_not_found_before_echoed_found_prefers_not_found(self, bg):
        """Prompt echoes after NOT_FOUND should not be parsed as coordinates."""
        result = bg._parse_coordinates(
            "If you find it, return the coordinates as floating point values.\n\n"
            'NOT_FOUND\nFOUND: x="29.1" y="11.1" y="11.1"'
        )
        assert result is None

    def test_found_with_extra_whitespace_around_equals(self, bg):
        """Extra whitespace around = and , signs."""
        result = bg._parse_coordinates("FOUND: x = 300 , y = 400")
        assert result == (300.0, 400.0)

    def test_mixed_case_found(self, bg):
        """Mixed case 'Found:' should still work (re.IGNORECASE)."""
        result = bg._parse_coordinates("Found: x=100, y=200")
        assert result == (100.0, 200.0)

    def test_found_with_quoted_numbers(self, bg):
        """FOUND with quoted numbers should still parse."""
        result = bg._parse_coordinates('FOUND: x="82.1" y="5.4"')
        assert result == (82.1, 5.4)

    def test_qwen3_vl_found_response(self, bg):
        """qwen3-vl with thinking disabled returns standard FOUND format."""
        result = bg._parse_coordinates("FOUND: x=10, y=10")
        assert result == (10.0, 10.0)

    def test_molmo2_points_coords_format(self, bg):
        """Molmo2 native <points coords="1 483 127"/> -> (483.0, 127.0)."""
        result = bg._parse_coordinates('<points coords="1 483 127"/>')
        assert result == (483.0, 127.0)

    def test_molmo2_points_coords_with_alt_attr(self, bg):
        """Molmo2 <points alt="close button" coords="1 483 127"/> also parses."""
        result = bg._parse_coordinates('<points alt="close button" coords="1 483 127"/>')
        assert result == (483.0, 127.0)

    def test_molmo2_points_coords_large_values(self, bg):
        """Molmo2 coordinates at scale extremes."""
        result = bg._parse_coordinates('<points coords="1 999 001"/>')
        assert result == (999.0, 1.0)

    def test_molmo2_points_coords_with_frame_prefix(self, bg):
        """Molmo2 single-image output may include a leading frame id."""
        result = bg._parse_coordinates('<points coords="1 1 914 074"/>')
        assert result == (914.0, 74.0)

    def test_molmo2_found_no_y_label(self, bg):
        """Molmo2 sometimes omits 'y=' label: FOUND: x=851 090 -> (851.0, 90.0)."""
        result = bg._parse_coordinates("FOUND: x=851 090")
        assert result == (851.0, 90.0)

    def test_molmo2_found_no_y_label_large_y(self, bg):
        """FOUND: x=190 1000 -> (190.0, 1000.0)."""
        result = bg._parse_coordinates("FOUND: x=190 1000")
        assert result == (190.0, 1000.0)

    def test_molmo2_found_no_y_label_three_digit_coords(self, bg):
        """FOUND: x=301 559 -> (301.0, 559.0)."""
        result = bg._parse_coordinates("FOUND: x=301 559")
        assert result == (301.0, 559.0)

    def test_found_with_y_label_not_affected(self, bg):
        """Standard FOUND: x=100, y=200 still works when no-y-label fallback exists."""
        result = bg._parse_coordinates("FOUND: x=100, y=200")
        assert result == (100.0, 200.0)


# ---------------------------------------------------------------------------
# 8. _resolve_coordinate_space  (5 tests)
# ---------------------------------------------------------------------------


class TestResolveCoordinateSpace:
    """Tests for _resolve_coordinate_space(model) -> Optional[str]."""

    def test_molmo_exact(self, bg):
        """Exact match 'molmo' -> 'normalized_0_100'."""
        assert bg._resolve_coordinate_space("molmo") == "normalized_0_100"

    def test_qwen25vl_exact(self, bg):
        """Exact match 'qwen2.5-vl' -> 'normalized_0_1000'."""
        assert bg._resolve_coordinate_space("qwen2.5-vl") == "normalized_0_1000"

    def test_qwen25vl_prefix_match(self, bg):
        """Prefix match 'qwen2.5-vl:7b' -> 'normalized_0_1000'."""
        assert bg._resolve_coordinate_space("qwen2.5-vl:7b") == "normalized_0_1000"

    def test_unknown_model(self, bg):
        """Unknown model -> None."""
        assert bg._resolve_coordinate_space("unknown-model-xyz") is None

    def test_claude_exact(self, bg):
        """Exact match 'claude-sonnet-4-20250514' -> 'pixel'."""
        assert bg._resolve_coordinate_space("claude-sonnet-4-20250514") == "pixel"

    def test_case_insensitive_lookup(self, bg):
        """Lookup should be case insensitive (spec says model_lower)."""
        # The dict keys are lowercase, and lookup uses model.lower()
        assert bg._resolve_coordinate_space("Molmo") == "normalized_0_100"

    def test_qwen3_vl(self, bg):
        """qwen3-vl should also be in the coordinate spaces."""
        assert bg._resolve_coordinate_space("qwen3-vl") == "normalized_0_1000"

    def test_qwen2_vl(self, bg):
        """qwen2-vl should also be in the coordinate spaces."""
        assert bg._resolve_coordinate_space("qwen2-vl") == "normalized_0_1000"

    def test_molmo2_exact(self, bg):
        """Exact match 'molmo2' -> 'normalized_0_1000'."""
        assert bg._resolve_coordinate_space("molmo2") == "normalized_0_1000"

    def test_molmo2_differs_from_molmo(self, bg):
        """molmo2 resolves differently from molmo: 0_1000 vs 0_100."""
        assert bg._resolve_coordinate_space("molmo2") == "normalized_0_1000"
        assert bg._resolve_coordinate_space("molmo") == "normalized_0_100"

    def test_molmo2_substring_match(self, bg):
        """HuggingFace-style 'mlx-community/Molmo2-8B-4bit' -> normalized_0_1000."""
        assert bg._resolve_coordinate_space("mlx-community/Molmo2-8B-4bit") == "normalized_0_1000"


class TestMolmoRequestSizing:
    """Molmo-family models should use the right mlx-vlm resize cap."""

    def test_molmo_request_max_image_dim(self, bg):
        assert bg._molmo_request_max_image_dim("mlx-community/Molmo-7B-D-0924-3bit") == 768
        assert bg._molmo_request_max_image_dim("mlx-community/Molmo2-8B-5bit") == 0
        assert bg._molmo_request_max_image_dim("mlx-community/MolmoPoint-8B-4bit") == 0

    def test_molmo_request_timeout(self, bg):
        assert bg._molmo_request_timeout_s("mlx-community/Molmo-7B-D-0924-3bit") == 120
        assert bg._molmo_request_timeout_s("mlx-community/MolmoPoint-8B-4bit") == 120
        assert bg._molmo_request_timeout_s("allenai/MolmoPoint-GUI-8B") == 300


# ---------------------------------------------------------------------------
# 9. JSON output schema  (1 comprehensive test)
# ---------------------------------------------------------------------------


class TestJSONOutputSchema:
    """Verify JSON output has all required keys from spec section G."""

    def test_json_has_all_required_top_level_keys(self, bg):
        """save_json_results produces JSON with all required top-level keys,
        per-backend keys, and per-sample keys from spec section G.
        """
        sample_result = bg.BackendResult(
            sample_file_name="test.png",
            instruction="close",
            backend_name="claude-sonnet",
            predicted_x=0.5,
            predicted_y=0.5,
            ground_truth_bbox=[0.4, 0.4, 0.6, 0.6],
            hit=True,
            distance_px=0.0,
            latency_s=1.0,
            raw_response="FOUND: x=0.5, y=0.5",
            error=None,
        )

        required_top_keys = {
            "timestamp", "n_samples", "category", "backends_requested",
            "backends_skipped", "results", "winner",
        }
        required_per_backend_keys = {
            "accuracy", "mean_distance_px", "avg_latency_s",
            "estimated_cost_usd", "n_samples", "n_hits", "n_misses", "samples",
        }
        required_per_sample_keys = {
            "file_name", "instruction", "predicted_x", "predicted_y",
            "ground_truth_bbox", "hit", "distance_px", "latency_s",
            "raw_response", "error",
        }

        output_path = bg.save_json_results(
            all_results={"claude-sonnet": [sample_result]},
            backends_requested=["claude-sonnet"],
            backends_skipped=[],
            n_samples=1,
            category=None,
            winner="claude-sonnet",
        )

        try:
            assert output_path.exists()
            with open(output_path) as f:
                output = json.load(f)
        finally:
            # Clean up
            if output_path.exists():
                output_path.unlink()

        assert required_top_keys.issubset(output.keys()), (
            f"Missing top-level keys: {required_top_keys - output.keys()}"
        )

        for backend_name, backend_data in output["results"].items():
            assert required_per_backend_keys.issubset(backend_data.keys()), (
                f"Backend '{backend_name}' missing keys: "
                f"{required_per_backend_keys - backend_data.keys()}"
            )
            for sample in backend_data.get("samples", []):
                assert required_per_sample_keys.issubset(sample.keys()), (
                    f"Sample missing keys: {required_per_sample_keys - sample.keys()}"
                )


# ---------------------------------------------------------------------------
# 10. CLI flags  (2 tests)
# ---------------------------------------------------------------------------


class TestCLIFlags:
    """Test CLI argument parsing."""

    def test_help_flag(self):
        """--help exits without error (exit code 0)."""
        import subprocess

        result = subprocess.run(
            [sys.executable, SCRIPT_PATH, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "benchmark" in result.stdout.lower()

    def test_n_flag_accepted(self, bg):
        """--n flag is accepted by argparse (parse only, don't run)."""
        import argparse

        # Try to find the argument parser
        if hasattr(bg, "parse_args"):
            args = bg.parse_args(["--n", "5"])
            assert args.n == 5
        elif hasattr(bg, "build_parser"):
            parser = bg.build_parser()
            args = parser.parse_args(["--n", "5"])
            assert args.n == 5
        elif hasattr(bg, "create_parser"):
            parser = bg.create_parser()
            args = parser.parse_args(["--n", "5"])
            assert args.n == 5
        else:
            # Fallback: run subprocess with --n and check it doesn't crash on parse
            import subprocess

            result = subprocess.run(
                [sys.executable, SCRIPT_PATH, "--n", "5", "--backends", "nonexistent"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # It might fail because backend doesn't exist, but it shouldn't fail
            # on argument parsing. Check stderr doesn't mention 'unrecognized arguments'.
            assert "unrecognized arguments" not in result.stderr


# ---------------------------------------------------------------------------
# 11. Error paths  (4 tests)
# ---------------------------------------------------------------------------


class TestErrorPaths:
    """Test error handling for garbled responses, timeouts, missing keys."""

    def test_garbled_response_yields_none_prediction(self, bg):
        """Model returns unparseable text -> _parse_coordinates returns None."""
        result = bg._parse_coordinates("I see a button at the top of the screen")
        assert result is None

    def test_garbled_response_json_like(self, bg):
        """Model returns JSON-like text -> _parse_coordinates returns None."""
        result = bg._parse_coordinates('{"x": 100, "y": 200}')
        assert result is None

    def test_backend_timeout_mock(self, bg):
        """Backend timeout creates BackendResult with error='timeout'.

        We verify the data shape: if prediction is None, hit must be False.
        """
        # Simulate a timeout result
        r = bg.BackendResult(
            sample_file_name="test.png",
            instruction="click button",
            backend_name="test",
            predicted_x=None,
            predicted_y=None,
            ground_truth_bbox=[0.0, 0.0, 1.0, 1.0],
            hit=False,
            distance_px=math.sqrt(960**2 + 540**2),
            latency_s=120.0,
            raw_response="",
            error="timeout",
        )
        assert r.predicted_x is None
        assert r.predicted_y is None
        assert r.hit is False
        assert r.error == "timeout"

    def test_missing_anthropic_key_skips_backend(self, bg):
        """If ANTHROPIC_API_KEY is unset, claude-sonnet backend should not be available."""
        with patch.dict(os.environ, {}, clear=True):
            result = bg.check_anthropic_backend()
            assert result is False

    def test_missing_anthropic_key_via_check_backend(self, bg):
        """check_backend('claude-sonnet', cfg) returns False when API key missing."""
        with patch.dict(os.environ, {}, clear=True):
            result = bg.check_backend("claude-sonnet", bg.BACKENDS["claude-sonnet"])
            assert result is False


# ---------------------------------------------------------------------------
# 12. BACKENDS dict structure  (3 tests)
# ---------------------------------------------------------------------------


class TestBackendsDict:
    """Verify BACKENDS dict matches spec section D."""

    def test_backends_has_expected_keys(self, bg):
        """BACKENDS dict should contain at least the documented backends."""
        assert "claude-sonnet" in bg.BACKENDS
        assert "gpt-5.4" in bg.BACKENDS
        assert "qwen3-vl-ollama" in bg.BACKENDS
        assert "molmo-mlx" in bg.BACKENDS
        assert "molmo2-mlx" in bg.BACKENDS
        assert "qwen2.5-vl-llamacpp" in bg.BACKENDS

    def test_anthropic_backend_type(self, bg):
        """claude-sonnet backend has type=anthropic."""
        assert bg.BACKENDS["claude-sonnet"]["type"] == "anthropic"
        assert bg.BACKENDS["claude-sonnet"]["model"] == "claude-sonnet-4-20250514"

    def test_openai_compat_backends(self, bg):
        """Local backends have type=openai_compat and correct URLs."""
        llamacpp = bg.BACKENDS["qwen2.5-vl-llamacpp"]
        assert llamacpp["type"] == "openai_compat"
        assert "8090" in llamacpp["url"]

    def test_ollama_native_backend(self, bg):
        """qwen3-vl-ollama uses ollama_native type with /api/chat endpoint."""
        qwen3 = bg.BACKENDS["qwen3-vl-ollama"]
        assert qwen3["type"] == "ollama_native"
        assert "11434" in qwen3["url"]
        assert "/api/chat" in qwen3["url"]

    def test_gpt_backend_uses_computer_use(self, bg):
        """gpt-5.4 should benchmark via Responses API computer use, not chat completions."""
        gpt = bg.BACKENDS["gpt-5.4"]
        assert gpt["type"] == "openai_computer_use"
        assert gpt["model"] == "gpt-5.4"


# ---------------------------------------------------------------------------
# 13. COORDINATE_SPACES dict  (2 tests)
# ---------------------------------------------------------------------------


class TestCoordinateSpaces:
    """Verify COORDINATE_SPACES dict matches spec section C."""

    def test_has_expected_models(self, bg):
        """COORDINATE_SPACES should have all models from the spec."""
        expected = {
            "molmo2",
            "molmo",
            "qwen3-vl",
            "qwen2.5-vl",
            "qwen2-vl",
            "claude-sonnet-4-20250514",
            "gpt-5.4",
        }
        actual = set(bg.COORDINATE_SPACES.keys())
        assert expected.issubset(actual), f"Missing models: {expected - actual}"

    def test_space_values_are_valid(self, bg):
        """Each coordinate space is one of the documented types."""
        valid = {"normalized_0_1", "normalized_0_100", "normalized_0_1000", "pixel"}
        for model, space in bg.COORDINATE_SPACES.items():
            assert space in valid, f"Model '{model}' has invalid space '{space}'"


# ---------------------------------------------------------------------------
# 14. PRICING dict  (2 tests)
# ---------------------------------------------------------------------------


class TestPricing:
    """Verify PRICING dict matches spec section E."""

    def test_has_expected_backends(self, bg):
        """PRICING should have entries for all documented backends."""
        assert "claude-sonnet" in bg.PRICING
        assert "qwen3-vl-ollama" in bg.PRICING
        assert "qwen2.5-vl-ollama" in bg.PRICING
        assert "qwen2.5-vl-llamacpp" in bg.PRICING
        assert "molmo-mlx" in bg.PRICING
        assert "molmo2-mlx" in bg.PRICING

    def test_local_backends_are_free(self, bg):
        """Local backends have 0.0 input and output pricing."""
        for name in ("qwen3-vl-ollama", "qwen2.5-vl-ollama", "qwen2.5-vl-llamacpp", "molmo-mlx", "molmo2-mlx"):
            assert bg.PRICING[name]["input"] == 0.0
            assert bg.PRICING[name]["output"] == 0.0

    def test_claude_pricing(self, bg):
        """Claude pricing: input $3.00/1M, output $15.00/1M."""
        assert bg.PRICING["claude-sonnet"]["input"] == 3.00
        assert bg.PRICING["claude-sonnet"]["output"] == 15.00


# ---------------------------------------------------------------------------
# 15. BenchmarkSample dataclass  (2 tests)
# ---------------------------------------------------------------------------


class TestBenchmarkSample:
    """Verify BenchmarkSample dataclass matches spec section A."""

    def test_has_expected_fields(self, bg):
        """BenchmarkSample should have all fields from the spec."""
        expected_fields = {
            "file_name",
            "instruction",
            "bbox",
            "data_type",
            "data_source",
            "image_width",
            "image_height",
            "image_bytes",
        }
        actual = set(bg.BenchmarkSample.__dataclass_fields__.keys())
        assert expected_fields.issubset(actual), (
            f"Missing fields: {expected_fields - actual}"
        )

    def test_can_construct(self, bg):
        """Can construct a BenchmarkSample with all fields."""
        s = bg.BenchmarkSample(
            file_name="test.png",
            instruction="click button",
            bbox=[0.1, 0.2, 0.3, 0.4],
            data_type="icon",
            data_source="macOS",
            image_width=960,
            image_height=540,
            image_bytes=b"\x89PNG\r\n",
        )
        assert s.file_name == "test.png"
        assert s.bbox == [0.1, 0.2, 0.3, 0.4]


# ---------------------------------------------------------------------------
# 16. BackendResult dataclass  (2 tests)
# ---------------------------------------------------------------------------


class TestBackendResult:
    """Verify BackendResult dataclass matches spec section A."""

    def test_has_expected_fields(self, bg):
        """BackendResult should have all fields from the spec."""
        expected_fields = {
            "sample_file_name",
            "instruction",
            "backend_name",
            "predicted_x",
            "predicted_y",
            "ground_truth_bbox",
            "hit",
            "distance_px",
            "latency_s",
            "raw_response",
            "error",
        }
        actual = set(bg.BackendResult.__dataclass_fields__.keys())
        assert expected_fields.issubset(actual), (
            f"Missing fields: {expected_fields - actual}"
        )

    def test_none_prediction_fields(self, bg):
        """BackendResult accepts None for predicted_x, predicted_y."""
        r = bg.BackendResult(
            sample_file_name="test.png",
            instruction="click",
            backend_name="test",
            predicted_x=None,
            predicted_y=None,
            ground_truth_bbox=[0.0, 0.0, 1.0, 1.0],
            hit=False,
            distance_px=100.0,
            latency_s=0.5,
            raw_response="NOT_FOUND",
            error=None,
        )
        assert r.predicted_x is None
        assert r.predicted_y is None
        assert r.error is None


# ---------------------------------------------------------------------------
# 17. Standalone constraint  (1 test)
# ---------------------------------------------------------------------------


class TestStandaloneConstraint:
    """Verify the script does not import from automation_agent (spec section H)."""

    def test_no_automation_agent_import(self):
        """The script must not import from automation_agent."""
        with open(SCRIPT_PATH, "r") as f:
            source = f.read()

        # Check for any import of automation_agent
        import_pattern = re.compile(
            r"^\s*(from\s+automation_agent|import\s+automation_agent)", re.MULTILINE
        )
        match = import_pattern.search(source)
        assert match is None, (
            f"Script imports from automation_agent at: {match.group()}"
        )


# ---------------------------------------------------------------------------
# 18. Default backends exclude Claude  (1 test)
# ---------------------------------------------------------------------------


class TestDefaultBackends:
    """Verify default backends do not include paid APIs."""

    def test_default_backends_exclude_claude(self, bg):
        """Default backends should not include claude-sonnet."""
        assert "claude-sonnet" not in bg.DEFAULT_BACKENDS
        assert "molmo-mlx" in bg.DEFAULT_BACKENDS
        assert "molmo2-mlx" in bg.DEFAULT_BACKENDS
        assert "qwen3-vl-ollama" in bg.DEFAULT_BACKENDS


# ---------------------------------------------------------------------------
# 19. Ollama native backend  (3 tests)
# ---------------------------------------------------------------------------


class TestOllamaNativeBackend:
    """Tests for call_ollama_native_backend function."""

    def test_call_ollama_native_backend_exists(self, bg):
        """call_ollama_native_backend function should exist."""
        assert hasattr(bg, "call_ollama_native_backend")
        assert callable(bg.call_ollama_native_backend)

    def test_call_ollama_native_uses_api_chat_endpoint(self, bg):
        """Verify native backend hits /api/chat, not /v1/chat/completions."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "message": {"content": "FOUND: x=10, y=10"}
        }).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            bg.call_ollama_native_backend(
                "http://localhost:11434/api/chat", "qwen3-vl:latest",
                "dGVzdA==", "test prompt"
            )
            call_args = mock_urlopen.call_args
            req = call_args[0][0]
            assert "/api/chat" in req.full_url

    def test_call_ollama_native_sets_think_false(self, bg):
        """Verify think: false is in the payload."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "message": {"content": "FOUND: x=10, y=10"}
        }).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            bg.call_ollama_native_backend(
                "http://localhost:11434/api/chat", "qwen3-vl:latest",
                "dGVzdA==", "test prompt"
            )
            call_args = mock_urlopen.call_args
            req = call_args[0][0]
            body = json.loads(req.data)
            assert body["think"] is False

    def test_call_ollama_native_returns_message_content(self, bg):
        """Verify we read result['message']['content'], not choices[0]."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "message": {"content": "FOUND: x=42, y=99"}
        }).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            content, latency = bg.call_ollama_native_backend(
                "http://localhost:11434/api/chat", "qwen3-vl:latest",
                "dGVzdA==", "test prompt"
            )
            assert content == "FOUND: x=42, y=99"
            assert latency > 0


class TestOpenAIComputerUseBackend:
    """Tests for call_openai_computer_use_backend."""

    def test_call_openai_computer_use_backend_exists(self, bg):
        assert hasattr(bg, "call_openai_computer_use_backend")
        assert callable(bg.call_openai_computer_use_backend)

    def test_call_openai_computer_use_returns_found_from_action(self, bg):
        first = MagicMock()
        first.read.return_value = json.dumps({
            "id": "resp_1",
            "output": [{"type": "computer_call", "call_id": "call_1", "actions": []}],
        }).encode()
        first.__enter__ = MagicMock(return_value=first)
        first.__exit__ = MagicMock(return_value=False)

        second = MagicMock()
        second.read.return_value = json.dumps({
            "id": "resp_2",
            "output": [
                {
                    "type": "computer_call",
                    "call_id": "call_1",
                    "actions": [{"type": "click", "x": 320, "y": 180}],
                }
            ],
        }).encode()
        second.__enter__ = MagicMock(return_value=second)
        second.__exit__ = MagicMock(return_value=False)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with patch("urllib.request.urlopen", side_effect=[first, second]) as mock_urlopen:
                text, _ = bg.call_openai_computer_use_backend(
                    "gpt-5.4",
                    "dGVzdA==",
                    "search bar",
                    1024,
                    768,
                )

        assert text == "FOUND: x=320.0, y=180.0"
        assert mock_urlopen.call_count == 2
        second_req = mock_urlopen.call_args_list[1][0][0]
        second_body = json.loads(second_req.data)
        assert second_body["input"][0]["output"]["detail"] == "original"

    def test_call_openai_computer_use_falls_back_to_text(self, bg):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "id": "resp_1",
            "output_text": "NOT_FOUND",
            "output": [],
        }).encode()
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with patch("urllib.request.urlopen", return_value=response):
                text, _ = bg.call_openai_computer_use_backend(
                    "gpt-5.4",
                    "dGVzdA==",
                    "search bar",
                    1024,
                    768,
                )

        assert text == "NOT_FOUND"
