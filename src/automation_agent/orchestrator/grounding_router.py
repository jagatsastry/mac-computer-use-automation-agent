"""Mixture-of-Grounding: route element finding to the best expert.

Inspired by Agent S2's Mixture-of-Grounding (MoG) approach.  No single
grounding strategy works for all elements — accessibility is great for
standard widgets, vision for visual elements, and OCR for text content.
This router classifies each description and tries strategies in order,
falling back automatically when a strategy fails.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class GroundingStrategy(Enum):
    """Available grounding strategies."""

    ACCESSIBILITY = "accessibility"
    VISION = "vision"
    OCR = "ocr"


@dataclass
class GroundingResult:
    """Result from a grounding strategy."""

    x: int
    y: int
    strategy_used: GroundingStrategy
    confidence: float
    element_info: Optional[dict] = None


# Keyword sets for classification.
_WIDGET_KEYWORDS = frozenset({
    "button", "text field", "input", "checkbox", "radio",
    "dropdown", "menu", "tab", "slider", "link", "submit",
})

_TEXT_KEYWORDS = frozenset({
    "text that says", "label", "heading", "price", "number",
})

_VISUAL_KEYWORDS = frozenset({
    "image", "icon", "thumbnail", "video", "logo", "chart",
})

_POSITION_KEYWORDS = frozenset({
    "first", "second", "top", "bottom", "closest", "most popular",
})


class GroundingRouter:
    """Routes element-finding to the best grounding expert.

    Classifies each description by keyword matching and returns an ordered
    list of strategies.  ``find_element`` iterates through strategies
    until one succeeds or all have been exhausted.
    """

    def __init__(
        self,
        accessibility: Any = None,
        vision_coordinator: Any = None,
    ):
        self.accessibility = accessibility
        self.vision = vision_coordinator

    def classify(self, description: str) -> list[GroundingStrategy]:
        """Return ordered strategies to try for *description*."""
        desc = description.lower()

        # Standard widgets -> Accessibility first
        if any(kw in desc for kw in _WIDGET_KEYWORDS):
            return [GroundingStrategy.ACCESSIBILITY, GroundingStrategy.VISION]

        # Text content -> OCR first
        if any(kw in desc for kw in _TEXT_KEYWORDS):
            return [GroundingStrategy.OCR, GroundingStrategy.VISION]

        # Visual elements -> Vision first
        if any(kw in desc for kw in _VISUAL_KEYWORDS):
            return [GroundingStrategy.VISION, GroundingStrategy.ACCESSIBILITY]

        # Position-based -> Vision first (needs spatial reasoning)
        if any(kw in desc for kw in _POSITION_KEYWORDS):
            return [GroundingStrategy.VISION, GroundingStrategy.ACCESSIBILITY]

        # Default
        return [GroundingStrategy.ACCESSIBILITY, GroundingStrategy.VISION]

    def _prefer_accessibility_first(self, description: str) -> bool:
        """Return True when Accessibility should be attempted before classification."""
        desc = description.lower()
        if any(kw in desc for kw in _VISUAL_KEYWORDS):
            return False
        if any(kw in desc for kw in _POSITION_KEYWORDS):
            return False
        return True

    async def find_element(
        self, description: str
    ) -> Optional[GroundingResult]:
        """Find element using best available strategy with fallback."""
        tried: set[GroundingStrategy] = set()

        if self._prefer_accessibility_first(description):
            try:
                result = await self._ground_accessibility(description)
                tried.add(GroundingStrategy.ACCESSIBILITY)
                if result is not None:
                    logger.debug(
                        "Grounded '%s' via %s at (%d, %d)",
                        description,
                        result.strategy_used.value,
                        result.x,
                        result.y,
                    )
                    return result
            except Exception:
                logger.debug(
                    "Accessibility-first lookup failed for '%s'",
                    description,
                    exc_info=True,
                )

        strategies = self.classify(description)
        for strategy in strategies:
            if strategy in tried:
                continue
            try:
                result = await self._try_strategy(strategy, description)
                tried.add(strategy)
                if result is not None:
                    logger.debug(
                        "Grounded '%s' via %s at (%d, %d)",
                        description, strategy.value, result.x, result.y,
                    )
                    return result
            except Exception:
                logger.debug(
                    "Strategy %s failed for '%s', trying next",
                    strategy.value, description, exc_info=True,
                )

        if GroundingStrategy.VISION not in tried and self.vision is not None:
            try:
                result = await self._ground_vision(description)
                if result is not None:
                    logger.debug(
                        "Grounded '%s' via %s at (%d, %d)",
                        description,
                        result.strategy_used.value,
                        result.x,
                        result.y,
                    )
                    return result
            except Exception:
                logger.debug(
                    "Final vision fallback failed for '%s'",
                    description,
                    exc_info=True,
                )
        return None

    async def _try_strategy(
        self, strategy: GroundingStrategy, description: str
    ) -> Optional[GroundingResult]:
        """Dispatch to a specific grounding strategy."""
        if strategy == GroundingStrategy.ACCESSIBILITY:
            return await self._ground_accessibility(description)
        elif strategy == GroundingStrategy.VISION:
            return await self._ground_vision(description)
        elif strategy == GroundingStrategy.OCR:
            return await self._ground_ocr(description)
        return None

    async def _ground_accessibility(
        self, description: str
    ) -> Optional[GroundingResult]:
        """Find element via the macOS Accessibility API."""
        if not self.accessibility:
            return None
        elem = self.accessibility.find_element_by_description(description)
        if not elem or not elem.center:
            return None
        return GroundingResult(
            x=elem.center[0],
            y=elem.center[1],
            strategy_used=GroundingStrategy.ACCESSIBILITY,
            confidence=0.95,
            element_info={
                "role": elem.role,
                "title": elem.title,
                "value": elem.value,
                "description": elem.description,
                "position": elem.position,
                "size": elem.size,
                "focused": elem.focused,
            },
        )

    async def _ground_vision(
        self, description: str
    ) -> Optional[GroundingResult]:
        """Find element via the vision model coordinator."""
        if not self.vision:
            return None
        location = await self.vision.find_element(description)
        if not location:
            return None
        x = location["x"] if isinstance(location, dict) else location.x
        y = location["y"] if isinstance(location, dict) else location.y
        confidence = (
            location.get("confidence", 0.75)
            if isinstance(location, dict)
            else getattr(location, "confidence", 0.75)
        )
        return GroundingResult(
            x=x,
            y=y,
            strategy_used=GroundingStrategy.VISION,
            confidence=confidence or 0.75,
        )

    async def _ground_ocr(
        self, description: str
    ) -> Optional[GroundingResult]:
        """Find element via OCR text matching.

        Screenshot OCR is intentionally disabled. Text grounding should use
        Accessibility-first lookup when available, then fall back to vision.
        """
        logger.debug("Screenshot OCR disabled, skipping OCR strategy for '%s'", description)
        return None
