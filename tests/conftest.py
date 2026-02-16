"""Pytest configuration and fixtures."""

import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def sample_config_data():
    """Sample configuration data for testing."""
    return {
        "ollama_host": "http://test:11434",
        "vision_model": "test-vision",
        "text_model": "test-text",
        "log_level": "DEBUG",
    }


@pytest.fixture
def mock_ollama_response():
    """Mock Ollama API response."""
    return {
        'message': {'content': 'Mock response'},
        'model': 'test-model',
    }


# ============================================================================
# Orchestrator fixtures (shared across all test modules)
# ============================================================================

@pytest.fixture
def mock_ollama_client():
    """Create a mock OllamaClient for testing."""
    client = MagicMock()
    client.generate = AsyncMock()
    client.generate_vision = AsyncMock()
    client.test_connection = AsyncMock(return_value=True)
    client.check_model_available = AsyncMock(return_value=True)
    client.list_models = AsyncMock(return_value=["gemma2:9b", "qwen3-vl"])
    return client


@pytest.fixture
def mock_screen_capturer():
    """Create a mock ScreenCapturer for testing."""
    capturer = MagicMock()
    capturer.capture_screen_b64 = MagicMock(return_value="base64_screenshot_data")
    capturer.get_screen_size = MagicMock(return_value=(1920, 1080))
    capturer.capture_screen = MagicMock()
    return capturer


@pytest.fixture
def sample_intent_json():
    """Sample parsed intent JSON for testing."""
    return {
        "steps": [
            {"action": "activate_app", "params": {"app_name": "Safari"}},
            {"action": "open_url", "params": {"url": "https://youtube.com", "browser": "Safari"}}
        ],
        "requires_observation": False
    }


@pytest.fixture
def sample_complex_intent_json():
    """Sample complex intent requiring observation."""
    return {
        "steps": [
            {"action": "open_url", "params": {"url": "https://youtube.com", "browser": "Safari"}},
            {"action": "type_text", "params": {"text": "cooking tutorials"}},
            {"action": "press_key", "params": {"keys": ["return"]}},
            {"action": "click_element", "params": {"description": "the most popular video"}}
        ],
        "requires_observation": True
    }
