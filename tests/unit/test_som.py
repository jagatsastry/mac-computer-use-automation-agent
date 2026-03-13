"""Unit tests for Set-of-Mark (SoM) prompting — Gap 1.

Tests cover: annotator drawing, label cap, JXA/pyobjc format, SoM response
parsing, confidence cap, coordinate fallback, element count fallback, config gate.
"""

import base64
import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from automation_agent.config import AgentConfig
from automation_agent.protocols import CoordinatorCapability
from automation_agent.shared_models import FindElementResult
from automation_agent.vision.annotator import (
    _extract_element_bounds,
    annotate_screenshot,
)
from automation_agent.vision.capture import ScreenCapture
from automation_agent.vision.coordinator import ScreenCoordinatorImpl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "vision_model": "molmo",
        "log_dir": "/tmp/test_som_logs",
        "model_provider": "local",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_b64_image(width: int = 200, height: int = 200) -> str:
    """Create a minimal JPEG image encoded as base64."""
    img = Image.new("RGB", (width, height), (128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=50)
    return base64.b64encode(buf.getvalue()).decode()


def _make_coordinator(config: AgentConfig) -> ScreenCoordinatorImpl:
    capture = MagicMock(spec=ScreenCapture)
    capture.get_screen_size.return_value = (1024, 768)
    capture.target_resolution = (1024, 768)
    fake_b64 = _make_b64_image(1024, 768)
    capture.capture_b64.return_value = fake_b64
    coord = ScreenCoordinatorImpl(config, capture=capture)
    coord._call_vision_model = AsyncMock()
    return coord


def _jxa_elements(n: int) -> list:
    """Generate n JXA-format elements spaced across the screen."""
    return [
        {
            "center_x": 100 + i * 50,
            "center_y": 100 + i * 30,
            "width": 80,
            "height": 24,
            "title": f"Button {i + 1}",
            "role": "AXButton",
        }
        for i in range(n)
    ]


def _pyobjc_elements(n: int) -> list:
    """Generate n pyobjc-format elements."""
    return [
        {
            "position": [50 + i * 60, 50 + i * 30],
            "size": [80, 24],
            "title": f"Link {i + 1}",
            "role": "AXLink",
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Test: annotate_screenshot draws labels (AC-11)
# ---------------------------------------------------------------------------


def test_annotate_screenshot_draws_labels():
    """AC-11: Numbered boxes drawn at correct image coords."""
    b64 = _make_b64_image(400, 300)
    elements = _jxa_elements(5)
    result_b64 = annotate_screenshot(b64, elements, (400, 300))

    # Result should be valid base64-encoded JPEG
    img_bytes = base64.b64decode(result_b64)
    img = Image.open(io.BytesIO(img_bytes))
    assert img.size == (400, 300)

    # Should be a different image (annotations were drawn)
    assert result_b64 != b64


# ---------------------------------------------------------------------------
# Test: annotate_screenshot respects cap (AC-12)
# ---------------------------------------------------------------------------


def test_annotate_screenshot_respects_cap():
    """AC-12: Max 20 labels drawn even when more elements provided."""
    b64 = _make_b64_image(800, 600)
    elements = _jxa_elements(30)  # More than max_labels=20
    result_b64 = annotate_screenshot(b64, elements, (800, 600))

    # Should succeed without error (only first 20 drawn)
    img_bytes = base64.b64decode(result_b64)
    img = Image.open(io.BytesIO(img_bytes))
    assert img.size == (800, 600)


# ---------------------------------------------------------------------------
# Test: annotate_screenshot handles JXA format (AC-11)
# ---------------------------------------------------------------------------


def test_annotate_screenshot_jxa_format():
    """AC-11: Handles JXA element format (center_x, center_y, width, height)."""
    el = {"center_x": 200, "center_y": 150, "width": 80, "height": 30}
    cx, cy, w, h = _extract_element_bounds(el)
    assert (cx, cy, w, h) == (200.0, 150.0, 80.0, 30.0)


# ---------------------------------------------------------------------------
# Test: annotate_screenshot handles pyobjc format (AC-11)
# ---------------------------------------------------------------------------


def test_annotate_screenshot_pyobjc_format():
    """AC-11: Handles pyobjc element format (position, size)."""
    el = {"position": [100, 200], "size": [60, 20]}
    cx, cy, w, h = _extract_element_bounds(el)
    # center = position + size/2
    assert cx == 130.0
    assert cy == 210.0
    assert w == 60.0
    assert h == 20.0


# ---------------------------------------------------------------------------
# Test: _parse_som_response parses element_number (AC-13)
# ---------------------------------------------------------------------------


def test_parse_som_response_element_number():
    """AC-13: FOUND: element_number=3 parsed correctly."""
    config = _make_config(som_enabled=True)
    coord = _make_coordinator(config)

    candidates = _jxa_elements(5)
    response = "FOUND: element_number=3, confidence=0.9"

    result = coord._parse_som_response(response, candidates, "Button 3")
    assert result is not None
    assert result.source == "som"
    # Element 3 (1-indexed) = candidates[2]
    assert result.x == int(candidates[2]["center_x"])
    assert result.y == int(candidates[2]["center_y"])


# ---------------------------------------------------------------------------
# Test: SoM confidence capped at 0.85 (AC-13)
# ---------------------------------------------------------------------------


def test_parse_som_confidence_capped_at_085():
    """AC-13: SoM confidence capped at 0.85 (below critical 0.9 threshold)."""
    config = _make_config(som_enabled=True)
    coord = _make_coordinator(config)

    candidates = _jxa_elements(5)
    # VLM reports 0.95 but SoM caps at 0.85
    response = "FOUND: element_number=1, confidence=0.95"

    result = coord._parse_som_response(response, candidates, "Button 3")
    assert result is not None
    assert result.confidence == 0.85


# ---------------------------------------------------------------------------
# Test: SoM falls back to coordinate parsing (AC-13 + AC-14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_som_response_falls_back_to_coords():
    """AC-13+AC-14: Raw coordinates accepted as secondary when no element_number."""
    config = _make_config(som_enabled=True)
    coord = _make_coordinator(config)

    # VLM gives raw coordinates instead of element_number
    coord._call_vision_model.return_value = (
        "FOUND: x=50.0, y=25.0, confidence=0.7"
    )

    candidates = _jxa_elements(5)
    screenshot_b64 = _make_b64_image(1024, 768)
    result = await coord.find_element(
        "Submit button", screenshot_b64=screenshot_b64, candidates=candidates
    )

    assert result is not None
    # Molmo 0-100: x=50/100*1024=512, y=25/100*768=192
    assert result.x == 512
    assert result.y == 192
    assert result.source == "vision"


# ---------------------------------------------------------------------------
# Test: SoM fallback under 3 elements (AC-14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_som_fallback_under_3_elements():
    """AC-14: Standard find_element.md used when < 3 candidates."""
    config = _make_config(som_enabled=True)
    coord = _make_coordinator(config)

    # Standard response (not SoM)
    coord._call_vision_model.return_value = "FOUND: x=50.0, y=25.0"

    candidates = _jxa_elements(2)  # Only 2 — below SoM threshold
    screenshot_b64 = _make_b64_image(1024, 768)
    result = await coord.find_element(
        "OK button", screenshot_b64=screenshot_b64, candidates=candidates
    )

    assert result is not None
    # Should use standard path, not SoM
    assert result.source == "vision"
    # Check the prompt did NOT use SoM template
    call_args = coord._call_vision_model.call_args
    prompt_used = call_args[0][0]
    assert "element_number" not in prompt_used


# ---------------------------------------------------------------------------
# Test: SoM config gate (AC-15)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_som_config_gate():
    """AC-15: SoM disabled when config flag is False."""
    config = _make_config(som_enabled=False)
    coord = _make_coordinator(config)

    coord._call_vision_model.return_value = "FOUND: x=50.0, y=25.0"

    candidates = _jxa_elements(10)  # Enough for SoM, but disabled
    screenshot_b64 = _make_b64_image(1024, 768)
    result = await coord.find_element(
        "OK button", screenshot_b64=screenshot_b64, candidates=candidates
    )

    assert result is not None
    # Should use standard path
    call_args = coord._call_vision_model.call_args
    prompt_used = call_args[0][0]
    assert "element_number" not in prompt_used


# ---------------------------------------------------------------------------
# Test: VLM confidence used when below cap (AC-13)
# ---------------------------------------------------------------------------


def test_parse_som_confidence_uses_vlm_value():
    """AC-13: VLM-reported confidence used when < 0.85."""
    config = _make_config(som_enabled=True)
    coord = _make_coordinator(config)

    candidates = _jxa_elements(5)
    # VLM reports 0.7, below the 0.85 cap — should be used as-is
    response = "FOUND: element_number=2, confidence=0.7"

    result = coord._parse_som_response(response, candidates, "Button 2")
    assert result is not None
    assert result.confidence == 0.7


# ---------------------------------------------------------------------------
# Test: Screen-to-image coordinate mapping in annotator (AC-11)
# ---------------------------------------------------------------------------


def test_coordinate_space_mapping():
    """AC-11: Screen coords correctly mapped to image space when sizes differ."""
    # Screen is 1024x768, image is 512x384 (half resolution)
    b64 = _make_b64_image(512, 384)
    elements = [
        {"center_x": 512, "center_y": 384, "width": 100, "height": 50}
    ]
    result_b64 = annotate_screenshot(b64, elements, (1024, 768))

    # Element at screen (512,384) should map to image (256,192)
    # Bounding box drawn at correct image location
    img_bytes = base64.b64decode(result_b64)
    img = Image.open(io.BytesIO(img_bytes))
    assert img.size == (512, 384)
    # Image should differ from original (labels drawn)
    assert result_b64 != b64


# ---------------------------------------------------------------------------
# Test: SoM try/except fallback on annotator error (AC-14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_som_error_falls_back_to_standard():
    """SoM path catches exceptions and falls through to standard grounding."""
    config = _make_config(som_enabled=True)
    coord = _make_coordinator(config)

    # Standard path response
    coord._call_vision_model.return_value = "FOUND: x=50.0, y=25.0"

    # Mock get_screen_size to raise — simulates annotator failure
    coord.capture.get_screen_size.side_effect = RuntimeError("screen size error")

    candidates = _jxa_elements(5)
    screenshot_b64 = _make_b64_image(1024, 768)
    result = await coord.find_element(
        "OK button", screenshot_b64=screenshot_b64, candidates=candidates
    )

    # Should have fallen back to standard path successfully
    assert result is not None
    assert result.source == "vision"


# ---------------------------------------------------------------------------
# Test: SoM conf=0.85 + destructive keyword triggers confirmation (AC-13+AC-8)
# ---------------------------------------------------------------------------


def test_som_destructive_triggers_confirmation():
    """AC-13+AC-8: SoM caps confidence at 0.85 < _CRITICAL_CONFIDENCE_THRESHOLD (0.9),
    so destructive actions always trigger phase 2 confirmation in smart mode."""
    from automation_agent.orchestrator.agent import AutomationAgent
    from automation_agent.shared_models import ActionStep

    config = _make_config(som_enabled=True, confirm_destructive="smart")
    # Build a minimal agent to access _should_confirm_phase2
    planner = MagicMock()
    skill_registry = MagicMock()
    actuator = MagicMock()
    coordinator = _make_coordinator(config)
    logger_mock = MagicMock()
    logger_mock.run_dir = "/tmp/test_som_destruct"
    logger_mock.log_event = MagicMock()
    agent = AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger_mock,
    )

    step = ActionStep(
        action="click",
        params={"element": "Delete item"},
        verify="Item deleted",
    )

    # SoM caps at 0.85 — should trigger confirmation (below 0.9 threshold)
    assert agent._should_confirm_phase2(step, confidence=0.85, matched_keyword="delete")

    # Even without a hard-destructive keyword, 0.85 < 0.9 triggers confirmation
    assert agent._should_confirm_phase2(step, confidence=0.85, matched_keyword=None)

    # Verify the threshold boundary: 0.9 would NOT trigger (for non-destructive step)
    safe_step = ActionStep(
        action="click",
        params={"element": "Next page"},
        verify="Page loaded",
    )
    assert not agent._should_confirm_phase2(safe_step, confidence=0.9, matched_keyword=None)
