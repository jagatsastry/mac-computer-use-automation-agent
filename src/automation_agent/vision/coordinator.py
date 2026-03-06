"""Screen coordinator implementation using vision models for element finding and verification."""

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import structlog

from automation_agent.config import AgentConfig
from automation_agent.shared_models import FindElementResult
from automation_agent.vision.capture import ScreenCapture

logger = structlog.get_logger(__name__)

# Registry mapping model names to their coordinate output format.
# This is explicit — no heuristic guessing. If a model is not listed,
# we raise an error rather than silently misinterpret coordinates.
COORDINATE_SPACES: Dict[str, str] = {
    "molmo": "normalized_0_100",  # Molmo v1 returns 0-100 normalized coordinates
    "molmo2": "normalized_0_1000",  # Molmo2 returns 0-1000 normalized coordinates
    "qwen3-vl": "normalized_0_1000",  # Qwen3-VL returns 0-1000 normalized
    "qwen2.5-vl": "normalized_0_1000",  # Qwen2.5-VL returns 0-1000 normalized
    "qwen2-vl": "normalized_0_1000",  # Qwen2-VL returns 0-1000 normalized
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
        self,
        config: AgentConfig,
        capture: Optional[ScreenCapture] = None,
        accessibility: Optional[Any] = None,
    ):
        self.config = config
        self.capture = capture or ScreenCapture(config.screenshot_resolution)
        self._validate_model()
        self._grounding_enabled = bool(config.grounding_model)

        # Accessibility bridge: use provided instance, auto-create if enabled, or None.
        self.accessibility = accessibility
        if self.accessibility is None and config.use_accessibility:
            try:
                from automation_agent.perception.accessibility import AccessibilityBridge

                self.accessibility = AccessibilityBridge()
                logger.info("🔧 Accessibility bridge initialized")
            except Exception:
                logger.warning(
                    "🔧 Failed to initialize AccessibilityBridge, falling back to vision-only mode",
                    exc_info=True,
                )

    def _validate_model(self) -> None:
        """Ensure we know the coordinate space for our vision model.

        Raises ValueError for completely unknown models (no prefix match).
        Uses case-insensitive prefix matching to handle GGUF filenames
        (e.g. 'Qwen2.5-VL-7B-Instruct-q4_k_m.gguf' matches 'qwen2.5-vl').
        """
        for model in self._get_models_to_validate():
            if self._resolve_coordinate_space(model) is None:
                raise ValueError(
                    f"Vision model '{model}' not in COORDINATE_SPACES registry. "
                    f"Known models: {list(COORDINATE_SPACES.keys())}"
                )

    def _get_models_to_validate(self) -> list:
        """Return the list of model names that need validation."""
        models = [self.config.vision_model]
        if self.config.model_provider.value == "anthropic":
            models.append(self.config.anthropic_vision_model)
        if self.config.grounding_model:
            models.append(self.config.grounding_model)
        return models

    def _get_active_model(self) -> str:
        """Return the model name actually used for vision calls based on provider.

        When model_provider is 'anthropic', the Anthropic vision model is used.
        Otherwise, the local vision model is used.
        """
        if self.config.model_provider.value == "anthropic":
            return self.config.anthropic_vision_model
        return self.config.vision_model

    @staticmethod
    def _resolve_coordinate_space(model: str) -> Optional[str]:
        """Resolve coordinate space for a model via case-insensitive prefix matching.

        Handles GGUF filenames like 'Qwen2.5-VL-7B-Instruct-q4_k_m.gguf'
        matching registry key 'qwen2.5-vl'.

        Returns None if no match found.
        """
        model_lower = model.lower()
        # Exact match first
        if model_lower in COORDINATE_SPACES:
            return COORDINATE_SPACES[model_lower]
        # Case-insensitive prefix match
        for key, space in COORDINATE_SPACES.items():
            if model_lower.startswith(key):
                return space
        return None

    def _get_coordinate_space(self, model: str) -> str:
        """Get the coordinate space for a model.

        Args:
            model: The model name to look up.

        Returns:
            The coordinate space string.

        Raises:
            ValueError: If the model is not in the registry.
        """
        space = self._resolve_coordinate_space(model)
        if space is None:
            raise ValueError(
                f"Unknown coordinate space for model '{model}'. "
                f"Known models: {list(COORDINATE_SPACES.keys())}"
            )
        return space

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
            x = min(int(raw_x * screen_width), screen_width - 1)
            y = min(int(raw_y * screen_height), screen_height - 1)
            return x, y
        elif space == "normalized_0_100":
            x = min(int(raw_x / 100.0 * screen_width), screen_width - 1)
            y = min(int(raw_y / 100.0 * screen_height), screen_height - 1)
            return x, y
        elif space == "normalized_0_1000":
            x = min(int(raw_x / 1000 * screen_width), screen_width - 1)
            y = min(int(raw_y / 1000 * screen_height), screen_height - 1)
            return x, y
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
            return await self._call_local_vision(prompt, screenshot_b64)

    async def _call_local_vision(self, prompt: str, screenshot_b64: str) -> str:
        """Call local vision model via OpenAI-compatible API (e.g. llama.cpp server).

        Args:
            prompt: The text prompt.
            screenshot_b64: Base64-encoded screenshot.

        Returns:
            The model's text response.
        """
        import httpx

        url = f"{self.config.vision_server_url}/v1/chat/completions"
        payload = {
            "model": self.config.vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{screenshot_b64}",
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self.config.vision_server_timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    async def _call_anthropic_vision(self, prompt: str, screenshot_b64: str) -> str:
        """Call Anthropic vision model with retry on overloaded/rate-limit errors.

        Retries on 429/529 errors up to 4 times with exponential backoff.

        Args:
            prompt: The text prompt.
            screenshot_b64: Base64-encoded screenshot.

        Returns:
            The model's text response.
        """
        import asyncio

        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.config.anthropic_api_key)

        max_retries = 4
        base_delay = 1.0

        for attempt in range(max_retries + 1):
            try:
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
            except anthropic.APIStatusError as e:
                if e.status_code in (429, 529) and attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "🔄 Anthropic API error, retrying",
                        status_code=e.status_code,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        delay_s=delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

    async def _call_grounding_model(self, prompt: str, screenshot_b64: str) -> str:
        """Call dedicated grounding model via OpenAI-compatible API.

        Uses grounding_server_url if configured, otherwise falls back to
        the general vision_server_url.

        Args:
            prompt: The text prompt.
            screenshot_b64: Base64-encoded screenshot.

        Returns:
            The model's text response.
        """
        import httpx

        base_url = (
            self.config.grounding_server_url
            if self.config.grounding_server_url
            else self.config.vision_server_url
        )
        url = f"{base_url}/v1/chat/completions"
        payload = {
            "model": self.config.grounding_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{screenshot_b64}",
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self.config.vision_server_timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    def _parse_coordinates(self, response: str) -> Optional[Tuple[float, float, float]]:
        """Parse coordinates and optional confidence from a vision model response.

        Expects either:
            FOUND: x=<number>, y=<number> [, confidence=<0.0-1.0>]
        or:
            NOT_FOUND

        Args:
            response: The raw text response from the vision model.

        Returns:
            Tuple of (x, y, confidence) raw coordinates, or None if not found.
            confidence defaults to 0.0 if not present in response.
        """
        response = response.strip()
        if response.upper().startswith("NOT_FOUND"):
            return None

        # Match "FOUND: x=<number>, y=<number>" with optional confidence
        match = re.search(
            r"FOUND:\s*x\s*=\s*([0-9]*\.?[0-9]+)\s*,\s*y\s*=\s*([0-9]*\.?[0-9]+)"
            r"(?:\s*,?\s*confidence\s*=\s*([0-9]*\.?[0-9]+))?",
            response,
            re.IGNORECASE,
        )
        if match:
            x = float(match.group(1))
            y = float(match.group(2))
            conf = float(match.group(3)) if match.group(3) else 0.0
            return x, y, conf

        return None

    def _build_candidate_prefix(self, candidates: list, description: str) -> str:
        """Build a structured candidate list prefix for the vision prompt.

        Caps the list at 20 elements to avoid context overflow.
        Truncates labels longer than 80 characters.

        Args:
            candidates: List of accessibility element dicts.
            description: The element being searched for.

        Returns:
            Formatted string to prepend to the vision prompt.
        """
        capped = candidates[:20]
        lines = ["The following interactive UI elements are visible on screen:"]
        for i, elem in enumerate(capped, start=1):
            label = str(elem.get("label", ""))[:80]
            role = elem.get("role", "")
            cx = elem.get("center_x", elem.get("x", 0))
            cy = elem.get("center_y", elem.get("y", 0))
            lines.append(f'{i}. "{label}" ({role}) at center ({cx}, {cy})')
        lines.append("")
        lines.append(f'Which element best matches: "{description}"?')
        lines.append(
            "If one of the numbered elements matches, respond: FOUND: x=<center_x>, y=<center_y>"
        )
        lines.append("If none match, use the screenshot to locate the element.")
        lines.append("")
        return "\n".join(lines)

    async def find_element(
        self,
        description: str,
        screenshot_b64: Optional[str] = None,
        candidates: Optional[list] = None,
    ) -> Optional[FindElementResult]:
        """Find UI element by description.

        Strategy (in order):
        1. Accessibility API (instant, accurate, logical coords)
        2. Grounding model if configured
        3. General vision model (slowest fallback)

        Args:
            description: Natural language description of the element to find.
            screenshot_b64: Optional pre-captured screenshot. If None, captures one.
            candidates: Optional list of accessibility candidate dicts from
                get_accessibility_elements(). When non-empty, a structured
                candidate list is prepended to the vision prompt to reduce
                search ambiguity. Falls back to raw vision when empty or None.

        Returns:
            FindElementResult with x, y pixel coordinates, confidence, and source,
            or None if the element could not be found.
        """
        # FAST PATH: Accessibility API
        if self.accessibility is not None:
            try:
                ax_element = self.accessibility.find_element_by_description(description)
                if ax_element and ax_element.center:
                    cx, cy = ax_element.center
                    logger.debug(
                        "👁️ Accessibility hit",
                        description=description,
                        x=cx,
                        y=cy,
                    )
                    return FindElementResult(x=cx, y=cy, confidence=1.0, source="accessibility")
            except Exception:
                logger.debug(
                    "👁️ Accessibility lookup failed, falling back to vision",
                    description=description,
                    exc_info=True,
                )

        # SLOW PATH: Vision model
        if screenshot_b64 is None:
            screenshot_b64 = self.capture.capture_b64()

        base_prompt = self._load_prompt("find_element.md").replace(
            "{{element_description}}", description
        )

        # Prepend structured candidate list if provided (cap at 20)
        if candidates:
            candidate_prefix = self._build_candidate_prefix(candidates, description)
            prompt = candidate_prefix + base_prompt
        else:
            prompt = base_prompt

        # Try grounding model first if configured
        if self._grounding_enabled:
            try:
                response = await self._call_grounding_model(prompt, screenshot_b64)
                raw_coords = self._parse_coordinates(response)
                if raw_coords is not None:
                    model = self.config.grounding_model
                    w, h = self.config.screenshot_resolution
                    x, y = self._convert_coordinates(
                        raw_coords[0], raw_coords[1], model, w, h
                    )
                    conf = raw_coords[2]
                    logger.info("👁️ Element found via grounding model", description=description, x=x, y=y)
                    return FindElementResult(
                        x=x, y=y, confidence=conf, source="vision", raw_response=response
                    )
                logger.info(
                    "👁️ Grounding model returned no result, falling back to vision model"
                )
            except Exception:
                logger.warning(
                    "👁️ Grounding model failed, falling back to vision model",
                    exc_info=True,
                )

        # Fall back to general vision model
        response = await self._call_vision_model(prompt, screenshot_b64)

        raw_coords = self._parse_coordinates(response)
        if raw_coords is None:
            return None

        model = self._get_active_model()
        w, h = self.config.screenshot_resolution
        x, y = self._convert_coordinates(raw_coords[0], raw_coords[1], model, w, h)
        conf = raw_coords[2]

        return FindElementResult(x=x, y=y, confidence=conf, source="vision", raw_response=response)

    async def describe_screen(
        self,
        screenshot_b64: Optional[str] = None,
        desktop_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Describe current screen state, optionally merging desktop state.

        Args:
            screenshot_b64: Optional pre-captured screenshot. If None, captures one.
            desktop_state: Optional dict with keys like 'app_name', 'window_title'
                from the actuator. If provided, this info is prepended to the description.

        Returns:
            Natural language description of the screen.
        """
        if screenshot_b64 is None:
            screenshot_b64 = self.capture.capture_b64()

        prompt = self._load_prompt("describe_screen.md")
        vision_description = await self._call_vision_model(prompt, screenshot_b64)
        logger.info("👁️ Screen described", length=len(vision_description))

        if desktop_state:
            app_name = desktop_state.get("app_name", "Unknown")
            window_title = desktop_state.get("window_title", "Unknown")
            return (
                f"Frontmost app: {app_name} (window: '{window_title}'). "
                f"{vision_description}"
            )

        return vision_description

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
        result = response_lower.startswith("yes")
        emoji = "✅" if result else "❌"
        logger.info(f"{emoji} Vision verify", condition=condition, result=result)
        return result

    async def capture_screenshot(self) -> str:
        """Capture and return base64 screenshot.

        Returns:
            Base64-encoded JPEG screenshot string.
        """
        return self.capture.capture_b64()
