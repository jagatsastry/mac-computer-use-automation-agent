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

    def capture_screen_b64(self) -> str:
        """Capture screenshot and return base64 encoded string."""
        screenshot = self.capture_screen()
        buffered = io.BytesIO()
        screenshot.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')
