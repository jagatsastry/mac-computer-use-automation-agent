"""Adversarial tests for the GroundingRouter (Mixture-of-Grounding).

Targets edge cases in classify(), find_element() fallback chains,
GroundingResult validation, strategy routing accuracy, component
availability, concurrency, and OCR stub behaviour.

All dependencies are mocked -- no live desktop, vision server, or
accessibility API required.
"""

import asyncio
import sys
import types
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level mocks for pyobjc frameworks (may not be installed)
# ---------------------------------------------------------------------------
_mock_appkit = MagicMock()
_mock_app_services = MagicMock()
_mock_app_services.AXUIElementCreateSystemWide = MagicMock()
_mock_app_services.AXUIElementCreateApplication = MagicMock()
_mock_app_services.AXUIElementCopyAttributeValue = MagicMock()

sys.modules.setdefault("AppKit", _mock_appkit)
sys.modules.setdefault("ApplicationServices", _mock_app_services)
sys.modules.setdefault("CoreFoundation", MagicMock())
sys.modules.setdefault("Quartz", MagicMock())
sys.modules.setdefault("objc", MagicMock())
sys.modules.setdefault("Foundation", MagicMock())
sys.modules.setdefault("PyObjCTools", MagicMock())

from automation_agent.orchestrator.grounding_router import (  # noqa: E402
    GroundingResult,
    GroundingRouter,
    GroundingStrategy,
)
from automation_agent.perception.accessibility import AXElement  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ax_element(
    role: str = "AXButton",
    title: Optional[str] = "OK",
    position: Optional[Tuple[int, int]] = (100, 200),
    size: Optional[Tuple[int, int]] = (80, 30),
    enabled: bool = True,
) -> AXElement:
    """Create an AXElement with sensible defaults."""
    return AXElement(
        role=role,
        title=title,
        position=position,
        size=size,
        enabled=enabled,
    )


def _make_accessibility(
    find_result: Optional[AXElement] = None,
    find_raises: Optional[Exception] = None,
) -> MagicMock:
    """Return a mock AccessibilityBridge."""
    bridge = MagicMock()
    if find_raises is not None:
        bridge.find_element_by_description = MagicMock(side_effect=find_raises)
    else:
        bridge.find_element_by_description = MagicMock(return_value=find_result)
    return bridge


def _make_vision(
    find_result: Optional[Dict[str, Any]] = None,
    find_raises: Optional[Exception] = None,
) -> AsyncMock:
    """Return a mock vision coordinator (ScreenCoordinator)."""
    vision = AsyncMock()
    if find_raises is not None:
        vision.find_element = AsyncMock(side_effect=find_raises)
    else:
        vision.find_element = AsyncMock(return_value=find_result)
    return vision


# ============================================================================
# 1. classify() Edge Cases
# ============================================================================


@pytest.mark.unit
class TestClassifyEdgeCases:

    def test_empty_string_returns_default_order(self):
        """Empty description should return the default strategy order."""
        router = GroundingRouter()
        result = router.classify("")
        assert isinstance(result, list)
        assert len(result) >= 1
        # Default order per design: accessibility first
        assert GroundingStrategy.ACCESSIBILITY in result

    def test_only_filler_words(self):
        """Description with only filler words ('the', 'a', 'that') should
        not crash and should return a valid list."""
        router = GroundingRouter()
        result = router.classify("the a that an")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_description_matching_multiple_categories(self):
        """'the button icon' matches both widget ('button') and visual ('icon').
        classify() must return a list, not crash or return empty."""
        router = GroundingRouter()
        result = router.classify("the button icon")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_case_sensitivity_uppercase(self):
        """'BUTTON' should still match 'button' keyword (case-insensitive)."""
        router = GroundingRouter()
        result = router.classify("BUTTON")
        assert GroundingStrategy.ACCESSIBILITY in result

    def test_case_sensitivity_mixed_case(self):
        """'BuTtOn' should still be classified as widget."""
        router = GroundingRouter()
        result = router.classify("BuTtOn")
        assert GroundingStrategy.ACCESSIBILITY in result

    def test_case_sensitivity_title_case(self):
        """'Button' (title case) should match widget category."""
        router = GroundingRouter()
        result = router.classify("Button")
        assert GroundingStrategy.ACCESSIBILITY in result

    def test_unicode_description(self):
        """Non-ASCII description should not crash classify()."""
        router = GroundingRouter()
        result = router.classify("\u30af\u30ea\u30c3\u30af")  # Japanese for "click"
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_very_long_description(self):
        """1000+ character description should not crash or take too long."""
        router = GroundingRouter()
        desc = "click the button " * 100  # ~1700 chars
        result = router.classify(desc)
        assert isinstance(result, list)
        assert len(result) >= 1
        # Should still match "button" keyword
        assert GroundingStrategy.ACCESSIBILITY in result

    def test_special_chars_in_description(self):
        """Description with special characters should not crash."""
        router = GroundingRouter()
        for desc in [
            "button<script>alert('xss')</script>",
            "button\n\twith\r\nnewlines",
            "button\x00with\x01nulls",
            'button "quoted" \'single\'',
            "button & ampersand | pipe",
        ]:
            result = router.classify(desc)
            assert isinstance(result, list), f"Failed for: {desc!r}"

    def test_substring_trap_buttonhole(self):
        """'buttonhole' contains 'button' as substring. This tests whether
        the classifier does naive substring matching. Either behaviour
        (match or not) is acceptable, but it must not crash."""
        router = GroundingRouter()
        result = router.classify("buttonhole")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_submit_alone_vs_submit_button(self):
        """'submit' alone and 'submit button' should both route to
        accessibility (both contain the keyword 'submit')."""
        router = GroundingRouter()
        r1 = router.classify("submit")
        r2 = router.classify("submit button")
        # Both should include ACCESSIBILITY
        assert GroundingStrategy.ACCESSIBILITY in r1
        assert GroundingStrategy.ACCESSIBILITY in r2

    def test_none_description_raises_or_handles(self):
        """None as description: should raise TypeError or handle gracefully."""
        router = GroundingRouter()
        with pytest.raises((TypeError, AttributeError)):
            router.classify(None)  # type: ignore[arg-type]

    def test_numeric_description(self):
        """Pure numeric string should not crash."""
        router = GroundingRouter()
        result = router.classify("12345")
        assert isinstance(result, list)

    def test_emoji_description(self):
        """Emoji-only description should not crash."""
        router = GroundingRouter()
        result = router.classify("\U0001f600\U0001f680\U0001f4a5")
        assert isinstance(result, list)

    def test_keyword_with_surrounding_punctuation(self):
        """Quoted keyword 'button' should still be matched."""
        router = GroundingRouter()
        result = router.classify("click the 'button'")
        # The word "button" is present in the lowered string
        assert GroundingStrategy.ACCESSIBILITY in result

    def test_keyword_with_surrounding_brackets(self):
        """'[button]' should still match because 'button' is a substring."""
        router = GroundingRouter()
        result = router.classify("[button]")
        assert GroundingStrategy.ACCESSIBILITY in result

    def test_all_widget_keywords(self):
        """Every widget keyword from the design doc should route to
        ACCESSIBILITY first."""
        router = GroundingRouter()
        widget_keywords = [
            "button", "text field", "input", "checkbox", "radio",
            "dropdown", "menu", "tab", "slider", "link", "submit",
        ]
        for kw in widget_keywords:
            result = router.classify(kw)
            assert result[0] == GroundingStrategy.ACCESSIBILITY, (
                f"Keyword '{kw}' did not route to ACCESSIBILITY first"
            )

    def test_all_text_keywords(self):
        """Every text keyword from the design doc should route to OCR first."""
        router = GroundingRouter()
        text_keywords = [
            "text that says", "label", "heading", "price", "number",
        ]
        for kw in text_keywords:
            result = router.classify(kw)
            assert result[0] == GroundingStrategy.OCR, (
                f"Keyword '{kw}' did not route to OCR first"
            )

    def test_all_visual_keywords(self):
        """Every visual keyword from the design doc should route to
        VISION first."""
        router = GroundingRouter()
        visual_keywords = [
            "image", "icon", "thumbnail", "video", "logo", "chart",
        ]
        for kw in visual_keywords:
            result = router.classify(kw)
            assert result[0] == GroundingStrategy.VISION, (
                f"Keyword '{kw}' did not route to VISION first"
            )

    def test_all_positional_keywords(self):
        """Position-based keywords should route to VISION first."""
        router = GroundingRouter()
        pos_keywords = [
            "first", "second", "top", "bottom", "closest", "most popular",
        ]
        for kw in pos_keywords:
            result = router.classify(kw)
            assert result[0] == GroundingStrategy.VISION, (
                f"Keyword '{kw}' did not route to VISION first"
            )

    def test_no_keyword_matches_default_order(self):
        """Description with no known keywords should return the default
        strategy order (accessibility, then vision)."""
        router = GroundingRouter()
        result = router.classify("xyzzy flurbknot")
        assert result == [
            GroundingStrategy.ACCESSIBILITY,
            GroundingStrategy.VISION,
        ]

    def test_classify_returns_no_duplicates(self):
        """Strategy list should not contain duplicates."""
        router = GroundingRouter()
        for desc in ["button", "icon", "price", "xyzzy", "first button icon"]:
            result = router.classify(desc)
            assert len(result) == len(set(result)), (
                f"Duplicates in classify('{desc}'): {result}"
            )

    def test_classify_always_returns_at_least_one(self):
        """classify() should never return an empty list."""
        router = GroundingRouter()
        for desc in ["", "   ", "\n", "xyzzy", "button", "icon"]:
            result = router.classify(desc)
            assert len(result) >= 1, f"Empty result for: {desc!r}"


# ============================================================================
# 2. find_element Fallback Chain
# ============================================================================


@pytest.mark.unit
class TestFindElementFallbackChain:

    @pytest.mark.asyncio
    async def test_first_strategy_succeeds(self):
        """When accessibility finds the element, vision should not be called."""
        ax = _make_accessibility(_make_ax_element(position=(100, 200), size=(80, 30)))
        vis = _make_vision({"x": 999, "y": 999})
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        result = await router.find_element("submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY
        vis.find_element.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_first_returns_none_falls_through(self):
        """Accessibility returns None => falls through to vision."""
        ax = _make_accessibility(None)
        vis = _make_vision({"x": 300, "y": 400})
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        result = await router.find_element("submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION

    @pytest.mark.asyncio
    async def test_first_raises_exception_falls_through(self):
        """Accessibility raises exception => falls through to vision."""
        ax = _make_accessibility(find_raises=RuntimeError("AX unavailable"))
        vis = _make_vision({"x": 300, "y": 400})
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        result = await router.find_element("submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION

    @pytest.mark.asyncio
    async def test_all_strategies_raise_returns_none(self):
        """All strategies throw exceptions => returns None gracefully."""
        ax = _make_accessibility(find_raises=RuntimeError("AX crashed"))
        vis = _make_vision(find_raises=TimeoutError("Vision timed out"))
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        result = await router.find_element("submit button")
        assert result is None

    @pytest.mark.asyncio
    async def test_all_strategies_return_none(self):
        """All strategies return None => returns None."""
        ax = _make_accessibility(None)
        vis = _make_vision(None)
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        result = await router.find_element("submit button")
        assert result is None

    @pytest.mark.asyncio
    async def test_accessibility_returns_element_without_center(self):
        """AXElement with no position/size => center is None => skip,
        fall through to vision."""
        elem = _make_ax_element(position=None, size=None)
        assert elem.center is None  # precondition
        ax = _make_accessibility(elem)
        vis = _make_vision({"x": 300, "y": 400})
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        result = await router.find_element("submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION

    @pytest.mark.asyncio
    async def test_strategy_returns_result_with_zero_coordinates(self):
        """GroundingResult with x=0, y=0 is still a valid result (top-left
        corner could be legitimate)."""
        ax = _make_accessibility(
            _make_ax_element(position=(0, 0), size=(80, 30))
        )
        router = GroundingRouter(accessibility=ax)
        result = await router.find_element("submit button")
        assert result is not None
        # (0 + 80//2, 0 + 30//2) = (40, 15)
        assert result.x == 40
        assert result.y == 15

    @pytest.mark.asyncio
    async def test_vision_result_with_zero_coordinates(self):
        """Vision returning x=0, y=0 is treated as a valid result."""
        ax = _make_accessibility(None)
        vis = _make_vision({"x": 0, "y": 0})
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        result = await router.find_element("icon at corner")
        assert result is not None
        assert result.x == 0
        assert result.y == 0

    @pytest.mark.asyncio
    async def test_no_components_available(self):
        """Both accessibility and vision are None => returns None."""
        router = GroundingRouter(accessibility=None, vision_coordinator=None)
        result = await router.find_element("submit button")
        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_order_respects_classify(self):
        """For 'icon' description, vision should be tried first."""
        ax = _make_accessibility(
            _make_ax_element(position=(100, 100), size=(50, 50))
        )
        vis = _make_vision({"x": 500, "y": 600})
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        result = await router.find_element("application icon")
        assert result is not None
        # "icon" routes to vision first per design
        assert result.strategy_used == GroundingStrategy.VISION

    @pytest.mark.asyncio
    async def test_vision_first_fails_then_accessibility(self):
        """For visual element, vision fails => falls back to accessibility."""
        ax = _make_accessibility(
            _make_ax_element(position=(100, 100), size=(50, 50))
        )
        vis = _make_vision(None)  # Vision returns nothing
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        result = await router.find_element("application icon")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY


# ============================================================================
# 3. GroundingResult Validation
# ============================================================================


@pytest.mark.unit
class TestGroundingResultValidation:

    def test_zero_coordinates(self):
        """(0, 0) is a valid coordinate (top-left corner)."""
        r = GroundingResult(
            x=0, y=0,
            strategy_used=GroundingStrategy.ACCESSIBILITY,
            confidence=0.95,
        )
        assert r.x == 0
        assert r.y == 0

    def test_negative_coordinates(self):
        """Negative coordinates should be storable (whether valid is
        context-dependent; the dataclass should not reject them)."""
        r = GroundingResult(
            x=-10, y=-20,
            strategy_used=GroundingStrategy.VISION,
            confidence=0.5,
        )
        assert r.x == -10
        assert r.y == -20

    def test_very_large_coordinates(self):
        """Coordinates beyond typical screen bounds should be storable."""
        r = GroundingResult(
            x=10000, y=10000,
            strategy_used=GroundingStrategy.VISION,
            confidence=0.3,
        )
        assert r.x == 10000
        assert r.y == 10000

    def test_confidence_zero(self):
        """confidence=0.0 is a valid (low) confidence."""
        r = GroundingResult(
            x=100, y=200,
            strategy_used=GroundingStrategy.VISION,
            confidence=0.0,
        )
        assert r.confidence == 0.0

    def test_confidence_one(self):
        """confidence=1.0 is a valid (max) confidence."""
        r = GroundingResult(
            x=100, y=200,
            strategy_used=GroundingStrategy.ACCESSIBILITY,
            confidence=1.0,
        )
        assert r.confidence == 1.0

    def test_confidence_negative(self):
        """confidence=-0.1 is a weird value but should be storable."""
        r = GroundingResult(
            x=100, y=200,
            strategy_used=GroundingStrategy.VISION,
            confidence=-0.1,
        )
        assert r.confidence == -0.1

    def test_confidence_above_one(self):
        """confidence=1.5 is out of [0,1] but should be storable."""
        r = GroundingResult(
            x=100, y=200,
            strategy_used=GroundingStrategy.VISION,
            confidence=1.5,
        )
        assert r.confidence == 1.5

    def test_element_info_none(self):
        """element_info=None is the default and should work."""
        r = GroundingResult(
            x=100, y=200,
            strategy_used=GroundingStrategy.ACCESSIBILITY,
            confidence=0.9,
            element_info=None,
        )
        assert r.element_info is None

    def test_element_info_empty_dict(self):
        """element_info={} should be accepted."""
        r = GroundingResult(
            x=100, y=200,
            strategy_used=GroundingStrategy.ACCESSIBILITY,
            confidence=0.9,
            element_info={},
        )
        assert r.element_info == {}

    def test_element_info_deeply_nested(self):
        """Deeply nested element_info should be storable."""
        nested = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
        r = GroundingResult(
            x=100, y=200,
            strategy_used=GroundingStrategy.ACCESSIBILITY,
            confidence=0.9,
            element_info=nested,
        )
        assert r.element_info["a"]["b"]["c"]["d"]["e"] == "deep"

    def test_all_strategy_enum_values(self):
        """Every GroundingStrategy value should be usable in GroundingResult."""
        for strategy in GroundingStrategy:
            r = GroundingResult(
                x=100, y=200,
                strategy_used=strategy,
                confidence=0.8,
            )
            assert r.strategy_used == strategy


# ============================================================================
# 4. Strategy Routing Accuracy (comprehensive keyword coverage)
# ============================================================================


@pytest.mark.unit
class TestStrategyRoutingAccuracy:

    def test_widget_keyword_routes_accessibility_first(self):
        """All widget keywords from design doc section 7 should route to
        ACCESSIBILITY as first strategy."""
        router = GroundingRouter()
        for kw in [
            "button", "text field", "input", "checkbox", "radio",
            "dropdown", "menu", "tab", "slider", "link", "submit",
        ]:
            strategies = router.classify(kw)
            assert strategies[0] == GroundingStrategy.ACCESSIBILITY, (
                f"'{kw}' first strategy is {strategies[0]}, expected ACCESSIBILITY"
            )
            assert GroundingStrategy.VISION in strategies, (
                f"'{kw}' should include VISION as fallback"
            )

    def test_text_keyword_routes_ocr_first(self):
        """All text keywords from design doc should route to OCR first."""
        router = GroundingRouter()
        for kw in [
            "text that says", "label", "heading", "price", "number",
        ]:
            strategies = router.classify(kw)
            assert strategies[0] == GroundingStrategy.OCR, (
                f"'{kw}' first strategy is {strategies[0]}, expected OCR"
            )

    def test_visual_keyword_routes_vision_first(self):
        """Visual keywords should route to VISION first."""
        router = GroundingRouter()
        for kw in [
            "image", "icon", "thumbnail", "video", "logo", "chart",
        ]:
            strategies = router.classify(kw)
            assert strategies[0] == GroundingStrategy.VISION, (
                f"'{kw}' first strategy is {strategies[0]}, expected VISION"
            )

    def test_positional_keyword_routes_vision_first(self):
        """Position-based keywords should route to VISION first."""
        router = GroundingRouter()
        for kw in [
            "first", "second", "top", "bottom", "closest", "most popular",
        ]:
            strategies = router.classify(kw)
            assert strategies[0] == GroundingStrategy.VISION, (
                f"'{kw}' first strategy is {strategies[0]}, expected VISION"
            )

    def test_overlapping_keywords_deterministic(self):
        """Descriptions matching 2+ categories should produce a deterministic
        result (same input => same output)."""
        router = GroundingRouter()
        desc = "the first submit button icon"
        r1 = router.classify(desc)
        r2 = router.classify(desc)
        assert r1 == r2

    def test_default_for_unknown_description(self):
        """Unknown description should use default order:
        [ACCESSIBILITY, VISION]."""
        router = GroundingRouter()
        result = router.classify("xyzzy flurbknot zorkian")
        assert result == [
            GroundingStrategy.ACCESSIBILITY,
            GroundingStrategy.VISION,
        ]


# ============================================================================
# 5. Component Availability
# ============================================================================


@pytest.mark.unit
class TestComponentAvailability:

    @pytest.mark.asyncio
    async def test_both_none(self):
        """Both accessibility and vision are None => all strategies fail,
        returns None gracefully."""
        router = GroundingRouter(accessibility=None, vision_coordinator=None)
        result = await router.find_element("submit button")
        assert result is None

    @pytest.mark.asyncio
    async def test_accessibility_available_but_always_returns_none(self):
        """Accessibility works but never finds elements => falls to vision."""
        ax = _make_accessibility(None)
        vis = _make_vision({"x": 200, "y": 300})
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        result = await router.find_element("submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION

    @pytest.mark.asyncio
    async def test_vision_available_but_always_raises(self):
        """Vision always raises => accessibility fallback succeeds."""
        ax = _make_accessibility(
            _make_ax_element(position=(100, 200), size=(80, 30))
        )
        vis = _make_vision(find_raises=ConnectionError("Vision server down"))
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        # For "icon" vision is first, but it fails => falls to accessibility
        result = await router.find_element("application icon")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY

    @pytest.mark.asyncio
    async def test_only_accessibility_available(self):
        """Only accessibility (no vision) => works when AX finds element."""
        ax = _make_accessibility(
            _make_ax_element(position=(100, 200), size=(80, 30))
        )
        router = GroundingRouter(accessibility=ax, vision_coordinator=None)
        result = await router.find_element("submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.ACCESSIBILITY

    @pytest.mark.asyncio
    async def test_only_vision_available(self):
        """Only vision (no accessibility) => works when vision finds element."""
        vis = _make_vision({"x": 500, "y": 600})
        router = GroundingRouter(accessibility=None, vision_coordinator=vis)
        result = await router.find_element("submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION

    @pytest.mark.asyncio
    async def test_accessibility_returns_malformed_element(self):
        """AXElement with position but no size => center is None => skip."""
        elem = AXElement(
            role="AXButton", title="OK",
            position=(100, 200), size=None,
        )
        assert elem.center is None
        ax = _make_accessibility(elem)
        vis = _make_vision({"x": 300, "y": 400})
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        result = await router.find_element("submit button")
        assert result is not None
        assert result.strategy_used == GroundingStrategy.VISION

    @pytest.mark.asyncio
    async def test_vision_returns_dict_without_x_y(self):
        """Vision returns a dict missing 'x' or 'y' keys. The router
        should either raise KeyError (propagated from _ground_vision)
        or handle gracefully."""
        ax = _make_accessibility(None)
        vis = _make_vision({"width": 100, "height": 50})  # missing x, y
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        # _ground_vision tries location["x"] and location["y"]; with
        # missing keys this should either raise or be caught
        try:
            result = await router.find_element("submit button")
            # If it handles gracefully, result may be None
        except KeyError:
            pass  # Also acceptable: propagate the error

    @pytest.mark.asyncio
    async def test_accessibility_element_with_position_zero_size(self):
        """Element with size (0, 0) => center = position itself."""
        elem = AXElement(
            role="AXButton", title="OK",
            position=(100, 200), size=(0, 0),
        )
        # center = (100 + 0//2, 200 + 0//2) = (100, 200)
        assert elem.center == (100, 200)
        ax = _make_accessibility(elem)
        router = GroundingRouter(accessibility=ax)
        result = await router.find_element("submit button")
        assert result is not None
        assert result.x == 100
        assert result.y == 200


# ============================================================================
# 6. OCR Strategy
# ============================================================================


@pytest.mark.unit
class TestOCRStrategy:

    @pytest.mark.asyncio
    async def test_ocr_route_falls_through_to_vision(self):
        """Description routed to OCR => OCR is stub (returns None) =>
        falls through to VISION."""
        ax = _make_accessibility(None)
        vis = _make_vision({"x": 400, "y": 500})
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        # "price" routes to OCR first per design
        result = await router.find_element("the price tag")
        assert result is not None
        # OCR stub returns None, so should fall to vision
        assert result.strategy_used == GroundingStrategy.VISION

    @pytest.mark.asyncio
    async def test_ocr_route_all_fail(self):
        """OCR stub + vision None + no accessibility => None."""
        router = GroundingRouter(accessibility=None, vision_coordinator=None)
        result = await router.find_element("heading text")
        assert result is None

    @pytest.mark.asyncio
    async def test_label_description_tries_ocr_before_vision(self):
        """'label' keyword should try OCR before vision.
        Since OCR is a stub, vision should be the actual strategy used."""
        vis = _make_vision({"x": 100, "y": 200})
        router = GroundingRouter(accessibility=None, vision_coordinator=vis)
        result = await router.find_element("the label next to the form")
        assert result is not None
        # OCR stub returns None, falls through
        assert result.strategy_used == GroundingStrategy.VISION

    @pytest.mark.asyncio
    async def test_number_keyword_routes_ocr_first(self):
        """'number' keyword should try OCR strategy first."""
        router = GroundingRouter()
        strategies = router.classify("number displayed on screen")
        assert strategies[0] == GroundingStrategy.OCR


# ============================================================================
# 7. Concurrency
# ============================================================================


@pytest.mark.unit
class TestConcurrency:

    @pytest.mark.asyncio
    async def test_concurrent_find_element_calls(self):
        """Multiple concurrent find_element calls should not interfere."""
        ax = _make_accessibility(
            _make_ax_element(position=(100, 200), size=(80, 30))
        )
        vis = _make_vision({"x": 500, "y": 600})
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)

        results = await asyncio.gather(
            router.find_element("submit button"),
            router.find_element("search icon"),
            router.find_element("first heading"),
        )
        # All should return results (none should be corrupted)
        for r in results:
            assert r is not None
            assert isinstance(r, GroundingResult)

    @pytest.mark.asyncio
    async def test_concurrent_classify_calls(self):
        """Multiple concurrent classify calls should all succeed."""
        router = GroundingRouter()
        descriptions = [
            "submit button", "app icon", "heading text",
            "first result", "the slider", "logo image",
        ]
        # classify is sync but called from async context
        results = [router.classify(d) for d in descriptions]
        for i, r in enumerate(results):
            assert isinstance(r, list), f"Failed for {descriptions[i]}"
            assert len(r) >= 1

    @pytest.mark.asyncio
    async def test_state_not_leaked_between_calls(self):
        """Router should have no mutable state that leaks between
        find_element calls."""
        elem1 = _make_ax_element(position=(10, 20), size=(40, 40))
        elem2 = _make_ax_element(position=(500, 600), size=(100, 100))
        ax = MagicMock()
        ax.find_element_by_description = MagicMock(
            side_effect=[elem1, elem2]
        )
        router = GroundingRouter(accessibility=ax, vision_coordinator=None)

        r1 = await router.find_element("button one")
        r2 = await router.find_element("button two")

        assert r1 is not None
        assert r2 is not None
        # Coordinates should be different
        assert r1.x == 30  # 10 + 40//2
        assert r1.y == 40  # 20 + 40//2
        assert r2.x == 550  # 500 + 100//2
        assert r2.y == 650  # 600 + 100//2


# ============================================================================
# 8. GroundingStrategy Enum
# ============================================================================


@pytest.mark.unit
class TestGroundingStrategyEnum:

    def test_enum_values(self):
        """Verify the enum has the expected members."""
        assert GroundingStrategy.ACCESSIBILITY.value == "accessibility"
        assert GroundingStrategy.VISION.value == "vision"
        assert GroundingStrategy.OCR.value == "ocr"

    def test_enum_members_count(self):
        """Exactly 3 strategies per design."""
        assert len(GroundingStrategy) == 3

    def test_enum_from_value(self):
        """Can create enum from string value."""
        assert GroundingStrategy("accessibility") == GroundingStrategy.ACCESSIBILITY
        assert GroundingStrategy("vision") == GroundingStrategy.VISION
        assert GroundingStrategy("ocr") == GroundingStrategy.OCR

    def test_enum_invalid_value(self):
        """Invalid string should raise ValueError."""
        with pytest.raises(ValueError):
            GroundingStrategy("invalid")


# ============================================================================
# 9. Router Constructor
# ============================================================================


@pytest.mark.unit
class TestRouterConstructor:

    def test_no_args(self):
        """Router with no args should be constructible."""
        router = GroundingRouter()
        assert router.accessibility is None
        assert router.vision is None

    def test_only_accessibility(self):
        """Router with only accessibility."""
        ax = _make_accessibility(None)
        router = GroundingRouter(accessibility=ax)
        assert router.accessibility is ax
        assert router.vision is None

    def test_only_vision(self):
        """Router with only vision."""
        vis = _make_vision(None)
        router = GroundingRouter(vision_coordinator=vis)
        assert router.accessibility is None
        assert router.vision is vis

    def test_both_components(self):
        """Router with both components."""
        ax = _make_accessibility(None)
        vis = _make_vision(None)
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        assert router.accessibility is ax
        assert router.vision is vis


# ============================================================================
# 10. GroundingResult from accessibility produces correct coordinates
# ============================================================================


@pytest.mark.unit
class TestGroundingResultCoordinates:

    @pytest.mark.asyncio
    async def test_center_calculation_odd_size(self):
        """Element with odd size: integer division for center."""
        elem = _make_ax_element(position=(100, 200), size=(81, 31))
        # center = (100 + 81//2, 200 + 31//2) = (140, 215)
        ax = _make_accessibility(elem)
        router = GroundingRouter(accessibility=ax)
        result = await router.find_element("button")
        assert result is not None
        assert result.x == 140
        assert result.y == 215

    @pytest.mark.asyncio
    async def test_center_calculation_large_element(self):
        """Very large element spanning most of screen."""
        elem = _make_ax_element(position=(0, 0), size=(1920, 1080))
        ax = _make_accessibility(elem)
        router = GroundingRouter(accessibility=ax)
        result = await router.find_element("button")
        assert result is not None
        assert result.x == 960
        assert result.y == 540

    @pytest.mark.asyncio
    async def test_center_calculation_position_1x1(self):
        """1x1 element: center should equal position."""
        elem = _make_ax_element(position=(500, 300), size=(1, 1))
        ax = _make_accessibility(elem)
        router = GroundingRouter(accessibility=ax)
        result = await router.find_element("button")
        assert result is not None
        assert result.x == 500
        assert result.y == 300

    @pytest.mark.asyncio
    async def test_accessibility_result_has_element_info(self):
        """GroundingResult from accessibility should include role and title."""
        elem = _make_ax_element(
            role="AXButton", title="Submit",
            position=(100, 200), size=(80, 30),
        )
        ax = _make_accessibility(elem)
        router = GroundingRouter(accessibility=ax)
        result = await router.find_element("submit button")
        assert result is not None
        assert result.element_info is not None
        assert result.element_info.get("role") == "AXButton"
        assert result.element_info.get("title") == "Submit"

    @pytest.mark.asyncio
    async def test_accessibility_result_confidence(self):
        """Accessibility results should have high confidence (0.95 per design)."""
        elem = _make_ax_element(position=(100, 200), size=(80, 30))
        ax = _make_accessibility(elem)
        router = GroundingRouter(accessibility=ax)
        result = await router.find_element("submit button")
        assert result is not None
        assert result.confidence == 0.95

    @pytest.mark.asyncio
    async def test_vision_result_confidence(self):
        """Vision results should have lower confidence (0.75 per design)."""
        ax = _make_accessibility(None)
        vis = _make_vision({"x": 300, "y": 400})
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)
        result = await router.find_element("submit button")
        assert result is not None
        assert result.confidence == 0.75


# ============================================================================
# 11. _try_strategy Directly (if accessible)
# ============================================================================


@pytest.mark.unit
class TestTryStrategyDirect:

    @pytest.mark.asyncio
    async def test_unknown_strategy_returns_none(self):
        """Passing an unexpected strategy value to _try_strategy should
        return None rather than crash."""
        router = GroundingRouter()
        # _try_strategy has a final `return None` for unknown strategies
        result = await router._try_strategy(
            GroundingStrategy.OCR, "some text"
        )
        # OCR is a stub, should return None
        assert result is None

    @pytest.mark.asyncio
    async def test_try_accessibility_with_none_bridge(self):
        """_ground_accessibility with no bridge returns None."""
        router = GroundingRouter(accessibility=None)
        result = await router._ground_accessibility("button")
        assert result is None

    @pytest.mark.asyncio
    async def test_try_vision_with_none_coordinator(self):
        """_ground_vision with no coordinator returns None."""
        router = GroundingRouter(vision_coordinator=None)
        result = await router._ground_vision("icon")
        assert result is None


# ============================================================================
# 12. Edge Cases in find_element Descriptions
# ============================================================================


@pytest.mark.unit
class TestFindElementDescriptions:

    @pytest.mark.asyncio
    async def test_whitespace_only_description(self):
        """Whitespace-only description should not crash find_element."""
        router = GroundingRouter()
        result = await router.find_element("   \t\n  ")
        # No components => None
        assert result is None

    @pytest.mark.asyncio
    async def test_very_long_description_find(self):
        """Very long description passed to find_element."""
        ax = _make_accessibility(
            _make_ax_element(position=(100, 200), size=(80, 30))
        )
        router = GroundingRouter(accessibility=ax)
        desc = "submit button " * 200  # ~2800 chars
        result = await router.find_element(desc)
        assert result is not None

    @pytest.mark.asyncio
    async def test_newline_in_description(self):
        """Description with newlines should work (classify lowercases)."""
        ax = _make_accessibility(
            _make_ax_element(position=(100, 200), size=(80, 30))
        )
        router = GroundingRouter(accessibility=ax)
        result = await router.find_element("submit\nbutton")
        assert result is not None

    @pytest.mark.asyncio
    async def test_html_in_description(self):
        """HTML tags in description should not crash."""
        ax = _make_accessibility(
            _make_ax_element(position=(100, 200), size=(80, 30))
        )
        router = GroundingRouter(accessibility=ax)
        result = await router.find_element("<b>submit</b> button")
        assert result is not None


# ============================================================================
# 13. Multiple Sequential Lookups (statelessness)
# ============================================================================


@pytest.mark.unit
class TestSequentialLookups:

    @pytest.mark.asyncio
    async def test_alternating_strategies(self):
        """Alternate between widget and visual descriptions to ensure
        router correctly switches strategy ordering."""
        ax = _make_accessibility(
            _make_ax_element(position=(100, 200), size=(80, 30))
        )
        vis = _make_vision({"x": 500, "y": 600})
        router = GroundingRouter(accessibility=ax, vision_coordinator=vis)

        # Widget => accessibility first
        r1 = await router.find_element("submit button")
        assert r1.strategy_used == GroundingStrategy.ACCESSIBILITY

        # Visual => vision first
        r2 = await router.find_element("app icon")
        assert r2.strategy_used == GroundingStrategy.VISION

        # Widget again => back to accessibility
        r3 = await router.find_element("dropdown menu")
        assert r3.strategy_used == GroundingStrategy.ACCESSIBILITY

    @pytest.mark.asyncio
    async def test_repeated_same_description(self):
        """Calling find_element with the same description multiple times
        should yield consistent results."""
        elem = _make_ax_element(position=(100, 200), size=(80, 30))
        ax = MagicMock()
        ax.find_element_by_description = MagicMock(return_value=elem)
        router = GroundingRouter(accessibility=ax)

        results = []
        for _ in range(5):
            r = await router.find_element("submit button")
            results.append(r)

        for r in results:
            assert r is not None
            assert r.x == results[0].x
            assert r.y == results[0].y
            assert r.strategy_used == results[0].strategy_used
