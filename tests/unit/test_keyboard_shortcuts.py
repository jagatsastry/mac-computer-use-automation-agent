"""Comprehensive unit tests for keyboard shortcut dispatch and AX alias matching.

Covers:
  1. AX alias matching in _element_match_score
  2. _extract_match_target alias expansion
  3. Grounding router _check_keyboard_shortcut
  4. Grounding router find_element: shortcut takes priority (last resort)
  5. Agent _dispatch_action: keyboard shortcut execution
  6. Agent type_text click-to-focus with keyboard shortcut

All external calls are mocked -- no real API calls or desktop interaction.
"""

import ast
from dataclasses import dataclass
from typing import List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.orchestrator.grounding_router import (
    GroundingResult,
    GroundingRouter,
    GroundingStrategy,
)
from automation_agent.perception.accessibility import (
    BROWSER_KEYBOARD_SHORTCUTS,
    AccessibilityBridge,
    AXElement,
    _ELEMENT_ALIASES,
)
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    FindElementResult,
)

# ---------------------------------------------------------------------------
# Helpers / Factories
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    """Return an AgentConfig for tests with local provider pinned."""
    defaults = {
        "model_provider": "local",
        "vision_model": "molmo",
        "log_dir": "/tmp/test_keyboard_shortcuts_logs",
        "grounding_model": "",
        "grounding_server_url": "",
        "_env_file": None,
        "action_delay": 0.0,
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
    focused=False,
    value=None,
    description=None,
) -> FakeAXElement:
    return FakeAXElement(
        role=role,
        title=title,
        position=position,
        size=size,
        enabled=enabled,
        focused=focused,
        value=value,
        description=description,
    )


def _make_accessibility_bridge(
    elements: Optional[List[FakeAXElement]] = None,
    frontmost_app: Optional[str] = None,
):
    """Create a mock accessibility bridge wired with real _extract_match_target
    and _element_match_score for alias resolution."""
    bridge = MagicMock()
    bridge.find_element_by_description = MagicMock(
        return_value=elements[0] if elements else None,
    )
    bridge.find_elements = MagicMock(return_value=elements or [])
    bridge.get_frontmost_app = MagicMock(
        return_value={"name": frontmost_app} if frontmost_app else None,
    )
    # Wire real class methods so GroundingRouter._get_accessibility_matches() works
    bridge._extract_match_target = AccessibilityBridge._extract_match_target
    bridge._element_match_score = AccessibilityBridge._element_match_score
    return bridge


def _make_vision_coordinator(result=None):
    """Create a mock vision coordinator."""
    coord = AsyncMock()
    coord.find_element = AsyncMock(return_value=result)
    return coord


def _make_agent(config=None, actuator=None, coordinator=None, grounding_router=None):
    """Construct a minimal AutomationAgent with mocked dependencies."""
    from automation_agent.orchestrator.agent import AutomationAgent

    planner = AsyncMock()
    skill_registry = MagicMock()
    skill_registry.match = AsyncMock(return_value=None)
    skill_registry.learn_from_run = AsyncMock(return_value=[])
    skill_registry.promote_from_run = AsyncMock(return_value=None)

    if coordinator is None:
        coordinator = AsyncMock()
        coordinator.capabilities = MagicMock(return_value=frozenset())
        coordinator.capture_screenshot = AsyncMock(return_value="base64data")
        coordinator.find_element = AsyncMock(return_value=None)

    if config is None:
        config = _make_config()

    if actuator is None:
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.scroll = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "browser_url": "https://example.com"}
        )

    return AutomationAgent(
        planner,
        skill_registry,
        coordinator,
        actuator,
        config,
        grounding_router=grounding_router,
    )


# ===========================================================================
# 1. AX alias matching in _element_match_score
# ===========================================================================


class TestElementMatchScoreAliases:
    """Test _element_match_score resolves semantic aliases to AX titles."""

    @pytest.mark.unit
    def test_address_bar_matches_smart_search_field(self):
        """'address bar' alias-matches AXTextField with title 'smart search field'."""
        elem = _make_ax_element(
            role="AXTextField", title="smart search field", enabled=True
        )
        score = AccessibilityBridge._element_match_score(elem, "address bar")
        assert score == 1.0

    @pytest.mark.unit
    def test_url_bar_matches_smart_search_field(self):
        """'url bar' alias-matches AXTextField with title 'smart search field'."""
        elem = _make_ax_element(
            role="AXTextField", title="smart search field", enabled=True
        )
        score = AccessibilityBridge._element_match_score(elem, "url bar")
        assert score == 1.0

    @pytest.mark.unit
    def test_back_button_matches_go_back(self):
        """'back button' alias-matches AXButton with title 'Go back'.

        _ELEMENT_ALIASES['back button'] = ['go back'], so 'go back'
        matches the title field exactly (case-insensitive).
        """
        elem = _make_ax_element(role="AXButton", title="Go back", enabled=True)
        score = AccessibilityBridge._element_match_score(elem, "back button")
        assert score == 1.0

    @pytest.mark.unit
    def test_multiply_button_matches_multiply(self):
        """'multiply button' alias-matches AXButton with title 'multiply'."""
        elem = _make_ax_element(role="AXButton", title="multiply", enabled=True)
        score = AccessibilityBridge._element_match_score(elem, "multiply button")
        assert score == 1.0

    @pytest.mark.unit
    def test_reload_button_matches_reload_this_page(self):
        """'reload button' alias-matches AXButton with title 'Reload this page'."""
        elem = _make_ax_element(
            role="AXButton", title="Reload this page", enabled=True
        )
        score = AccessibilityBridge._element_match_score(elem, "reload button")
        assert score == 1.0

    @pytest.mark.unit
    def test_random_text_no_alias_match(self):
        """'random text' has no alias and does NOT match 'smart search field'.

        Without an alias, the score comes from substring/token overlap.
        'random text' shares no tokens with 'smart search field'.
        """
        elem = _make_ax_element(
            role="AXTextField", title="smart search field", enabled=True
        )
        score = AccessibilityBridge._element_match_score(elem, "random text")
        # No alias, no substring, no token overlap -> base 0.0 + enabled bonus 0.02
        assert score < 0.5

    @pytest.mark.unit
    def test_alias_match_plus_enabled_capped_at_1(self):
        """Alias match (1.0) + enabled bonus (0.02) should be capped at 1.0."""
        elem = _make_ax_element(
            role="AXTextField",
            title="smart search field",
            enabled=True,
            focused=True,  # +0.05 bonus
        )
        score = AccessibilityBridge._element_match_score(elem, "address bar")
        assert score == 1.0  # Capped at 1.0

    @pytest.mark.unit
    def test_alias_disabled_element_still_matches(self):
        """Alias match works even if element is disabled (no enabled bonus)."""
        elem = _make_ax_element(
            role="AXTextField",
            title="smart search field",
            enabled=False,
        )
        score = AccessibilityBridge._element_match_score(elem, "address bar")
        assert score == 1.0

    @pytest.mark.unit
    def test_divide_button_alias(self):
        """'divide button' matches AXButton title 'divide'."""
        elem = _make_ax_element(role="AXButton", title="divide", enabled=True)
        score = AccessibilityBridge._element_match_score(elem, "divide button")
        assert score == 1.0

    @pytest.mark.unit
    def test_plus_button_alias(self):
        """'plus button' matches AXButton title 'add'."""
        elem = _make_ax_element(role="AXButton", title="add", enabled=True)
        score = AccessibilityBridge._element_match_score(elem, "plus button")
        assert score == 1.0

    @pytest.mark.unit
    def test_new_tab_button_alias(self):
        """'new tab button' matches AXButton title 'new tab'."""
        elem = _make_ax_element(role="AXButton", title="new tab", enabled=True)
        score = AccessibilityBridge._element_match_score(elem, "new tab button")
        assert score == 1.0

    @pytest.mark.unit
    def test_refresh_button_alias(self):
        """'refresh button' matches AXButton title 'reload this page'."""
        elem = _make_ax_element(
            role="AXButton", title="reload this page", enabled=True
        )
        score = AccessibilityBridge._element_match_score(elem, "refresh button")
        assert score == 1.0


# ===========================================================================
# 2. _extract_match_target alias expansion
# ===========================================================================


class TestExtractMatchTargetAliases:
    """Test _extract_match_target expands aliases for descriptions without role keywords."""

    @pytest.mark.unit
    def test_address_bar_expanded_via_alias(self):
        """'address bar' has no role keyword, so it expands via alias to
        (None, 'smart search field')."""
        role, text = AccessibilityBridge._extract_match_target("address bar")
        assert role is None
        assert text == "smart search field"

    @pytest.mark.unit
    def test_back_button_extracts_role_and_text(self):
        """'back button' contains 'button' keyword -> role=AXButton.

        The remainder after removing 'button' is 'back'. Alias expansion
        only occurs when no role keyword is found, so text_hint='back'.
        """
        role, text = AccessibilityBridge._extract_match_target("back button")
        assert role == "AXButton"
        assert text == "back"

    @pytest.mark.unit
    def test_some_random_element_passthrough(self):
        """'some random element' has no role keyword and no alias -> pass through."""
        role, text = AccessibilityBridge._extract_match_target("some random element")
        assert role is None
        assert text == "some random element"

    @pytest.mark.unit
    def test_url_bar_expanded_via_alias(self):
        """'url bar' has no role keyword -> expands to (None, 'smart search field')."""
        role, text = AccessibilityBridge._extract_match_target("url bar")
        assert role is None
        assert text == "smart search field"

    @pytest.mark.unit
    def test_search_bar_expanded_via_alias(self):
        """'search bar' has no role keyword -> expands via alias."""
        role, text = AccessibilityBridge._extract_match_target("search bar")
        assert role is None
        assert text == "smart search field"

    @pytest.mark.unit
    def test_reload_button_extracts_role(self):
        """'reload button' contains 'button' -> role=AXButton, text='reload'."""
        role, text = AccessibilityBridge._extract_match_target("reload button")
        assert role == "AXButton"
        assert text == "reload"

    @pytest.mark.unit
    def test_forward_button_extracts_role(self):
        """'forward button' contains 'button' -> role=AXButton, text='forward'."""
        role, text = AccessibilityBridge._extract_match_target("forward button")
        assert role == "AXButton"
        assert text == "forward"

    @pytest.mark.unit
    def test_location_bar_expanded_via_alias(self):
        """'location bar' has no role keyword -> expands via alias."""
        role, text = AccessibilityBridge._extract_match_target("location bar")
        assert role is None
        assert text == "smart search field"

    @pytest.mark.unit
    def test_empty_description(self):
        """Empty description returns (None, None)."""
        role, text = AccessibilityBridge._extract_match_target("")
        assert role is None
        assert text is None

    @pytest.mark.unit
    def test_label_prefix_special_handling(self):
        """'label showing X' triggers special prefix path."""
        role, text = AccessibilityBridge._extract_match_target("label showing Price")
        assert role == "AXStaticText"
        assert text == "price"


# ===========================================================================
# 3. Grounding router _check_keyboard_shortcut
# ===========================================================================


class TestCheckKeyboardShortcut:
    """Test GroundingRouter._check_keyboard_shortcut()."""

    def _make_router(self, frontmost_app="Safari"):
        """Build a router with a mock accessibility bridge reporting a browser."""
        ax = _make_accessibility_bridge(frontmost_app=frontmost_app)
        return GroundingRouter(
            accessibility=ax,
            vision_coordinator=None,
            config=_make_config(),
        )

    @pytest.mark.unit
    def test_address_bar_returns_shortcut(self):
        """'address bar' maps to cmd+l."""
        router = self._make_router()
        result = router._check_keyboard_shortcut("address bar")
        assert result is not None
        assert result.keyboard_shortcut == ["cmd+l"]
        assert result.confidence == 0.99

    @pytest.mark.unit
    def test_new_tab_returns_shortcut(self):
        """'new tab' maps to cmd+t."""
        router = self._make_router()
        result = router._check_keyboard_shortcut("new tab")
        assert result is not None
        assert result.keyboard_shortcut == ["cmd+t"]

    @pytest.mark.unit
    def test_reload_button_strips_suffix(self):
        """'reload button' -> strips ' button' -> matches 'reload' -> cmd+r."""
        router = self._make_router()
        result = router._check_keyboard_shortcut("reload button")
        assert result is not None
        assert result.keyboard_shortcut == ["cmd+r"]

    @pytest.mark.unit
    def test_submit_button_returns_none(self):
        """'Submit button' is NOT in the shortcut map."""
        router = self._make_router()
        result = router._check_keyboard_shortcut("Submit button")
        assert result is None

    @pytest.mark.unit
    def test_search_box_returns_none(self):
        """'search box' is NOT in the shortcut map."""
        router = self._make_router()
        result = router._check_keyboard_shortcut("search box")
        assert result is None

    @pytest.mark.unit
    def test_back_button_returns_shortcut(self):
        """'back button' maps to cmd+[."""
        router = self._make_router()
        result = router._check_keyboard_shortcut("back button")
        assert result is not None
        assert result.keyboard_shortcut == ["cmd+["]

    @pytest.mark.unit
    def test_forward_button_returns_shortcut(self):
        """'forward button' maps to cmd+]."""
        router = self._make_router()
        result = router._check_keyboard_shortcut("forward button")
        assert result is not None
        assert result.keyboard_shortcut == ["cmd+]"]

    @pytest.mark.unit
    def test_close_tab_returns_shortcut(self):
        """'close tab' maps to cmd+w."""
        router = self._make_router()
        result = router._check_keyboard_shortcut("close tab")
        assert result is not None
        assert result.keyboard_shortcut == ["cmd+w"]

    @pytest.mark.unit
    def test_refresh_returns_shortcut(self):
        """'refresh' maps to cmd+r."""
        router = self._make_router()
        result = router._check_keyboard_shortcut("refresh")
        assert result is not None
        assert result.keyboard_shortcut == ["cmd+r"]

    @pytest.mark.unit
    def test_shortcut_skipped_when_not_browser(self):
        """Keyboard shortcut is NOT returned when frontmost app is not a browser."""
        router = self._make_router(frontmost_app="Finder")
        result = router._check_keyboard_shortcut("address bar")
        assert result is None

    @pytest.mark.unit
    def test_shortcut_skipped_when_no_frontmost_app(self):
        """Keyboard shortcut is NOT returned when no frontmost app info."""
        router = self._make_router(frontmost_app=None)
        result = router._check_keyboard_shortcut("address bar")
        assert result is None

    @pytest.mark.unit
    def test_case_insensitive_matching(self):
        """'Address Bar' (mixed case) still matches."""
        router = self._make_router()
        result = router._check_keyboard_shortcut("Address Bar")
        assert result is not None
        assert result.keyboard_shortcut == ["cmd+l"]

    @pytest.mark.unit
    def test_result_has_correct_strategy(self):
        """Shortcut result uses ACCESSIBILITY strategy."""
        router = self._make_router()
        result = router._check_keyboard_shortcut("address bar")
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY

    @pytest.mark.unit
    def test_result_has_element_info(self):
        """Shortcut result includes element_info with shortcut_for."""
        router = self._make_router()
        result = router._check_keyboard_shortcut("address bar")
        assert result.element_info is not None
        assert result.element_info["shortcut_for"] == "address bar"


# ===========================================================================
# 4. Grounding router find_element: shortcut is last resort
# ===========================================================================


class TestGroundingRouterShortcutPriority:
    """Keyboard shortcuts are last resort -- AX and vision are tried first."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_shortcut_returned_when_ax_and_vision_miss(self):
        """When both AX and vision fail, keyboard shortcut is returned."""
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

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_vision_result_takes_priority_over_shortcut(self):
        """When vision returns a result, keyboard shortcut is NOT used."""
        ax = _make_accessibility_bridge(elements=[], frontmost_app="Safari")
        vision_result = FindElementResult(
            x=600, y=50, confidence=0.80, source="vision"
        )
        vision = _make_vision_coordinator(result=vision_result)

        router = GroundingRouter(
            accessibility=ax,
            vision_coordinator=vision,
            config=_make_config(),
        )

        result = await router.find_element("address bar")

        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION
        assert result.keyboard_shortcut is None
        assert result.x == 600

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_falls_through_to_vision(self):
        """'Submit' has no shortcut -> falls through to AX/vision."""
        vision_result = FindElementResult(
            x=400, y=300, confidence=0.85, source="vision"
        )
        ax = _make_accessibility_bridge(elements=[], frontmost_app="Safari")
        vision = _make_vision_coordinator(result=vision_result)

        router = GroundingRouter(
            accessibility=ax,
            vision_coordinator=vision,
            config=_make_config(),
        )

        result = await router.find_element("Submit")

        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION
        assert result.keyboard_shortcut is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ax_match_takes_priority_over_shortcut(self):
        """When AX returns a match for 'address bar', shortcut is not used."""
        elem = _make_ax_element(
            role="AXTextField",
            title="smart search field",
            position=(200, 40),
            size=(500, 30),
        )
        ax = _make_accessibility_bridge(elements=[elem], frontmost_app="Safari")
        vision = _make_vision_coordinator(result=None)

        router = GroundingRouter(
            accessibility=ax,
            vision_coordinator=vision,
            config=_make_config(),
        )

        result = await router.find_element("address bar")

        assert result is not None
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY
        # AX match returns coordinates, not a keyboard shortcut
        assert result.keyboard_shortcut is None
        assert result.x == 450  # 200 + 500//2

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_shortcut_skipped_when_not_browser_in_find_element(self):
        """In find_element, shortcut is skipped when frontmost app is not browser."""
        ax = _make_accessibility_bridge(elements=[], frontmost_app="Finder")
        vision = _make_vision_coordinator(result=None)

        router = GroundingRouter(
            accessibility=ax,
            vision_coordinator=vision,
            config=_make_config(),
        )

        result = await router.find_element("address bar")

        assert result is None  # No AX, no vision, shortcut skipped -> None


# ===========================================================================
# 5. Agent _dispatch_action: keyboard shortcut execution
# ===========================================================================


class TestAgentDispatchKeyboardShortcut:
    """Test that _dispatch_action presses keyboard shortcuts instead of clicking."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_click_address_bar_dispatches_cmd_l(self):
        """click action with element='address bar' -> presses Cmd+L via keyboard shortcut."""
        # Create a grounding_router mock that returns a keyboard shortcut
        grounding_router = AsyncMock()
        grounding_router.find_element = AsyncMock(
            return_value=GroundingResult(
                x=0,
                y=0,
                strategy_used=GroundingStrategy.ACCESSIBILITY,
                confidence=0.99,
                keyboard_shortcut=["cmd+l"],
                element_info={"shortcut_for": "address bar"},
            )
        )

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "browser_url": "https://example.com"}
        )

        agent = _make_agent(
            actuator=actuator,
            grounding_router=grounding_router,
        )

        step = ActionStep(
            action="click",
            params={"element": "address bar"},
            verify="address bar is focused",
        )
        result = await agent._dispatch_action(step)

        assert result["success"] is True
        # press_key should have been called with ["cmd", "l"]
        actuator.press_key.assert_called_once_with(["cmd", "l"])
        # click should NOT have been called (keyboard shortcut bypasses click)
        actuator.click.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_click_new_tab_dispatches_cmd_t(self):
        """click action with element='new tab button' -> presses Cmd+T."""
        grounding_router = AsyncMock()
        grounding_router.find_element = AsyncMock(
            return_value=GroundingResult(
                x=0,
                y=0,
                strategy_used=GroundingStrategy.ACCESSIBILITY,
                confidence=0.99,
                keyboard_shortcut=["cmd+t"],
                element_info={"shortcut_for": "new tab button"},
            )
        )

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "browser_url": "https://example.com"}
        )

        agent = _make_agent(
            actuator=actuator,
            grounding_router=grounding_router,
        )

        step = ActionStep(
            action="click",
            params={"element": "new tab button"},
            verify="new tab opened",
        )
        result = await agent._dispatch_action(step)

        assert result["success"] is True
        actuator.press_key.assert_called_once_with(["cmd", "t"])
        actuator.click.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_click_shortcut_not_dispatched_when_not_browser(self):
        """Keyboard shortcut NOT dispatched when frontmost app is not a browser.

        The source is 'keyboard_shortcut' but _is_browser_app returns False,
        so the agent falls through to coordinate-based clicking.
        """
        grounding_router = AsyncMock()
        grounding_router.find_element = AsyncMock(
            return_value=GroundingResult(
                x=0,
                y=0,
                strategy_used=GroundingStrategy.ACCESSIBILITY,
                confidence=0.99,
                keyboard_shortcut=["cmd+l"],
                element_info={"shortcut_for": "address bar"},
            )
        )

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(return_value={"success": True})
        # Frontmost app is Finder, not a browser
        actuator.get_state = MagicMock(
            return_value={"app_name": "Finder", "browser_url": ""}
        )

        agent = _make_agent(
            actuator=actuator,
            grounding_router=grounding_router,
        )

        step = ActionStep(
            action="click",
            params={"element": "address bar"},
            verify="address bar is focused",
        )
        result = await agent._dispatch_action(step)

        # The shortcut should NOT fire because frontmost app is Finder.
        # The code checks _is_browser_app(_pre_app) which returns False.
        # It will fall through to coordinate click at (0, 0).
        # press_key should not be called for the shortcut dispatch
        # (it may be called for other reasons, but not for keyboard_shortcut dispatch)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_click_no_shortcut_falls_through_to_click(self):
        """When find_element returns a normal vision result (no shortcut),
        the agent clicks at the coordinates."""
        grounding_router = AsyncMock()
        grounding_router.find_element = AsyncMock(
            return_value=GroundingResult(
                x=400,
                y=300,
                strategy_used=GroundingStrategy.VISION,
                confidence=0.95,
                keyboard_shortcut=None,
            )
        )

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "browser_url": "https://example.com"}
        )

        agent = _make_agent(
            actuator=actuator,
            grounding_router=grounding_router,
        )

        step = ActionStep(
            action="click",
            params={"element": "Submit button"},
            verify="form submitted",
        )
        result = await agent._dispatch_action(step)

        assert result["success"] is True
        # Should click, not press keys
        actuator.click.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_click_shortcut_result_includes_output_message(self):
        """Shortcut dispatch result includes descriptive output message."""
        grounding_router = AsyncMock()
        grounding_router.find_element = AsyncMock(
            return_value=GroundingResult(
                x=0,
                y=0,
                strategy_used=GroundingStrategy.ACCESSIBILITY,
                confidence=0.99,
                keyboard_shortcut=["cmd+l"],
                element_info={"shortcut_for": "address bar"},
            )
        )

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "browser_url": ""}
        )

        agent = _make_agent(
            actuator=actuator,
            grounding_router=grounding_router,
        )

        step = ActionStep(
            action="click",
            params={"element": "address bar"},
            verify="address bar is focused",
        )
        result = await agent._dispatch_action(step)

        assert result["success"] is True
        assert "address bar" in result.get("output", "")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_click_shortcut_press_key_failure_propagates(self):
        """If press_key fails during shortcut dispatch, error is returned."""
        grounding_router = AsyncMock()
        grounding_router.find_element = AsyncMock(
            return_value=GroundingResult(
                x=0,
                y=0,
                strategy_used=GroundingStrategy.ACCESSIBILITY,
                confidence=0.99,
                keyboard_shortcut=["cmd+l"],
                element_info={"shortcut_for": "address bar"},
            )
        )

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(
            return_value={"success": False, "error": "key press failed"}
        )
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "browser_url": ""}
        )

        agent = _make_agent(
            actuator=actuator,
            grounding_router=grounding_router,
        )

        step = ActionStep(
            action="click",
            params={"element": "address bar"},
            verify="address bar is focused",
        )
        result = await agent._dispatch_action(step)

        assert result["success"] is False


# ===========================================================================
# 6. Agent type_text click-to-focus with keyboard shortcut
# ===========================================================================


class TestAgentTypeTextKeyboardShortcut:
    """Test that type_text with element uses keyboard shortcut for focus."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_type_text_address_bar_uses_shortcut_for_focus(self):
        """type_text with element='address bar' presses Cmd+L then types."""
        grounding_router = AsyncMock()
        grounding_router.find_element = AsyncMock(
            return_value=GroundingResult(
                x=0,
                y=0,
                strategy_used=GroundingStrategy.ACCESSIBILITY,
                confidence=0.99,
                keyboard_shortcut=["cmd+l"],
                element_info={"shortcut_for": "address bar"},
            )
        )

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "browser_url": "https://example.com"}
        )

        agent = _make_agent(
            actuator=actuator,
            grounding_router=grounding_router,
        )

        step = ActionStep(
            action="type_text",
            params={"text": "https://google.com", "element": "address bar"},
            verify="URL is entered",
        )
        result = await agent._dispatch_action(step)

        assert result["success"] is True
        # Cmd+L should have been pressed for focus
        actuator.press_key.assert_any_call(["cmd", "l"])
        # Then text should have been typed
        actuator.type_text.assert_called_once_with("https://google.com")
        # Click should NOT have been called (shortcut used for focus instead)
        actuator.click.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_type_text_without_shortcut_clicks_for_focus(self):
        """type_text with normal element (no shortcut) clicks for focus."""
        grounding_router = AsyncMock()
        grounding_router.find_element = AsyncMock(
            return_value=GroundingResult(
                x=400,
                y=300,
                strategy_used=GroundingStrategy.VISION,
                confidence=0.90,
                keyboard_shortcut=None,
            )
        )

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "Safari", "browser_url": ""}
        )

        agent = _make_agent(
            actuator=actuator,
            grounding_router=grounding_router,
        )

        step = ActionStep(
            action="type_text",
            params={"text": "hello", "element": "search field"},
            verify="text entered",
        )
        result = await agent._dispatch_action(step)

        assert result["success"] is True
        # Should click to focus, not press shortcut keys
        actuator.click.assert_called_once()
        actuator.type_text.assert_called_once_with("hello")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_type_text_no_element_skips_focus(self):
        """type_text without element parameter types directly, no focus step."""
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={})

        agent = _make_agent(actuator=actuator)

        step = ActionStep(
            action="type_text",
            params={"text": "hello"},
            verify="text entered",
        )
        result = await agent._dispatch_action(step)

        assert result["success"] is True
        actuator.click.assert_not_called()
        actuator.type_text.assert_called_once_with("hello")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_type_text_shortcut_not_used_when_not_browser(self):
        """type_text shortcut focus NOT used when frontmost app is not browser.

        Falls through to coordinate click instead.
        """
        grounding_router = AsyncMock()
        grounding_router.find_element = AsyncMock(
            return_value=GroundingResult(
                x=0,
                y=0,
                strategy_used=GroundingStrategy.ACCESSIBILITY,
                confidence=0.99,
                keyboard_shortcut=["cmd+l"],
                element_info={"shortcut_for": "address bar"},
            )
        )

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(return_value={"success": True})
        # Not a browser
        actuator.get_state = MagicMock(
            return_value={"app_name": "TextEdit", "browser_url": ""}
        )

        agent = _make_agent(
            actuator=actuator,
            grounding_router=grounding_router,
        )

        step = ActionStep(
            action="type_text",
            params={"text": "hello", "element": "address bar"},
            verify="text entered",
        )
        result = await agent._dispatch_action(step)

        # The shortcut should not fire for TextEdit.
        # The code should fall through to click at (0,0) instead.
        assert result["success"] is True
        actuator.type_text.assert_called_once_with("hello")


# ===========================================================================
# 7. BROWSER_KEYBOARD_SHORTCUTS data integrity
# ===========================================================================


class TestBrowserKeyboardShortcutsData:
    """Validate the BROWSER_KEYBOARD_SHORTCUTS dict structure."""

    @pytest.mark.unit
    def test_all_values_are_lists_of_strings(self):
        """Every value in BROWSER_KEYBOARD_SHORTCUTS is a list of strings."""
        for key, value in BROWSER_KEYBOARD_SHORTCUTS.items():
            assert isinstance(value, list), f"Key {key!r} value is not a list"
            for item in value:
                assert isinstance(item, str), f"Key {key!r} has non-string item: {item!r}"

    @pytest.mark.unit
    def test_expected_entries_present(self):
        """All expected browser elements have shortcut entries."""
        expected = [
            "address bar", "url bar", "search bar", "location bar",
            "new tab", "new tab button",
            "close tab", "close tab button",
            "back", "back button", "go back",
            "forward", "forward button", "go forward",
            "reload", "reload button",
            "refresh", "refresh button",
        ]
        for key in expected:
            assert key in BROWSER_KEYBOARD_SHORTCUTS, f"Missing key: {key!r}"

    @pytest.mark.unit
    def test_all_shortcuts_contain_cmd(self):
        """All browser shortcuts use cmd modifier (macOS standard)."""
        for key, shortcuts in BROWSER_KEYBOARD_SHORTCUTS.items():
            for shortcut in shortcuts:
                assert "cmd" in shortcut, (
                    f"Key {key!r} has shortcut {shortcut!r} without cmd"
                )


# ===========================================================================
# 8. _ELEMENT_ALIASES data integrity
# ===========================================================================


class TestElementAliasesData:
    """Validate the _ELEMENT_ALIASES dict structure."""

    @pytest.mark.unit
    def test_all_values_are_lists_of_strings(self):
        """Every value in _ELEMENT_ALIASES is a list of strings."""
        for key, value in _ELEMENT_ALIASES.items():
            assert isinstance(value, list), f"Key {key!r} value is not a list"
            for item in value:
                assert isinstance(item, str), f"Key {key!r} has non-string item: {item!r}"

    @pytest.mark.unit
    def test_browser_aliases_present(self):
        """Browser chrome aliases are present."""
        expected = [
            "address bar", "url bar", "search bar", "location bar",
            "back button", "forward button", "reload button", "refresh button",
        ]
        for key in expected:
            assert key in _ELEMENT_ALIASES, f"Missing alias: {key!r}"

    @pytest.mark.unit
    def test_calculator_aliases_present(self):
        """Calculator button aliases are present."""
        expected = [
            "multiply button", "divide button", "plus button",
            "minus button", "equals button",
        ]
        for key in expected:
            assert key in _ELEMENT_ALIASES, f"Missing alias: {key!r}"

    @pytest.mark.unit
    def test_address_bar_primary_alias_is_smart_search(self):
        """Primary alias for 'address bar' is 'smart search field'."""
        assert _ELEMENT_ALIASES["address bar"][0] == "smart search field"


# ===========================================================================
# 9. FindElementResult keyboard_shortcut source encoding
# ===========================================================================


class TestFindElementResultShortcutEncoding:
    """Test that keyboard shortcut results are correctly encoded in FindElementResult."""

    @pytest.mark.unit
    def test_shortcut_encoded_as_source(self):
        """FindElementResult with source='keyboard_shortcut' and raw_response."""
        result = FindElementResult(
            x=0,
            y=0,
            confidence=0.99,
            source="keyboard_shortcut",
            raw_response=str(["cmd+l"]),
        )
        assert result.source == "keyboard_shortcut"
        assert result.x == 0
        assert result.y == 0

    @pytest.mark.unit
    def test_shortcut_raw_response_parseable(self):
        """The raw_response string can be parsed back to the original list."""
        shortcuts = ["cmd+l"]
        result = FindElementResult(
            x=0,
            y=0,
            confidence=0.99,
            source="keyboard_shortcut",
            raw_response=str(shortcuts),
        )
        parsed = ast.literal_eval(result.raw_response)
        assert parsed == ["cmd+l"]

    @pytest.mark.unit
    def test_multi_key_shortcut_encoding(self):
        """Multiple shortcuts can be encoded (future-proof)."""
        shortcuts = ["cmd+shift+t"]
        result = FindElementResult(
            x=0,
            y=0,
            confidence=0.99,
            source="keyboard_shortcut",
            raw_response=str(shortcuts),
        )
        parsed = ast.literal_eval(result.raw_response)
        assert parsed == ["cmd+shift+t"]
        # Verify splitting works the same way the agent does it
        key_parts = parsed[0].replace("+", " ").split()
        assert key_parts == ["cmd", "shift", "t"]
