"""Screen capture using PyAutoGUI."""

import pyautogui
import numpy as np
from typing import Tuple


class ScreenCapturer:
    """Captures screenshots using PyAutoGUI."""

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
