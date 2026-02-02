"""LLM-specific exceptions."""


class LLMError(Exception):
    """Base exception for LLM errors."""
    pass


class ModelNotFoundError(LLMError):
    """Model not found in Ollama."""
    pass


class ModelTimeoutError(LLMError):
    """Model request timeout."""
    pass


class InvalidResponseError(LLMError):
    """Invalid response from model."""
    pass
