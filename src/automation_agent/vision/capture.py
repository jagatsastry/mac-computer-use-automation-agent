"""Screenshot capture with downscaling for vision models."""

import base64
import io
import subprocess
import tempfile
from pathlib import Path
from typing import Tuple

from PIL import Image


class ScreenCapture:
    """Capture screenshots at target resolution for vision models."""

    def __init__(self, target_resolution: Tuple[int, int] = (1024, 768)):
        self.target_resolution = target_resolution

    def capture(self) -> bytes:
        """Capture screenshot, downscale to target resolution, return JPEG bytes.

        Uses macOS screencapture command to capture the screen, then
        downscales to the configured target resolution using Lanczos
        resampling for high quality.

        Returns:
            JPEG-encoded bytes of the downscaled screenshot.
        """
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name

        try:
            subprocess.run(["screencapture", "-x", tmp_path], check=True)

            img = Image.open(tmp_path)
            img = img.resize(self.target_resolution, Image.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def capture_b64(self) -> str:
        """Capture and return base64-encoded JPEG.

        Returns:
            Base64-encoded string of the JPEG screenshot.
        """
        return base64.b64encode(self.capture()).decode()

    def save(self, path: str) -> str:
        """Capture and save to file.

        Args:
            path: File path to save the screenshot to.

        Returns:
            The path the screenshot was saved to.
        """
        data = self.capture()
        with open(path, "wb") as f:
            f.write(data)
        return path
