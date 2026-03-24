"""Regression tests for scripts/benchmark_groundcua.py."""

from __future__ import annotations

import base64
import importlib.util
import io
import os
import sys

import pytest
from PIL import Image


SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "scripts", "benchmark_groundcua.py"
)


@pytest.fixture(scope="session")
def bgc():
    """Import benchmark_groundcua.py as a module, once per test session."""
    spec = importlib.util.spec_from_file_location("benchmark_groundcua", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["benchmark_groundcua"] = mod
    spec.loader.exec_module(mod)
    return mod


def _png_bytes(width: int, height: int, color=(255, 0, 0, 255)) -> bytes:
    img = Image.new("RGBA", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestPrepareImage:
    """prepare_image should preserve screenshots losslessly for tiny UI targets."""

    def test_prepare_image_returns_png_media_type(self, bgc, monkeypatch):
        monkeypatch.setattr(bgc, "_download_file", lambda _path: _png_bytes(32, 24))
        sample = bgc.Sample(
            platform="Test",
            image_path="fake/sample.png",
            text="button",
            bbox=[1, 1, 10, 10],
            category="",
            element_id="id-1",
            image_width=32,
            image_height=24,
        )

        image_b64, width, height, media_type = bgc.prepare_image(sample, resize=False)
        decoded = base64.b64decode(image_b64)

        assert (width, height) == (32, 24)
        assert media_type == "image/png"
        assert decoded.startswith(b"\x89PNG\r\n\x1a\n")

    def test_prepare_image_resize_keeps_png(self, bgc, monkeypatch):
        monkeypatch.setattr(bgc, "_download_file", lambda _path: _png_bytes(2048, 1024))
        sample = bgc.Sample(
            platform="Test",
            image_path="fake/wide.png",
            text="button",
            bbox=[100, 100, 200, 160],
            category="",
            element_id="id-2",
            image_width=2048,
            image_height=1024,
        )

        image_b64, width, height, media_type = bgc.prepare_image(sample, resize=True)
        decoded = base64.b64decode(image_b64)

        assert (width, height) == (1024, 512)
        assert media_type == "image/png"
        assert decoded.startswith(b"\x89PNG\r\n\x1a\n")


class TestMolmoPointHandling:
    """MolmoPoint should not be misrouted through plain Molmo assumptions."""

    def test_get_prompt_uses_pointing_style(self, bgc):
        prompt = bgc.get_prompt("molmo-point", "maximize")
        assert prompt == "Point to maximize."

    def test_get_prompt_uses_pointing_style_for_gui_variant(self, bgc):
        prompt = bgc.get_prompt("molmo-point-gui", "maximize")
        assert prompt == "Point to maximize."

    def test_convert_coordinates_treats_molmo_point_as_pixel(self, bgc):
        x, y = bgc.convert_coordinates(1812.4, 69.2, "molmo-point", 1920, 1080)
        assert (x, y) == (1812, 69)

    def test_convert_coordinates_treats_molmo_point_gui_as_pixel(self, bgc):
        x, y = bgc.convert_coordinates(1812.4, 69.2, "molmo-point-gui", 1920, 1080)
        assert (x, y) == (1812, 69)

    def test_original_image_policy_is_model_specific(self, bgc):
        assert bgc.should_use_original_image("gemini-flash") is True
        assert bgc.should_use_original_image("molmo") is True
        assert bgc.should_use_original_image("molmo2") is True
        assert bgc.should_use_original_image("molmo-point") is True
        assert bgc.should_use_original_image("molmo-point-gui") is True
        assert bgc.should_use_original_image("gpt") is False

    def test_molmo_request_max_image_dim_is_model_specific(self, bgc):
        assert bgc.molmo_request_max_image_dim("molmo") == 768
        assert bgc.molmo_request_max_image_dim("molmo2") == 0
        assert bgc.molmo_request_max_image_dim("molmo-point") == 0
        assert bgc.molmo_request_max_image_dim("molmo-point-gui") == 0

    def test_molmo_request_timeout_is_model_specific(self, bgc):
        assert bgc.molmo_request_timeout_s("molmo") == 120
        assert bgc.molmo_request_timeout_s("molmo-point") == 120
        assert bgc.molmo_request_timeout_s("molmo-point-gui") == 300


class TestParseCoordinates:
    """Parser should prefer the first line-level answer token over echoed prompt text."""

    def test_not_found_before_echoed_found_stays_not_found(self, bgc):
        response = (
            "If you find it, return the coordinates as floating point values.\n\n"
            "NOT_FOUND\n"
            'FOUND: x="29.1" y="11.1" y="11.1"'
        )
        assert bgc.parse_coordinates(response) is None

    def test_preamble_before_found_is_ignored(self, bgc):
        response = "Some preamble text\nFOUND: x=29.1, y=11.1"
        assert bgc.parse_coordinates(response) == (29.1, 11.1)


class TestSummary:
    """GroundCUA summary metrics should distinguish total hit rate from found-only accuracy."""

    def test_summarize_model_stats_exposes_both_rates(self, bgc):
        summary = bgc.summarize_model_stats(
            {"hits": 5, "misses": 15, "not_found": 30, "errors": 0}
        )
        assert summary["accuracy"] == 10.0
        assert summary["hit_rate"] == 10.0
        assert summary["found_accuracy"] == 25.0
        assert summary["total"] == 50
