"""Screenshot capture with downscaling for vision models."""

import base64
import io
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Tuple

from PIL import Image

logger = logging.getLogger(__name__)

# Maximum allowed JPEG size: 4.5 MB (4,718,592 bytes)
_MAX_JPEG_SIZE = 4_718_592

# Quality levels to try when image exceeds size limit
_QUALITY_STEPS = [85, 70, 50]


class ScreenCapture:
    """Capture screenshots at target resolution for vision models."""

    def __init__(self, target_resolution: Tuple[int, int] = (1024, 768)):
        self.target_resolution = target_resolution

    def _encode_jpeg(self, img: Image.Image, quality: int) -> bytes:
        """Encode a PIL Image to JPEG bytes at the given quality.

        Args:
            img: PIL Image in RGB mode.
            quality: JPEG quality (1-100).

        Returns:
            JPEG-encoded bytes.
        """
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def _enforce_size_limit(self, img: Image.Image) -> bytes:
        """Encode image to JPEG, enforcing the 4.5MB size limit.

        Tries progressively lower quality settings. If still over the limit
        at quality=50, resizes the image to 75% of its dimensions and retries.

        Args:
            img: PIL Image in RGB mode.

        Returns:
            JPEG-encoded bytes within the size limit.
        """
        for quality in _QUALITY_STEPS:
            data = self._encode_jpeg(img, quality)
            if len(data) <= _MAX_JPEG_SIZE:
                return data
            logger.debug(
                "JPEG size %d bytes at quality=%d exceeds limit, trying lower quality",
                len(data), quality,
            )

        # All quality levels exceeded the limit; resize down and retry
        w, h = img.size
        new_w, new_h = int(w * 0.75), int(h * 0.75)
        logger.warning(
            "JPEG still too large after quality reduction. Resizing from %dx%d to %dx%d",
            w, h, new_w, new_h,
        )
        img = img.resize((new_w, new_h), Image.LANCZOS)
        return self._encode_jpeg(img, 50)

    def capture(self) -> bytes:
        """Capture screenshot, downscale to target resolution, return JPEG bytes.

        Uses macOS screencapture command to capture the screen, then
        downscales to the configured target resolution using Lanczos
        resampling for high quality. Enforces a 4.5MB size limit.

        Returns:
            JPEG-encoded bytes of the downscaled screenshot.
        """
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name

        try:
            subprocess.run(["screencapture", "-x", tmp_path], check=True)

            img = Image.open(tmp_path)
            img = img.resize(self.target_resolution, Image.LANCZOS)
            # Convert non-RGB modes to RGB for JPEG compatibility
            if img.mode in ("RGBA", "P", "LA", "CMYK", "I"):
                img = img.convert("RGB")

            return self._enforce_size_limit(img)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def capture_b64(self) -> str:
        """Capture and return base64-encoded JPEG.

        Returns:
            Base64-encoded string of the JPEG screenshot.
        """
        return base64.b64encode(self.capture()).decode()

    def get_screen_size(self) -> Tuple[int, int]:
        """Return the logical screen size used for input actions."""
        try:
            import pyautogui

            size = pyautogui.size()
            return int(size.width), int(size.height)
        except Exception:
            return self.target_resolution

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
