"""Unit tests for cloud grounding routing and GPT computer-use integration.

Covers:
  1. Config: grounding_model_provider resolution via resolve_step_model()
  2. Coordinator: cloud vs local grounding routing in find_element()
  3. GPT computer-use grounding path (OpenAIClient.locate_element)
  4. Coordinate space mapping for cloud models

All external calls are mocked — no real API calls, no AppleScript.
"""

import base64
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.shared_models import FindElementResult
from automation_agent.vision.coordinator import COORDINATE_SPACES, ScreenCoordinatorImpl
from automation_agent.vision.capture import ScreenCapture


# ---------------------------------------------------------------------------
# Helpers / Factories
# ---------------------------------------------------------------------------

# Patch target for OpenAIClient.  The coordinator imports it lazily inside
# find_element via ``from automation_agent.llm.openai_client import OpenAIClient``,
# so we patch at the *source module* level.
_OPENAI_CLIENT_PATCH = "automation_agent.llm.openai_client.OpenAIClient"


def _make_config(**overrides) -> AgentConfig:
    """Return an AgentConfig for tests, suppressing .env leakage."""
    defaults = dict(
        _env_file=None,
        model_provider="local",
        vision_model="molmo",
        grounding_model="",
        grounding_server_url="",
        log_dir="/tmp/test_cloud_grounding_logs",
        anthropic_api_key="test-key-not-real",
        openai_api_key="test-key-not-real",
        gemini_api_key="test-key-not-real",
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_coordinator(config=None, **config_overrides) -> ScreenCoordinatorImpl:
    """Return a ScreenCoordinatorImpl with vision/grounding calls mocked out."""
    if config is None:
        config = _make_config(**config_overrides)

    mock_capture = MagicMock(spec=ScreenCapture)
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    mock_capture.capture_b64.return_value = base64.b64encode(fake_jpeg).decode()
    mock_capture.get_screen_size.return_value = (1024, 768)

    coord = ScreenCoordinatorImpl(
        config, capture=mock_capture, accessibility=None,
    )
    coord._call_vision_model = AsyncMock(return_value="NOT_FOUND")
    coord._call_grounding_model = AsyncMock(return_value="NOT_FOUND")
    return coord


# ---------------------------------------------------------------------------
# 1. Config: grounding_model_provider resolution
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGroundingModelProviderResolution:
    """resolve_step_model('grounding') must respect grounding_model_provider."""

    def test_default_grounding_provider_is_openai(self):
        """Default grounding_model_provider='openai'."""
        config = _make_config()
        assert config.grounding_model_provider == "openai"

    def test_resolve_grounding_default_openai(self):
        """resolve_step_model('grounding') -> ('openai', 'gpt-5.4') by default."""
        config = _make_config()
        provider, model = config.resolve_step_model("grounding")
        assert provider == "openai"
        assert model == "gpt-5.4"

    def test_resolve_grounding_explicit_openai_with_model(self):
        """grounding_model_provider='openai:gpt-4o' resolves correctly."""
        config = _make_config(grounding_model_provider="openai:gpt-4o")
        provider, model = config.resolve_step_model("grounding")
        assert provider == "openai"
        assert model == "gpt-4o"

    def test_resolve_grounding_gemini_with_model(self):
        """grounding_model_provider='gemini:gemini-2.5-flash' resolves correctly."""
        config = _make_config(grounding_model_provider="gemini:gemini-2.5-flash")
        provider, model = config.resolve_step_model("grounding")
        assert provider == "gemini"
        assert model == "gemini-2.5-flash"

    def test_resolve_grounding_gemini_bare(self):
        """grounding_model_provider='gemini' uses gemini default model."""
        config = _make_config(grounding_model_provider="gemini")
        provider, model = config.resolve_step_model("grounding")
        assert provider == "gemini"
        assert model == config.gemini_model

    def test_resolve_grounding_anthropic_bare(self):
        """grounding_model_provider='anthropic' uses anthropic default model."""
        config = _make_config(grounding_model_provider="anthropic")
        provider, model = config.resolve_step_model("grounding")
        assert provider == "anthropic"
        assert model == config.anthropic_model

    def test_resolve_grounding_none_falls_back_to_global(self):
        """grounding_model_provider=None falls back to global model_provider."""
        config = _make_config(grounding_model_provider=None, model_provider="local")
        provider, model = config.resolve_step_model("grounding")
        assert provider == "local"
        assert model == "molmo"  # vision_model default

    def test_resolve_grounding_none_with_anthropic_global(self):
        """grounding_model_provider=None with global anthropic falls back."""
        config = _make_config(
            grounding_model_provider=None,
            model_provider="anthropic",
        )
        provider, model = config.resolve_step_model("grounding")
        assert provider == "anthropic"
        assert model == config.anthropic_model

    def test_resolve_grounding_local_explicit(self):
        """grounding_model_provider='local' resolves to local + vision_model."""
        config = _make_config(grounding_model_provider="local")
        provider, model = config.resolve_step_model("grounding")
        assert provider == "local"
        assert model == "molmo"

    def test_resolve_grounding_local_with_model(self):
        """grounding_model_provider='local:qwen3-vl' resolves to local + qwen3-vl."""
        config = _make_config(grounding_model_provider="local:qwen3-vl")
        provider, model = config.resolve_step_model("grounding")
        assert provider == "local"
        assert model == "qwen3-vl"

    def test_resolve_grounding_openai_bare_uses_openai_model(self):
        """grounding_model_provider='openai' without colon uses openai_model default."""
        config = _make_config(
            grounding_model_provider="openai",
            openai_model="gpt-5.4",
        )
        provider, model = config.resolve_step_model("grounding")
        assert provider == "openai"
        assert model == "gpt-5.4"

    def test_resolve_grounding_custom_openai_model(self):
        """openai_model override is reflected when grounding provider is openai."""
        config = _make_config(
            grounding_model_provider="openai",
            openai_model="gpt-4.1",
        )
        provider, model = config.resolve_step_model("grounding")
        assert provider == "openai"
        assert model == "gpt-4.1"


# ---------------------------------------------------------------------------
# 2. Coordinator: cloud vs local grounding routing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCloudVsLocalGroundingRouting:
    """Verify coordinator dispatches to the right path based on grounding_model_provider."""

    @pytest.mark.asyncio
    async def test_openai_provider_skips_local_grounding(self):
        """grounding_model_provider='openai' must NOT call _call_grounding_model."""
        coord = _make_coordinator(grounding_model_provider="openai")

        # Mock the OpenAI computer-use path to return a result
        mock_client = MagicMock()
        mock_client.locate_element = AsyncMock(return_value=(100, 200, 0.8))

        with patch(_OPENAI_CLIENT_PATCH, return_value=mock_client):
            result = await coord.find_element("Search button")

        coord._call_grounding_model.assert_not_called()
        assert result is not None
        assert result.source == "grounding"

    @pytest.mark.asyncio
    async def test_gemini_provider_skips_local_grounding(self):
        """grounding_model_provider='gemini' must NOT call _call_grounding_model,
        falls through to _call_vision_model with step='grounding'."""
        coord = _make_coordinator(grounding_model_provider="gemini")
        coord._call_vision_model = AsyncMock(
            return_value="FOUND: x=512 y=384 confidence=0.9"
        )

        result = await coord.find_element("Search button")

        coord._call_grounding_model.assert_not_called()
        # The gemini path falls through to _call_vision_model
        coord._call_vision_model.assert_called()

    @pytest.mark.asyncio
    async def test_anthropic_provider_skips_local_grounding(self):
        """grounding_model_provider='anthropic' must NOT call _call_grounding_model."""
        coord = _make_coordinator(grounding_model_provider="anthropic")
        coord._call_vision_model = AsyncMock(
            return_value="FOUND: x=512 y=384 confidence=0.9"
        )

        result = await coord.find_element("Search button")

        coord._call_grounding_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_local_provider_with_grounding_model_uses_local(self):
        """grounding_model_provider=None + grounding_model set -> _call_grounding_model."""
        config = _make_config(
            grounding_model_provider=None,
            grounding_model="molmo",
            grounding_server_url="http://localhost:8091",
        )
        coord = _make_coordinator(config=config)
        coord._call_grounding_model = AsyncMock(
            return_value="FOUND: x=50.0 y=60.0 confidence=0.85"
        )

        result = await coord.find_element("Search button")

        coord._call_grounding_model.assert_called_once()
        assert result is not None
        assert result.source == "grounding"

    @pytest.mark.asyncio
    async def test_local_explicit_provider_uses_local_grounding(self):
        """grounding_model_provider='local' + grounding_model set -> _call_grounding_model."""
        config = _make_config(
            grounding_model_provider="local",
            grounding_model="molmo",
            grounding_server_url="http://localhost:8091",
        )
        coord = _make_coordinator(config=config)
        coord._call_grounding_model = AsyncMock(
            return_value="FOUND: x=50.0 y=60.0 confidence=0.85"
        )

        result = await coord.find_element("Search button")

        coord._call_grounding_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_grounding_model_no_provider_skips_local(self):
        """No grounding_model and None provider -> skip _call_grounding_model entirely."""
        config = _make_config(
            grounding_model_provider=None,
            grounding_model="",
        )
        coord = _make_coordinator(config=config)
        coord._call_vision_model = AsyncMock(return_value="NOT_FOUND")

        result = await coord.find_element("Search button")

        coord._call_grounding_model.assert_not_called()
        assert result is None

    @pytest.mark.asyncio
    async def test_openai_provider_no_grounding_model_uses_computer_use(self):
        """grounding_model_provider='openai' without grounding_model still uses
        the GPT computer-use path (cloud), not local grounding."""
        coord = _make_coordinator(
            grounding_model_provider="openai",
            grounding_model="",
        )

        mock_client = MagicMock()
        mock_client.locate_element = AsyncMock(return_value=(300, 400, 0.8))

        with patch(
            _OPENAI_CLIENT_PATCH,
            return_value=mock_client,
        ):
            result = await coord.find_element("Submit button")

        coord._call_grounding_model.assert_not_called()
        assert result is not None
        assert result.x == 300
        assert result.y == 400


# ---------------------------------------------------------------------------
# 3. GPT computer-use grounding path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGPTComputerUseGrounding:
    """Tests for the OpenAI computer-use branch in find_element."""

    @pytest.mark.asyncio
    async def test_locate_element_returns_coordinates(self):
        """OpenAI locate_element returns (x, y, conf) -> FindElementResult."""
        coord = _make_coordinator(grounding_model_provider="openai")

        mock_client = MagicMock()
        mock_client.locate_element = AsyncMock(return_value=(512, 384, 0.8))

        with patch(
            _OPENAI_CLIENT_PATCH,
            return_value=mock_client,
        ):
            result = await coord.find_element("Login button")

        assert result is not None
        assert isinstance(result, FindElementResult)
        assert result.x == 512
        assert result.y == 384
        assert result.confidence == 0.8
        assert result.source == "grounding"
        assert "GPT computer-use" in result.raw_response

    @pytest.mark.asyncio
    async def test_locate_element_not_found_falls_to_vision(self):
        """OpenAI locate_element returns None -> falls through to text vision."""
        coord = _make_coordinator(grounding_model_provider="openai")
        coord._call_vision_model = AsyncMock(
            return_value="FOUND: x=200 y=300 confidence=0.7"
        )

        mock_client = MagicMock()
        mock_client.locate_element = AsyncMock(return_value=None)

        with patch(
            _OPENAI_CLIENT_PATCH,
            return_value=mock_client,
        ):
            result = await coord.find_element("Login button")

        # Should have fallen through to text vision
        coord._call_vision_model.assert_called()
        # The text vision returns coords, so result should be non-None
        assert result is not None
        assert result.source == "vision"

    @pytest.mark.asyncio
    async def test_locate_element_raises_falls_to_vision(self):
        """OpenAI locate_element raises exception -> graceful fallback to text vision."""
        coord = _make_coordinator(grounding_model_provider="openai")
        coord._call_vision_model = AsyncMock(
            return_value="FOUND: x=100 y=150 confidence=0.6"
        )

        mock_client = MagicMock()
        mock_client.locate_element = AsyncMock(side_effect=RuntimeError("API error"))

        with patch(
            _OPENAI_CLIENT_PATCH,
            return_value=mock_client,
        ):
            result = await coord.find_element("Login button")

        # Should have fallen through to text vision
        coord._call_vision_model.assert_called()
        assert result is not None
        assert result.source == "vision"

    @pytest.mark.asyncio
    async def test_locate_element_import_error_falls_to_vision(self):
        """ImportError for OpenAIClient -> graceful fallback to text vision."""
        coord = _make_coordinator(grounding_model_provider="openai")
        coord._call_vision_model = AsyncMock(
            return_value="FOUND: x=100 y=150 confidence=0.6"
        )

        with patch(
            _OPENAI_CLIENT_PATCH,
            side_effect=ImportError("No module openai_client"),
        ):
            result = await coord.find_element("Login button")

        coord._call_vision_model.assert_called()
        assert result is not None

    @pytest.mark.asyncio
    async def test_correct_model_passed_to_openai_client(self):
        """OpenAIClient receives the resolved model from config."""
        config = _make_config(
            grounding_model_provider="openai:gpt-5.4",
            openai_api_key="sk-test",
        )
        coord = _make_coordinator(config=config)

        captured_kwargs = {}

        class FakeOpenAIClient:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

            async def locate_element(self, *args, **kwargs):
                return (100, 200, 0.8)

        with patch(
            _OPENAI_CLIENT_PATCH,
            FakeOpenAIClient,
        ):
            result = await coord.find_element("Button")

        assert captured_kwargs.get("model") == "gpt-5.4"
        assert captured_kwargs.get("api_key") == "sk-test"
        assert result is not None

    @pytest.mark.asyncio
    async def test_correct_model_gpt4o_passed(self):
        """OpenAIClient receives gpt-4o when grounding_model_provider='openai:gpt-4o'."""
        config = _make_config(
            grounding_model_provider="openai:gpt-4o",
            openai_api_key="sk-test",
        )
        coord = _make_coordinator(config=config)

        captured_kwargs = {}

        class FakeOpenAIClient:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

            async def locate_element(self, *args, **kwargs):
                return (100, 200, 0.8)

        with patch(
            _OPENAI_CLIENT_PATCH,
            FakeOpenAIClient,
        ):
            result = await coord.find_element("Button")

        assert captured_kwargs.get("model") == "gpt-4o"

    @pytest.mark.asyncio
    async def test_screenshot_dimensions_passed_to_locate_element(self):
        """locate_element receives screenshot resolution from config."""
        config = _make_config(
            grounding_model_provider="openai",
            screenshot_resolution=(1024, 768),
        )
        coord = _make_coordinator(config=config)

        locate_args = []

        class FakeOpenAIClient:
            def __init__(self, **kwargs):
                pass

            async def locate_element(self, description, screenshot_b64, w, h):
                locate_args.append((description, w, h))
                return (100, 200, 0.8)

        with patch(
            _OPENAI_CLIENT_PATCH,
            FakeOpenAIClient,
        ):
            await coord.find_element("Button")

        assert len(locate_args) == 1
        desc, w, h = locate_args[0]
        assert desc == "Button"
        assert w == 1024
        assert h == 768

    @pytest.mark.asyncio
    async def test_both_openai_and_vision_fail_returns_none(self):
        """When both OpenAI and text vision fail, find_element returns None."""
        coord = _make_coordinator(grounding_model_provider="openai")
        coord._call_vision_model = AsyncMock(return_value="NOT_FOUND")

        mock_client = MagicMock()
        mock_client.locate_element = AsyncMock(return_value=None)

        with patch(
            _OPENAI_CLIENT_PATCH,
            return_value=mock_client,
        ):
            result = await coord.find_element("Nonexistent button")

        assert result is None

    @pytest.mark.asyncio
    async def test_openai_timeout_falls_to_vision(self):
        """Timeout in OpenAI call -> graceful fallback to text vision."""
        from automation_agent.llm.exceptions import ModelTimeoutError

        coord = _make_coordinator(grounding_model_provider="openai")
        coord._call_vision_model = AsyncMock(
            return_value="FOUND: x=100 y=200 confidence=0.7"
        )

        mock_client = MagicMock()
        mock_client.locate_element = AsyncMock(
            side_effect=ModelTimeoutError("timeout")
        )

        with patch(
            _OPENAI_CLIENT_PATCH,
            return_value=mock_client,
        ):
            result = await coord.find_element("Button")

        coord._call_vision_model.assert_called()
        assert result is not None
        assert result.source == "vision"

    @pytest.mark.asyncio
    async def test_openai_result_has_correct_raw_response(self):
        """raw_response field includes GPT computer-use label."""
        coord = _make_coordinator(grounding_model_provider="openai")

        mock_client = MagicMock()
        mock_client.locate_element = AsyncMock(return_value=(42, 99, 0.8))

        with patch(
            _OPENAI_CLIENT_PATCH,
            return_value=mock_client,
        ):
            result = await coord.find_element("Close icon")

        assert result is not None
        assert "42" in result.raw_response
        assert "99" in result.raw_response
        assert "GPT computer-use" in result.raw_response


# ---------------------------------------------------------------------------
# 4. Coordinate space mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCoordinateSpaceMapping:
    """Verify COORDINATE_SPACES registry entries for cloud models."""

    def test_gpt_54_maps_to_pixel(self):
        assert COORDINATE_SPACES["gpt-5.4"] == "pixel"

    def test_gpt_4o_maps_to_pixel(self):
        assert COORDINATE_SPACES["gpt-4o"] == "pixel"

    def test_gpt_41_maps_to_pixel(self):
        assert COORDINATE_SPACES["gpt-4.1"] == "pixel"

    def test_molmo_maps_to_normalized_0_100(self):
        assert COORDINATE_SPACES["molmo"] == "normalized_0_100"

    def test_gemini_maps_to_normalized_0_1000(self):
        assert COORDINATE_SPACES["gemini"] == "normalized_0_1000"

    def test_claude_sonnet_maps_to_pixel(self):
        assert COORDINATE_SPACES["claude-sonnet-4-20250514"] == "pixel"

    def test_qwen3_vl_maps_to_normalized_0_1000(self):
        assert COORDINATE_SPACES["qwen3-vl"] == "normalized_0_1000"

    def test_molmo2_maps_to_normalized_0_1000(self):
        assert COORDINATE_SPACES["molmo2"] == "normalized_0_1000"

    def test_resolve_coordinate_space_gpt_prefix(self):
        """GPT model prefixes resolve via case-insensitive matching."""
        space = ScreenCoordinatorImpl._resolve_coordinate_space("gpt-5.4")
        assert space == "pixel"

    def test_resolve_coordinate_space_unknown_returns_none(self):
        """Unknown model returns None (caller decides whether to raise)."""
        space = ScreenCoordinatorImpl._resolve_coordinate_space("unknown-model-xyz")
        assert space is None

    def test_all_gpt_models_are_pixel_space(self):
        """All GPT entries in the registry must use 'pixel' coordinate space."""
        for key, space in COORDINATE_SPACES.items():
            if key.startswith("gpt"):
                assert space == "pixel", f"{key} should be 'pixel', got '{space}'"

    def test_pixel_space_returns_raw_coordinates(self):
        """Pixel-space coordinates are returned unchanged by _convert_coordinates."""
        config = _make_config(grounding_model_provider="openai")
        coord = _make_coordinator(config=config)
        x, y = coord._convert_coordinates(512, 384, "gpt-5.4", 1024, 768)
        assert x == 512
        assert y == 384

    def test_normalized_0_100_converts_correctly(self):
        """Molmo normalized 0-100 coords convert to pixels."""
        config = _make_config(grounding_model_provider=None)
        coord = _make_coordinator(config=config)
        x, y = coord._convert_coordinates(50.0, 50.0, "molmo", 1024, 768)
        assert x == 512
        assert y == 384

    def test_normalized_0_1000_converts_correctly(self):
        """Gemini/Qwen 0-1000 coords convert to pixels."""
        config = _make_config(grounding_model_provider=None)
        coord = _make_coordinator(config=config)
        x, y = coord._convert_coordinates(500.0, 500.0, "gemini", 1024, 768)
        assert x == 512
        assert y == 384


# ---------------------------------------------------------------------------
# 5. Edge cases and integration-like scenarios
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGroundingEdgeCases:
    """Edge cases in the grounding routing logic."""

    @pytest.mark.asyncio
    async def test_openai_client_receives_api_key_from_config(self):
        """The API key from config is passed to OpenAIClient."""
        config = _make_config(
            grounding_model_provider="openai",
            openai_api_key="sk-real-key-123",
        )
        coord = _make_coordinator(config=config)

        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def locate_element(self, *a, **kw):
                return (10, 20, 0.8)

        with patch(
            _OPENAI_CLIENT_PATCH,
            FakeClient,
        ):
            await coord.find_element("OK button")

        assert captured["api_key"] == "sk-real-key-123"

    @pytest.mark.asyncio
    async def test_openai_client_receives_timeout_from_config(self):
        """The vision_server_timeout from config is passed to OpenAIClient."""
        config = _make_config(
            grounding_model_provider="openai",
            vision_server_timeout=60,
        )
        coord = _make_coordinator(config=config)

        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def locate_element(self, *a, **kw):
                return (10, 20, 0.8)

        with patch(
            _OPENAI_CLIENT_PATCH,
            FakeClient,
        ):
            await coord.find_element("OK button")

        assert captured["timeout"] == 60

    @pytest.mark.asyncio
    async def test_gemini_provider_falls_through_to_vision_model(self):
        """Gemini grounding uses _call_vision_model with step='grounding'."""
        coord = _make_coordinator(grounding_model_provider="gemini")
        coord._call_vision_model = AsyncMock(
            return_value="FOUND: x=500 y=500 confidence=0.85"
        )

        result = await coord.find_element("Search bar")

        # Verify _call_vision_model was called with step="grounding"
        call_args = coord._call_vision_model.call_args
        assert call_args is not None
        assert call_args.kwargs.get("step") == "grounding" or (
            len(call_args.args) >= 3 and call_args.args[2] == "grounding"
        )

    @pytest.mark.asyncio
    async def test_local_grounding_failure_falls_to_vision(self):
        """When _call_grounding_model raises, falls through to _call_vision_model."""
        config = _make_config(
            grounding_model_provider=None,
            grounding_model="molmo",
            grounding_server_url="http://localhost:8091",
        )
        coord = _make_coordinator(config=config)
        coord._call_grounding_model = AsyncMock(
            side_effect=RuntimeError("Connection refused")
        )
        coord._call_vision_model = AsyncMock(
            return_value="FOUND: x=50 y=60 confidence=0.7"
        )

        result = await coord.find_element("Some button")

        coord._call_grounding_model.assert_called_once()
        coord._call_vision_model.assert_called()
        assert result is not None

    @pytest.mark.asyncio
    async def test_local_grounding_no_coords_falls_to_vision(self):
        """When _call_grounding_model returns no parseable coords, fall to vision."""
        config = _make_config(
            grounding_model_provider=None,
            grounding_model="molmo",
            grounding_server_url="http://localhost:8091",
        )
        coord = _make_coordinator(config=config)
        coord._call_grounding_model = AsyncMock(
            return_value="I cannot locate the element on screen."
        )
        coord._call_vision_model = AsyncMock(
            return_value="FOUND: x=50 y=60 confidence=0.7"
        )

        result = await coord.find_element("Missing button")

        coord._call_grounding_model.assert_called_once()
        coord._call_vision_model.assert_called()

    @pytest.mark.asyncio
    async def test_grounding_provider_with_colon_splits_correctly(self):
        """grounding_model_provider='openai:gpt-5.4' splits on first colon only."""
        config = _make_config(grounding_model_provider="openai:gpt-5.4")
        coord = _make_coordinator(config=config)

        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def locate_element(self, *a, **kw):
                return (10, 20, 0.8)

        with patch(
            _OPENAI_CLIENT_PATCH,
            FakeClient,
        ):
            await coord.find_element("Button")

        # The model should be gpt-5.4, not "gpt-5.4" or something else
        assert captured["model"] == "gpt-5.4"

    @pytest.mark.asyncio
    async def test_accessibility_hit_short_circuits_cloud_grounding(self):
        """Accessibility API hit returns immediately, never reaching cloud grounding."""
        config = _make_config(grounding_model_provider="openai")
        mock_capture = MagicMock(spec=ScreenCapture)
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        mock_capture.capture_b64.return_value = base64.b64encode(fake_jpeg).decode()
        mock_capture.get_screen_size.return_value = (1024, 768)

        mock_ax = MagicMock()
        mock_ax_elem = MagicMock()
        mock_ax_elem.center = (200, 300)
        mock_ax.find_element_by_description.return_value = mock_ax_elem

        coord = ScreenCoordinatorImpl(
            config, capture=mock_capture, accessibility=mock_ax,
        )
        coord._call_vision_model = AsyncMock()
        coord._call_grounding_model = AsyncMock()

        result = await coord.find_element("OK button")

        assert result is not None
        assert result.source == "accessibility"
        assert result.x == 200
        assert result.y == 300
        coord._call_vision_model.assert_not_called()
        coord._call_grounding_model.assert_not_called()
