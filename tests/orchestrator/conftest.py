"""Pytest fixtures for orchestrator tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


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


@pytest.fixture
def mock_applescript_result():
    """Create a mock AppleScriptResult."""
    from automation_agent.actions.applescript import AppleScriptResult
    return AppleScriptResult(success=True, output="", error="")


@pytest.fixture
def mock_action_result():
    """Create a mock ActionResult for testing."""
    from automation_agent.orchestrator.models import ActionResult
    return ActionResult(
        success=True,
        action="activate_app",
        params={"app_name": "Safari"},
        output="Safari activated",
    )


@pytest.fixture
def sample_observation():
    """Create a sample observation for testing."""
    from automation_agent.orchestrator.models import Observation
    return Observation(
        screenshot_b64="base64_data",
        description="Safari browser is open showing YouTube homepage",
        timestamp=datetime.now(),
    )
