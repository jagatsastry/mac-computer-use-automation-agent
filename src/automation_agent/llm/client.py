"""Ollama client for LLM inference."""

import asyncio
from typing import Dict, Optional, List
import ollama

from .exceptions import ModelNotFoundError, ModelTimeoutError


class OllamaClient:
    """Async client for Ollama API."""

    def __init__(self, host: str = "http://localhost:11434", timeout: float = 120.0):
        self.host = host
        self.timeout = timeout
        self._client = ollama.AsyncClient(host=host)

    async def check_model_available(self, model_name: str) -> bool:
        """Check if a model is available."""
        try:
            response = await asyncio.wait_for(
                self._client.list(),
                timeout=10.0
            )
            # Response has .models attribute which is a list of Model objects
            model_names = [m.model for m in response.models]
            # Check for exact match or with :latest tag
            return model_name in model_names or f"{model_name}:latest" in model_names
        except asyncio.TimeoutError:
            raise ModelTimeoutError(f"Timeout checking model availability")
        except Exception as e:
            return False

    async def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        format: Optional[str] = None
    ) -> str:
        """Generate completion from model."""
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            response = await asyncio.wait_for(
                self._client.chat(
                    model=model,
                    messages=messages,
                    format=format if format == "json" else None
                ),
                timeout=self.timeout
            )

            return response['message']['content']

        except asyncio.TimeoutError:
            raise ModelTimeoutError(f"Model {model} timeout")
        except Exception as e:
            if "not found" in str(e).lower():
                raise ModelNotFoundError(f"Model {model} not found")
            raise

    async def test_connection(self) -> bool:
        """Test connection to Ollama server."""
        try:
            await asyncio.wait_for(self._client.list(), timeout=5.0)
            return True
        except:
            return False

    async def list_models(self) -> List[str]:
        """List all available models."""
        try:
            response = await asyncio.wait_for(self._client.list(), timeout=10.0)
            return [m.model for m in response.models]
        except Exception:
            return []

    async def generate_stream(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        options: Optional[Dict] = None
    ):
        """Generate completion with streaming."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        stream = await self._client.chat(
            model=model,
            messages=messages,
            stream=True,
            options=options or {}
        )

        async for chunk in stream:
            if 'message' in chunk and 'content' in chunk['message']:
                yield chunk['message']['content']

    async def generate_vision(
        self,
        model: str,
        prompt: str,
        image_b64: str,
        system: Optional[str] = None,
        options: Optional[Dict] = None
    ) -> str:
        """Generate completion with vision model."""
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})

            # Add user message with image
            messages.append({
                "role": "user",
                "content": prompt,
                "images": [image_b64]
            })

            response = await asyncio.wait_for(
                self._client.chat(
                    model=model,
                    messages=messages,
                    options=options or {}
                ),
                timeout=self.timeout
            )

            return response['message']['content']

        except asyncio.TimeoutError:
            raise ModelTimeoutError(f"Model {model} timeout")
        except Exception as e:
            if "not found" in str(e).lower():
                raise ModelNotFoundError(f"Model {model} not found")
            raise
