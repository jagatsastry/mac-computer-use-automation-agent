"""LLM client implementations."""

from .client import OllamaClient
from .exceptions import ModelNotFoundError, ModelTimeoutError
from .molmo_client import MolmoVisionClient
from .openai_client import OpenAIClient

# Optional Anthropic client (requires anthropic package)
try:
    from .anthropic_client import AnthropicClient, ANTHROPIC_AVAILABLE
except ImportError:
    AnthropicClient = None
    ANTHROPIC_AVAILABLE = False

__all__ = [
    "OllamaClient",
    "MolmoVisionClient",
    "OpenAIClient",
    "AnthropicClient",
    "ANTHROPIC_AVAILABLE",
    "ModelNotFoundError",
    "ModelTimeoutError",
]
