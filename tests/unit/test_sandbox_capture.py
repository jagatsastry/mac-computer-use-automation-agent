"""Unit tests for sandbox screenshot capture (subprocess mocked)."""

import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from automation_agent.sandbox.capture import SandboxScreenCapture


def _png_bytes(width, height, color=(40, 90, 160)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _geometry_ok(w, h):
    return MagicMock(returncode=0, stdout=f"{w} {h}\n", stderr="")


class TestCapture:
    def test_capture_native_resolution_passthrough(self):
        cap = SandboxScreenCapture(container="testbox", target_resolution=(1024, 768))
        png = _png_bytes(1024, 768)

        def fake_run(cmd, **kwargs):
            if "getdisplaygeometry" in " ".join(map(str, cmd)):
                return _geometry_ok(1024, 768)
            return MagicMock(returncode=0, stdout=png, stderr=b"")

        with patch("automation_agent.sandbox.capture.subprocess.run", side_effect=fake_run):
            jpeg = cap.capture()

        img = Image.open(io.BytesIO(jpeg))
        assert img.format == "JPEG"
        assert img.size == (1024, 768)

    def test_capture_letterboxes_larger_display(self):
        cap = SandboxScreenCapture(container="testbox", target_resolution=(1024, 768))
        png = _png_bytes(1280, 800, color=(255, 0, 0))

        def fake_run(cmd, **kwargs):
            if "getdisplaygeometry" in " ".join(map(str, cmd)):
                return _geometry_ok(1280, 800)
            return MagicMock(returncode=0, stdout=png, stderr=b"")

        with patch("automation_agent.sandbox.capture.subprocess.run", side_effect=fake_run):
            jpeg = cap.capture()

        img = Image.open(io.BytesIO(jpeg))
        assert img.size == (1024, 768)
        # content scaled by 0.8 -> 1024x640, letterboxed top/bottom by 64px black
        top_pixel = img.convert("RGB").getpixel((512, 10))
        assert sum(top_pixel) < 60  # black bar
        mid_pixel = img.convert("RGB").getpixel((512, 384))
        assert mid_pixel[0] > 180  # red content

    def test_capture_raises_on_scrot_failure(self):
        cap = SandboxScreenCapture(container="testbox")

        def fake_run(cmd, **kwargs):
            if "getdisplaygeometry" in " ".join(map(str, cmd)):
                return _geometry_ok(1024, 768)
            return MagicMock(returncode=1, stdout=b"", stderr=b"scrot: no display")

        with patch("automation_agent.sandbox.capture.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="scrot"):
                cap.capture()


class TestScreenSize:
    def test_screen_size_parsed_and_cached(self):
        cap = SandboxScreenCapture(container="testbox")
        with patch(
            "automation_agent.sandbox.capture.subprocess.run",
            return_value=_geometry_ok(1024, 768),
        ) as run:
            assert cap.get_screen_size() == (1024, 768)
            assert cap.get_screen_size() == (1024, 768)
        assert run.call_count == 1

    def test_screen_size_fallback_when_container_down(self):
        cap = SandboxScreenCapture(container="testbox", target_resolution=(800, 600))
        with patch(
            "automation_agent.sandbox.capture.subprocess.run",
            side_effect=Exception("docker down"),
        ):
            assert cap.get_screen_size() == (800, 600)
