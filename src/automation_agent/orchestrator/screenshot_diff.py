"""Fast action verification via screenshot comparison.

Captures before/after screenshots and compares pixel values to detect
whether an action (typically a click) had a visible effect on the screen.
This is a ~50ms check that can trigger immediate retry without waiting
for a full vision verification round-trip.
"""

import io

import numpy as np
from PIL import Image
from typing import Any, Optional


class ScreenshotDiffVerifier:
    """Verifies that actions had visible effect by comparing screenshots.

    Strategies:
    1. Full-screen diff: did anything change?
    2. Region diff: did the click target area change?

    A pixel is considered "changed" if its intensity differs by more than 10 units.
    The screen/region is considered "changed" if the fraction of changed pixels
    exceeds ``change_threshold``.

    The capturer can be any object with a ``capture()`` method returning JPEG bytes
    or a ``capture_screen()`` method returning a PIL Image.
    """

    def __init__(
        self,
        capturer: Any,
        change_threshold: float = 0.01,
        strong_diff: int = 40,
        min_changed_pixels: int = 60,
    ):
        self.capturer = capturer
        self.change_threshold = change_threshold
        # A "strong" pixel change (intensity delta > strong_diff on any channel)
        # is what a text glyph / icon produces; antialiasing and JPEG noise stay
        # under it. min_changed_pixels is the absolute floor of strong pixels
        # that counts as a real change even when it covers < change_threshold of
        # the frame — catches small but meaningful updates like a counter badge
        # incrementing, whose false "no effect" reading drove destructive
        # re-click loops.
        self.strong_diff = strong_diff
        self.min_changed_pixels = min_changed_pixels
        self._before: Optional[np.ndarray] = None

    def _grab(self) -> np.ndarray:
        """Capture current screen as numpy array, adapting to capturer API."""
        if hasattr(self.capturer, "capture_screen"):
            return np.array(self.capturer.capture_screen())
        # ScreenCapture.capture() returns JPEG bytes
        raw = self.capturer.capture()
        img = Image.open(io.BytesIO(raw))
        return np.array(img)

    def capture_before(self) -> None:
        """Capture screenshot before action.

        Stores the current screen as a numpy array for later comparison.
        """
        self._before = self._grab()

    def _is_changed(self, before: np.ndarray, after: np.ndarray) -> bool:
        """Two-signal change test: fraction-of-frame OR small-strong-cluster.

        A frame counts as changed if either a meaningful FRACTION of pixels
        moved a little (large/global change), or an absolute FLOOR of pixels
        moved a lot (small localized glyph/icon change). The second signal
        catches updates too small for the fraction threshold — e.g. a counter
        badge ticking up — without tripping on scattered low-magnitude noise.
        """
        diff = np.abs(before.astype(np.float32) - after.astype(np.float32))
        changed_fraction = float(np.mean(diff > 10))
        if changed_fraction > self.change_threshold:
            return True
        if diff.ndim == 3:
            strong = np.any(diff > self.strong_diff, axis=-1)
        else:
            strong = diff > self.strong_diff
        return int(np.count_nonzero(strong)) >= self.min_changed_pixels

    def screen_changed(self) -> bool:
        """Check if the full screen changed since capture_before().

        Returns True (safe default) if no before-screenshot was captured
        or if the screen dimensions changed between captures.
        """
        if self._before is None:
            return True
        after = self._grab()
        if self._before.shape != after.shape:
            return True
        return self._is_changed(self._before, after)

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
        after = self._grab()
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
        return self._is_changed(pre_region, post_region)
