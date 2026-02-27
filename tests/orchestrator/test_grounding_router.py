"""Comprehensive tests for the GroundingRouter (Mixture-of-Grounding).

Tests cover:
- classify() returns correct strategy order for each keyword category
- find_element() tries strategies in order, stops at first success
- find_element() falls back when first strategy returns None
- All strategies fail -> returns None
- Accessibility not available -> skips to next strategy
- Vision not available -> skips to next strategy
- GroundingResult has correct fields
- Confidence levels: accessibility=0.95, vision=0.75
- Concurrent find_element calls
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.orchestrator.grounding_router import (
    GroundingResult,
    GroundingRouter,
    GroundingStrategy,
)
from automation_agent.perception.accessibility import AXElement


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_ax_element():
    """AXElement with a known center."""
    return AXElement(
        role="AXButton",
        title="Submit",
        position=(100, 200),
        size=(80, 30),
    )


@pytest.fixture
def mock_accessibility(mock_ax_element):
    bridge = MagicMock()
    bridge.find_element_by_description = MagicMock(return_value=mock_ax_element)
    return bridge


@pytest.fixture
def mock_vision():
    coord = AsyncMock()
    coord.find_element = AsyncMock(return_value={"x": 500, "y": 400})
    return coord


@pytest.fixture
def router(mock_accessibility, mock_vision):
    return GroundingRouter(
        accessibility=mock_accessibility,
        vision_coordinator=mock_vision,
    )


@pytest.fixture
def router_no_accessibility(mock_vision):
    return GroundingRouter(accessibility=None, vision_coordinator=mock_vision)


@pytest.fixture
def router_no_vision(mock_accessibility):
    return GroundingRouter(accessibility=mock_accessibility, vision_coordinator=None)


@pytest.fixture
def router_nothing():
    return GroundingRouter(accessibility=None, vision_coordinator=None)


# ---------------------------------------------------------------------------
# classify() tests
# ---------------------------------------------------------------------------

class TestClassify:
    """Tests for keyword-based strategy classification."""

    @pytest.mark.unit
    @pytest.mark.parametrize("desc", [
        "click the Submit button",
        "text field for username",
        "input for email",
        "the checkbox labeled agree",
        "radio option female",
        "open the dropdown",
        "click menu item File",
        "select the tab Settings",
        "move the slider to 50",
        "click the link More Info",
        "press submit",
    ])
    def test_widget_keywords_return_accessibility_first(self, desc):
        router = GroundingRouter()
        strategies = router.classify(desc)
        assert strategies[0] == GroundingStrategy.ACCESSIBILITY
        assert strategies[1] == GroundingStrategy.VISION

    @pytest.mark.unit
    @pytest.mark.parametrize("desc", [
        "text that says Hello World",
        "label showing Total",
        "heading About Us",
        "price of the item",
        "number of results",
    ])
    def test_text_keywords_return_ocr_first(self, desc):
        router = GroundingRouter()
        strategies = router.classify(desc)
        assert strategies[0] == GroundingStrategy.OCR
        assert strategies[1] == GroundingStrategy.VISION

    @pytest.mark.unit
    @pytest.mark.parametrize("desc", [
        "the product image",
        "settings icon",
        "video thumbnail",
        "video player",
        "company logo",
        "chart showing revenue",
    ])
    def test_visual_keywords_return_vision_first(self, desc):
        router = GroundingRouter()
        strategies = router.classify(desc)
        assert strategies[0] == GroundingStrategy.VISION
        assert strategies[1] == GroundingStrategy.ACCESSIBILITY

    @pytest.mark.unit
    @pytest.mark.parametrize("desc", [
        "first search result",
        "second item in list",
        "top navigation bar",
        "bottom of the page",
        "closest match",
        "most popular item",
    ])
    def test_position_keywords_return_vision_first(self, desc):
        router = GroundingRouter()
        strategies = router.classify(desc)
        assert strategies[0] == GroundingStrategy.VISION
        assert strategies[1] == GroundingStrategy.ACCESSIBILITY

    @pytest.mark.unit
    def test_default_returns_accessibility_first(self):
        router = GroundingRouter()
        strategies = router.classify("something unrecognized entirely")
        assert strategies[0] == GroundingStrategy.ACCESSIBILITY
        assert strategies[1] == GroundingStrategy.VISION

    @pytest.mark.unit
    def test_classify_is_case_insensitive(self):
        router = GroundingRouter()
        assert router.classify("BUTTON")[0] == GroundingStrategy.ACCESSIBILITY
        assert router.classify("Image")[0] == GroundingStrategy.VISION

    @pytest.mark.unit
    def test_classify_returns_exactly_two_strategies(self):
        router = GroundingRouter()
        for desc in ["button", "label", "image", "first", "xyz"]:
            strategies = router.classify(desc)
            assert len(strategies) == 2


# ---------------------------------------------------------------------------
# find_element() tests
# ---------------------------------------------------------------------------

class TestFindElement:
    """Tests for the strategy-cascade find_element method."""

    @pytest.mark.unit
    async def test_stops_at_first_success(self, router, mock_accessibility):
        result = await router.find_element("Submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY
        # Vision should not have been called
        router.vision.find_element.assert_not_called()

    @pytest.mark.unit
    async def test_falls_back_when_first_returns_none(
        self, router, mock_accessibility, mock_vision
    ):
        mock_accessibility.find_element_by_description.return_value = None
        result = await router.find_element("Submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION
        assert result.x == 500
        assert result.y == 400

    @pytest.mark.unit
    async def test_all_strategies_fail_returns_none(self, router_nothing):
        result = await router_nothing.find_element("Submit button")
        assert result is None

    @pytest.mark.unit
    async def test_accessibility_not_available_skips(
        self, router_no_accessibility, mock_vision
    ):
        result = await router_no_accessibility.find_element("Submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION

    @pytest.mark.unit
    async def test_vision_not_available_skips(
        self, router_no_vision, mock_accessibility
    ):
        result = await router_no_vision.find_element("Submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY

    @pytest.mark.unit
    async def test_vision_returns_none_falls_through(self, router):
        router.accessibility.find_element_by_description.return_value = None
        router.vision.find_element.return_value = None
        result = await router.find_element("Submit button")
        assert result is None

    @pytest.mark.unit
    async def test_accessibility_exception_falls_through(self, router, mock_vision):
        router.accessibility.find_element_by_description.side_effect = RuntimeError("AX crash")
        result = await router.find_element("Submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION

    @pytest.mark.unit
    async def test_vision_exception_returns_none(self, router):
        router.accessibility.find_element_by_description.return_value = None
        router.vision.find_element.side_effect = RuntimeError("vision crash")
        result = await router.find_element("Submit button")
        assert result is None

    @pytest.mark.unit
    async def test_ocr_strategy_stub_returns_none(self, router):
        """OCR stub returns None; vision fallback should succeed."""
        result = await router.find_element("text that says Welcome")
        # OCR first (returns None), then vision
        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION


# ---------------------------------------------------------------------------
# GroundingResult tests
# ---------------------------------------------------------------------------

class TestGroundingResult:
    """Tests for GroundingResult dataclass fields and confidence."""

    @pytest.mark.unit
    async def test_accessibility_result_fields(self, router):
        result = await router.find_element("Submit button")
        assert result.x == 140  # 100 + 80//2
        assert result.y == 215  # 200 + 30//2
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY
        assert result.confidence == 0.95
        assert result.element_info is not None
        assert result.element_info["role"] == "AXButton"
        assert result.element_info["title"] == "Submit"

    @pytest.mark.unit
    async def test_vision_result_fields(self, router):
        router.accessibility.find_element_by_description.return_value = None
        result = await router.find_element("Submit button")
        assert result.x == 500
        assert result.y == 400
        assert result.strategy_used == GroundingStrategy.VISION
        assert result.confidence == 0.75
        assert result.element_info is None

    @pytest.mark.unit
    def test_grounding_result_default_element_info(self):
        gr = GroundingResult(
            x=10, y=20,
            strategy_used=GroundingStrategy.VISION,
            confidence=0.8,
        )
        assert gr.element_info is None

    @pytest.mark.unit
    def test_grounding_result_with_element_info(self):
        gr = GroundingResult(
            x=10, y=20,
            strategy_used=GroundingStrategy.ACCESSIBILITY,
            confidence=0.95,
            element_info={"role": "AXButton", "title": "OK"},
        )
        assert gr.element_info["role"] == "AXButton"


# ---------------------------------------------------------------------------
# Concurrent calls
# ---------------------------------------------------------------------------

class TestConcurrency:
    """Tests for concurrent find_element calls."""

    @pytest.mark.unit
    async def test_concurrent_find_element_calls(self, mock_accessibility, mock_vision):
        router = GroundingRouter(
            accessibility=mock_accessibility,
            vision_coordinator=mock_vision,
        )
        results = await asyncio.gather(
            router.find_element("Submit button"),
            router.find_element("Cancel button"),
            router.find_element("the company logo"),
        )
        assert len(results) == 3
        # First two are widget keywords -> accessibility first
        assert results[0] is not None
        assert results[1] is not None
        # Third is visual keyword -> vision first
        assert results[2] is not None

    @pytest.mark.unit
    async def test_concurrent_with_mixed_failures(self, mock_vision):
        """Some calls find via accessibility, some fall through to vision."""
        ax = MagicMock()
        call_count = 0

        def alternating_find(desc):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                return None
            return AXElement(
                role="AXButton", title="Test",
                position=(50, 50), size=(20, 20),
            )

        ax.find_element_by_description = MagicMock(side_effect=alternating_find)
        router = GroundingRouter(
            accessibility=ax, vision_coordinator=mock_vision,
        )
        results = await asyncio.gather(
            router.find_element("button A"),
            router.find_element("button B"),
            router.find_element("button C"),
        )
        assert all(r is not None for r in results)


# ---------------------------------------------------------------------------
# GroundingStrategy enum
# ---------------------------------------------------------------------------

class TestGroundingStrategy:

    @pytest.mark.unit
    def test_enum_values(self):
        assert GroundingStrategy.ACCESSIBILITY.value == "accessibility"
        assert GroundingStrategy.VISION.value == "vision"
        assert GroundingStrategy.OCR.value == "ocr"

    @pytest.mark.unit
    def test_enum_members_count(self):
        assert len(GroundingStrategy) == 3


# ---------------------------------------------------------------------------
# Agent integration tests
# ---------------------------------------------------------------------------

class TestAgentIntegration:
    """Test that AutomationAgent uses grounding_router when provided."""

    @pytest.mark.unit
    async def test_agent_uses_grounding_router_for_click(self):
        """When grounding_router is set, _find_element delegates to it."""
        from automation_agent.orchestrator.agent import AutomationAgent

        mock_router = MagicMock()
        mock_router.find_element = AsyncMock(
            return_value=GroundingResult(
                x=300, y=250,
                strategy_used=GroundingStrategy.ACCESSIBILITY,
                confidence=0.95,
            )
        )

        agent = AutomationAgent(
            planner=MagicMock(),
            skill_registry=MagicMock(),
            coordinator=MagicMock(),
            actuator=MagicMock(),
            config=MagicMock(event_log_dir=None),
            logger=MagicMock(),
            grounding_router=mock_router,
        )

        result = await agent._find_element("Submit button")
        assert result is not None
        assert result["x"] == 300
        assert result["y"] == 250
        assert result["source"] == "accessibility"
        mock_router.find_element.assert_awaited_once_with("Submit button")

    @pytest.mark.unit
    async def test_agent_falls_back_to_coordinator_without_router(self):
        """Without grounding_router, _find_element uses coordinator."""
        from automation_agent.orchestrator.agent import AutomationAgent

        mock_coordinator = MagicMock()
        mock_coordinator.find_element = AsyncMock(
            return_value={"x": 100, "y": 200, "source": "vision"}
        )

        agent = AutomationAgent(
            planner=MagicMock(),
            skill_registry=MagicMock(),
            coordinator=mock_coordinator,
            actuator=MagicMock(),
            config=MagicMock(event_log_dir=None),
            logger=MagicMock(),
            grounding_router=None,
        )

        result = await agent._find_element("Submit button")
        assert result is not None
        assert result["x"] == 100
        mock_coordinator.find_element.assert_awaited_once_with("Submit button")

    @pytest.mark.unit
    async def test_agent_router_returns_none_propagates(self):
        """When router returns None, _find_element returns None."""
        from automation_agent.orchestrator.agent import AutomationAgent

        mock_router = MagicMock()
        mock_router.find_element = AsyncMock(return_value=None)

        agent = AutomationAgent(
            planner=MagicMock(),
            skill_registry=MagicMock(),
            coordinator=MagicMock(),
            actuator=MagicMock(),
            config=MagicMock(event_log_dir=None),
            logger=MagicMock(),
            grounding_router=mock_router,
        )

        result = await agent._find_element("nonexistent thing")
        assert result is None
