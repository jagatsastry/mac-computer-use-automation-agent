"""Unit tests for P1-1: type_text element focus fix.

Validates that type_text with an element parameter captures a screenshot,
finds the element, and clicks it before typing. Also tests confidence
gating, fallthrough on failure, and the _skip_focus flag.

All external calls are mocked -- no real API calls.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    FindElementResult,
    StepResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",
        "grounding_model": "",
        "grounding_server_url": "",
        "action_delay": 0.0,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_agent(config=None, actuator=None, coordinator=None):
    """Construct a minimal AutomationAgent with mocked dependencies."""
    planner = AsyncMock()
    skill_registry = MagicMock()
    skill_registry.match = AsyncMock(return_value=None)
    skill_registry.learn_from_run = AsyncMock(return_value=[])
    skill_registry.promote_from_run = AsyncMock(return_value=None)

    if coordinator is None:
        coordinator = AsyncMock()
        coordinator.capabilities = MagicMock(return_value=frozenset())
        coordinator.capture_screenshot = AsyncMock(return_value="base64data")
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(
                x=500, y=300, confidence=0.9, source="vision"
            )
        )

    if config is None:
        config = _make_config()

    if actuator is None:
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.scroll = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={})

    return AutomationAgent(planner, skill_registry, coordinator, actuator, config)


# ---------------------------------------------------------------------------
# Tests: type_text captures screenshot before find_element
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_type_text_captures_screenshot():
    """capture_screenshot() must be called before find_element for type_text."""
    agent = _make_agent()
    step = ActionStep(
        action="type_text",
        params={"text": "hello", "element": "search field"},
        verify="text appears in search field",
    )
    result = await agent._dispatch_action(step)

    agent.coordinator.capture_screenshot.assert_awaited_once()
    agent.coordinator.find_element.assert_awaited_once()
    # The screenshot should be passed to find_element
    call_kwargs = agent.coordinator.find_element.call_args
    assert call_kwargs[1].get("screenshot_b64") == "base64data"


@pytest.mark.asyncio
async def test_type_text_clicks_element_first():
    """type_text with element should click the found element before typing."""
    agent = _make_agent()
    step = ActionStep(
        action="type_text",
        params={"text": "hello", "element": "search field"},
        verify="text appears in search field",
    )
    result = await agent._dispatch_action(step)

    # Click should be called with element coordinates
    agent.actuator.click.assert_called_once_with(500, 300)
    # Then type_text should be called
    agent.actuator.type_text.assert_called_once_with("hello")
    assert result.get("success") is True


@pytest.mark.asyncio
async def test_type_text_no_element_no_click():
    """type_text without element param should not call click or find_element."""
    agent = _make_agent()
    step = ActionStep(
        action="type_text",
        params={"text": "hello"},
        verify="text appears",
    )
    result = await agent._dispatch_action(step)

    agent.coordinator.find_element.assert_not_awaited()
    agent.actuator.click.assert_not_called()
    agent.actuator.type_text.assert_called_once_with("hello")


@pytest.mark.asyncio
async def test_type_text_find_element_fails_fallthrough():
    """If find_element returns None, type_text should still proceed."""
    coordinator = AsyncMock()
    coordinator.capabilities = MagicMock(return_value=frozenset())
    coordinator.capture_screenshot = AsyncMock(return_value="base64data")
    coordinator.find_element = AsyncMock(return_value=None)

    agent = _make_agent(coordinator=coordinator)
    step = ActionStep(
        action="type_text",
        params={"text": "hello", "element": "nonexistent field"},
        verify="text appears",
    )
    result = await agent._dispatch_action(step)

    agent.actuator.click.assert_not_called()
    agent.actuator.type_text.assert_called_once_with("hello")
    assert result.get("success") is True


@pytest.mark.asyncio
async def test_type_text_find_element_exception_fallthrough():
    """If find_element raises, type_text should still proceed."""
    coordinator = AsyncMock()
    coordinator.capabilities = MagicMock(return_value=frozenset())
    coordinator.capture_screenshot = AsyncMock(return_value="base64data")
    coordinator.find_element = AsyncMock(side_effect=RuntimeError("vision error"))

    agent = _make_agent(coordinator=coordinator)
    step = ActionStep(
        action="type_text",
        params={"text": "hello", "element": "search field"},
        verify="text appears",
    )
    result = await agent._dispatch_action(step)

    agent.actuator.click.assert_not_called()
    agent.actuator.type_text.assert_called_once_with("hello")
    assert result.get("success") is True


@pytest.mark.asyncio
async def test_type_text_skip_focus_flag():
    """_skip_focus=True should bypass element finding entirely."""
    agent = _make_agent()
    step = ActionStep(
        action="type_text",
        params={"text": "hello", "element": "search field", "_skip_focus": True},
        verify="text appears",
    )
    result = await agent._dispatch_action(step)

    agent.coordinator.find_element.assert_not_awaited()
    agent.coordinator.capture_screenshot.assert_not_awaited()
    agent.actuator.click.assert_not_called()
    agent.actuator.type_text.assert_called_once_with("hello")


@pytest.mark.asyncio
async def test_type_text_low_confidence_still_clicks():
    """Low confidence should still click — wrong focus is recoverable, no focus is not."""
    coordinator = AsyncMock()
    coordinator.capabilities = MagicMock(return_value=frozenset())
    coordinator.capture_screenshot = AsyncMock(return_value="base64data")
    coordinator.find_element = AsyncMock(
        return_value=FindElementResult(
            x=500, y=300, confidence=0.3, source="vision"
        )
    )

    agent = _make_agent(coordinator=coordinator)
    step = ActionStep(
        action="type_text",
        params={"text": "hello", "element": "search field"},
        verify="text appears",
    )
    result = await agent._dispatch_action(step)

    # Always click for focus — no confidence gating per spec
    agent.actuator.click.assert_called_once_with(500, 300)
    agent.actuator.type_text.assert_called_once_with("hello")
    assert result.get("success") is True


@pytest.mark.asyncio
async def test_type_text_uses_screen_coords_when_available():
    """When screen_x/screen_y are available, use those instead of x/y."""
    coordinator = AsyncMock()
    coordinator.capabilities = MagicMock(return_value=frozenset())
    coordinator.capture_screenshot = AsyncMock(return_value="base64data")
    coordinator.find_element = AsyncMock(
        return_value=FindElementResult(
            x=500, y=300, confidence=0.9, source="vision",
            screen_x=750, screen_y=450,
        )
    )

    agent = _make_agent(coordinator=coordinator)
    step = ActionStep(
        action="type_text",
        params={"text": "hello", "element": "search field"},
        verify="text appears",
    )
    result = await agent._dispatch_action(step)

    agent.actuator.click.assert_called_once_with(750, 450)


@pytest.mark.asyncio
async def test_type_text_no_confidence_attr_still_clicks():
    """FindElementResult without confidence should still click (backward compat)."""
    coordinator = AsyncMock()
    coordinator.capabilities = MagicMock(return_value=frozenset())
    coordinator.capture_screenshot = AsyncMock(return_value="base64data")
    coordinator.find_element = AsyncMock(
        return_value=FindElementResult(
            x=500, y=300, confidence=None, source="vision"
        )
    )

    agent = _make_agent(coordinator=coordinator)
    step = ActionStep(
        action="type_text",
        params={"text": "hello", "element": "search field"},
        verify="text appears",
    )
    result = await agent._dispatch_action(step)

    # No confidence info — click anyway (backward compat)
    agent.actuator.click.assert_called_once_with(500, 300)
    agent.actuator.type_text.assert_called_once_with("hello")


@pytest.mark.asyncio
async def test_type_text_find_element_x_none_fallthrough():
    """FindElementResult with x=None should fall through to typing at current focus."""
    coordinator = AsyncMock()
    coordinator.capabilities = MagicMock(return_value=frozenset())
    coordinator.capture_screenshot = AsyncMock(return_value="base64data")
    coordinator.find_element = AsyncMock(
        return_value=FindElementResult(
            x=None, y=None, confidence=0.8, source="vision"
        )
    )

    agent = _make_agent(coordinator=coordinator)
    step = ActionStep(
        action="type_text",
        params={"text": "hello", "element": "search field"},
        verify="text appears",
    )
    result = await agent._dispatch_action(step)

    # x=None means no coordinates — no click, but type still proceeds
    agent.actuator.click.assert_not_called()
    agent.actuator.type_text.assert_called_once_with("hello")
    assert result.get("success") is True
