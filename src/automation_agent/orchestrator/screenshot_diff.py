"""Fast action verification via screenshot comparison.

Captures before/after screenshots and compares pixel values to detect
whether an action (typically a click) had a visible effect on the screen.
This is a ~50ms check that can trigger immediate retry without waiting
for a full vision verification round-trip.
"""

import numpy as np
from typing import Optional

from automation_agent.perception.capture import ScreenCapturer


class ScreenshotDiffVerifier:
    """Verifies that actions had visible effect by comparing screenshots.

    Strategies:
    1. Full-screen diff: did anything change?
    2. Region diff: did the click target area change?

    A pixel is considered "changed" if its intensity differs by more than 10 units.
    The screen/region is considered "changed" if the fraction of changed pixels
    exceeds ``change_threshold``.
    """

    def __init__(self, capturer: ScreenCapturer, change_threshold: float = 0.01):
        self.capturer = capturer
        self.change_threshold = change_threshold
        self._before: Optional[np.ndarray] = None

    def capture_before(self) -> None:
        """Capture screenshot before action.

        Stores the current screen as a numpy array for later comparison.
        """
        img = self.capturer.capture_screen()
        self._before = np.array(img)

    def screen_changed(self) -> bool:
        """Check if the full screen changed since capture_before().

        Returns True (safe default) if no before-screenshot was captured
        or if the screen dimensions changed between captures.
        """
        if self._before is None:
            return True
        after = np.array(self.capturer.capture_screen())
        if self._before.shape != after.shape:
            return True
        diff = np.abs(self._before.astype(np.float32) - after.astype(np.float32))
        changed_fraction = float(np.mean(diff > 10))
        return changed_fraction > self.change_threshold

    def region_changed(self, x: int, y: int, radius: int = 100) -> bool:
        """Check if the region around (x, y) changed since capture_before().

        Extracts a square region of side ``2 * radius`` centered on (x, y),
        clipped to image boundaries. Returns True (safe default) if no
        before-screenshot was captured or if image shapes differ.

        Args:
            x: Horizontal pixel coordinate of the region center.
            y: Vertical pixel coordinate of the region center.
            radius: Half-width of the square region to compare.
        """
        if self._before is None:
            return True
        after = np.array(self.capturer.capture_screen())
        if self._before.shape != after.shape:
            return True
        h, w = self._before.shape[:2]
        x1 = max(0, x - radius)
        y1 = max(0, y - radius)
        x2 = min(w, x + radius)
        y2 = min(h, y + radius)
        if x1 >= x2 or y1 >= y2:
            return True
        pre_region = self._before[y1:y2, x1:x2]
        post_region = after[y1:y2, x1:x2]
        diff = np.abs(pre_region.astype(np.float32) - post_region.astype(np.float32))
        changed_fraction = float(np.mean(diff > 10))
        return changed_fraction > self.change_threshold
