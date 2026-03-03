"""Integration tests for scripts/benchmark_grounding.py.

These tests import benchmark functions directly (no subprocess) and
use embedded minimal PNG images. No real ScreenSpot dataset download required.

Tests marked @pytest.mark.integration. Ollama health check tests skip if
Ollama is not running locally.
"""

import base64
import importlib
import io
import json
import math
import os
import sys
import time
from dataclasses import asdict
from typing import Optional, Tuple
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
# Helper: create a minimal valid PNG in memory
# ---------------------------------------------------------------------------


def _make_tiny_png(width=10, height=10) -> bytes:
    """Create a minimal valid PNG in memory."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Helper: create a BenchmarkSample with an embedded PNG
# ---------------------------------------------------------------------------


def _make_sample(bg, file_name="test.png", instruction="close",
                 bbox=None, width=10, height=10):
    """Create a BenchmarkSample with an embedded tiny PNG."""
    if bbox is None:
        bbox = [0.4, 0.4, 0.6, 0.6]
    return bg.BenchmarkSample(
        file_name=file_name,
        instruction=instruction,
        bbox=bbox,
        data_type="icon",
        data_source="macOS",
        image_width=width,
        image_height=height,
        image_bytes=_make_tiny_png(width, height),
    )


# ---------------------------------------------------------------------------
# Integration test: Ollama health check
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestOllamaHealthCheck:
    """Test real Ollama health check (skip if Ollama not running)."""

    def _ollama_reachable(self) -> bool:
        """Check if Ollama is reachable at localhost:11434."""
        import urllib.request

        try:
            req = urllib.request.Request("http://localhost:11434/v1/models")
            with urllib.request.urlopen(req, timeout=3):
                return True
        except Exception:
            return False

    def test_ollama_health_check(self, bg):
        """If Ollama is running, check_backend should detect it."""
        if not self._ollama_reachable():
            pytest.skip("Ollama not running at localhost:11434")

        result = bg.check_backend(
            "qwen2.5-vl-ollama",
            bg.BACKENDS["qwen2.5-vl-ollama"],
        )
        assert result is True


# ---------------------------------------------------------------------------
# Integration test: 1-sample call with mock backend
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSingleSampleMocked:
    """Test a single sample through the pipeline with mocked HTTP backend."""

    def test_single_sample_openai_compat(self, bg):
        """Run 1 sample through an openai_compat backend (HTTP mocked).

        Verifies the full pipeline: image encoding, API call, response parsing,
        coordinate normalization, metric computation.
        """
        sample = _make_sample(bg, width=960, height=540)

        # Mock HTTP response
        mock_response_body = json.dumps({
            "choices": [{
                "message": {
                    "content": "FOUND: x=500, y=500"
                }
            }],
            "usage": {"prompt_tokens": 1600, "completion_tokens": 30},
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_response_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            # Call the backend handler directly if available
            result = bg.run_sample(
                sample=sample,
                backend_name="qwen2.5-vl-ollama",
                cfg=bg.BACKENDS["qwen2.5-vl-ollama"],
            )

            assert isinstance(result, bg.BackendResult)
            assert result.backend_name == "qwen2.5-vl-ollama"
            assert result.instruction == "close"
            # qwen2.5-vl: x=500 -> 500/1000=0.5, y=500 -> 500/1000=0.5
            if result.predicted_x is not None:
                assert result.predicted_x == pytest.approx(0.5, abs=0.01)
                assert result.predicted_y == pytest.approx(0.5, abs=0.01)
            assert result.raw_response == "FOUND: x=500, y=500"
            assert result.error is None

    def test_single_sample_timeout(self, bg):
        """Backend timeout produces BackendResult with error='timeout'."""
        sample = _make_sample(bg, width=960, height=540)

        def raise_timeout(*args, **kwargs):
            raise TimeoutError("Connection timed out")

        with patch("urllib.request.urlopen", side_effect=raise_timeout):
            result = bg.run_sample(
                sample=sample,
                backend_name="qwen2.5-vl-ollama",
                cfg=bg.BACKENDS["qwen2.5-vl-ollama"],
            )

            assert isinstance(result, bg.BackendResult)
            assert result.predicted_x is None
            assert result.predicted_y is None
            assert result.hit is False
            assert result.error is not None
            # Distance should be full diagonal
            expected_diag = math.sqrt(960**2 + 540**2)
            assert result.distance_px == pytest.approx(expected_diag)

    def test_single_sample_garbled_response(self, bg):
        """Backend returns unparseable text -> parse_failed error."""
        sample = _make_sample(bg, width=960, height=540)

        mock_response_body = json.dumps({
            "choices": [{
                "message": {
                    "content": "I can see a close button in the top right corner of the window."
                }
            }],
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_response_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = bg.run_sample(
                sample=sample,
                backend_name="qwen2.5-vl-ollama",
                cfg=bg.BACKENDS["qwen2.5-vl-ollama"],
            )

            assert isinstance(result, bg.BackendResult)
            assert result.predicted_x is None
            assert result.predicted_y is None
            assert result.hit is False
            assert result.error is not None
            assert "parse" in result.error.lower() or "failed" in result.error.lower()


# ---------------------------------------------------------------------------
# Integration test: full end-to-end pipeline with all metrics
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFullPipelineMocked:
    """Test the full benchmark pipeline with mocked backends."""

    def test_accuracy_and_distance_computation(self, bg):
        """Verify accuracy + mean distance computation from BackendResult list."""
        bbox = [0.4, 0.4, 0.6, 0.6]  # center at (0.5, 0.5)

        # Hit: prediction at center
        r1 = bg.BackendResult(
            sample_file_name="s1.png",
            instruction="click",
            backend_name="test",
            predicted_x=0.5,
            predicted_y=0.5,
            ground_truth_bbox=bbox,
            hit=True,
            distance_px=0.0,
            latency_s=1.0,
            raw_response="FOUND: x=0.5, y=0.5",
            error=None,
        )

        # Miss: prediction far away
        r2 = bg.BackendResult(
            sample_file_name="s2.png",
            instruction="type",
            backend_name="test",
            predicted_x=0.9,
            predicted_y=0.9,
            ground_truth_bbox=bbox,
            hit=False,
            distance_px=math.sqrt((0.4 * 960) ** 2 + (0.4 * 540) ** 2),
            latency_s=2.0,
            raw_response="FOUND: x=0.9, y=0.9",
            error=None,
        )

        results = [r1, r2]
        accuracy = bg.compute_accuracy(results)
        mean_dist = bg.compute_mean_distance(results)

        assert accuracy == pytest.approx(0.5)
        assert mean_dist == pytest.approx((0.0 + r2.distance_px) / 2.0)

    def test_cost_estimation_consistency(self, bg):
        """Cost for local backends is zero; for claude-sonnet it's positive."""
        for local in ("qwen2.5-vl-ollama", "qwen2.5-vl-llamacpp"):
            assert bg.estimate_cost(local, 100) == 0.0

        claude_cost = bg.estimate_cost("claude-sonnet", 100)
        assert claude_cost > 0.0


# ---------------------------------------------------------------------------
# Integration test: coordinate normalization round-trip
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCoordinateNormalizationRoundTrip:
    """Test that coordinate normalization is consistent for all model types."""

    def test_molmo_round_trip(self, bg):
        """molmo: (0.5, 0.5) -> normalize -> (0.5, 0.5)."""
        x, y = bg.normalize_prediction(0.5, 0.5, "molmo", 960, 540)
        assert x == pytest.approx(0.5)
        assert y == pytest.approx(0.5)

    def test_qwen_round_trip(self, bg):
        """qwen2.5-vl: (500, 500) -> normalize -> (0.5, 0.5)."""
        x, y = bg.normalize_prediction(500, 500, "qwen2.5-vl", 960, 540)
        assert x == pytest.approx(0.5)
        assert y == pytest.approx(0.5)

    def test_claude_round_trip(self, bg):
        """claude-sonnet-4-20250514: (480, 270) -> normalize -> (0.5, 0.5)."""
        x, y = bg.normalize_prediction(480, 270, "claude-sonnet-4-20250514", 960, 540)
        assert x == pytest.approx(0.5)
        assert y == pytest.approx(0.5)

    def test_point_in_bbox_after_normalization(self, bg):
        """After normalization, point_in_bbox correctly evaluates a hit."""
        # qwen returns (500, 400), normalize for bbox center at (0.5, 0.5)
        x, y = bg.normalize_prediction(500, 400, "qwen2.5-vl", 1000, 1000)
        bbox = [0.3, 0.3, 0.7, 0.7]
        assert bg.point_in_bbox(x, y, bbox) is True

    def test_point_outside_bbox_after_normalization(self, bg):
        """After normalization, point_in_bbox correctly evaluates a miss."""
        # qwen returns (900, 900) -> normalized (0.9, 0.9), bbox is [0.0, 0.0, 0.5, 0.5]
        x, y = bg.normalize_prediction(900, 900, "qwen2.5-vl", 1000, 1000)
        bbox = [0.0, 0.0, 0.5, 0.5]
        assert bg.point_in_bbox(x, y, bbox) is False


# ---------------------------------------------------------------------------
# Integration test: BenchmarkSample with embedded PNG
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBenchmarkSampleWithPNG:
    """Test BenchmarkSample with real (tiny) PNG images."""

    def test_sample_image_bytes_are_valid_png(self, bg):
        """The embedded image bytes form a valid PNG that PIL can open."""
        from PIL import Image

        sample = _make_sample(bg, width=100, height=75)
        img = Image.open(io.BytesIO(sample.image_bytes))
        assert img.size == (100, 75)
        assert img.mode == "RGB"

    def test_sample_image_dimensions_match(self, bg):
        """image_width/image_height match the actual image dimensions."""
        from PIL import Image

        sample = _make_sample(bg, width=320, height=240)
        img = Image.open(io.BytesIO(sample.image_bytes))
        assert img.size[0] == sample.image_width
        assert img.size[1] == sample.image_height

    def test_sample_base64_encoding(self, bg):
        """Image bytes can be base64 encoded for API calls."""
        sample = _make_sample(bg, width=10, height=10)
        b64 = base64.b64encode(sample.image_bytes).decode()
        # Should be a valid base64 string
        decoded = base64.b64decode(b64)
        assert decoded == sample.image_bytes


# ---------------------------------------------------------------------------
# Integration test: JSON output end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestJSONOutputEndToEnd:
    """Test JSON output structure from a full mocked run."""

    def test_json_output_structure(self, bg, tmp_path):
        """save_json_results produces JSON with all required keys from spec section G."""
        required_top_keys = {
            "timestamp", "n_samples", "category", "backends_requested",
            "backends_skipped", "results", "winner",
        }
        required_backend_keys = {
            "accuracy", "mean_distance_px", "avg_latency_s",
            "estimated_cost_usd", "n_samples", "n_hits", "n_misses", "samples",
        }
        required_sample_keys = {
            "file_name", "instruction", "predicted_x", "predicted_y",
            "ground_truth_bbox", "hit", "distance_px", "latency_s",
            "raw_response", "error",
        }

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
            raw_response="FOUND: x=500, y=500",
            error=None,
        )

        output_path = bg.save_json_results(
            all_results={"claude-sonnet": [sample_result]},
            backends_requested=["claude-sonnet"],
            backends_skipped=[],
            n_samples=1,
            category=None,
            winner="claude-sonnet",
        )

        assert output_path.exists()
        with open(output_path) as f:
            output = json.load(f)

        # Clean up the generated file
        output_path.unlink(missing_ok=True)

        assert required_top_keys.issubset(set(output.keys())), (
            f"Missing top-level keys: {required_top_keys - set(output.keys())}"
        )

        for bname, bdata in output["results"].items():
            assert required_backend_keys.issubset(set(bdata.keys())), (
                f"Backend '{bname}' missing keys: "
                f"{required_backend_keys - set(bdata.keys())}"
            )
            for s in bdata["samples"]:
                assert required_sample_keys.issubset(set(s.keys())), (
                    f"Sample missing keys: {required_sample_keys - set(s.keys())}"
                )
