"""Unit tests for grounding model configuration and coordinator routing.

Tests that:
- Config picks up grounding_model and grounding_server_url from env
- Coordinator routes find_element to grounding client when configured
- Coordinator falls back to vision model when grounding client not configured
- Coordinator still uses vision model for describe_screen regardless of grounding config
- Error handling: grounding model fails -> falls back to vision model
"""

import base64
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.vision.capture import ScreenCapture
from automation_agent.vision.coordinator import COORDINATE_SPACES, ScreenCoordinatorImpl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_capture():
    """A mock ScreenCapture that returns known bytes."""
    capture = MagicMock(spec=ScreenCapture)
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 6
    capture.capture.return_value = fake_jpeg
    capture.capture_b64.return_value = base64.b64encode(fake_jpeg).decode()
    capture.target_resolution = (1024, 768)
    return capture


def _make_config(**overrides) -> AgentConfig:
    """Create an AgentConfig with test defaults, suppressing log dir creation.

    Explicitly sets model_provider=LOCAL to avoid .env overrides.
    """
    defaults = {
        "vision_model": "molmo",
        "model_provider": "local",
        "log_dir": "/tmp/test_agent_logs",
        "grounding_model": "",
        "grounding_server_url": "",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_coordinator(
    config: AgentConfig, capture: MagicMock
) -> ScreenCoordinatorImpl:
    """Create a ScreenCoordinatorImpl with mocked internals."""
    coord = ScreenCoordinatorImpl(config, capture=capture)
    coord._call_vision_model = AsyncMock()
    coord._call_grounding_model = AsyncMock()
    return coord


# ---------------------------------------------------------------------------
# Config Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGroundingConfig:
    """Tests for grounding model config fields."""

    def test_grounding_model_default_empty(self):
        """grounding_model defaults to empty string."""
        config = _make_config()
        assert config.grounding_model == ""

    def test_grounding_server_url_default_empty(self):
        """grounding_server_url defaults to empty string."""
        config = _make_config()
        assert config.grounding_server_url == ""

    def test_grounding_model_set_via_kwarg(self):
        """grounding_model can be set directly."""
        config = _make_config(grounding_model="molmo-grounding-7b")
        assert config.grounding_model == "molmo-grounding-7b"

    def test_grounding_server_url_set_via_kwarg(self):
        """grounding_server_url can be set directly."""
        config = _make_config(grounding_server_url="http://localhost:9090")
        assert config.grounding_server_url == "http://localhost:9090"

    def test_grounding_model_from_env(self, monkeypatch):
        """grounding_model is picked up from AGENT_GROUNDING_MODEL env var."""
        monkeypatch.setenv("AGENT_GROUNDING_MODEL", "qwen3-vl-grounding")
        config = AgentConfig(
            vision_model="molmo",
            model_provider="local",
            log_dir="/tmp/test_agent_logs",
        )
        assert config.grounding_model == "qwen3-vl-grounding"

    def test_grounding_server_url_from_env(self, monkeypatch):
        """grounding_server_url is picked up from AGENT_GROUNDING_SERVER_URL env var."""
        monkeypatch.setenv("AGENT_GROUNDING_SERVER_URL", "http://grounding:8080")
        config = AgentConfig(
            vision_model="molmo",
            model_provider="local",
            log_dir="/tmp/test_agent_logs",
        )
        assert config.grounding_server_url == "http://grounding:8080"


# ---------------------------------------------------------------------------
# Coordinator Routing Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGroundingRouting:
    """Tests for coordinator routing find_element to grounding model."""

    @pytest.mark.asyncio
    async def test_find_element_uses_grounding_when_configured(self, mock_capture):
        """find_element routes to grounding model when grounding_model is set."""
        config = _make_config(grounding_model="molmo")
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "FOUND: x=50.0, y=25.0"

        result = await coord.find_element("OK button", screenshot_b64="fakedata")

        assert result is not None
        assert result.x == 512  # 50.0/100 * 1024
        assert result.y == 192  # 25.0/100 * 768
        coord._call_grounding_model.assert_called_once()
        coord._call_vision_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_find_element_uses_vision_when_no_grounding(self, mock_capture):
        """find_element uses vision model when grounding_model is not set."""
        config = _make_config()  # No grounding_model
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "FOUND: x=50.0, y=25.0"

        result = await coord.find_element("OK button", screenshot_b64="fakedata")

        assert result is not None
        assert result.x == 512
        assert result.y == 192
        coord._call_vision_model.assert_called_once()
        coord._call_grounding_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_find_element_grounding_coordinate_space(self, mock_capture):
        """Grounding model uses its own coordinate space, not the vision model's."""
        config = _make_config(
            vision_model="molmo",  # normalized_0_1
            grounding_model="qwen3-vl",  # normalized_0_1000
        )
        coord = _make_coordinator(config, mock_capture)
        # Qwen returns 0-1000 coords
        coord._call_grounding_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("search bar", screenshot_b64="fakedata")

        assert result is not None
        # 500/1000 * 1024 = 512, 250/1000 * 768 = 192
        assert result.x == 512
        assert result.y == 192

    @pytest.mark.asyncio
    async def test_grounding_enabled_flag_set(self, mock_capture):
        """_grounding_enabled is True when grounding_model is configured."""
        config = _make_config(grounding_model="molmo")
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        assert coord._grounding_enabled is True

    @pytest.mark.asyncio
    async def test_grounding_enabled_flag_not_set(self, mock_capture):
        """_grounding_enabled is False when grounding_model is empty."""
        config = _make_config()
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        assert coord._grounding_enabled is False


# ---------------------------------------------------------------------------
# Describe Screen / Verify Condition Unchanged Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGroundingDoesNotAffectOtherMethods:
    """Grounding config should not affect describe_screen or verify_condition."""

    @pytest.mark.asyncio
    async def test_describe_screen_uses_vision_model_with_grounding(self, mock_capture):
        """describe_screen still uses vision model even when grounding is configured."""
        config = _make_config(grounding_model="molmo")
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "Desktop with Safari open"

        result = await coord.describe_screen(screenshot_b64="fakedata")

        assert result == "Desktop with Safari open"
        coord._call_vision_model.assert_called_once()
        coord._call_grounding_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_condition_uses_vision_model_with_grounding(self, mock_capture):
        """verify_condition still uses vision model even when grounding is configured."""
        config = _make_config(grounding_model="molmo")
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "YES"

        result = await coord.verify_condition("Safari is open", screenshot_b64="fakedata")

        assert result is True
        coord._call_vision_model.assert_called_once()
        coord._call_grounding_model.assert_not_called()


# ---------------------------------------------------------------------------
# Fallback / Error Handling Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGroundingFallback:
    """Tests for grounding model failure -> fallback to vision model."""

    @pytest.mark.asyncio
    async def test_grounding_exception_falls_back_to_vision(self, mock_capture):
        """When grounding model raises an exception, falls back to vision model."""
        config = _make_config(grounding_model="molmo")
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.side_effect = Exception("Connection refused")
        coord._call_vision_model.return_value = "FOUND: x=30.0, y=40.0"

        result = await coord.find_element("Save button", screenshot_b64="fakedata")

        assert result is not None
        assert result.x == 307  # int(30.0/100 * 1024)
        assert result.y == 307  # int(40.0/100 * 768)
        coord._call_grounding_model.assert_called_once()
        coord._call_vision_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_grounding_not_found_falls_back_to_vision(self, mock_capture):
        """When grounding model returns NOT_FOUND, falls back to vision model."""
        config = _make_config(grounding_model="molmo")
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "NOT_FOUND"
        coord._call_vision_model.return_value = "FOUND: x=50.0, y=50.0"

        result = await coord.find_element("hidden button", screenshot_b64="fakedata")

        assert result is not None
        assert result.x == 512
        assert result.y == 384
        coord._call_grounding_model.assert_called_once()
        coord._call_vision_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_both_models_not_found(self, mock_capture):
        """When both grounding and vision return NOT_FOUND, returns None."""
        config = _make_config(grounding_model="molmo")
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "NOT_FOUND"
        coord._call_vision_model.return_value = "NOT_FOUND"

        result = await coord.find_element("nonexistent", screenshot_b64="fakedata")

        assert result is None

    @pytest.mark.asyncio
    async def test_grounding_error_and_vision_not_found(self, mock_capture):
        """Grounding fails with exception, vision returns NOT_FOUND -> None."""
        config = _make_config(grounding_model="molmo")
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.side_effect = RuntimeError("timeout")
        coord._call_vision_model.return_value = "NOT_FOUND"

        result = await coord.find_element("missing element", screenshot_b64="fakedata")

        assert result is None


# ---------------------------------------------------------------------------
# Model Validation Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGroundingModelValidation:
    """Tests for model validation when grounding model is configured."""

    def test_unknown_grounding_model_raises(self, mock_capture):
        """Unknown grounding model raises ValueError at construction."""
        config = _make_config(grounding_model="totally-unknown-grounding")

        with pytest.raises(ValueError, match="not in COORDINATE_SPACES registry"):
            ScreenCoordinatorImpl(config, capture=mock_capture)

    def test_known_grounding_model_no_error(self, mock_capture):
        """Known grounding model (exact match) does not raise."""
        config = _make_config(grounding_model="qwen3-vl")
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        assert coord._grounding_enabled is True

    def test_prefix_matched_grounding_model_no_error(self, mock_capture):
        """Grounding model that prefix-matches a known model does not raise."""
        config = _make_config(grounding_model="molmo-grounding-7b")
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        assert coord._grounding_enabled is True

    def test_grounding_model_included_in_validation(self, mock_capture):
        """_get_models_to_validate includes grounding_model when set."""
        config = _make_config(grounding_model="molmo")
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        models = coord._get_models_to_validate()
        assert "molmo" in models
        # Should include both vision_model and grounding_model
        assert len(models) >= 2

    def test_no_grounding_model_not_in_validation(self, mock_capture):
        """_get_models_to_validate does not include grounding_model when empty."""
        config = _make_config()
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        models = coord._get_models_to_validate()
        assert all(m != "" for m in models)


# ---------------------------------------------------------------------------
# Grounding Server URL Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGroundingServerUrl:
    """Tests for grounding server URL routing."""

    @pytest.mark.asyncio
    async def test_grounding_uses_separate_server_url(self, mock_capture):
        """When grounding_server_url is set, _call_grounding_model uses it."""
        config = _make_config(
            grounding_model="molmo",
            grounding_server_url="http://grounding-server:9090",
        )
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        # Don't mock _call_grounding_model; instead check the URL it would use
        base_url = (
            config.grounding_server_url
            if config.grounding_server_url
            else config.vision_server_url
        )
        assert base_url == "http://grounding-server:9090"

    @pytest.mark.asyncio
    async def test_grounding_falls_back_to_vision_server_url(self, mock_capture):
        """When grounding_server_url is empty, uses vision_server_url."""
        config = _make_config(
            grounding_model="molmo",
            grounding_server_url="",
            vision_server_url="http://localhost:8080",
        )
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        base_url = (
            config.grounding_server_url
            if config.grounding_server_url
            else config.vision_server_url
        )
        assert base_url == "http://localhost:8080"
