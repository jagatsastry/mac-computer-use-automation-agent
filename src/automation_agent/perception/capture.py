"""Screen capture using PyAutoGUI."""

import pyautogui
import numpy as np
from typing import Tuple
from PIL import Image
import base64
import io


class ScreenCapturer:
    """Captures screenshots using PyAutoGUI."""

    def __init__(self, config=None):
        """Initialize screen capturer."""
        self.config = config

    def capture(self) -> Tuple[np.ndarray, Tuple[int, int]]:
        """
        Capture screenshot.

        Returns:
            (frame as numpy array, (width, height))
        """
        try:
            screenshot = pyautogui.screenshot()
            frame = np.array(screenshot)
            h, w = frame.shape[:2]
            return frame, (w, h)
        except Exception as e:
            raise RuntimeError(f"Failed to capture screen: {e}")

    def get_screen_size(self) -> Tuple[int, int]:
        """Get screen size."""
        return pyautogui.size()

    def capture_screen(self) -> Image.Image:
        """Capture full screenshot as PIL Image."""
        try:
            return pyautogui.screenshot()
        except Exception as e:
            raise RuntimeError(f"Failed to capture screen: {e}")

    def capture_region(self, x: int, y: int, width: int, height: int) -> Image.Image:
        """Capture region of screen as PIL Image."""
        try:
            return pyautogui.screenshot(region=(x, y, width, height))
        except Exception as e:
            raise RuntimeError(f"Failed to capture region: {e}")

    def capture_screen_b64(self, max_size_bytes: int = 4_500_000) -> str:
        """
        Capture screenshot and return base64 encoded string.

        Automatically compresses to stay under max_size_bytes (default 4.5MB
        to leave room for base64 encoding overhead under Anthropic's 5MB limit).
        """
        screenshot = self.capture_screen()

        # Try JPEG with decreasing quality until under size limit
        for quality in [85, 70, 55, 40, 30]:
            buffered = io.BytesIO()
            # Convert to RGB (JPEG doesn't support alpha)
            if screenshot.mode == 'RGBA':
                screenshot = screenshot.convert('RGB')
            screenshot.save(buffered, format="JPEG", quality=quality, optimize=True)

            if buffered.tell() <= max_size_bytes:
                return base64.b64encode(buffered.getvalue()).decode('utf-8')

        # If still too large, resize the image
        width, height = screenshot.size
        for scale in [0.75, 0.5, 0.4, 0.3]:
            new_size = (int(width * scale), int(height * scale))
            resized = screenshot.resize(new_size, Image.LANCZOS)

            buffered = io.BytesIO()
            resized.save(buffered, format="JPEG", quality=50, optimize=True)

            if buffered.tell() <= max_size_bytes:
                return base64.b64encode(buffered.getvalue()).decode('utf-8')

        # Last resort: return whatever we have
        buffered = io.BytesIO()
        screenshot.resize((width // 3, height // 3), Image.LANCZOS).save(
            buffered, format="JPEG", quality=40, optimize=True
        )
        return base64.b64encode(buffered.getvalue()).decode('utf-8')
