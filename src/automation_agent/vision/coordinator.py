"""Screen coordinator implementation using vision models for element finding and verification."""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

import structlog

from automation_agent.config import AgentConfig
from automation_agent.logging.models import EventType
from automation_agent.protocols import CoordinatorCapability
from automation_agent.shared_models import FindElementResult
from automation_agent.vision.capture import ScreenCapture
from automation_agent.vision.geometry import fit_screen_into_image

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
    "gpt-4.1": "pixel",  # GPT computer-use returns pixel coords
    "gpt-4o": "pixel",
    "gpt-5.4": "pixel",
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
        event_logger: Optional[Any] = None,
    ):
        self.config = config
        self.capture = capture or ScreenCapture(config.screenshot_resolution)
        self._event_logger = event_logger
        self._validate_model()
        self._grounding_enabled = bool(config.grounding_model)
        # Last call's full prompt/response — read by agent for report logging
        self.last_prompt: str = ""
        self.last_response: str = ""

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
        # Gemini and OpenAI handle their own model routing — no coordinate space to validate
        if self.config.model_provider.value in ("gemini", "openai"):
            models = []
        elif self.config.model_provider.value == "anthropic":
            models = [self.config.vision_model, self.config.anthropic_vision_model]
        else:
            models = [self.config.vision_model]
        if self.config.grounding_model:
            models.append(self.config.grounding_model)
        return models

    def _get_active_model(self) -> str:
        """Return the model name actually used for vision calls based on provider.

        When model_provider is 'anthropic', the Anthropic vision model is used.
        When model_provider is 'gemini', the Gemini model is used.
        When model_provider is 'openai', the OpenAI model is used.
        Otherwise, the local vision model is used.
        """
        if self.config.model_provider.value == "anthropic":
            return self.config.anthropic_vision_model
        if self.config.model_provider.value == "gemini":
            return self.config.gemini_model
        if self.config.model_provider.value == "openai":
            return self.config.openai_model
        return self.config.vision_model

    @staticmethod
    def _resolve_coordinate_space(model: str) -> Optional[str]:
        """Resolve coordinate space for a model via case-insensitive prefix matching.

        Handles GGUF filenames like 'Qwen2.5-VL-7B-Instruct-q4_k_m.gguf'
        matching registry key 'qwen2.5-vl', and HuggingFace IDs like
        'mlx-community/Molmo-7B-D-0924-3bit' matching 'molmo'.

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
        # Strip HuggingFace org prefix (e.g. 'mlx-community/Molmo-...' -> 'molmo-...')
        if "/" in model_lower:
            basename = model_lower.split("/", 1)[1]
            if basename in COORDINATE_SPACES:
                return COORDINATE_SPACES[basename]
            for key, space in COORDINATE_SPACES.items():
                if basename.startswith(key):
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

        Includes out-of-range detection: if coordinates exceed the expected
        range for the model's coordinate space, auto-escalates to the next
        larger space (e.g., 0-100 → 0-1000) to avoid silent clamping bugs.

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

        # Out-of-range detection: if any coordinate exceeds the declared
        # range, the model likely outputted in a different coordinate space.
        # Escalate to the next plausible space rather than silently clamping.
        if space == "normalized_0_100" and (raw_x > 100 or raw_y > 100):
            logger.warning(
                "coordinate_range_exceeded",
                raw_x=raw_x,
                raw_y=raw_y,
                declared_space=space,
                escalated_to="normalized_0_1000",
                model=model,
            )
            space = "normalized_0_1000"
        elif space == "normalized_0_1" and (raw_x > 1 or raw_y > 1):
            if raw_x <= 100 and raw_y <= 100:
                space = "normalized_0_100"
            else:
                space = "normalized_0_1000"
            logger.warning(
                "coordinate_range_exceeded",
                raw_x=raw_x,
                raw_y=raw_y,
                declared_space="normalized_0_1",
                escalated_to=space,
                model=model,
            )

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

    async def _call_vision_model(
        self, prompt: str, screenshot_b64: str, step: Optional[str] = None,
    ) -> str:
        """Call the configured vision model with a prompt and screenshot.

        Args:
            prompt: The text prompt for the vision model.
            screenshot_b64: Base64-encoded screenshot image.
            step: Optional per-step routing key passed through to
                ``_call_vision_model_with_images``.

        Returns:
            The model's text response.
        """
        return await self._call_vision_model_with_images(
            prompt, [screenshot_b64], step=step,
        )

    async def _call_vision_model_with_images(
        self,
        prompt: str,
        screenshots_b64: Sequence[str],
        step: Optional[str] = None,
    ) -> str:
        """Call the configured vision model with one or more screenshots.

        Args:
            prompt: The text prompt for the vision model.
            screenshots_b64: One or more base64-encoded screenshots.
            step: Optional per-step routing key (e.g., 'grounding',
                'verification', 'screen_description'). When set, the
                provider is resolved via ``config.resolve_step_model(step)``
                instead of the global ``model_provider``.
        """
        self.last_prompt = prompt
        if step:
            provider, model = self.config.resolve_step_model(step)
        else:
            provider = self.config.model_provider.value
            model = ""
        if provider == "anthropic":
            result = await self._call_anthropic_vision(prompt, screenshots_b64, model=model)
        elif provider == "gemini":
            result = await self._call_gemini_vision(prompt, screenshots_b64, model=model)
        elif provider == "openai":
            result = await self._call_openai_vision(prompt, screenshots_b64, model=model)
        else:
            result = await self._call_local_vision(prompt, screenshots_b64, model=model)
        self.last_response = result
        return result

    async def _call_local_vision(
        self, prompt: str, screenshots_b64: Sequence[str], model: str = "",
    ) -> str:
        """Call local vision model via OpenAI-compatible API (e.g. llama.cpp server).

        Args:
            prompt: The text prompt.
            screenshots_b64: Base64-encoded screenshots.
            model: Resolved model name. Falls back to config.vision_model.

        Returns:
            The model's text response.
        """
        import httpx

        url = f"{self.config.vision_server_url}/v1/chat/completions"
        content = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{screenshot_b64}",
                },
            }
            for screenshot_b64 in screenshots_b64
        ]
        content.append(
            {
                "type": "text",
                "text": prompt,
            }
        )
        payload = {
            "model": model or self.config.vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
            "max_tokens": 1024,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self.config.vision_server_timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    async def _call_gemini_vision(
        self, prompt: str, screenshots_b64: Sequence[str], model: str = "",
    ) -> str:
        """Call Google Gemini vision model with retry on overloaded errors.

        Args:
            prompt: The text prompt.
            screenshots_b64: Base64-encoded screenshots.
            model: Resolved model name. Falls back to config.gemini_model.

        Returns:
            The model's text response.
        """
        import asyncio
        import base64

        from google import genai

        client = genai.Client(api_key=self.config.gemini_api_key)
        resolved_model = model or self.config.gemini_model

        max_retries = 4
        base_delay = 1.0

        parts = []
        for screenshot_b64 in screenshots_b64:
            image_bytes = base64.b64decode(screenshot_b64)
            parts.append(genai.types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))
        parts.append(prompt)

        for attempt in range(max_retries + 1):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=resolved_model,
                    contents=parts,
                    config=genai.types.GenerateContentConfig(
                        max_output_tokens=1024,
                        temperature=0.0,
                    ),
                )
                return response.text or ""
            except Exception as e:
                if attempt < max_retries and ("429" in str(e) or "503" in str(e)):
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "Gemini API error, retrying",
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        delay_s=delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

    async def _call_anthropic_vision(
        self, prompt: str, screenshots_b64: Sequence[str], model: str = "",
    ) -> str:
        """Call Anthropic vision model with retry on overloaded/rate-limit errors.

        Retries on 429/529 errors up to 4 times with exponential backoff.

        Args:
            prompt: The text prompt.
            screenshots_b64: Base64-encoded screenshots.
            model: Resolved model name. Falls back to config.anthropic_vision_model.

        Returns:
            The model's text response.
        """
        import asyncio

        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.config.anthropic_api_key)
        resolved_model = model or self.config.anthropic_vision_model

        max_retries = 4
        base_delay = 1.0

        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": screenshot_b64,
                },
            }
            for screenshot_b64 in screenshots_b64
        ]
        content.append(
            {
                "type": "text",
                "text": prompt,
            }
        )
        for attempt in range(max_retries + 1):
            try:
                message = await client.messages.create(
                    model=resolved_model,
                    max_tokens=1024,
                    messages=[
                        {
                            "role": "user",
                            "content": content,
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

    async def _call_openai_vision(
        self, prompt: str, screenshots_b64: Sequence[str], model: str = "",
    ) -> str:
        """Call OpenAI GPT vision model via the Responses API.

        Args:
            prompt: The text prompt.
            screenshots_b64: Base64-encoded screenshots.
            model: Resolved model name. Falls back to config.openai_model.

        Returns:
            The model's text response.
        """
        from automation_agent.llm.openai_client import OpenAIClient

        client = OpenAIClient(
            api_key=self.config.openai_api_key or "",
            model=model or self.config.openai_model,
            timeout=self.config.vision_server_timeout,
        )

        max_retries = 4
        base_delay = 1.0

        for attempt in range(max_retries + 1):
            try:
                return await client.generate_vision(prompt, screenshots_b64)
            except Exception as e:
                err_str = str(e)
                if attempt < max_retries and ("429" in err_str or "529" in err_str):
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        "OpenAI API error, retrying",
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

        self.last_prompt = prompt
        async with httpx.AsyncClient(timeout=self.config.vision_server_timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
            self.last_response = text
            return text

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
        patterns = [
            # x=N, y=N (comma separated)
            r'FOUND:\s*x\s*=\s*"?([0-9]*\.?[0-9]+)"?\s*,\s*y\s*=\s*"?([0-9]*\.?[0-9]+)"?'
            r'(?:\s*,?\s*confidence\s*=\s*"?([0-9]*\.?[0-9]+)"?)?',
            # x=N y=N (space separated, with y= label — Molmo v1 format)
            r'FOUND:\s*x\s*=\s*"?([0-9]*\.?[0-9]+)"?\s+y\s*=\s*"?([0-9]*\.?[0-9]+)"?'
            r'(?:\s*,?\s*confidence\s*=\s*"?([0-9]*\.?[0-9]+)"?)?',
            # x=N N (space separated, no y= label — Molmo2 fallback format)
            r'FOUND:\s*x\s*=\s*"?([0-9]*\.?[0-9]+)"?\s+"?([0-9]*\.?[0-9]+)"?'
            r'(?:\s*,?\s*confidence\s*=\s*"?([0-9]*\.?[0-9]+)"?)?',
        ]
        for pattern in patterns:
            match = re.search(pattern, response, re.IGNORECASE)
            if match:
                x = float(match.group(1))
                y = float(match.group(2))
                conf = float(match.group(3)) if match.lastindex and match.group(3) else 0.0
                return x, y, conf

        point_match = re.search(
            r'<point\b[^>]*\bx="([0-9]*\.?[0-9]+)"[^>]*\by="([0-9]*\.?[0-9]+)"[^>]*'
            r'(?:\bconfidence="([0-9]*\.?[0-9]+)")?[^>]*/?>',
            response,
            re.IGNORECASE,
        )
        if point_match:
            conf = float(point_match.group(3)) if point_match.group(3) else 0.0
            return float(point_match.group(1)), float(point_match.group(2)), conf

        points_match = re.search(
            r'<points\b[^>]*\bcoords="([^"]+)"[^>]*/?>',
            response,
            re.IGNORECASE,
        )
        if points_match:
            coords_str = points_match.group(1)
            triplet = re.search(r'([0-9]+)\s+([0-9]*\.?[0-9]+)\s+([0-9]*\.?[0-9]+)', coords_str)
            if triplet:
                return float(triplet.group(2)), float(triplet.group(3)), 0.0

        # <points x1="N" y1="N" x2="N" y2="N"> — Molmo bounding-box format.
        # Take the center of the bounding box as the click target.
        points_xy_match = re.search(
            r'<points\b[^>]*\bx1="([0-9]*\.?[0-9]+)"[^>]*\by1="([0-9]*\.?[0-9]+)"'
            r'(?:[^>]*\bx2="([0-9]*\.?[0-9]+)"[^>]*\by2="([0-9]*\.?[0-9]+)")?',
            response,
            re.IGNORECASE,
        )
        if points_xy_match:
            x1 = float(points_xy_match.group(1))
            y1 = float(points_xy_match.group(2))
            if points_xy_match.group(3) and points_xy_match.group(4):
                x2 = float(points_xy_match.group(3))
                y2 = float(points_xy_match.group(4))
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
            else:
                cx, cy = x1, y1
            return cx, cy, 0.0

        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            try:
                payload = json.loads(json_match.group(0))
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                point = payload.get("point") or payload.get("target") or payload
                if isinstance(point, dict) and "x" in point and "y" in point:
                    return (
                        float(point["x"]),
                        float(point["y"]),
                        float(point.get("confidence", 0.0) or 0.0),
                    )

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
        _find_start = time.monotonic()

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
                    _dur = int((time.monotonic() - _find_start) * 1000)
                    self._log_element_search(
                        element=description,
                        prompt_summary="accessibility lookup (no vision prompt)",
                        response="",
                        source="accessibility",
                        confidence=1.0,
                        duration_ms=_dur,
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

        # AC-15: SoM path — gate on config, candidates, and threshold
        if (
            self.config.som_enabled
            and candidates is not None
            and len(candidates) >= 3
        ):
            try:
                from automation_agent.vision.annotator import annotate_screenshot

                screen_size = self.capture.get_screen_size()
                _ann_start = time.monotonic()
                annotated_b64 = annotate_screenshot(
                    screenshot_b64, candidates, screen_size
                )
                _ann_ms = int((time.monotonic() - _ann_start) * 1000)
                logger.info(
                    "som_annotate",
                    element_count=len(candidates),
                    duration_ms=_ann_ms,
                )
                if self._event_logger:
                    self._event_logger.log_event(
                        EventType.SOM_ANNOTATE,
                        f"SoM annotated {len(candidates)} elements",
                        data={"element_count": len(candidates)},
                        duration_ms=_ann_ms,
                    )

                som_prompt = self._load_prompt("find_element_som.md")
                som_prompt = som_prompt.replace(
                    "{{element_description}}", description
                )
                som_prompt = som_prompt.replace(
                    "{{element_list}}",
                    self._build_candidate_prefix(candidates, description),
                )
                response = await self._call_vision_model(
                    som_prompt, annotated_b64, step="grounding",
                )

                # AC-13: parse element_number response
                result = self._parse_som_response(response, candidates, description)
                if result is not None:
                    _num_match = re.search(
                        r"element_number\s*=\s*(\d+)", response
                    )
                    logger.info(
                        "som_parse",
                        element_number=int(_num_match.group(1)) if _num_match else 0,
                        confidence=result.confidence,
                        source="som",
                    )
                    if self._event_logger:
                        self._event_logger.log_event(
                            EventType.SOM_PARSE,
                            f"SoM matched element #{int(_num_match.group(1)) if _num_match else '?'}",
                            data={
                                "element_number": int(_num_match.group(1)) if _num_match else 0,
                                "confidence": result.confidence,
                            },
                        )
                    _dur = int((time.monotonic() - _find_start) * 1000)
                    self._log_element_search(
                        element=description,
                        prompt_summary=som_prompt[:200],
                        response=response[:300] if response else "",
                        source="som",
                        confidence=result.confidence,
                        duration_ms=_dur,
                        screenshot_b64=annotated_b64,
                    )
                    return result

                # AC-14: SoM didn't match by number, try raw coordinate parsing
                raw_coords = self._parse_coordinates(response)
                if raw_coords is not None:
                    model = self._get_active_model()
                    w, h = self.config.screenshot_resolution
                    x, y = self._convert_coordinates(
                        raw_coords[0], raw_coords[1], model, w, h
                    )
                    _dur = int((time.monotonic() - _find_start) * 1000)
                    self._log_element_search(
                        element=description,
                        prompt_summary=som_prompt[:200],
                        response=response[:300] if response else "",
                        source="vision",
                        confidence=raw_coords[2],
                        duration_ms=_dur,
                        screenshot_b64=annotated_b64,
                    )
                    return FindElementResult(
                        x=x, y=y, confidence=raw_coords[2],
                        source="vision", raw_response=response,
                    )
            except Exception as e:
                logger.warning(
                    "som_error",
                    error=str(e),
                    element_count=len(candidates) if candidates else 0,
                )
                if self._event_logger:
                    self._event_logger.log_event(
                        EventType.SOM_ERROR,
                        f"SoM error: {e}",
                        data={"error": str(e)},
                    )
                # Fall through to standard grounding path below

        base_prompt = self._load_prompt("find_element.md").replace(
            "{{element_description}}", description
        )

        # Prepend structured candidate list if provided (cap at 20)
        if candidates:
            candidate_prefix = self._build_candidate_prefix(candidates, description)
            prompt = candidate_prefix + base_prompt
        else:
            prompt = base_prompt

        # Log letterbox geometry for GPT grounding debugging
        try:
            _screen_size = self.capture.get_screen_size()
            _letterbox = fit_screen_into_image(_screen_size, self.config.screenshot_resolution)
            logger.debug(
                "grounding_letterbox",
                screen_size=_screen_size,
                target_resolution=self.config.screenshot_resolution,
                content_left=round(_letterbox.left, 1),
                content_top=round(_letterbox.top, 1),
                content_width=round(_letterbox.width, 1),
                content_height=round(_letterbox.height, 1),
                scale=round(_letterbox.scale, 4),
            )
        except Exception:
            pass  # non-fatal debug logging

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
                    logger.info(
                        "👁️ Element found via grounding model",
                        description=description,
                        x=x,
                        y=y,
                        raw_x=raw_coords[0],
                        raw_y=raw_coords[1],
                        grounding_response=response[:200] if response else "(empty)",
                    )
                    _dur = int((time.monotonic() - _find_start) * 1000)
                    self._log_element_search(
                        element=description,
                        prompt_summary=prompt[:200],
                        response=response[:300] if response else "",
                        source="grounding",
                        confidence=conf,
                        duration_ms=_dur,
                        screenshot_b64=screenshot_b64,
                    )
                    return FindElementResult(
                        x=x, y=y, confidence=conf, source="grounding", raw_response=response
                    )
                logger.info(
                    "👁️ Grounding model returned no result, falling back to vision model",
                    grounding_response=response[:200] if response else "(empty)",
                )
            except Exception:
                logger.warning(
                    "👁️ Grounding model failed, falling back to vision model",
                    exc_info=True,
                )

        # Fall back to general vision model
        response = await self._call_vision_model(
            prompt, screenshot_b64, step="grounding",
        )

        raw_coords = self._parse_coordinates(response)
        if raw_coords is None:
            _dur = int((time.monotonic() - _find_start) * 1000)
            self._log_element_search(
                element=description,
                prompt_summary=prompt[:200],
                response=response[:300] if response else "",
                source="vision",
                confidence=0.0,
                duration_ms=_dur,
                screenshot_b64=screenshot_b64,
            )
            return None

        model = self._get_active_model()
        w, h = self.config.screenshot_resolution
        x, y = self._convert_coordinates(raw_coords[0], raw_coords[1], model, w, h)
        conf = raw_coords[2]

        _dur = int((time.monotonic() - _find_start) * 1000)
        self._log_element_search(
            element=description,
            prompt_summary=prompt[:200],
            response=response[:300] if response else "",
            source="vision",
            confidence=conf,
            duration_ms=_dur,
            screenshot_b64=screenshot_b64,
        )
        return FindElementResult(x=x, y=y, confidence=conf, source="vision", raw_response=response)

    def _log_element_search(
        self,
        element: str,
        prompt_summary: str,
        response: str,
        source: str,
        confidence: float,
        duration_ms: int,
        screenshot_b64: Optional[str] = None,
    ) -> None:
        """Log an ELEMENT_SEARCH event with grounding details.

        Saves the screenshot to the debug directory when an event logger
        is available and screenshot data is provided.
        """
        if not self._event_logger:
            return

        screenshot_path: Optional[str] = None
        if screenshot_b64:
            try:
                import base64 as _b64
                ts = int(time.time() * 1000)
                img_bytes = _b64.b64decode(screenshot_b64)
                screenshot_path = self._event_logger.save_screenshot(
                    img_bytes, f"grounding_{ts}",
                )
            except Exception:
                logger.debug(
                    "Failed to save grounding screenshot", exc_info=True
                )

        self._event_logger.log_event(
            EventType.ELEMENT_SEARCH,
            f"find_element('{element}') via {source} "
            f"conf={confidence:.2f} ({duration_ms}ms)",
            data={
                "element": element,
                "prompt_summary": prompt_summary[:200],
                "response": response[:300],
                "source": source,
                "confidence": confidence,
                "duration_ms": duration_ms,
            },
            screenshot_path=screenshot_path,
            duration_ms=duration_ms,
        )

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
        vision_description = await self._call_vision_model(
            prompt, screenshot_b64, step="screen_description",
        )
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
    ) -> Optional[bool]:
        """Check if a visual condition holds.

        Returns:
            True if confirmed, False if denied, None if inconclusive.
        """
        if screenshot_b64 is None:
            screenshot_b64 = self.capture.capture_b64()

        prompt = self._load_prompt("verify_condition.md").replace(
            "{{condition}}", condition
        )
        response = await self._call_vision_model(
            prompt, screenshot_b64, step="verification",
        )

        # AC-3: Ternary parsing — YES / UNCLEAR / anything else
        response_lower = response.strip().lower()
        if response_lower.startswith("yes"):
            result = True
        elif response_lower.startswith("unclear"):
            result = None
        else:
            result = False

        logger.info(
            "Vision verify",
            condition=condition,
            result=result,
        )
        return result

    async def verify_multiscale_target(
        self,
        target_description: str,
        detail_b64: str,
        context_b64: str,
    ) -> bool:
        """Verify a target using both a tight crop and a wider context crop."""
        prompt = (
            "You are validating a UI grounding target using two images of the same point.\n"
            "Image 1 is a tight detail crop centered on the proposed target.\n"
            "Image 2 is a wider context crop centered on the same point.\n\n"
            f"Target description: {target_description}\n\n"
            "Respond with ONLY YES if both images support that the centered target matches.\n"
            "Respond with ONLY NO otherwise."
        )
        response = await self._call_vision_model_with_images(
            prompt, [detail_b64, context_b64], step="verification",
        )
        return response.strip().lower().startswith("yes")

    async def reflect_action_outcome(
        self,
        action: str,
        params: Dict[str, Any],
        expected_observation: str,
        screenshot_b64: Optional[str] = None,
    ) -> Dict[str, str]:
        """Ask the vision model what actually happened after a failed action."""
        if screenshot_b64 is None:
            screenshot_b64 = self.capture.capture_b64()

        prompt = (
            "A desktop automation agent attempted an action and the explicit verification failed.\n"
            f"Action: {action}\n"
            f"Params: {params}\n"
            f"Expected observation: {expected_observation}\n\n"
            "Look at the screenshot and respond with STRICT JSON in this schema:\n"
            '{"worked":"yes|no","observed":"short summary","hint":"none|scroll_to_top|refine_target|dismiss_modal|refocus_text_field|keyboard_submit"}'
        )
        response = await self._call_vision_model(prompt, screenshot_b64)
        try:
            payload = json.loads(response.strip())
        except json.JSONDecodeError:
            payload = {}

        worked = str(payload.get("worked", "no")).strip().lower()
        hint = str(payload.get("hint", "none")).strip().lower()
        observed = str(payload.get("observed", response.strip())).strip()
        if hint not in {
            "none",
            "scroll_to_top",
            "refine_target",
            "dismiss_modal",
            "refocus_text_field",
            "keyboard_submit",
        }:
            hint = "none"
        return {
            "worked": "yes" if worked.startswith("y") else "no",
            "observed": observed,
            "hint": hint,
            "raw_response": response.strip(),
        }

    async def suggest_alternative_affordance(
        self,
        missing_target: str,
        task_goal: str,
        expected_observation: str,
        screenshot_b64: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """Suggest a visible fallback control when the requested target is absent."""
        if screenshot_b64 is None:
            screenshot_b64 = self.capture.capture_b64()

        prompt = (
            "A desktop automation agent could not find the requested UI control.\n"
            f"Missing target: {missing_target}\n"
            f"Task goal: {task_goal}\n"
            f"Expected observation after the intended action: {expected_observation}\n\n"
            "Inspect the screenshot and decide whether there is a DIFFERENT, clearly visible control "
            "on the same relevant card/section that best advances toward the same goal.\n"
            "Examples: View item, Order details, See return options.\n"
            "Do NOT suggest a control unless it is visibly present.\n"
            "Do NOT suggest generic keyboard actions.\n"
            "If no good visible control exists, say so.\n\n"
            "Respond with STRICT JSON in this schema:\n"
            '{"affordance":"visible label or empty","reason":"short summary","safe_to_try":"yes|no"}'
        )
        response = await self._call_vision_model(prompt, screenshot_b64)
        try:
            payload = json.loads(response.strip())
        except json.JSONDecodeError:
            return None

        affordance = str(payload.get("affordance", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        safe_to_try = str(payload.get("safe_to_try", "no")).strip().lower()
        if not affordance or safe_to_try not in {"yes", "y", "true"}:
            return None
        return {
            "affordance": affordance,
            "reason": reason,
            "safe_to_try": "yes",
            "raw_response": response.strip(),
        }

    async def capture_screenshot(self) -> str:
        """Capture and return base64 screenshot.

        Returns:
            Base64-encoded JPEG screenshot string.
        """
        return self.capture.capture_b64()

    def capabilities(self) -> FrozenSet[CoordinatorCapability]:
        """Advertise capabilities based on config and model support."""
        caps: set[CoordinatorCapability] = set()
        if self._supports_multi_image():
            caps.add(CoordinatorCapability.DUAL_RESOLUTION)
        if self._supports_vision_prediction():
            caps.add(CoordinatorCapability.LOOKAHEAD)
        caps.add(CoordinatorCapability.SOM)
        return frozenset(caps)

    def _supports_vision_prediction(self) -> bool:
        """Check if current vision backend supports text prediction prompts."""
        return True  # All VLM backends support text prompts

    def _supports_multi_image(self) -> bool:
        """Check if current vision backend supports multi-image input."""
        return hasattr(self, "_call_vision_model_with_images")

    async def find_element_dual(
        self,
        description: str,
        screenshot_b64: str,
        context_b64: str,
        candidates: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[FindElementResult]:
        """AC-21: Find element using dual-resolution images.

        Args:
            description: Element description.
            screenshot_b64: Detail crop (zoomed region).
            context_b64: Full-page overview.
            candidates: Optional AX candidates.

        Returns:
            FindElementResult or None.
        """
        prompt = self._load_prompt("find_element_dual.md").replace(
            "{{element_description}}", description
        )
        if candidates and len(candidates) >= 3:
            prefix = self._build_candidate_prefix(candidates, description)
            prompt = prefix + "\n\n" + prompt

        # Send both images with timeout protection (spec requirement)
        try:
            response = await asyncio.wait_for(
                self._call_vision_model_with_images(
                    prompt, [context_b64, screenshot_b64],
                    step="grounding",
                ),
                timeout=self.config.dual_res_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Dual-res VLM call timed out",
                timeout_s=self.config.dual_res_timeout_s,
                element=description,
            )
            return None

        raw_coords = self._parse_coordinates(response)
        if raw_coords is None:
            return None

        model = self._get_active_model()
        w, h = self.config.screenshot_resolution
        x, y = self._convert_coordinates(raw_coords[0], raw_coords[1], model, w, h)
        return FindElementResult(
            x=x, y=y, confidence=raw_coords[2],
            source="vision", raw_response=response,
        )

    def _parse_som_response(
        self,
        response: str,
        candidates: List[Dict[str, Any]],
        description: str = "",
    ) -> Optional[FindElementResult]:
        """AC-13: Parse FOUND: element_number=N response and map to AX element coords.

        Confidence capped at 0.85 (below _CRITICAL_CONFIDENCE_THRESHOLD of 0.9).
        """
        match = re.search(
            r"FOUND:\s*element_number\s*=\s*(\d+)"
            r"(?:,\s*confidence\s*=\s*([0-9.]+))?",
            response,
        )
        if not match:
            return None
        number = int(match.group(1))
        if number < 1 or number > len(candidates):
            return None

        _SOM_CONFIDENCE_CAP = 0.85
        raw_conf = float(match.group(2)) if match.group(2) else 0.8
        confidence = min(raw_conf, _SOM_CONFIDENCE_CAP)

        el = candidates[number - 1]  # 1-indexed

        # Security (finding 9): Cross-check AX element title against search description
        el_title = str(
            el.get("title", "") or el.get("description", "") or ""
        ).lower()
        if el_title and len(el_title) > 2:
            search_words = set(description.lower().split())
            el_words = set(el_title.split())
            if not search_words & el_words:
                confidence = min(confidence, 0.5)

        from automation_agent.vision.annotator import _extract_element_bounds

        cx, cy, _, _ = _extract_element_bounds(el)
        return FindElementResult(
            x=int(cx), y=int(cy),
            confidence=confidence,
            source="som",
            raw_response=response,
        )

    async def predict_action_outcome(
        self,
        action: str,
        params: Dict[str, Any],
        expected_observation: str,
        screenshot_b64: str,
        is_hard_destructive: bool = False,
    ) -> Dict[str, Any]:
        """AC-30: Predict what will happen after an action.

        Args:
            action: The action type (click, type_text, etc.).
            params: Action parameters.
            expected_observation: What the caller expects to see.
            screenshot_b64: Current screen as base64.
            is_hard_destructive: When True, parse failures use pessimistic default.

        Returns:
            Dict with likely_success, predicted_state, risk, mismatch_reason.
        """
        prompt = self._load_prompt("predict_outcome.md")
        prompt = prompt.replace("{{action}}", action)
        prompt = prompt.replace("{{params}}", str(params))
        prompt = prompt.replace("{{expected_observation}}", expected_observation)
        response = await self._call_vision_model(prompt, screenshot_b64)
        return self._parse_prediction_response(response, is_hard_destructive=is_hard_destructive)

    def _parse_prediction_response(
        self, response: str, is_hard_destructive: bool = False
    ) -> Dict[str, Any]:
        """Parse lookahead VLM response into structured prediction.

        Fallback behavior:
        - Non-destructive: optimistic default (likely_success=True)
        - Hard-destructive: pessimistic default (likely_success=False)
        """
        _OPTIMISTIC_DEFAULT: Dict[str, Any] = {
            "likely_success": True,
            "predicted_state": "",
            "risk": "",
            "mismatch_reason": "",
        }
        _PESSIMISTIC_DEFAULT: Dict[str, Any] = {
            "likely_success": False,
            "predicted_state": "",
            "risk": "Prediction unavailable for destructive action",
            "mismatch_reason": "VLM response unparseable; blocking as safety precaution",
        }
        _fallback = _PESSIMISTIC_DEFAULT if is_hard_destructive else _OPTIMISTIC_DEFAULT
        try:
            import json as _json

            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            parsed = _json.loads(text)
            if not isinstance(parsed, dict):
                return _fallback

            return {
                "likely_success": bool(parsed.get("likely_success", True)),
                "predicted_state": str(parsed.get("predicted_state", "")),
                "risk": str(parsed.get("risk", "")),
                "mismatch_reason": str(parsed.get("mismatch_reason", "")),
            }
        except (ValueError, KeyError, TypeError):
            fallback_type = "pessimistic" if is_hard_destructive else "optimistic"
            logger.debug(
                "Lookahead prediction parse failed, using %s default",
                fallback_type,
                exc_info=True,
            )
            return _fallback
