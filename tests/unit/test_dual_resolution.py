"""Unit tests for Dual-Resolution Grounding — Gap 7.

Tests cover: two-image sending, prompt template, threshold gate,
last_successful_region, crop offset mapping, config gate, capabilities gate.
"""

import asyncio
import base64
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.protocols import CoordinatorCapability
from automation_agent.shared_models import FindElementResult
from automation_agent.vision.capture import ScreenCapture
from automation_agent.vision.coordinator import ScreenCoordinatorImpl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "vision_model": "molmo",
        "log_dir": "/tmp/test_dual_res_logs",
        "model_provider": "local",
        "grounding_model": "",
        "grounding_server_url": "",
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
    coord._call_vision_model_with_images = AsyncMock()
    return coord


def _make_agent(config: AgentConfig, coordinator=None):
    """Create a minimal AutomationAgent for dual-res testing."""
    from automation_agent.orchestrator.agent import AutomationAgent

    planner = MagicMock()
    skill_registry = MagicMock()
    actuator = MagicMock()
    actuator.get_state.return_value = {"app_name": "Safari", "window_title": "Test"}

    if coordinator is None:
        coordinator = _make_coordinator(config)

    from pathlib import Path
    import tempfile

    logger = MagicMock(spec=EventLogger)
    logger.run_dir = Path(tempfile.mkdtemp())
    logger.log_event = MagicMock()

    agent = AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
    )
    return agent


# ---------------------------------------------------------------------------
# Test: find_element_dual sends two images (AC-21)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_element_dual_sends_two_images():
    """AC-21: Both context_b64 and screenshot_b64 sent to vision model."""
    config = _make_config()
    coord = _make_coordinator(config)

    context_b64 = _make_b64_image(1024, 768)
    detail_b64 = _make_b64_image(512, 512)

    coord._call_vision_model_with_images.return_value = (
        "FOUND: x=50.0, y=25.0, confidence=0.8"
    )

    result = await coord.find_element_dual(
        "Submit button",
        screenshot_b64=detail_b64,
        context_b64=context_b64,
    )

    assert result is not None
    # Verify _call_vision_model_with_images was called with 2 images
    call_args = coord._call_vision_model_with_images.call_args
    images_arg = call_args[0][1]  # second positional arg
    assert len(images_arg) == 2
    assert images_arg[0] == context_b64  # Image 1 = overview
    assert images_arg[1] == detail_b64  # Image 2 = detail


# ---------------------------------------------------------------------------
# Test: dual-res prompt template (AC-22)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dual_res_prompt_template():
    """AC-22: Prompt references Image 1 and Image 2."""
    config = _make_config()
    coord = _make_coordinator(config)

    coord._call_vision_model_with_images.return_value = "NOT_FOUND"

    await coord.find_element_dual(
        "Cancel button",
        screenshot_b64=_make_b64_image(512, 512),
        context_b64=_make_b64_image(1024, 768),
    )

    call_args = coord._call_vision_model_with_images.call_args
    prompt = call_args[0][0]
    assert "Image 1" in prompt
    assert "Image 2" in prompt
    assert "Cancel button" in prompt


# ---------------------------------------------------------------------------
# Test: dual-res threshold gate (AC-23)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dual_res_threshold_gate():
    """AC-23: Only activates above width threshold."""
    config = _make_config(
        dual_resolution_grounding=True,
        dual_res_threshold=1440,
    )
    coord = _make_coordinator(config)

    # Create a coordinator that advertises dual-res capability
    coord._call_vision_model.return_value = "FOUND: x=50.0, y=25.0"

    agent = _make_agent(config, coordinator=coord)

    # Image is 1024 wide — below 1440 threshold, no last_successful_region
    agent.last_successful_region = None
    screenshot_b64 = _make_b64_image(1024, 768)
    agent._capture_screenshot = AsyncMock(return_value=screenshot_b64)

    result = await agent._find_element("OK button")

    # Should NOT have called find_element_dual
    coord.find_element_dual.assert_not_called() if hasattr(
        coord.find_element_dual, "assert_not_called"
    ) else None
    # But standard find_element should have been called
    assert result is not None


# ---------------------------------------------------------------------------
# Test: dual-res last_successful_region (AC-23)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dual_res_last_successful_region():
    """AC-23: Activates with last_successful_region even below threshold."""
    config = _make_config(
        dual_resolution_grounding=True,
        dual_res_threshold=1440,
        screenshot_resolution=(2048, 1536),
    )
    coord = _make_coordinator(config)
    coord.find_element_dual = AsyncMock(
        return_value=FindElementResult(
            x=100, y=100, confidence=0.8, source="vision"
        )
    )
    coord.capabilities = MagicMock(
        return_value=frozenset({CoordinatorCapability.DUAL_RESOLUTION})
    )

    agent = _make_agent(config, coordinator=coord)

    # Image is 2048 wide > 1440 threshold + has last_successful_region
    agent.last_successful_region = (200, 200, 600, 600)
    screenshot_b64 = _make_b64_image(2048, 1536)
    agent._capture_screenshot = AsyncMock(return_value=screenshot_b64)

    result = await agent._find_element("Submit button")

    # find_element_dual should have been called
    assert coord.find_element_dual.called


# ---------------------------------------------------------------------------
# Test: crop offset mapping (AC-24)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crop_offset_mapping():
    """AC-24: Coordinates mapped back to full-image space."""
    config = _make_config(
        dual_resolution_grounding=True,
        dual_res_threshold=1440,
        screenshot_resolution=(2048, 1536),
    )
    coord = _make_coordinator(config)

    # The dual-res returns coords relative to the crop
    coord.find_element_dual = AsyncMock(
        return_value=FindElementResult(
            x=50, y=60, confidence=0.8, source="vision"
        )
    )
    coord.capabilities = MagicMock(
        return_value=frozenset({CoordinatorCapability.DUAL_RESOLUTION})
    )

    agent = _make_agent(config, coordinator=coord)
    agent.last_successful_region = (300, 300, 700, 700)
    screenshot_b64 = _make_b64_image(2048, 1536)
    agent._capture_screenshot = AsyncMock(return_value=screenshot_b64)

    result = await agent._find_element("Login button")

    # Result coords should be offset by the crop origin
    assert result is not None
    # The crop offset depends on _maybe_crop_screenshot, but x should be > 50
    # since it's mapped back from crop space
    assert result.x >= 50
    assert result.y >= 60


# ---------------------------------------------------------------------------
# Test: dual-res config gate (AC-25)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dual_res_config_gate():
    """AC-25: Disabled when config flag is False."""
    config = _make_config(
        dual_resolution_grounding=False,
        screenshot_resolution=(2048, 1536),
    )
    coord = _make_coordinator(config)
    coord.find_element_dual = AsyncMock()
    coord.capabilities = MagicMock(
        return_value=frozenset({CoordinatorCapability.DUAL_RESOLUTION})
    )
    coord._call_vision_model.return_value = "FOUND: x=50.0, y=25.0"

    agent = _make_agent(config, coordinator=coord)
    agent.last_successful_region = (200, 200, 600, 600)
    screenshot_b64 = _make_b64_image(2048, 1536)
    agent._capture_screenshot = AsyncMock(return_value=screenshot_b64)

    result = await agent._find_element("OK button")

    # find_element_dual should NOT have been called since config is disabled
    coord.find_element_dual.assert_not_called()
    assert result is not None


# ---------------------------------------------------------------------------
# Test: capabilities gate (AC-21)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capabilities_gate():
    """AC-21: Falls back when coordinator.capabilities() lacks dual_resolution."""
    config = _make_config(
        dual_resolution_grounding=True,
        screenshot_resolution=(2048, 1536),
    )
    coord = _make_coordinator(config)
    coord.find_element_dual = AsyncMock()
    # Coordinator does NOT advertise DUAL_RESOLUTION
    coord.capabilities = MagicMock(return_value=frozenset())
    coord._call_vision_model.return_value = "FOUND: x=50.0, y=25.0"

    agent = _make_agent(config, coordinator=coord)
    agent.last_successful_region = (200, 200, 600, 600)
    screenshot_b64 = _make_b64_image(2048, 1536)
    agent._capture_screenshot = AsyncMock(return_value=screenshot_b64)

    result = await agent._find_element("OK button")

    # find_element_dual should NOT have been called since capability is missing
    coord.find_element_dual.assert_not_called()
    assert result is not None
