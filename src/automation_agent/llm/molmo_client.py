"""Molmo vision client using OpenRouter-compatible API."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from .exceptions import ModelNotFoundError, ModelTimeoutError


class MolmoVisionClient:
    """
    Async client for Molmo vision inference over OpenRouter-compatible chat API.

    This client is intended for vision grounding with Molmo models. It supports
    the same `generate` / `generate_vision` interface pattern used by the
    existing LLM clients so it can be dropped into the observer path.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "allenai/molmo-2-8b:free",
        timeout: float = 120.0,
        base_url: str = "https://openrouter.ai/api/v1",
        app_name: str = "macos-automation-agent",
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name

    async def test_connection(self) -> bool:
        """Test if Molmo endpoint is reachable with current API key/model."""
        try:
            _ = await self.generate(
                model=self.model,
                prompt="Reply with OK only.",
            )
            return True
        except Exception:
            return False

    async def check_model_available(self, model_name: str) -> bool:
        """
        Check model availability.

        OpenRouter model listing can vary by account tier; this performs a
        best-effort quick call.
        """
        try:
            _ = await self.generate(model=model_name, prompt="Reply with OK only.")
            return True
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        """Return a best-effort list containing configured model."""
        return [self.model]

    async def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        format: Optional[str] = None,
    ) -> str:
        """Generate text completion."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: Dict[str, object] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
        }
        # OpenRouter supports response_format for JSON mode with some providers.
        if format == "json":
            payload["response_format"] = {"type": "json_object"}

        data = await self._post_json("/chat/completions", payload)
        return self._extract_text(data)

    async def generate_vision(
        self,
        model: str,
        prompt: str,
        image_b64: str,
        system: Optional[str] = None,
        options: Optional[Dict] = None,
    ) -> str:
        """Generate completion from prompt + image."""
        content: List[Dict[str, object]] = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
            },
        ]

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})

        payload: Dict[str, object] = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
        }
        if options:
            payload.update(options)

        data = await self._post_json("/chat/completions", payload)
        return self._extract_text(data)

    async def _post_json(self, path: str, payload: Dict[str, object]) -> Dict:
        """POST JSON payload and return parsed JSON response."""

        def _request() -> Dict:
            url = f"{self.base_url}{path}"
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://localhost",
                    "X-Title": self.app_name,
                },
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw)

        try:
            return await asyncio.wait_for(asyncio.to_thread(_request), timeout=self.timeout + 2)
        except asyncio.TimeoutError:
            raise ModelTimeoutError("Molmo request timed out")
        except urllib.error.HTTPError as e:
            msg = ""
            try:
                msg = e.read().decode("utf-8")
            except Exception:
                msg = str(e)
            lowered = msg.lower()
            if "model not found" in lowered or "no endpoints found" in lowered:
                raise ModelNotFoundError(f"Model {payload.get('model')} not found")
            raise RuntimeError(f"Molmo API HTTP error: {msg}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Molmo API connection error: {e}")

    def _extract_text(self, data: Dict) -> str:
        """Extract assistant text from OpenAI-compatible response shape."""
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            return ""

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return "".join(parts)
        return str(content)
