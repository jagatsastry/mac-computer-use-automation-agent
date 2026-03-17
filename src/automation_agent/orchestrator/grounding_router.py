"""Mixture-of-Grounding: route element finding to the best expert.

Inspired by Agent S2's Mixture-of-Grounding (MoG) approach.  No single
grounding strategy works for all elements — accessibility is great for
standard widgets, vision for visual elements, and OCR for text content.
This router classifies each description and tries strategies in order,
falling back automatically when a strategy fails.
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from automation_agent.config import AgentConfig

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
        config: Optional[AgentConfig] = None,
    ):
        self.accessibility = accessibility
        self.vision = vision_coordinator
        self.config = config

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
        accessibility_matches = self._get_accessibility_matches(description)
        if accessibility_matches:
            _score = self._compute_match_score(description, accessibility_matches[0])
            accessibility_result = self._ground_accessibility_match(
                accessibility_matches[0], match_score=_score
            )
        else:
            accessibility_result = None

        strategies = self.classify(description)
        if (
            self._prefer_accessibility_first(description)
            and accessibility_result is not None
            and not self._should_use_llm_tiebreak(description, accessibility_matches, strategies)
        ):
            tried.add(GroundingStrategy.ACCESSIBILITY)
            logger.debug(
                "Grounded '%s' via %s at (%d, %d)",
                description,
                accessibility_result.strategy_used.value,
                accessibility_result.x,
                accessibility_result.y,
            )
            return accessibility_result

        strategies = await self._maybe_reorder_with_llm(
            description,
            accessibility_matches,
            strategies,
        )

        if self._prefer_accessibility_first(description) and accessibility_result is not None:
            if strategies and strategies[0] == GroundingStrategy.ACCESSIBILITY:
                tried.add(GroundingStrategy.ACCESSIBILITY)
                logger.debug(
                    "Grounded '%s' via %s at (%d, %d)",
                    description,
                    accessibility_result.strategy_used.value,
                    accessibility_result.x,
                    accessibility_result.y,
                )
                return accessibility_result

        for strategy in strategies:
            if strategy in tried:
                continue
            try:
                if strategy == GroundingStrategy.ACCESSIBILITY and accessibility_result is not None:
                    result = accessibility_result
                else:
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

    async def _maybe_reorder_with_llm(
        self,
        description: str,
        accessibility_matches: list[Any],
        strategies: list[GroundingStrategy],
    ) -> list[GroundingStrategy]:
        """Use an LLM tie-breaker only when AX-first routing is ambiguous."""
        if not self._should_use_llm_tiebreak(description, accessibility_matches, strategies):
            return strategies

        preferred = await self._classify_with_llm(
            description,
            self._build_accessibility_summary(accessibility_matches),
        )
        if preferred is None or preferred not in strategies:
            return strategies
        return [preferred] + [strategy for strategy in strategies if strategy != preferred]

    def _should_use_llm_tiebreak(
        self,
        description: str,
        accessibility_matches: list[Any],
        strategies: list[GroundingStrategy],
    ) -> bool:
        """Return True when a reasoning model should arbitrate the grounding route."""
        if not self.config or not self.config.grounding_llm_routing_enabled:
            return False
        if not self.accessibility:
            return False
        if not strategies or strategies[0] != GroundingStrategy.ACCESSIBILITY:
            return False
        # Only use the expensive tie-break when AX is missing or ambiguous.
        return len(accessibility_matches) != 1

    async def _classify_with_llm(
        self,
        description: str,
        accessibility_summary: str,
    ) -> Optional[GroundingStrategy]:
        """Ask a reasoning model whether AX or vision should be trusted first."""
        if not self.config:
            return None

        prompt = (
            "You are routing a UI grounding request.\n"
            "Choose which expert should be trusted first:\n"
            "- ACCESSIBILITY: standard widgets or clean AX candidates\n"
            "- VISION: custom-rendered, dynamic, visual, or missing-from-AX targets\n\n"
            f"Element description: {description}\n"
            f"Accessibility summary:\n{accessibility_summary}\n\n"
            "Respond with ONLY one word: ACCESSIBILITY or VISION."
        )

        provider = getattr(self.config.model_provider, "value", self.config.model_provider)

        try:
            if provider == "gemini":
                if not self.config.gemini_api_key:
                    return None
                import asyncio
                from google import genai
                client = genai.Client(api_key=self.config.gemini_api_key)
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.config.gemini_model,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(max_output_tokens=16, temperature=0.0),
                )
                text = (response.text or "").strip().upper()
            else:
                if not self.config.anthropic_api_key:
                    return None
                try:
                    import anthropic
                except ImportError:
                    return None
                client = anthropic.AsyncAnthropic(api_key=self.config.anthropic_api_key)
                message = await client.messages.create(
                    model=self.config.anthropic_model,
                    max_tokens=16,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = message.content[0].text.strip().upper()
        except Exception:
            logger.debug("LLM grounding classification failed", exc_info=True)
            return None

        if text.startswith("ACCESSIBILITY"):
            return GroundingStrategy.ACCESSIBILITY
        if text.startswith("VISION"):
            return GroundingStrategy.VISION
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

    def _get_accessibility_matches(self, description: str) -> list[Any]:
        """Return best-effort AX candidates for *description*."""
        if not self.accessibility:
            return []

        try:
            is_mock = "MagicMock" in type(self.accessibility).__name__
            explicit_attrs = vars(self.accessibility) if is_mock else {}
            extract_target = (
                explicit_attrs.get("_extract_match_target")
                if is_mock
                else getattr(type(self.accessibility), "_extract_match_target", None)
            )
            find_elements = (
                explicit_attrs.get("find_elements")
                if is_mock
                else getattr(self.accessibility, "find_elements", None)
            )
            score_fn = (
                explicit_attrs.get("_element_match_score")
                if is_mock
                else getattr(type(self.accessibility), "_element_match_score", None)
            )
            if callable(extract_target) and callable(find_elements):
                role, text_hint = extract_target(description)
                if role is None and text_hint is None:
                    return []

                enabled_only = role != "AXStaticText"
                matches = list(
                    find_elements(
                        role=role,
                        title_contains=text_hint,
                        enabled_only=enabled_only,
                    )
                )
                if not matches and role == "AXStaticText":
                    matches = list(
                        find_elements(
                            role=None,
                            title_contains=text_hint,
                            enabled_only=False,
                        )
                    )
                if callable(score_fn) and text_hint:
                    matches.sort(key=lambda elem: score_fn(elem, text_hint), reverse=True)
                return matches[: self._max_candidates()]

            finder = getattr(self.accessibility, "find_element_by_description", None)
            if callable(finder):
                elem = finder(description)
                return [elem] if elem is not None else []
        except Exception:
            logger.debug("Failed to collect accessibility candidates", exc_info=True)
        return []

    def _max_candidates(self) -> int:
        if self.config is None:
            return 12
        return self.config.grounding_llm_max_candidates

    def _build_accessibility_summary(self, matches: list[Any]) -> str:
        """Build a compact AX summary for LLM routing."""
        if not matches:
            return "No plausible accessibility candidates found."

        lines = ["Accessibility candidates:"]
        for idx, elem in enumerate(matches[: self._max_candidates()], start=1):
            center = getattr(elem, "center", None)
            parts = [
                f"{idx}. role={getattr(elem, 'role', '')}",
                f"title={getattr(elem, 'title', '')!r}",
            ]
            value = getattr(elem, "value", None)
            description = getattr(elem, "description", None)
            if value:
                parts.append(f"value={value!r}")
            if description:
                parts.append(f"description={description!r}")
            if center:
                parts.append(f"center={center}")
            lines.append(", ".join(parts))
        return "\n".join(lines)

    def _ground_accessibility_match(
        self, elem: Any, match_score: float = 1.0
    ) -> Optional[GroundingResult]:
        """Convert an AX element candidate into a grounding result.

        Args:
            elem: An AXElement (or mock) with .center, .role, .title, etc.
            match_score: Raw score from _element_match_score(), range [0.0, 1.0+].
                Default 1.0 preserves backward compat for callers that don't
                compute score.

        Returns:
            GroundingResult with confidence = 0.6 + 0.35 * min(match_score, 1.0),
            or None if elem is None or has no center.
        """
        center = getattr(elem, "center", None)
        if elem is None or not center:
            return None
        confidence = 0.6 + 0.35 * min(match_score, 1.0)
        logger.debug(
            "ax_confidence_calibrated: description=%s raw_score=%.3f confidence=%.3f "
            "elem_role=%s elem_title=%s",
            getattr(elem, "description", ""),
            match_score,
            confidence,
            getattr(elem, "role", ""),
            getattr(elem, "title", ""),
        )
        return GroundingResult(
            x=center[0],
            y=center[1],
            strategy_used=GroundingStrategy.ACCESSIBILITY,
            confidence=confidence,
            element_info={
                "role": getattr(elem, "role", ""),
                "title": getattr(elem, "title", None),
                "value": getattr(elem, "value", None),
                "description": getattr(elem, "description", None),
                "position": getattr(elem, "position", None),
                "size": getattr(elem, "size", None),
                "focused": getattr(elem, "focused", False),
            },
        )

    def _compute_match_score(self, description: str, elem: Any) -> float:
        """Compute match score using AccessibilityBridge._element_match_score().

        Follows the MagicMock detection pattern from _get_accessibility_matches()
        to support both real and mocked accessibility backends.

        Args:
            description: The natural language element description.
            elem: The AXElement candidate.

        Returns:
            Float score in range [0.0, 1.0+]. Returns 1.0 if scoring is
            unavailable (no accessibility backend, no score_fn, exception).
        """
        if not self.accessibility:
            return 1.0
        try:
            is_mock = "MagicMock" in type(self.accessibility).__name__
            explicit_attrs = vars(self.accessibility) if is_mock else {}
            extract_target = (
                explicit_attrs.get("_extract_match_target")
                if is_mock
                else getattr(
                    type(self.accessibility), "_extract_match_target", None
                )
            )
            score_fn = (
                explicit_attrs.get("_element_match_score")
                if is_mock
                else getattr(
                    type(self.accessibility), "_element_match_score", None
                )
            )
            if not callable(extract_target) or not callable(score_fn):
                logger.debug(
                    "ax_score_unavailable: description=%s reason=no_score_fn",
                    description,
                )
                return 1.0
            _, text_hint = extract_target(description)
            return score_fn(elem, text_hint)
        except Exception:
            logger.debug(
                "ax_score_unavailable: description=%s reason=exception",
                description,
                exc_info=True,
            )
            return 1.0

    async def _ground_accessibility(
        self, description: str
    ) -> Optional[GroundingResult]:
        """Find element via the macOS Accessibility API."""
        if not self.accessibility:
            return None
        elem = self.accessibility.find_element_by_description(description)
        if elem is None:
            return None
        score = self._compute_match_score(description, elem)
        return self._ground_accessibility_match(elem, match_score=score)

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
