"""Screen coordinator implementation using vision models for element finding and verification."""

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from automation_agent.config import AgentConfig
from automation_agent.vision.capture import ScreenCapture

logger = logging.getLogger(__name__)

# Registry mapping model names to their coordinate output format.
# This is explicit — no heuristic guessing. If a model is not listed,
# we raise an error rather than silently misinterpret coordinates.
COORDINATE_SPACES: Dict[str, str] = {
    "molmo": "normalized_0_1",  # Molmo returns 0.0-1.0 normalized
    "qwen3-vl": "normalized_0_1000",  # Qwen returns 0-1000 normalized
    "claude-sonnet-4-20250514": "pixel",  # Claude returns pixel coords
}

# Directory containing prompt template files
_PROMPTS_DIR = Path(__file__).parent / "prompts"


class ScreenCoordinatorImpl:
    """Coordinates vision model queries for screen understanding.

    Implements the ScreenCoordinator protocol from automation_agent.protocols.

    Key design principle: coordinate space is explicitly registered per model.
    No heuristic guessing — if we don't know the coordinate format for a model,
    we raise ValueError rather than silently producing wrong coordinates.
    """

    def __init__(
        self, config: AgentConfig, capture: Optional[ScreenCapture] = None
    ):
        self.config = config
        self.capture = capture or ScreenCapture(config.screenshot_resolution)
        self._validate_model()

    def _validate_model(self) -> None:
        """Ensure we know the coordinate space for our vision model.

        Logs a warning if the model is not in the coordinate space registry,
        but does not raise — the error is deferred to coordinate conversion time.
        """
        model = self.config.vision_model
        if model not in COORDINATE_SPACES:
            # Check if it matches a known model prefix
            known = any(model.startswith(k) for k in COORDINATE_SPACES)
            if not known:
                logger.warning(
                    "Vision model '%s' not in COORDINATE_SPACES registry. "
                    "Coordinate conversion will fail unless the model is added. "
                    "Known models: %s",
                    model,
                    list(COORDINATE_SPACES.keys()),
                )

    def _get_coordinate_space(self, model: str) -> str:
        """Get the coordinate space for a model, checking exact match then prefix match.

        Args:
            model: The model name to look up.

        Returns:
            The coordinate space string.

        Raises:
            ValueError: If the model is not in the registry.
        """
        # Exact match first
        if model in COORDINATE_SPACES:
            return COORDINATE_SPACES[model]

        # Prefix match (e.g., "molmo-7b" matches "molmo")
        for key, space in COORDINATE_SPACES.items():
            if model.startswith(key):
                return space

        raise ValueError(
            f"Unknown coordinate space for model '{model}'. "
            f"Known models: {list(COORDINATE_SPACES.keys())}"
        )

    def _convert_coordinates(
        self,
        raw_x: float,
        raw_y: float,
        model: str,
        screen_width: int,
        screen_height: int,
    ) -> Tuple[int, int]:
        """Convert model-specific coordinates to pixel coordinates.

        Args:
            raw_x: Raw x coordinate from the model.
            raw_y: Raw y coordinate from the model.
            model: Model name to determine coordinate space.
            screen_width: Target screen width in pixels.
            screen_height: Target screen height in pixels.

        Returns:
            Tuple of (x, y) pixel coordinates.

        Raises:
            ValueError: If the model's coordinate space is unknown.
        """
        space = self._get_coordinate_space(model)

        if space == "normalized_0_1":
            return int(raw_x * screen_width), int(raw_y * screen_height)
        elif space == "normalized_0_1000":
            return int(raw_x / 1000 * screen_width), int(raw_y / 1000 * screen_height)
        elif space == "pixel":
            return int(raw_x), int(raw_y)
        else:
            raise ValueError(f"Unknown coordinate space type: {space}")

    def _load_prompt(self, filename: str) -> str:
        """Load a prompt template from the prompts directory.

        Args:
            filename: Name of the prompt file (e.g., 'find_element.md').

        Returns:
            The prompt template text.
        """
        prompt_path = _PROMPTS_DIR / filename
        return prompt_path.read_text()

    async def _call_vision_model(self, prompt: str, screenshot_b64: str) -> str:
        """Call the configured vision model with a prompt and screenshot.

        This method dispatches to the appropriate API based on config.model_provider.

        Args:
            prompt: The text prompt for the vision model.
            screenshot_b64: Base64-encoded screenshot image.

        Returns:
            The model's text response.
        """
        if self.config.model_provider.value == "anthropic":
            return await self._call_anthropic_vision(prompt, screenshot_b64)
        else:
            return await self._call_ollama_vision(prompt, screenshot_b64)

    async def _call_ollama_vision(self, prompt: str, screenshot_b64: str) -> str:
        """Call Ollama vision model.

        Args:
            prompt: The text prompt.
            screenshot_b64: Base64-encoded screenshot.

        Returns:
            The model's text response.
        """
        import httpx

        url = f"{self.config.ollama_host}/api/generate"
        payload = {
            "model": self.config.vision_model,
            "prompt": prompt,
            "images": [screenshot_b64],
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self.config.ollama_timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()["response"]

    async def _call_anthropic_vision(self, prompt: str, screenshot_b64: str) -> str:
        """Call Anthropic vision model.

        Args:
            prompt: The text prompt.
            screenshot_b64: Base64-encoded screenshot.

        Returns:
            The model's text response.
        """
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.config.anthropic_api_key)
        message = await client.messages.create(
            model=self.config.anthropic_vision_model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": screenshot_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        )
        return message.content[0].text

    def _parse_coordinates(self, response: str) -> Optional[Tuple[float, float]]:
        """Parse coordinates from a vision model response.

        Expects either:
            FOUND: x=<number>, y=<number>
        or:
            NOT_FOUND

        Args:
            response: The raw text response from the vision model.

        Returns:
            Tuple of (x, y) raw coordinates, or None if not found.
        """
        response = response.strip()
        if response.upper().startswith("NOT_FOUND"):
            return None

        # Match "FOUND: x=<number>, y=<number>" pattern
        match = re.search(
            r"FOUND:\s*x\s*=\s*([0-9]*\.?[0-9]+)\s*,\s*y\s*=\s*([0-9]*\.?[0-9]+)",
            response,
            re.IGNORECASE,
        )
        if match:
            return float(match.group(1)), float(match.group(2))

        return None

    async def find_element(
        self, description: str, screenshot_b64: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Find UI element by description using vision model.

        Args:
            description: Natural language description of the element to find.
            screenshot_b64: Optional pre-captured screenshot. If None, captures one.

        Returns:
            Dict with 'x', 'y' pixel coordinates and 'raw_response', or None if not found.
        """
        if screenshot_b64 is None:
            screenshot_b64 = self.capture.capture_b64()

        prompt = self._load_prompt("find_element.md").replace(
            "{{element_description}}", description
        )
        response = await self._call_vision_model(prompt, screenshot_b64)

        raw_coords = self._parse_coordinates(response)
        if raw_coords is None:
            return None

        model = self.config.vision_model
        w, h = self.config.screenshot_resolution
        x, y = self._convert_coordinates(raw_coords[0], raw_coords[1], model, w, h)

        return {"x": x, "y": y, "raw_response": response}

    async def describe_screen(
        self, screenshot_b64: Optional[str] = None
    ) -> str:
        """Describe current screen state.

        Args:
            screenshot_b64: Optional pre-captured screenshot. If None, captures one.

        Returns:
            Natural language description of the screen.
        """
        if screenshot_b64 is None:
            screenshot_b64 = self.capture.capture_b64()

        prompt = self._load_prompt("describe_screen.md")
        return await self._call_vision_model(prompt, screenshot_b64)

    async def verify_condition(
        self, condition: str, screenshot_b64: Optional[str] = None
    ) -> bool:
        """Check if a visual condition holds. Conservative: ambiguous = False.

        Args:
            condition: The condition to verify (e.g., "Safari is open").
            screenshot_b64: Optional pre-captured screenshot. If None, captures one.

        Returns:
            True only if the model clearly responds YES.
        """
        if screenshot_b64 is None:
            screenshot_b64 = self.capture.capture_b64()

        prompt = self._load_prompt("verify_condition.md").replace(
            "{{condition}}", condition
        )
        response = await self._call_vision_model(prompt, screenshot_b64)

        # Parse YES/NO response, conservative default
        response_lower = response.strip().lower()
        return response_lower.startswith("yes")

    async def capture_screenshot(self) -> str:
        """Capture and return base64 screenshot.

        Returns:
            Base64-encoded JPEG screenshot string.
        """
        return self.capture.capture_b64()
