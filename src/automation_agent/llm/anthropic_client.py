"""Anthropic (Claude) client for LLM inference."""

import asyncio
import base64
from typing import Dict, List, Optional

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

from .exceptions import ModelNotFoundError, ModelTimeoutError


class AnthropicClient:
    """Async client for Anthropic Claude API.

    Provides the same interface as OllamaClient for drop-in replacement.
    """

    def __init__(
        self,
        api_key: str,
        timeout: float = 120.0,
        model: str = "claude-sonnet-4-20250514",
        vision_model: str = "claude-sonnet-4-20250514",
    ):
        if not ANTHROPIC_AVAILABLE:
            raise ImportError(
                "anthropic package not installed. "
                "Install with: pip install anthropic"
            )

        self.api_key = api_key
        self.timeout = timeout
        self.model = model
        self.vision_model = vision_model
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def check_model_available(self, model_name: str) -> bool:
        """Check if a model is available (Claude models are always available if API key works)."""
        try:
            # Test with a minimal request
            await asyncio.wait_for(
                self._client.messages.create(
                    model=model_name,
                    max_tokens=10,
                    messages=[{"role": "user", "content": "hi"}]
                ),
                timeout=10.0
            )
            return True
        except anthropic.NotFoundError:
            return False
        except asyncio.TimeoutError:
            raise ModelTimeoutError("Timeout checking model availability")
        except Exception:
            return False

    async def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        format: Optional[str] = None
    ) -> str:
        """Generate completion from Claude model."""
        try:
            # Build the request
            kwargs = {
                "model": model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}]
            }

            if system:
                kwargs["system"] = system

            response = await asyncio.wait_for(
                self._client.messages.create(**kwargs),
                timeout=self.timeout
            )

            # Extract text from response
            result = ""
            for block in response.content:
                if block.type == "text":
                    result += block.text

            return result

        except asyncio.TimeoutError:
            raise ModelTimeoutError(f"Model {model} timeout")
        except anthropic.NotFoundError:
            raise ModelNotFoundError(f"Model {model} not found")
        except Exception as e:
            raise RuntimeError(f"Anthropic API error: {e}")

    async def test_connection(self) -> bool:
        """Test connection to Anthropic API."""
        try:
            await asyncio.wait_for(
                self._client.messages.create(
                    model=self.model,
                    max_tokens=10,
                    messages=[{"role": "user", "content": "test"}]
                ),
                timeout=10.0
            )
            return True
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        """List available Claude models."""
        # Claude doesn't have a list API, return known models
        return [
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ]

    async def generate_stream(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        options: Optional[Dict] = None
    ):
        """Generate completion with streaming."""
        kwargs = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}]
        }

        if system:
            kwargs["system"] = system

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text

    async def generate_vision(
        self,
        model: str,
        prompt: str,
        image_b64: str,
        system: Optional[str] = None,
        options: Optional[Dict] = None
    ) -> str:
        """Generate completion with vision model (Claude supports images natively)."""
        try:
            # Detect image type from base64 header or default to PNG
            media_type = "image/png"
            if image_b64.startswith("/9j/"):
                media_type = "image/jpeg"
            elif image_b64.startswith("iVBOR"):
                media_type = "image/png"
            elif image_b64.startswith("R0lGOD"):
                media_type = "image/gif"
            elif image_b64.startswith("UklGR"):
                media_type = "image/webp"

            # Build message with image
            message_content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_b64,
                    }
                },
                {
                    "type": "text",
                    "text": prompt
                }
            ]

            kwargs = {
                "model": model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": message_content}]
            }

            if system:
                kwargs["system"] = system

            response = await asyncio.wait_for(
                self._client.messages.create(**kwargs),
                timeout=self.timeout
            )

            # Extract text from response
            result = ""
            for block in response.content:
                if block.type == "text":
                    result += block.text

            return result

        except asyncio.TimeoutError:
            raise ModelTimeoutError(f"Model {model} timeout")
        except anthropic.NotFoundError:
            raise ModelNotFoundError(f"Model {model} not found")
        except Exception as e:
            raise RuntimeError(f"Anthropic API error: {e}")
