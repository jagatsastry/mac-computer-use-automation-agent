"""Local Molmo client using Hugging Face Transformers."""

from __future__ import annotations

import asyncio
import base64
import io
import os
from typing import Dict, List, Optional

from .exceptions import ModelNotFoundError, ModelTimeoutError


class MolmoLocalClient:
    """Async wrapper for local Molmo inference via Transformers."""

    def __init__(
        self,
        model_name: str = "allenai/MolmoE-1B-0924",
        timeout: float = 1200.0,
        max_new_tokens: int = 200,
    ):
        self.model_name = model_name
        self.timeout = timeout
        self.max_new_tokens = max_new_tokens

        self._model = None
        self._processor = None
        self._generation_config = None

    @staticmethod
    def dependencies_available() -> bool:
        """Return True when required local inference dependencies are available."""
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
            import PIL  # noqa: F401
            return True
        except Exception:
            return False

    async def _ensure_loaded(self) -> None:
        """Lazy-load model and processor."""
        if self._model is not None and self._processor is not None:
            return

        # Xet-backed downloads can hang in some local environments.
        # Default to classic Hub download path unless user explicitly opts in.
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")

        def _load():
            from transformers import AutoModelForCausalLM, AutoProcessor, GenerationConfig

            processor = AutoProcessor.from_pretrained(
                self.model_name,
                trust_remote_code=True,
            )
            model = AutoModelForCausalLM.from_pretrained(
                self.model_name,
                trust_remote_code=True,
                torch_dtype="auto",
                low_cpu_mem_usage=True,
            )
            
            # Ensure weights are tied if the model structure requires it (fixes some loading issues)
            if hasattr(model, "tie_weights"):
                model.tie_weights()

            generation_config = GenerationConfig(
                max_new_tokens=self.max_new_tokens,
                stop_strings="<|endoftext|>",
            )
            return model, processor, generation_config

        try:
            model, processor, generation_config = await asyncio.wait_for(
                asyncio.to_thread(_load),
                timeout=self.timeout,
            )
            self._model = model
            self._processor = processor
            self._generation_config = generation_config
        except asyncio.TimeoutError:
            raise ModelTimeoutError(f"Timeout loading local Molmo model {self.model_name}")
        except Exception as e:
            msg = str(e).lower()
            if "repository not found" in msg or "404 client error" in msg:
                raise ModelNotFoundError(f"Local Molmo model {self.model_name} not found")
            raise RuntimeError(f"Failed to load local Molmo model {self.model_name}: {e}")

    async def test_connection(self) -> bool:
        """Test local Molmo availability by loading model components."""
        if not self.dependencies_available():
            return False
        try:
            await self._ensure_loaded()
            return True
        except Exception:
            return False

    async def check_model_available(self, model_name: str) -> bool:
        """Best-effort model availability check by attempting load for target name."""
        if model_name != self.model_name:
            return False
        return await self.test_connection()

    async def list_models(self) -> List[str]:
        """Return configured local model name."""
        return [self.model_name]

    async def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        format: Optional[str] = None,
    ) -> str:
        """
        Text-only generation is not used in this project for local Molmo path.
        """
        raise RuntimeError(
            "MolmoLocalClient is vision-focused in this project. "
            "Use generate_vision() for image-grounded prompts."
        )

    async def generate_vision(
        self,
        model: str,
        prompt: str,
        image_b64: str,
        system: Optional[str] = None,
        options: Optional[Dict] = None,
    ) -> str:
        """Run local Molmo vision generation."""
        await self._ensure_loaded()

        full_prompt = prompt if not system else f"{system}\n\n{prompt}"

        def _infer() -> str:
            from PIL import Image
            import torch

            image = Image.open(io.BytesIO(base64.b64decode(image_b64)))
            if image.mode != "RGB":
                image = image.convert("RGB")

            inputs = self._processor.process(images=[image], text=full_prompt)
            # Batch size 1 and move to model device
            model_device = self._model.device
            inputs = {k: v.to(model_device).unsqueeze(0) for k, v in inputs.items()}

            with torch.no_grad():
                output = self._model.generate_from_batch(
                    inputs,
                    self._generation_config,
                    tokenizer=self._processor.tokenizer,
                )

            generated_tokens = output[0, inputs["input_ids"].size(1):]
            generated_text = self._processor.tokenizer.decode(generated_tokens, skip_special_tokens=True)
            return generated_text

        try:
            return await asyncio.wait_for(asyncio.to_thread(_infer), timeout=self.timeout)
        except asyncio.TimeoutError:
            raise ModelTimeoutError(f"Local Molmo inference timeout for {self.model_name}")
        except Exception as e:
            raise RuntimeError(f"Local Molmo inference error: {e}")
