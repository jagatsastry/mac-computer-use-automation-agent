"""Screen observer using vision model to understand current screen state."""

import re
from datetime import datetime
from typing import Any, Optional, Tuple

from ..perception.capture import ScreenCapturer
from .models import Coordinates, Observation


# System prompt for screen observation
OBSERVER_SYSTEM_PROMPT = """You are a screen observation assistant. Analyze screenshots and describe what you see.

Be concise but thorough. Focus on:
1. Which application is in the foreground
2. Key UI elements visible (buttons, text fields, menus)
3. Any relevant text content
4. The current state (loading, ready, error, etc.)

Keep responses under 200 words."""


# System prompt for element finding
ELEMENT_FINDER_SYSTEM_PROMPT = """You are a UI element locator. Given a screenshot and element description, find the element and return its bounding box.

Return the bounding box in this EXACT format:
<box>(x1,y1,x2,y2)</box>

Where coordinates are normalized 0-1000 (top-left is 0,0 and bottom-right is 1000,1000).

If you cannot find the element, respond with:
<box>NOT_FOUND</box>

IMPORTANT:
- Be precise with coordinates
- The box should tightly contain the element
- Return ONLY the box tag, no other text

## Special Cases

### Time Slots
When asked to find a time slot closest to a specific time:
1. Look at ALL available time slots on the screen
2. Find the one CLOSEST to the requested time
3. If exact time not available, prefer the next available time AFTER the requested time
4. Return the bounding box of that specific time slot button

Example: "Find time slot closest to 7:00 PM"
- If 7:00 PM available → return that
- If not, but 7:15 PM and 6:45 PM available → return 7:15 PM (next after)
- If only earlier times → return the latest one before 7:00 PM

### Buttons with Text
For buttons, include the full clickable area, not just the text."""


class ScreenObserver:
    """
    Observes the screen using vision models to understand current state.

    Uses vision models to:
    1. Describe what's currently on screen
    2. Find UI elements by description
    3. Answer questions about screen content
    """

    def __init__(
        self,
        vision_client: Any,
        capturer: ScreenCapturer,
        model: str = "qwen3-vl",
    ):
        """
        Initialize screen observer.

        Args:
            vision_client: Ollama client for vision model inference
            capturer: Screen capture utility
            model: Vision model name (default: qwen3-vl)
        """
        self.vision = vision_client
        self.capturer = capturer
        self.model = model
        self._screen_size: Optional[Tuple[int, int]] = None

    def _get_screen_size(self) -> Tuple[int, int]:
        """
        Get and cache screen dimensions.

        IMPORTANT: Returns SCREENSHOT dimensions (not logical screen size).
        This ensures coordinates are in the same space as what Claude sees,
        and ClickAction can properly scale them for Retina displays.
        """
        if self._screen_size is None:
            # Prefer screenshot-space dimensions when available (Retina-safe).
            screenshot_size: Optional[Tuple[int, int]] = None
            try:
                screenshot = self.capturer.capture_screen()
                if hasattr(screenshot, "size") and screenshot.size:
                    screenshot_size = screenshot.size
            except Exception:
                screenshot_size = None

            # Test fixtures and some capturers expose logical size only.
            if screenshot_size is not None and len(screenshot_size) == 2:
                self._screen_size = screenshot_size
            else:
                self._screen_size = self.capturer.get_screen_size()
        return self._screen_size

    async def observe(self, question: Optional[str] = None) -> Observation:
        """
        Take a screenshot and analyze it with the vision model.

        Args:
            question: Specific question to ask about the screen.
                     If None, provides general description.

        Returns:
            Observation with screenshot and description
        """
        # Capture screenshot
        screenshot_b64 = self.capturer.capture_screen_b64()

        # Build prompt
        if question:
            prompt = question
        else:
            prompt = "Describe the current screen state. What app is open? What UI elements are visible?"

        # Get vision model analysis
        response = await self.vision.generate_vision(
            model=self.model,
            prompt=prompt,
            image_b64=screenshot_b64,
            system=OBSERVER_SYSTEM_PROMPT,
        )

        return Observation(
            screenshot_b64=screenshot_b64,
            description=response,
            timestamp=datetime.now(),
        )

    async def find_element(self, description: str) -> Optional[Coordinates]:
        """
        Find a UI element by description and return its coordinates.

        Args:
            description: Natural language description of the element
                        (e.g., "the search button", "the video with most views")

        Returns:
            Coordinates of the element center, or None if not found
        """
        # Capture screenshot
        screenshot_b64 = self.capturer.capture_screen_b64()

        # Build prompt
        prompt = f"Find this element: {description}\nReturn its bounding box."

        # Get vision model response
        response = await self.vision.generate_vision(
            model=self.model,
            prompt=prompt,
            image_b64=screenshot_b64,
            system=ELEMENT_FINDER_SYSTEM_PROMPT,
        )

        # Parse coordinates from response
        return self._parse_coordinates(response)

    def _parse_coordinates(self, response: str) -> Optional[Coordinates]:
        """
        Parse bounding box coordinates from vision model response.

        Expected format: <box>(x1,y1,x2,y2)</box>
        Coordinates are normalized 0-1000.

        Returns:
            Coordinates in actual screen pixels, or None if parsing fails
        """
        # Check for NOT_FOUND
        if "NOT_FOUND" in response:
            return None

        # Extract box coordinates
        match = re.search(r"<box>\s*\(?\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)?\s*</box>", response)
        if not match:
            # Try alternative format without parentheses
            match = re.search(r"<box>\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*</box>", response)

        if not match:
            return None

        x1_raw = int(match.group(1))
        y1_raw = int(match.group(2))
        x2_raw = int(match.group(3))
        y2_raw = int(match.group(4))

        screen_width, screen_height = self._get_screen_size()
        coord_space = self._infer_coordinate_space(
            response=response,
            x1=x1_raw,
            y1=y1_raw,
            x2=x2_raw,
            y2=y2_raw,
            screen_width=screen_width,
            screen_height=screen_height,
        )

        if coord_space == "pixel":
            x1, y1, x2, y2 = x1_raw, y1_raw, x2_raw, y2_raw
        else:
            x1 = int(x1_raw * screen_width / 1000)
            y1 = int(y1_raw * screen_height / 1000)
            x2 = int(x2_raw * screen_width / 1000)
            y2 = int(y2_raw * screen_height / 1000)

        return Coordinates.from_bbox(x1, y1, x2, y2)

    def _infer_coordinate_space(
        self,
        response: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        screen_width: int,
        screen_height: int,
    ) -> str:
        """
        Infer whether coordinates are normalized (0-1000) or pixel-space.

        Heuristics:
        1) Explicit response hints ("normalized"/"pixel")
        2) Values > 1000 are treated as pixel-space
        3) On high-resolution screens, <=1000 defaults to normalized
        4) On small screens, <=1000 is treated as pixel-space
        """
        response_lower = response.lower()
        if "normalized" in response_lower:
            return "normalized"
        if "pixel" in response_lower:
            return "pixel"

        max_val = max(x1, y1, x2, y2)
        if max_val > 1000:
            return "pixel"

        if screen_width > 1000 or screen_height > 1000:
            return "normalized"

        return "pixel"

    async def check_condition(self, condition: str) -> bool:
        """
        Check if a condition is met on the current screen.

        Args:
            condition: Condition to check (e.g., "Is YouTube loaded?",
                      "Is a video playing?", "Is there an error message?")

        Returns:
            True if condition appears to be met, False otherwise
        """
        # Capture and analyze
        screenshot_b64 = self.capturer.capture_screen_b64()

        prompt = f"""Answer YES or NO: {condition}

Respond with ONLY "YES" or "NO", nothing else."""

        response = await self.vision.generate_vision(
            model=self.model,
            prompt=prompt,
            image_b64=screenshot_b64,
        )

        # Parse response
        response_upper = response.strip().upper()
        return "YES" in response_upper

    async def extract_elements(self, element_type: str) -> list:
        """
        Extract a list of elements of a specific type from the screen.

        Args:
            element_type: Type of elements to find (e.g., "videos", "links", "buttons")

        Returns:
            List of dictionaries with element info (title, description, etc.)
        """
        screenshot_b64 = self.capturer.capture_screen_b64()

        prompt = f"""List all {element_type} visible on this screen.

For each item, provide:
- title or text
- any numerical info (views, likes, etc.)
- approximate position (top/middle/bottom, left/center/right)

Format as a numbered list."""

        response = await self.vision.generate_vision(
            model=self.model,
            prompt=prompt,
            image_b64=screenshot_b64,
        )

        # Return raw response - caller can parse as needed
        return [{"raw_description": response}]
