"""Pytest configuration and fixtures."""

import base64
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ============================================================================
# Legacy fixtures (for existing tests)
# ============================================================================


@pytest.fixture
def sample_config_data():
    """Sample configuration data for testing."""
    return {
        "vision_server_url": "http://test:8080",
        "vision_model": "test-vision",
        "text_model": "test-text",
        "log_level": "DEBUG",
    }


@pytest.fixture
def mock_ollama_response():
    """Mock Ollama API response."""
    return {
        "message": {"content": "Mock response"},
        "model": "test-model",
    }


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
            {
                "action": "open_url",
                "params": {"url": "https://youtube.com", "browser": "Safari"},
            },
        ],
        "requires_observation": False,
    }


@pytest.fixture
def sample_complex_intent_json():
    """Sample complex intent requiring observation."""
    return {
        "steps": [
            {
                "action": "open_url",
                "params": {"url": "https://youtube.com", "browser": "Safari"},
            },
            {"action": "type_text", "params": {"text": "cooking tutorials"}},
            {"action": "press_key", "params": {"keys": ["return"]}},
            {
                "action": "click_element",
                "params": {"description": "the most popular video"},
            },
        ],
        "requires_observation": True,
    }


# ============================================================================
# New component architecture fixtures
# ============================================================================


@pytest.fixture
def sample_action_step():
    """A sample ActionStep with all fields populated."""
    from automation_agent.shared_models import ActionStep

    return ActionStep(
        action="click",
        params={"x": 500, "y": 300},
        verify="Button state changes to 'pressed'",
        on_fail="retry_different",
        max_retries=3,
    )


@pytest.fixture
def sample_action_plan():
    """A sample ActionPlan with multiple steps."""
    from automation_agent.shared_models import ActionPlan, ActionStep

    return ActionPlan(
        steps=[
            ActionStep(
                action="activate_app",
                params={"app_name": "Calculator"},
                verify="Calculator app is in foreground",
            ),
            ActionStep(
                action="click",
                params={"element": "2"},
                verify="Display shows '2'",
            ),
            ActionStep(
                action="click",
                params={"element": "+"},
                verify="Display shows '2 +'",
            ),
            ActionStep(
                action="click",
                params={"element": "2"},
                verify="Display shows '2 + 2'",
            ),
            ActionStep(
                action="click",
                params={"element": "="},
                verify="Display shows '4'",
            ),
            ActionStep(action="done", params={}, verify=""),
        ],
        goal="Calculate 2 + 2",
    )


@pytest.fixture
def sample_step_result():
    """A sample successful StepResult."""
    from automation_agent.shared_models import ActionStep, StepResult

    return StepResult(
        step=ActionStep(
            action="activate_app",
            params={"app_name": "Calculator"},
            verify="Calculator app is in foreground",
        ),
        success=True,
        verification_method="actuator_state",
        evidence="Frontmost app is 'Calculator'",
        duration_ms=120,
    )


@pytest.fixture
def sample_failed_step_result():
    """A sample failed StepResult."""
    from automation_agent.shared_models import ActionStep, StepResult

    return StepResult(
        step=ActionStep(
            action="click",
            params={"element": "Submit"},
            verify="Form submitted successfully",
        ),
        success=False,
        verification_method="vision",
        evidence="Form still visible, error message 'Invalid email' shown",
        error="Postcondition not met",
        duration_ms=3200,
        retry_count=1,
        retry_strategies_used=["click_center"],
    )


@pytest.fixture
def mock_planner():
    """Mock ActionPlanner for testing."""
    from automation_agent.shared_models import ActionPlan, ActionStep

    planner = AsyncMock()
    planner.plan = AsyncMock(
        return_value=ActionPlan(
            steps=[
                ActionStep(
                    action="activate_app",
                    params={"app_name": "Safari"},
                    verify="Safari is frontmost app",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Open Safari",
        )
    )
    planner.replan = AsyncMock(
        return_value=ActionPlan(
            steps=[
                ActionStep(
                    action="activate_app",
                    params={"app_name": "Safari"},
                    verify="Safari is frontmost app",
                    on_fail="abort",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Open Safari (replanned)",
        )
    )
    return planner


@pytest.fixture
def mock_coordinator():
    """Mock ScreenCoordinator for testing."""
    coordinator = AsyncMock()
    coordinator.find_element = AsyncMock(return_value={"x": 500, "y": 300})
    coordinator.describe_screen = AsyncMock(return_value="Desktop with Safari open")
    coordinator.verify_condition = AsyncMock(return_value=True)
    coordinator.capture_screenshot = AsyncMock(
        return_value=base64.b64encode(b"fake_screenshot_png_data").decode()
    )
    return coordinator


@pytest.fixture
def mock_actuator():
    """Mock Actuator for testing."""
    actuator = MagicMock()
    actuator.is_available = MagicMock(return_value=True)
    actuator.click = MagicMock(return_value={"success": True, "output": ""})
    actuator.type_text = MagicMock(return_value={"success": True, "output": ""})
    actuator.press_key = MagicMock(return_value={"success": True, "output": ""})
    actuator.activate_app = MagicMock(return_value={"success": True, "output": ""})
    actuator.open_url = MagicMock(return_value={"success": True, "output": ""})
    actuator.quit_app = MagicMock(return_value={"success": True, "output": ""})
    actuator.get_state = MagicMock(
        return_value={
            "app_name": "Safari",
            "app_bundle": "com.apple.Safari",
            "window_title": "Google",
            "window_frame": '{"x":0,"y":25,"w":1440,"h":875}',
        }
    )
    return actuator


@pytest.fixture
def mock_skill_registry():
    """Mock SkillRegistry for testing."""
    registry = MagicMock()
    registry.match = AsyncMock(return_value=None)
    registry.list_skills = MagicMock(return_value=[])
    registry.expand = MagicMock(return_value=None)
    registry.validate_all = MagicMock(return_value=[])
    return registry


@pytest.fixture
def mock_verifier():
    """Mock Verifier for testing."""
    from automation_agent.shared_models import ActionStep, StepResult

    verifier = AsyncMock()
    verifier.verify = AsyncMock(
        return_value=StepResult(
            step=ActionStep(action="click", params={}, verify="element clicked"),
            success=True,
            verification_method="actuator_state",
            evidence="State confirmed",
        )
    )
    return verifier


@pytest.fixture
def tmp_log_dir(tmp_path):
    """Temporary directory for event logs."""
    log_dir = tmp_path / "test_logs"
    log_dir.mkdir()
    return log_dir


@pytest.fixture
def tmp_skill_dir(tmp_path):
    """Temporary directory for skill files."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    return skill_dir
