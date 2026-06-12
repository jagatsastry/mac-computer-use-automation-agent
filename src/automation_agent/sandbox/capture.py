"""Screenshot capture from the sandbox container's X display."""

from __future__ import annotations

import io
import subprocess

import structlog
from PIL import Image

from automation_agent.vision.capture import ScreenCapture
from automation_agent.vision.geometry import fit_screen_into_image

slog = structlog.get_logger(__name__)


class SandboxScreenCapture(ScreenCapture):
    """ScreenCapture that shoots the container display instead of the host.

    Drop-in replacement injected into ScreenCoordinatorImpl(capture=...).
    Inherits JPEG encoding/size-limit behavior; only the capture source and
    the logical screen size differ.
    """

    def __init__(
        self,
        container: str = "agent-sandbox",
        target_resolution: tuple[int, int] = (1024, 768),
        docker_bin: str = "docker",
    ):
        super().__init__(target_resolution)
        self.container = container
        self.docker_bin = docker_bin
        self._screen_size: tuple[int, int] | None = None

    def get_screen_size(self) -> tuple[int, int]:
        """Logical screen size = the container display geometry (cached)."""
        if self._screen_size is not None:
            return self._screen_size
        try:
            result = subprocess.run(
                [self.docker_bin, "exec", self.container, "xdotool", "getdisplaygeometry"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                width, height = result.stdout.split()[:2]
                self._screen_size = (int(width), int(height))
                return self._screen_size
        except Exception as exc:
            slog.debug("sandbox_display_geometry_failed", error=str(exc))
        return self.target_resolution

    def capture(self) -> bytes:
        """Capture the container display, letterbox to target resolution."""
        result = subprocess.run(
            [
                self.docker_bin,
                "exec",
                self.container,
                "sh",
                "-c",
                "scrot -z -o /tmp/_sandbox_shot.png && cat /tmp/_sandbox_shot.png",
            ],
            capture_output=True,
            timeout=20,
        )
        if result.returncode != 0 or not result.stdout:
            stderr = result.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            raise RuntimeError(f"sandbox scrot capture failed: {stderr}")

        img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
        rect = fit_screen_into_image(img.size, self.target_resolution)
        resized = img.resize(
            (max(round(rect.width), 1), max(round(rect.height), 1)), Image.LANCZOS
        )
        canvas = Image.new("RGB", self.target_resolution, (0, 0, 0))
        canvas.paste(resized, (round(rect.left), round(rect.top)))
        return self._enforce_size_limit(canvas)
