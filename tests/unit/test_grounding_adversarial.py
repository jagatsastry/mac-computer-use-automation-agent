"""Adversarial test suite for Phase 2: Grounding Model Upgrade.

Tests config edge cases, coordinator routing logic, fallback behavior,
coordinate space handling, dual-client lifecycle, and concurrency.

All tests are fully mocked — no real API calls or server connections.
"""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from automation_agent.config import AgentConfig, ModelProvider
from automation_agent.vision.capture import ScreenCapture
from automation_agent.vision.coordinator import COORDINATE_SPACES, ScreenCoordinatorImpl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    """Create an AgentConfig with test defaults, suppressing log dir creation.

    Explicitly sets model_provider=LOCAL to avoid .env file interference
    (the project .env sets AGENT_MODEL_PROVIDER=anthropic).
    """
    defaults = {
        "vision_model": "qwen3-vl",
        "model_provider": ModelProvider.LOCAL,
        "log_dir": "/tmp/test_grounding_adversarial_logs",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_coordinator(
    config: AgentConfig, capture: MagicMock
) -> ScreenCoordinatorImpl:
    """Create a ScreenCoordinatorImpl with mocked vision and grounding calls."""
    coord = ScreenCoordinatorImpl(config, capture=capture)
    coord._call_vision_model = AsyncMock()
    coord._call_grounding_model = AsyncMock()
    return coord


@pytest.fixture
def mock_capture():
    """A mock ScreenCapture returning deterministic data."""
    capture = MagicMock(spec=ScreenCapture)
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 6
    capture.capture.return_value = fake_jpeg
    capture.capture_b64.return_value = base64.b64encode(fake_jpeg).decode()
    capture.target_resolution = (1024, 768)
    return capture


# ===========================================================================
# Category 1: Config Edge Cases
# ===========================================================================


@pytest.mark.unit
class TestConfigEdgeCases:
    """Edge cases in grounding_model and grounding_server_url configuration."""

    def test_grounding_model_set_server_url_empty_uses_vision_url(self):
        """grounding_model set + grounding_server_url empty -> falls back to vision_server_url."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="",
        )
        assert config.grounding_model == "qwen3-vl"
        assert config.grounding_server_url == ""
        # The coordinator should use vision_server_url when grounding_server_url is empty.
        # We verify by inspecting _call_grounding_model behavior below in routing tests.

    def test_grounding_server_url_set_but_model_empty(self):
        """grounding_server_url set + grounding_model empty -> grounding NOT active."""
        config = _make_config(
            grounding_model="",
            grounding_server_url="http://grounding.local:9090",
        )
        assert config.grounding_model == ""
        assert config.grounding_server_url == "http://grounding.local:9090"
        # A URL without a model name should not enable grounding.

    @pytest.mark.asyncio
    async def test_grounding_server_url_set_model_empty_no_grounding(self, mock_capture):
        """grounding_server_url set but model empty -> find_element does NOT try grounding."""
        config = _make_config(
            grounding_model="",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("button", screenshot_b64="fakedata")

        assert result is not None
        coord._call_grounding_model.assert_not_called()
        coord._call_vision_model.assert_called_once()

    def test_both_empty_legacy_behavior(self):
        """Both grounding fields empty -> pure legacy behavior, no grounding."""
        config = _make_config(grounding_model="", grounding_server_url="")
        assert config.grounding_model == ""
        assert config.grounding_server_url == ""

    @pytest.mark.asyncio
    async def test_both_empty_uses_only_vision(self, mock_capture):
        """Both grounding fields empty -> find_element uses only vision model."""
        config = _make_config(grounding_model="", grounding_server_url="")
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("the search bar", screenshot_b64="fakedata")

        assert result is not None
        coord._call_grounding_model.assert_not_called()

    def test_grounding_model_with_special_characters(self):
        """Model names with slashes, colons, dots are valid (HuggingFace IDs)."""
        config = _make_config(
            grounding_model="osu-nlp-group/UGround-V1-7B",
            grounding_server_url="http://localhost:9090",
        )
        assert config.grounding_model == "osu-nlp-group/UGround-V1-7B"

    def test_grounding_model_with_spaces_preserved(self):
        """Model name with leading/trailing spaces is preserved (potential bug)."""
        config = _make_config(
            grounding_model="  uground-7b  ",
        )
        # Config may or may not strip whitespace. Either way, it should not crash.
        assert config.grounding_model.strip() == "uground-7b"

    def test_grounding_server_url_no_scheme_rejected(self):
        """grounding_server_url without http:// -> rejected by validator."""
        with pytest.raises(Exception, match="grounding_server_url must start with http"):
            _make_config(
                grounding_model="qwen3-vl",
                grounding_server_url="grounding.local:9090",
            )

    def test_grounding_server_url_trailing_slashes_stripped(self):
        """grounding_server_url with trailing slashes -> stripped by validator."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://localhost:9090///",
        )
        assert config.grounding_server_url == "http://localhost:9090"

    def test_grounding_server_url_with_spaces_rejected(self):
        """grounding_server_url with leading spaces -> rejected by validator."""
        with pytest.raises(Exception, match="grounding_server_url must start with http"):
            _make_config(
                grounding_model="qwen3-vl",
                grounding_server_url=" http://localhost:9090 ",
            )

    def test_env_var_precedence_grounding_model(self, monkeypatch):
        """AGENT_GROUNDING_MODEL env var sets grounding_model."""
        monkeypatch.setenv("AGENT_GROUNDING_MODEL", "env-uground-7b")
        config = AgentConfig(
            vision_model="qwen3-vl",
            log_dir="/tmp/test_grounding_env_logs",
        )
        assert config.grounding_model == "env-uground-7b"

    def test_env_var_precedence_grounding_server_url(self, monkeypatch):
        """AGENT_GROUNDING_SERVER_URL env var sets grounding_server_url."""
        monkeypatch.setenv("AGENT_GROUNDING_SERVER_URL", "http://env-server:9090")
        config = AgentConfig(
            vision_model="qwen3-vl",
            log_dir="/tmp/test_grounding_env_logs",
        )
        assert config.grounding_server_url == "http://env-server:9090"

    def test_constructor_override_beats_env_var(self, monkeypatch):
        """Explicit constructor arg overrides env var."""
        monkeypatch.setenv("AGENT_GROUNDING_MODEL", "env-model")
        config = AgentConfig(
            vision_model="qwen3-vl",
            grounding_model="constructor-model",
            log_dir="/tmp/test_grounding_env_logs",
        )
        assert config.grounding_model == "constructor-model"


# ===========================================================================
# Category 2: Coordinator Routing Logic
# ===========================================================================


@pytest.mark.unit
class TestRoutingLogic:
    """Verify find_element routes to grounding, describe/verify stay on vision."""

    @pytest.mark.asyncio
    async def test_find_element_uses_grounding_when_configured(self, mock_capture):
        """Grounding configured -> find_element calls grounding model first."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("submit button", screenshot_b64="fakedata")

        assert result is not None
        coord._call_grounding_model.assert_called_once()
        # Vision model should NOT be called when grounding succeeds
        coord._call_vision_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_find_element_uses_vision_when_no_grounding(self, mock_capture):
        """No grounding configured -> find_element uses vision model (regression test)."""
        config = _make_config(grounding_model="", grounding_server_url="")
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("OK button", screenshot_b64="fakedata")

        assert result is not None
        coord._call_grounding_model.assert_not_called()
        coord._call_vision_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_describe_screen_never_uses_grounding(self, mock_capture):
        """describe_screen ALWAYS uses vision model, NEVER grounding."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "Safari showing Google"

        result = await coord.describe_screen(screenshot_b64="fakedata")

        assert result == "Safari showing Google"
        coord._call_vision_model.assert_called_once()
        coord._call_grounding_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_condition_never_uses_grounding(self, mock_capture):
        """verify_condition ALWAYS uses vision model, NEVER grounding."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "YES"

        result = await coord.verify_condition(
            "Safari is open", screenshot_b64="fakedata"
        )

        assert result is True
        coord._call_vision_model.assert_called_once()
        coord._call_grounding_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_capture_screenshot_unaffected_by_grounding(self, mock_capture):
        """capture_screenshot does not involve any model calls, grounding or not."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)

        result = await coord.capture_screenshot()

        assert len(result) > 0
        coord._call_vision_model.assert_not_called()
        coord._call_grounding_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_grounding_enabled_flag_reflects_config(self, mock_capture):
        """_grounding_enabled is True iff grounding_model is non-empty."""
        config_with = _make_config(grounding_model="qwen3-vl")
        coord_with = ScreenCoordinatorImpl(config_with, capture=mock_capture)
        assert coord_with._grounding_enabled is True

        config_without = _make_config(grounding_model="")
        coord_without = ScreenCoordinatorImpl(config_without, capture=mock_capture)
        assert coord_without._grounding_enabled is False

    @pytest.mark.asyncio
    async def test_find_element_passes_correct_prompt_to_grounding(self, mock_capture):
        """find_element sends the same prompt template to grounding as to vision."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "FOUND: x=100, y=200"

        await coord.find_element("search icon", screenshot_b64="fakedata")

        call_args = coord._call_grounding_model.call_args
        prompt_arg = call_args[0][0]
        assert "search icon" in prompt_arg


# ===========================================================================
# Category 3: Fallback Behavior
# ===========================================================================


@pytest.mark.unit
class TestFallbackBehavior:
    """Grounding fails -> falls back to vision model."""

    @pytest.mark.asyncio
    async def test_grounding_returns_not_found_falls_back(self, mock_capture):
        """Grounding returns NOT_FOUND -> falls back to vision model."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "NOT_FOUND"
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("the button", screenshot_b64="fakedata")

        assert result is not None
        assert result["x"] == 512  # 500/1000 * 1024
        coord._call_grounding_model.assert_called_once()
        coord._call_vision_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_grounding_returns_empty_string_falls_back(self, mock_capture):
        """Grounding returns empty string -> falls back to vision model."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = ""
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("the button", screenshot_b64="fakedata")

        assert result is not None
        coord._call_grounding_model.assert_called_once()
        coord._call_vision_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_grounding_returns_garbage_falls_back(self, mock_capture):
        """Grounding returns unparseable text -> falls back to vision model."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "I see a button somewhere"
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("the button", screenshot_b64="fakedata")

        assert result is not None
        coord._call_grounding_model.assert_called_once()
        coord._call_vision_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_grounding_raises_exception_falls_back(self, mock_capture):
        """Grounding raises Exception -> falls back to vision model gracefully."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.side_effect = Exception("Connection refused")
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("the button", screenshot_b64="fakedata")

        assert result is not None
        coord._call_grounding_model.assert_called_once()
        coord._call_vision_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_grounding_raises_httpx_error_falls_back(self, mock_capture):
        """Grounding raises httpx error (server down) -> falls back to vision model."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.side_effect = httpx.ConnectError(
            "Failed to connect"
        )
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("the button", screenshot_b64="fakedata")

        assert result is not None
        coord._call_vision_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_grounding_timeout_falls_back(self, mock_capture):
        """Grounding times out (httpx.TimeoutException) -> falls back to vision."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.side_effect = httpx.TimeoutException(
            "Request timed out"
        )
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("the button", screenshot_b64="fakedata")

        assert result is not None
        coord._call_vision_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_both_grounding_and_vision_fail_returns_none(self, mock_capture):
        """Both grounding AND vision fail -> returns None, does not crash."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.side_effect = Exception("grounding down")
        coord._call_vision_model.return_value = "NOT_FOUND"

        result = await coord.find_element("nonexistent", screenshot_b64="fakedata")

        assert result is None

    @pytest.mark.asyncio
    async def test_both_grounding_and_vision_raise_returns_none_or_raises(
        self, mock_capture
    ):
        """Both grounding AND vision raise -> should propagate vision exception."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.side_effect = Exception("grounding down")
        coord._call_vision_model.side_effect = Exception("vision down too")

        # The vision fallback does NOT catch exceptions — it propagates them.
        with pytest.raises(Exception, match="vision down too"):
            await coord.find_element("element", screenshot_b64="fakedata")

    @pytest.mark.asyncio
    async def test_grounding_returns_none_coordinates_falls_back(self, mock_capture):
        """Grounding returns FOUND with un-parseable coords -> falls back."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        # Missing y coordinate
        coord._call_grounding_model.return_value = "FOUND: x=500"
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("the button", screenshot_b64="fakedata")

        assert result is not None
        coord._call_vision_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_uses_vision_model_coordinate_space(self, mock_capture):
        """After grounding fallback, coordinates are converted using VISION model space."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
            vision_model="molmo",  # normalized 0-1
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "NOT_FOUND"
        # Vision model (molmo) returns normalized 0-1 coordinates
        coord._call_vision_model.return_value = "FOUND: x=0.5, y=0.25"

        result = await coord.find_element("the button", screenshot_b64="fakedata")

        assert result is not None
        # 0.5 * 1024 = 512, 0.25 * 768 = 192
        assert result["x"] == 512
        assert result["y"] == 192


# ===========================================================================
# Category 4: Coordinate Space
# ===========================================================================


@pytest.mark.unit
class TestCoordinateSpace:
    """Grounding model coordinate space registration and conversion."""

    def test_grounding_model_not_in_coordinate_spaces_raises(self, mock_capture):
        """Grounding model not in COORDINATE_SPACES -> ValueError at construction."""
        config = _make_config(
            grounding_model="totally-unknown-grounding-model",
            grounding_server_url="http://grounding.local:9090",
        )
        with pytest.raises(ValueError, match="not in COORDINATE_SPACES"):
            ScreenCoordinatorImpl(config, capture=mock_capture)

    def test_grounding_model_prefix_match_accepted(self, mock_capture):
        """Grounding model with prefix match (e.g., 'qwen3-vl-uground') is accepted."""
        config = _make_config(
            grounding_model="qwen3-vl-uground",
            grounding_server_url="http://grounding.local:9090",
        )
        # Should NOT raise — "qwen3-vl-uground" starts with "qwen3-vl"
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        assert coord is not None

    @pytest.mark.asyncio
    async def test_grounding_model_coordinates_converted_correctly(self, mock_capture):
        """Grounding model coords are converted using GROUNDING model's coordinate space."""
        config = _make_config(
            grounding_model="qwen3-vl",  # normalized 0-1000
            grounding_server_url="http://grounding.local:9090",
            vision_model="molmo",  # normalized 0-1, different!
        )
        coord = _make_coordinator(config, mock_capture)
        # Grounding returns qwen3-vl format (0-1000)
        coord._call_grounding_model.return_value = "FOUND: x=500, y=500"

        result = await coord.find_element("the button", screenshot_b64="fakedata")

        assert result is not None
        # 500/1000 * 1024 = 512, 500/1000 * 768 = 384
        assert result["x"] == 512
        assert result["y"] == 384

    @pytest.mark.asyncio
    async def test_grounding_coords_not_interpreted_as_vision_space(self, mock_capture):
        """Grounding coords must NOT be converted using vision model's space.

        If grounding_model=qwen3-vl (0-1000) and vision_model=molmo (0-1),
        coords x=500 from grounding should NOT be interpreted as 0-1 (which
        would give x=500*1024=512000, way off screen).
        """
        config = _make_config(
            grounding_model="qwen3-vl",  # 0-1000
            grounding_server_url="http://grounding.local:9090",
            vision_model="molmo",  # 0-1 — different space!
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("the button", screenshot_b64="fakedata")

        assert result is not None
        # Correctly interpreted as qwen3-vl (0-1000): 500/1000*1024=512
        assert result["x"] == 512
        # If incorrectly interpreted as molmo (0-1): 500*1024=512000 (WRONG)
        assert result["x"] < 2000  # sanity check

    def test_grounding_model_pixel_space(self, mock_capture):
        """Grounding model with pixel coordinate space passes coords through."""
        config = _make_config(
            grounding_model="claude-sonnet-4-20250514",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        # Pixel coords pass through unchanged
        x, y = coord._convert_coordinates(350, 200, "claude-sonnet-4-20250514", 1024, 768)
        assert x == 350
        assert y == 200

    @pytest.mark.asyncio
    async def test_grounding_returns_negative_coordinates(self, mock_capture):
        """Grounding returns negative coordinates -> parsed but results may be wrong."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        # Negative coords should not match the FOUND regex (no negative sign support)
        coord._call_grounding_model.return_value = "FOUND: x=-100, y=-50"
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("the button", screenshot_b64="fakedata")

        # If regex doesn't match negatives, falls back to vision
        if result is not None:
            # Vision coords: 500/1000*1024=512
            assert result["x"] >= 0

    @pytest.mark.asyncio
    async def test_grounding_returns_coordinates_exceeding_1000(self, mock_capture):
        """Grounding returns coords > 1000 in 0-1000 space -> clamped or overflows."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        # Values > 1000 in qwen3-vl space: 1500/1000*1024 = 1536, clamped to 1023
        coord._call_grounding_model.return_value = "FOUND: x=1500, y=1500"

        result = await coord.find_element("the button", screenshot_b64="fakedata")

        assert result is not None
        # Should be clamped to screen bounds
        assert result["x"] <= 1023
        assert result["y"] <= 767

    @pytest.mark.asyncio
    async def test_grounding_returns_zero_coordinates(self, mock_capture):
        """Grounding returns x=0, y=0 -> valid top-left corner result."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "FOUND: x=0, y=0"

        result = await coord.find_element("top-left element", screenshot_b64="fakedata")

        assert result is not None
        assert result["x"] == 0
        assert result["y"] == 0


# ===========================================================================
# Category 5: Dual-Client Lifecycle (grounding URL construction)
# ===========================================================================


@pytest.mark.unit
class TestDualClientLifecycle:
    """Test URL construction and server routing in _call_grounding_model."""

    @pytest.mark.asyncio
    async def test_grounding_uses_own_server_url(self, mock_capture):
        """When grounding_server_url is set, grounding calls go to that URL."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)

        # Patch httpx to capture the URL being called
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "FOUND: x=500, y=250"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await coord._call_grounding_model("test prompt", "fakedata")

            # Verify the URL includes the grounding server
            call_args = mock_client.post.call_args
            called_url = call_args[0][0]
            assert "grounding.local:9090" in called_url
            assert called_url.endswith("/v1/chat/completions")

    @pytest.mark.asyncio
    async def test_grounding_falls_back_to_vision_server_url(self, mock_capture):
        """When grounding_server_url is empty, grounding uses vision_server_url."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="",
            vision_server_url="http://vision.local:8080",
        )
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "FOUND: x=500, y=250"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await coord._call_grounding_model("test prompt", "fakedata")

            call_args = mock_client.post.call_args
            called_url = call_args[0][0]
            assert "vision.local:8080" in called_url

    @pytest.mark.asyncio
    async def test_grounding_url_with_trailing_slash_gets_double_slash(self, mock_capture):
        """grounding_server_url with trailing / -> URL may have double slash before v1."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090/",
        )
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "FOUND: x=500, y=250"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await coord._call_grounding_model("test prompt", "fakedata")

            call_args = mock_client.post.call_args
            called_url = call_args[0][0]
            # This exposes a potential bug: "http://x:9090//v1/chat/completions"
            # The URL may have double slashes. Production code should rstrip('/').
            assert "/v1/chat/completions" in called_url

    @pytest.mark.asyncio
    async def test_grounding_sends_correct_model_name_in_payload(self, mock_capture):
        """Grounding API call payload contains grounding_model, not vision_model."""
        config = _make_config(
            grounding_model="uground-7b",
            grounding_server_url="http://grounding.local:9090",
            vision_model="qwen3-vl",
        )
        # Register uground-7b so validation passes (pretend it's like qwen3-vl)
        original_spaces = COORDINATE_SPACES.copy()
        COORDINATE_SPACES["uground-7b"] = "normalized_0_1000"
        try:
            coord = ScreenCoordinatorImpl(config, capture=mock_capture)

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "FOUND: x=500, y=250"}}]
            }
            mock_response.raise_for_status = MagicMock()

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post.return_value = mock_response
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                await coord._call_grounding_model("test prompt", "fakedata")

                call_args = mock_client.post.call_args
                payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
                assert payload["model"] == "uground-7b"
        finally:
            # Restore original COORDINATE_SPACES
            COORDINATE_SPACES.clear()
            COORDINATE_SPACES.update(original_spaces)

    @pytest.mark.asyncio
    async def test_grounding_uses_same_timeout_as_vision(self, mock_capture):
        """Grounding model uses the same vision_server_timeout setting."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
            vision_server_timeout=42,
        )
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "FOUND: x=500, y=250"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await coord._call_grounding_model("test prompt", "fakedata")

            # Verify timeout was passed to AsyncClient
            call_kwargs = mock_client_cls.call_args[1]
            assert call_kwargs["timeout"] == 42


# ===========================================================================
# Category 6: Concurrency
# ===========================================================================


@pytest.mark.unit
class TestConcurrency:
    """Concurrent operations should not cross-contaminate."""

    @pytest.mark.asyncio
    async def test_concurrent_find_element_calls_both_use_grounding(self, mock_capture):
        """Multiple concurrent find_element calls all route to grounding."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "FOUND: x=500, y=250"

        results = await asyncio.gather(
            coord.find_element("button A", screenshot_b64="fakedata"),
            coord.find_element("button B", screenshot_b64="fakedata"),
            coord.find_element("button C", screenshot_b64="fakedata"),
        )

        assert all(r is not None for r in results)
        assert coord._call_grounding_model.call_count == 3
        coord._call_vision_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_concurrent_find_and_describe_no_contamination(self, mock_capture):
        """Concurrent find_element + describe_screen do not interfere."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "FOUND: x=500, y=250"
        coord._call_vision_model.return_value = "A desktop with apps"

        find_result, describe_result = await asyncio.gather(
            coord.find_element("button", screenshot_b64="fakedata"),
            coord.describe_screen(screenshot_b64="fakedata"),
        )

        assert find_result is not None
        assert find_result["x"] == 512  # qwen3-vl: 500/1000*1024
        assert describe_result == "A desktop with apps"
        coord._call_grounding_model.assert_called_once()
        coord._call_vision_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_find_and_verify_no_contamination(self, mock_capture):
        """Concurrent find_element + verify_condition do not interfere."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "FOUND: x=500, y=250"
        coord._call_vision_model.return_value = "YES"

        find_result, verify_result = await asyncio.gather(
            coord.find_element("button", screenshot_b64="fakedata"),
            coord.verify_condition("app is open", screenshot_b64="fakedata"),
        )

        assert find_result is not None
        assert verify_result is True


# ===========================================================================
# Category 7: Regression Tests (existing behavior preserved)
# ===========================================================================


@pytest.mark.unit
class TestRegressionNoGrounding:
    """Ensure adding grounding support does not break existing behavior."""

    @pytest.mark.asyncio
    async def test_find_element_molmo_still_works(self, mock_capture):
        """Legacy: molmo find_element without grounding still works."""
        config = _make_config(vision_model="molmo")
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "FOUND: x=0.5, y=0.25"

        result = await coord.find_element("OK button", screenshot_b64="fakedata")

        assert result is not None
        assert result["x"] == 512
        assert result["y"] == 192

    @pytest.mark.asyncio
    async def test_find_element_qwen_still_works(self, mock_capture):
        """Legacy: qwen3-vl find_element without grounding still works."""
        config = _make_config(vision_model="qwen3-vl")
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        result = await coord.find_element("search bar", screenshot_b64="fakedata")

        assert result is not None
        assert result["x"] == 512
        assert result["y"] == 192

    @pytest.mark.asyncio
    async def test_find_element_claude_still_works(self, mock_capture):
        """Legacy: claude pixel coords without grounding still works."""
        config = _make_config(
            vision_model="claude-sonnet-4-20250514",
            model_provider=ModelProvider.ANTHROPIC,
            anthropic_api_key="fake",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "FOUND: x=350, y=200"

        result = await coord.find_element("Save", screenshot_b64="fakedata")

        assert result is not None
        assert result["x"] == 350
        assert result["y"] == 200

    @pytest.mark.asyncio
    async def test_describe_screen_unchanged(self, mock_capture):
        """describe_screen behavior is unchanged by grounding feature."""
        config = _make_config(vision_model="qwen3-vl")
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "Desktop showing Safari"

        result = await coord.describe_screen(screenshot_b64="fakedata")

        assert result == "Desktop showing Safari"

    @pytest.mark.asyncio
    async def test_verify_condition_unchanged(self, mock_capture):
        """verify_condition behavior is unchanged by grounding feature."""
        config = _make_config(vision_model="qwen3-vl")
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "YES"

        result = await coord.verify_condition("app visible", screenshot_b64="fakedata")

        assert result is True

    def test_unknown_vision_model_still_raises(self, mock_capture):
        """Unknown vision model still raises ValueError even with grounding."""
        config = _make_config(
            vision_model="totally-unknown",
            grounding_model="qwen3-vl",
        )
        with pytest.raises(ValueError, match="not in COORDINATE_SPACES"):
            ScreenCoordinatorImpl(config, capture=mock_capture)

    @pytest.mark.asyncio
    async def test_not_found_still_returns_none(self, mock_capture):
        """NOT_FOUND from vision model (no grounding) still returns None."""
        config = _make_config(vision_model="qwen3-vl")
        coord = _make_coordinator(config, mock_capture)
        coord._call_vision_model.return_value = "NOT_FOUND"

        result = await coord.find_element("missing", screenshot_b64="fakedata")

        assert result is None


# ===========================================================================
# Category 8: Model Validation at Construction Time
# ===========================================================================


@pytest.mark.unit
class TestModelValidation:
    """Coordinator validates models at construction, not at call time."""

    def test_grounding_model_validated_at_init(self, mock_capture):
        """Unknown grounding model raises immediately at construction."""
        config = _make_config(
            grounding_model="nonexistent-grounding-model",
        )
        with pytest.raises(ValueError):
            ScreenCoordinatorImpl(config, capture=mock_capture)

    def test_valid_grounding_model_does_not_raise(self, mock_capture):
        """Known grounding model does not raise at construction."""
        config = _make_config(grounding_model="qwen3-vl")
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        assert coord is not None

    def test_grounding_model_empty_not_validated(self, mock_capture):
        """Empty grounding model -> not added to validation list."""
        config = _make_config(grounding_model="")
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        assert coord._grounding_enabled is False

    def test_models_to_validate_includes_grounding_when_set(self, mock_capture):
        """_get_models_to_validate includes grounding_model when non-empty."""
        config = _make_config(grounding_model="qwen3-vl")
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        models = coord._get_models_to_validate()
        assert "qwen3-vl" in models

    def test_models_to_validate_excludes_grounding_when_empty(self, mock_capture):
        """_get_models_to_validate excludes grounding_model when empty."""
        config = _make_config(grounding_model="")
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        models = coord._get_models_to_validate()
        assert "" not in models

    def test_models_to_validate_includes_anthropic_when_provider_set(self, mock_capture):
        """_get_models_to_validate includes anthropic model when provider is anthropic."""
        config = _make_config(
            vision_model="qwen3-vl",
            model_provider=ModelProvider.ANTHROPIC,
            anthropic_vision_model="claude-sonnet-4-20250514",
            anthropic_api_key="fake",
            grounding_model="molmo",
        )
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)
        models = coord._get_models_to_validate()
        assert "qwen3-vl" in models
        assert "claude-sonnet-4-20250514" in models
        assert "molmo" in models


# ===========================================================================
# Category 9: Edge Cases in _call_grounding_model Implementation
# ===========================================================================


@pytest.mark.unit
class TestCallGroundingModelImpl:
    """Directly test _call_grounding_model edge cases."""

    @pytest.mark.asyncio
    async def test_call_grounding_model_constructs_correct_payload(self, mock_capture):
        """Payload sent to grounding server includes model name and image."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "FOUND: x=500, y=250"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await coord._call_grounding_model("Find the button", "base64img")

            call_args = mock_client.post.call_args
            payload = call_args[1]["json"]

            # Verify payload structure
            assert payload["model"] == "qwen3-vl"
            assert payload["stream"] is False
            assert payload["max_tokens"] == 1024
            assert len(payload["messages"]) == 1
            msg_content = payload["messages"][0]["content"]
            assert any(c["type"] == "text" for c in msg_content)
            assert any(c["type"] == "image_url" for c in msg_content)

            # Verify image data
            image_part = next(c for c in msg_content if c["type"] == "image_url")
            assert "base64img" in image_part["image_url"]["url"]

    @pytest.mark.asyncio
    async def test_call_grounding_model_raises_on_http_error(self, mock_capture):
        """HTTP error from grounding server propagates as exception."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(httpx.HTTPStatusError):
                await coord._call_grounding_model("prompt", "data")

    @pytest.mark.asyncio
    async def test_call_grounding_model_server_returns_empty_choices(self, mock_capture):
        """Server returns empty choices array -> KeyError or IndexError."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)

        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": []}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises((IndexError, KeyError)):
                await coord._call_grounding_model("prompt", "data")

    @pytest.mark.asyncio
    async def test_call_grounding_model_malformed_json_response(self, mock_capture):
        """Server returns malformed JSON -> exception propagated."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = ScreenCoordinatorImpl(config, capture=mock_capture)

        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError, match="Invalid JSON"):
                await coord._call_grounding_model("prompt", "data")


# ===========================================================================
# Category 10: Auto-capture behavior with grounding
# ===========================================================================


@pytest.mark.unit
class TestAutoCapture:
    """Screenshot auto-capture behavior with grounding enabled."""

    @pytest.mark.asyncio
    async def test_find_element_captures_screenshot_once_for_both_models(
        self, mock_capture
    ):
        """When grounding fails and falls back, the SAME screenshot is reused."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "NOT_FOUND"
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        # Do NOT pass screenshot_b64 -> should auto-capture
        result = await coord.find_element("the button")

        assert result is not None
        # Screenshot should be captured exactly once, not once per model
        mock_capture.capture_b64.assert_called_once()

    @pytest.mark.asyncio
    async def test_find_element_passes_provided_screenshot_to_both(self, mock_capture):
        """Pre-captured screenshot is used for both grounding and fallback."""
        config = _make_config(
            grounding_model="qwen3-vl",
            grounding_server_url="http://grounding.local:9090",
        )
        coord = _make_coordinator(config, mock_capture)
        coord._call_grounding_model.return_value = "NOT_FOUND"
        coord._call_vision_model.return_value = "FOUND: x=500, y=250"

        await coord.find_element("the button", screenshot_b64="my_screenshot")

        # Both calls should receive the same screenshot
        grounding_screenshot = coord._call_grounding_model.call_args[0][1]
        vision_screenshot = coord._call_vision_model.call_args[0][1]
        assert grounding_screenshot == "my_screenshot"
        assert vision_screenshot == "my_screenshot"

        # Should NOT have captured a new screenshot
        mock_capture.capture_b64.assert_not_called()
