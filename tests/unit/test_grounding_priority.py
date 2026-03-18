"""Unit tests for grounding priority order in GroundingRouter and type_text logging.

Tests verify:
1. AX accessibility is tried first, before vision.
2. Vision is used when AX misses.
3. Keyboard shortcuts are last resort (after AX + vision).
4. Vision result takes priority over keyboard shortcut.
5. Keyboard shortcuts are skipped when frontmost app is not a browser.
6. AX aliases expand before search (e.g., "address bar" -> "smart search field").
7. type_text logs ELEMENT_FOUND on successful click-to-focus.
8. type_text logs ELEMENT_NOT_FOUND when element not found.

All tests are fully mocked -- no real API calls or desktop interaction.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.grounding_router import (
    GroundingRouter,
    GroundingStrategy,
)
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    FindElementResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    """Create an AgentConfig for tests with local provider pinned."""
    defaults = {
        "model_provider": "local",
        "vision_model": "molmo",
        "log_dir": "/tmp/test_grounding_priority_logs",
        "grounding_model": "",
        "grounding_server_url": "",
        "_env_file": None,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


@dataclass
class FakeAXElement:
    """Lightweight stand-in for AXElement with center property."""

    role: str = "AXButton"
    title: Optional[str] = None
    value: Optional[str] = None
    description: Optional[str] = None
    position: Optional[Tuple[int, int]] = None
    size: Optional[Tuple[int, int]] = None
    enabled: bool = True
    focused: bool = False

    @property
    def center(self) -> Optional[Tuple[int, int]]:
        if self.position and self.size:
            return (
                self.position[0] + self.size[0] // 2,
                self.position[1] + self.size[1] // 2,
            )
        return None


def _make_ax_element(
    role="AXButton",
    title="OK",
    position=(100, 200),
    size=(80, 30),
    enabled=True,
) -> FakeAXElement:
    return FakeAXElement(
        role=role,
        title=title,
        position=position,
        size=size,
        enabled=enabled,
    )


def _make_accessibility_bridge(
    elements: Optional[List[FakeAXElement]] = None,
    frontmost_app: Optional[str] = None,
):
    """Create a mock accessibility bridge.

    Args:
        elements: Elements returned by find_elements(). None -> empty.
        frontmost_app: Name of the frontmost app for get_frontmost_app().
    """
    bridge = MagicMock()
    bridge.find_element_by_description = MagicMock(
        return_value=elements[0] if elements else None,
    )
    bridge.find_elements = MagicMock(return_value=elements or [])
    bridge.get_frontmost_app = MagicMock(
        return_value={"name": frontmost_app} if frontmost_app else None,
    )

    # Wire _extract_match_target and _element_match_score so the
    # GroundingRouter._get_accessibility_matches() path works.
    from automation_agent.perception.accessibility import AccessibilityBridge

    bridge._extract_match_target = AccessibilityBridge._extract_match_target
    bridge._element_match_score = AccessibilityBridge._element_match_score
    return bridge


def _make_vision_coordinator(
    result: Optional[FindElementResult] = None,
):
    """Create a mock vision coordinator."""
    coord = AsyncMock()
    coord.find_element = AsyncMock(return_value=result)
    return coord


# ---------------------------------------------------------------------------
# Tests: Grounding Priority Order
# ---------------------------------------------------------------------------


class TestGroundingPriority:
    """Tests that the grounding router follows AX -> Vision -> Keyboard order."""

    @pytest.mark.asyncio
    async def test_ax_match_returns_before_vision(self):
        """When AX returns a match, vision should never be called."""
        elem = _make_ax_element(title="Submit", position=(300, 400), size=(80, 30))
        ax = _make_accessibility_bridge(elements=[elem])
        vision = _make_vision_coordinator()

        router = GroundingRouter(
            accessibility=ax,
            vision_coordinator=vision,
            config=_make_config(),
        )

        result = await router.find_element("Submit button")

        assert result is not None
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY
        assert result.x == 340  # 300 + 80//2
        assert result.y == 415  # 400 + 30//2
        # Vision must NOT have been called
        vision.find_element.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_vision_used_when_ax_misses(self):
        """When AX returns nothing, vision should be called."""
        ax = _make_accessibility_bridge(elements=[])
        vision_result = FindElementResult(
            x=500, y=300, confidence=0.85, source="vision",
        )
        vision = _make_vision_coordinator(result=vision_result)

        router = GroundingRouter(
            accessibility=ax,
            vision_coordinator=vision,
            config=_make_config(),
        )

        result = await router.find_element("fancy icon")

        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION
        assert result.x == 500
        assert result.y == 300
        vision.find_element.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_keyboard_shortcut_is_last_resort(self):
        """When AX and vision both miss, keyboard shortcut for 'address bar'
        should be returned if frontmost app is a browser."""
        ax = _make_accessibility_bridge(elements=[], frontmost_app="Safari")
        vision = _make_vision_coordinator(result=None)

        router = GroundingRouter(
            accessibility=ax,
            vision_coordinator=vision,
            config=_make_config(),
        )

        result = await router.find_element("address bar")

        assert result is not None
        assert result.keyboard_shortcut == ["cmd+l"]
        assert result.confidence == 0.99

    @pytest.mark.asyncio
    async def test_keyboard_shortcut_not_returned_when_vision_finds(self):
        """When vision returns a result for 'address bar', keyboard shortcut
        should NOT be returned -- vision takes priority."""
        ax = _make_accessibility_bridge(elements=[], frontmost_app="Safari")
        vision_result = FindElementResult(
            x=600, y=50, confidence=0.80, source="vision",
        )
        vision = _make_vision_coordinator(result=vision_result)

        router = GroundingRouter(
            accessibility=ax,
            vision_coordinator=vision,
            config=_make_config(),
        )

        result = await router.find_element("address bar")

        assert result is not None
        # Should be vision, not keyboard shortcut
        assert result.strategy_used == GroundingStrategy.VISION
        assert result.keyboard_shortcut is None
        assert result.x == 600

    @pytest.mark.asyncio
    async def test_keyboard_shortcut_skipped_when_not_browser(self):
        """When AX/vision miss and frontmost app is Finder (not a browser),
        keyboard shortcut should NOT be returned."""
        ax = _make_accessibility_bridge(elements=[], frontmost_app="Finder")
        vision = _make_vision_coordinator(result=None)

        router = GroundingRouter(
            accessibility=ax,
            vision_coordinator=vision,
            config=_make_config(),
        )

        result = await router.find_element("address bar")

        # Should be None -- no match from any strategy, and keyboard
        # shortcut is skipped because Finder is not a browser
        assert result is None

    @pytest.mark.asyncio
    async def test_ax_alias_expands_before_search(self):
        """'address bar' should expand to 'smart search field' via AX aliases,
        and the AX search should use the expanded text."""
        elem = _make_ax_element(
            role="AXTextField",
            title="smart search field",
            position=(200, 50),
            size=(400, 30),
        )
        ax = _make_accessibility_bridge(elements=[elem])
        vision = _make_vision_coordinator()

        router = GroundingRouter(
            accessibility=ax,
            vision_coordinator=vision,
            config=_make_config(),
        )

        result = await router.find_element("address bar")

        assert result is not None
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY
        # The _extract_match_target for "address bar" should expand to
        # "smart search field" (primary alias), and find_elements should
        # be called with that expanded text.
        call_args_list = ax.find_elements.call_args_list
        assert len(call_args_list) > 0
        # Check that at least one call used the alias-expanded text
        found_alias = False
        for call in call_args_list:
            title_arg = call.kwargs.get("title_contains", "")
            if title_arg and "smart search field" in title_arg:
                found_alias = True
                break
        assert found_alias, (
            f"Expected 'smart search field' in find_elements calls, "
            f"got: {call_args_list}"
        )


# ---------------------------------------------------------------------------
# Tests: type_text click-to-focus logging
# ---------------------------------------------------------------------------


class TestTypeTextFocusLogging:
    """Tests that type_text click-to-focus logs proper events."""

    @pytest.mark.asyncio
    async def test_type_text_logs_element_found_on_focus_click(
        self, mock_planner, mock_coordinator, mock_actuator,
        mock_skill_registry, tmp_log_dir,
    ):
        """Verify ELEMENT_FOUND event is logged when click-to-focus succeeds."""
        from automation_agent.orchestrator.agent import AutomationAgent

        logger = EventLogger(tmp_log_dir)
        config = _make_config(
            anthropic_api_key="test-key-not-real",
        )

        # Plan: type_text with element field to trigger click-to-focus
        mock_planner.plan = AsyncMock(
            return_value=ActionPlan(
                steps=[
                    ActionStep(
                        action="type_text",
                        params={"text": "hello", "element": "search field"},
                        verify="text entered",
                    ),
                    ActionStep(action="done", params={}, verify=""),
                ],
                goal="Type hello",
            )
        )

        # Mock _find_element to return a found location
        find_result = FindElementResult(
            x=400, y=200, confidence=0.9, source="vision",
        )

        class _AutoApprove:
            async def confirm(self, step):
                return True

        agent = AutomationAgent(
            planner=mock_planner,
            skill_registry=mock_skill_registry,
            coordinator=mock_coordinator,
            actuator=mock_actuator,
            config=config,
            logger=logger,
            confirmation_handler=_AutoApprove(),
        )
        agent._find_element = AsyncMock(return_value=find_result)
        # Skip verification
        agent._verify_step = AsyncMock(
            return_value=MagicMock(success=True, verification_method="mock")
        )

        await agent.execute("Type hello")

        # Check that ELEMENT_FOUND was logged
        found_events = [
            e for e in logger.events
            if e.event_type == EventType.ELEMENT_FOUND
        ]
        assert len(found_events) >= 1, (
            f"Expected ELEMENT_FOUND event, got events: "
            f"{[e.event_type.value for e in logger.events]}"
        )
        msg = found_events[0].message
        assert "search field" in msg
        assert "click-to-focus" in msg

    @pytest.mark.asyncio
    async def test_type_text_logs_element_not_found(
        self, mock_planner, mock_coordinator, mock_actuator,
        mock_skill_registry, tmp_log_dir,
    ):
        """Verify ELEMENT_NOT_FOUND event when element not found."""
        from automation_agent.orchestrator.agent import AutomationAgent

        logger = EventLogger(tmp_log_dir)
        config = _make_config(
            anthropic_api_key="test-key-not-real",
        )

        mock_planner.plan = AsyncMock(
            return_value=ActionPlan(
                steps=[
                    ActionStep(
                        action="type_text",
                        params={"text": "hello", "element": "missing field"},
                        verify="text entered",
                    ),
                    ActionStep(action="done", params={}, verify=""),
                ],
                goal="Type hello",
            )
        )

        class _AutoApprove:
            async def confirm(self, step):
                return True

        agent = AutomationAgent(
            planner=mock_planner,
            skill_registry=mock_skill_registry,
            coordinator=mock_coordinator,
            actuator=mock_actuator,
            config=config,
            logger=logger,
            confirmation_handler=_AutoApprove(),
        )
        # _find_element returns None -> element not found
        agent._find_element = AsyncMock(return_value=None)
        agent._verify_step = AsyncMock(
            return_value=MagicMock(success=True, verification_method="mock")
        )

        await agent.execute("Type hello")

        # Check that ELEMENT_NOT_FOUND was logged
        not_found_events = [
            e for e in logger.events
            if e.event_type == EventType.ELEMENT_NOT_FOUND
        ]
        assert len(not_found_events) >= 1, (
            f"Expected ELEMENT_NOT_FOUND event, got events: "
            f"{[e.event_type.value for e in logger.events]}"
        )
        msg = not_found_events[0].message
        assert "missing field" in msg
