"""OpenAI GPT client for vision grounding, screen description, and planning.

Uses the OpenAI Responses API (``/v1/responses``) with the ``computer`` tool
for pixel-level grounding, and standard text/image input for discovery,
verification, and planning.

The client communicates via ``httpx`` (already a project dependency) -- the
``openai`` SDK is not required.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import structlog

from automation_agent.llm.exceptions import InvalidResponseError, ModelTimeoutError

logger = structlog.get_logger(__name__)

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

# Maximum computer-call loop iterations when waiting for a click coordinate.
_MAX_COMPUTER_CALL_ITERATIONS = 3


def _to_data_url(image_b64: str, mime_type: str = "image/jpeg") -> str:
    return f"data:{mime_type};base64,{image_b64}"


# ---------------------------------------------------------------------------
# Response parsing helpers (mirrors the TypeScript reference implementation)
# ---------------------------------------------------------------------------


def extract_text(response: Dict[str, Any]) -> str:
    """Extract text content from an OpenAI Responses API response."""
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = response.get("output")
    if not isinstance(output, list):
        return ""

    parts: List[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("text"), str):
            parts.append(item["text"])
        content = item.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
    return "\n".join(parts)


def extract_computer_point(response: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """Extract (x, y) pixel coordinates from a ``computer_call`` output.

    Handles click, double_click, move, and drag action types.
    """
    output = response.get("output")
    if not isinstance(output, list):
        return None

    for item in output:
        if not isinstance(item, dict) or item.get("type") != "computer_call":
            continue
        actions = item.get("actions")
        if not isinstance(actions, list):
            # Fallback: check item-level action (single-action shape)
            action = item.get("action")
            if isinstance(action, dict):
                actions = [action]
            else:
                continue
        for action in actions:
            if not isinstance(action, dict):
                continue
            atype = action.get("type", "")
            if atype not in ("click", "double_click", "move", "drag"):
                continue
            x = action.get("x")
            y = action.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                return int(x), int(y)
    return None


def extract_computer_call_id(response: Dict[str, Any]) -> Optional[str]:
    """Extract the ``call_id`` from a ``computer_call`` output item."""
    output = response.get("output")
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "computer_call":
            continue
        call_id = item.get("call_id")
        if isinstance(call_id, str):
            return call_id
    return None


def _parse_point_from_text(text: str) -> Optional[Tuple[int, int]]:
    """Fallback: try to parse pixel coordinates from freeform text."""
    keyed = re.search(
        r"(?:FOUND:\s*)?x\s*=\s*\"?([0-9]+(?:\.[0-9]+)?)\"?"
        r"\s*[, ]+\s*y\s*=\s*\"?([0-9]+(?:\.[0-9]+)?)\"?",
        text,
        re.IGNORECASE,
    )
    if keyed:
        return int(float(keyed.group(1))), int(float(keyed.group(2)))

    tup = re.search(
        r"\(?\s*([0-9]+(?:\.[0-9]+)?)\s*[, ]\s*([0-9]+(?:\.[0-9]+)?)\s*\)?",
        text,
    )
    if tup:
        return int(float(tup.group(1))), int(float(tup.group(2)))
    return None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OpenAIClient:
    """Async client wrapping the OpenAI Responses API for the automation agent.

    Provides:
    - ``find_element`` -- pixel-level grounding via the ``computer`` tool
    - ``describe_screen`` -- screen description via vision input
    - ``verify_condition`` -- visual condition verification
    - ``plan`` -- action plan generation (text-only)
    - ``generate_text`` -- generic text generation for planning
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4.1",
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    # -- low-level helpers --------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST to the Responses API and return the parsed JSON body."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(
                    OPENAI_RESPONSES_URL,
                    json=payload,
                    headers=self._headers(),
                )
            except httpx.TimeoutException:
                raise ModelTimeoutError("OpenAI Responses API request timed out")
            if resp.status_code == 429:
                raise ModelTimeoutError(
                    f"OpenAI rate-limited (429): {resp.text[:300]}"
                )
            if resp.status_code >= 400:
                raise InvalidResponseError(
                    f"OpenAI API error {resp.status_code}: {resp.text[:500]}"
                )
            return resp.json()

    # -- public API ---------------------------------------------------------

    async def find_element(
        self,
        description: str,
        screenshot_b64: str,
        image_width: int,
        image_height: int,
    ) -> Optional[Tuple[int, int, float]]:
        """Locate a UI element in a screenshot using GPT computer-use grounding.

        Returns ``(x, y, confidence)`` pixel coordinates, or ``None`` if
        the element could not be found. Confidence is 0.8 for a computer-use
        hit (GPT doesn't report confidence) and 0.5 for a text-parsed fallback.
        """
        grounding_prompt = (
            "Point to the exact center of the described UI element in the screenshot. "
            f'Target: "{description}". '
            "Use the computer tool for grounding. "
            "Do not scroll, type, or open anything. "
            "If the target is visible, emit a single pointer action on that target. "
            "If the target is not visible, reply with NOT_FOUND."
        )

        # Initial request with computer tool
        payload: Dict[str, Any] = {
            "input": grounding_prompt,
            "model": self.model,
            "tools": [{"type": "computer"}],
        }
        response = await self._post(payload)

        for _attempt in range(_MAX_COMPUTER_CALL_ITERATIONS):
            point = extract_computer_point(response)
            if point is not None:
                return point[0], point[1], 0.8

            response_id = response.get("id", "")
            call_id = extract_computer_call_id(response)

            if not response_id or not call_id:
                # No computer_call -- try text fallback
                text = extract_text(response)
                if "NOT_FOUND" in text.upper():
                    return None
                fallback = _parse_point_from_text(text)
                if fallback is not None:
                    return fallback[0], fallback[1], 0.5
                return None

            # Feed screenshot back for the computer call.
            # detail:"original" preserves coordinate accuracy per OpenAI docs —
            # without it, the API may downscale and return offset coordinates.
            payload = {
                "input": [
                    {
                        "type": "computer_call_output",
                        "call_id": call_id,
                        "output": {
                            "type": "computer_screenshot",
                            "image_url": _to_data_url(screenshot_b64),
                            "detail": "original",
                        },
                    }
                ],
                "model": self.model,
                "previous_response_id": response_id,
                "tools": [{"type": "computer"}],
            }
            response = await self._post(payload)

        # Final attempt: parse text from the last response
        text = extract_text(response)
        if "NOT_FOUND" in text.upper():
            return None
        fallback = _parse_point_from_text(text)
        if fallback is not None:
            return fallback[0], fallback[1], 0.5
        return None

    async def describe_screen(self, screenshot_b64: str) -> str:
        """Describe the current screen state."""
        payload: Dict[str, Any] = {
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Describe the current screen state concisely. "
                                "List the frontmost application, visible windows, "
                                "and key interactive elements."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": _to_data_url(screenshot_b64),
                        },
                    ],
                }
            ],
            "model": self.model,
            "max_output_tokens": 1024,
        }
        response = await self._post(payload)
        return extract_text(response)

    async def verify_condition(
        self,
        condition: str,
        screenshot_b64: str,
    ) -> Optional[bool]:
        """Verify a visual condition on screen.

        Returns True / False / None (inconclusive).
        """
        payload: Dict[str, Any] = {
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Look at this screenshot and answer strictly.\n"
                                f"Condition: {condition}\n"
                                "Reply with ONLY one of: YES, NO, or UNCLEAR."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": _to_data_url(screenshot_b64),
                        },
                    ],
                }
            ],
            "model": self.model,
            "max_output_tokens": 64,
        }
        response = await self._post(payload)
        text = extract_text(response).strip().lower()
        if text.startswith("yes"):
            return True
        if text.startswith("unclear"):
            return None
        return False

    async def generate_text(self, prompt: str) -> Dict[str, Any]:
        """Generate text (for planning). Returns dict with 'content' and 'usage'."""
        payload: Dict[str, Any] = {
            "input": [{"role": "user", "content": prompt}],
            "model": self.model,
            "max_output_tokens": 8192,
        }

        max_retries = 4
        base_delay = 1.0

        for attempt in range(max_retries + 1):
            try:
                response = await self._post(payload)
                text = extract_text(response)
                usage = response.get("usage", {})
                return {
                    "content": text,
                    "usage": {
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                    },
                }
            except ModelTimeoutError:
                if attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    await asyncio.sleep(delay)
                    continue
                raise

    async def generate_vision(
        self,
        prompt: str,
        screenshots_b64: Sequence[str],
    ) -> str:
        """Generate a vision response with one or more screenshots."""
        content: List[Dict[str, Any]] = [
            {"type": "input_text", "text": prompt},
        ]
        for img in screenshots_b64:
            # detail:"high" prevents OpenAI from downscaling the image,
            # preserving coordinate accuracy for grounding tasks.
            content.append(
                {
                    "type": "input_image",
                    "image_url": _to_data_url(img),
                    "detail": "high",
                }
            )

        payload: Dict[str, Any] = {
            "input": [{"role": "user", "content": content}],
            "model": self.model,
            "max_output_tokens": 1024,
        }
        response = await self._post(payload)
        return extract_text(response)
