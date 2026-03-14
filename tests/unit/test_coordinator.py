"""Unit tests for the Vision Coordinator component.

All tests are fully mocked — no real API calls or screencapture invocations.
"""

import base64
import io
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig, ModelProvider
from automation_agent.vision.capture import ScreenCapture, _MAX_JPEG_SIZE
from automation_agent.vision.coordinator import COORDINATE_SPACES, ScreenCoordinatorImpl
from automation_agent.vision.models import ElementLocation, ScreenState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_capture():
    """A mock ScreenCapture that returns known bytes."""
    capture = MagicMock(spec=ScreenCapture)
    # 10 bytes of fake JPEG data
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 6
    capture.capture.return_value = fake_jpeg
    capture.capture_b64.return_value = base64.b64encode(fake_jpeg).decode()
    capture.target_resolution = (1024, 768)
    return capture


def _make_config(**overrides) -> AgentConfig:
    """Create an AgentConfig with test defaults, suppressing log dir creation."""
    defaults = {
        "vision_model": "molmo",
        "log_dir": "/tmp/test_agent_logs",
        "model_provider": "local",  # Pin to local to avoid .env leakage (AGENT_MODEL_PROVIDER=anthropic)
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
    return coord


# ---------------------------------------------------------------------------
# Test 1: find_element with Molmo-format response (normalized 0-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_element_molmo_normalized(mock_capture):
    """Molmo returns 0-100 normalized coordinates -> correct pixel conversion."""
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)
    coord._call_vision_model.return_value = "FOUND: x=50.0, y=25.0"

    result = await coord.find_element("the OK button", screenshot_b64="fakedata")

    assert result is not None
    # 50.0/100 * 1024 = 512, 25.0/100 * 768 = 192
    assert result.x == 512
    assert result.y == 192
    assert result.raw_response != ""


# ---------------------------------------------------------------------------
# Test 2: find_element with Qwen-format response (normalized 0-1000)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_element_qwen_normalized(mock_capture):
    """Qwen returns 0-1000 normalized coordinates -> correct pixel conversion."""
    config = _make_config(vision_model="qwen3-vl")
    coord = _make_coordinator(config, mock_capture)
    coord._call_vision_model.return_value = "FOUND: x=500, y=250"

    result = await coord.find_element("the search bar", screenshot_b64="fakedata")

    assert result is not None
    # 500/1000 * 1024 = 512, 250/1000 * 768 = 192
    assert result.x == 512
    assert result.y == 192


@pytest.mark.asyncio
async def test_find_element_parses_native_point_tag(mock_capture):
    """Native <point> output is accepted in production parsing."""
    config = _make_config(vision_model="qwen3-vl")
    coord = _make_coordinator(config, mock_capture)
    coord._call_vision_model.return_value = '<point x="500" y="250" />'

    result = await coord.find_element("the search bar", screenshot_b64="fakedata")

    assert result is not None
    assert result.x == 512
    assert result.y == 192


@pytest.mark.asyncio
async def test_find_element_parses_native_points_coords(mock_capture):
    """Native <points coords=\"ID X Y\"> output is accepted in production parsing."""
    config = _make_config(vision_model="molmo2")
    coord = _make_coordinator(config, mock_capture)
    coord._call_vision_model.return_value = '<points coords="0 500 250" />'

    result = await coord.find_element("the search bar", screenshot_b64="fakedata")

    assert result is not None
    assert result.x == 512
    assert result.y == 192


# ---------------------------------------------------------------------------
# Test 3: find_element NOT_FOUND response -> returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_element_not_found(mock_capture):
    """Vision model responds NOT_FOUND -> find_element returns None."""
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)
    coord._call_vision_model.return_value = "NOT_FOUND"

    result = await coord.find_element("nonexistent button", screenshot_b64="fakedata")

    assert result is None


# ---------------------------------------------------------------------------
# Test 4: Unknown model -> raises ValueError
# ---------------------------------------------------------------------------


def test_unknown_model_raises_error(mock_capture):
    """Unknown model raises ValueError at construction time, not silently later."""
    config = _make_config(vision_model="unknown-model-xyz")

    with pytest.raises(ValueError, match="not in COORDINATE_SPACES registry"):
        ScreenCoordinatorImpl(config, capture=mock_capture)


# ---------------------------------------------------------------------------
# Test 5: describe_screen returns vision model text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_describe_screen(mock_capture):
    """describe_screen() returns the text from the vision model."""
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)
    expected = "Safari is open showing google.com. The search bar is visible."
    coord._call_vision_model.return_value = expected

    result = await coord.describe_screen(screenshot_b64="fakedata")

    assert result == expected
    coord._call_vision_model.assert_called_once()


# ---------------------------------------------------------------------------
# Test 6: verify_condition YES -> True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_condition_yes(mock_capture):
    """verify_condition returns True when model says YES."""
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)
    coord._call_vision_model.return_value = "YES"

    result = await coord.verify_condition("Safari is open", screenshot_b64="fakedata")

    assert result is True


# ---------------------------------------------------------------------------
# Test 7: verify_condition NO -> False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_condition_no(mock_capture):
    """verify_condition returns False when model says NO."""
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)
    coord._call_vision_model.return_value = "NO"

    result = await coord.verify_condition("Safari is open", screenshot_b64="fakedata")

    assert result is False


@pytest.mark.asyncio
async def test_verify_multiscale_target_uses_two_images(mock_capture):
    """Multi-scale verification submits both detail and context crops."""
    config = _make_config(vision_model="qwen3-vl")
    coord = ScreenCoordinatorImpl(config, capture=mock_capture)
    coord._call_vision_model_with_images = AsyncMock(return_value="YES")

    result = await coord.verify_multiscale_target("Search field", "detail", "context")

    assert result is True
    call = coord._call_vision_model_with_images.call_args
    assert call.args[1] == ["detail", "context"]


@pytest.mark.asyncio
async def test_reflect_action_outcome_parses_structured_json(mock_capture):
    """Reflection responses are parsed into a stable dict."""
    config = _make_config(vision_model="qwen3-vl")
    coord = ScreenCoordinatorImpl(config, capture=mock_capture)
    coord._call_vision_model = AsyncMock(
        return_value='{"worked":"no","observed":"The page is scrolled to the footer","hint":"scroll_to_top"}'
    )

    result = await coord.reflect_action_outcome(
        action="click",
        params={"element": "search orders text field"},
        expected_observation="The search field is focused",
        screenshot_b64="fakedata",
    )

    assert result["worked"] == "no"
    assert result["hint"] == "scroll_to_top"
    assert "footer" in result["observed"].lower()


@pytest.mark.asyncio
async def test_suggest_alternative_affordance_returns_visible_control(mock_capture):
    """Missing-target recovery should return a visible fallback control when safe."""
    config = _make_config(vision_model="qwen3-vl")
    coord = ScreenCoordinatorImpl(config, capture=mock_capture)
    coord._call_vision_model = AsyncMock(
        return_value='{"affordance":"View item","reason":"The order card shows View item but not Return or Replace Items","safe_to_try":"yes"}'
    )

    result = await coord.suggest_alternative_affordance(
        missing_target="Return or Replace Items",
        task_goal="Return the most recent Tylenol order on Amazon",
        expected_observation="Return options page is visible",
        screenshot_b64="fakedata",
    )

    assert result is not None
    assert result["affordance"] == "View item"
    assert "order card" in result["reason"].lower()


@pytest.mark.asyncio
async def test_suggest_alternative_affordance_rejects_unsafe_response(mock_capture):
    """Unsafe or empty suggestions should be ignored."""
    config = _make_config(vision_model="qwen3-vl")
    coord = ScreenCoordinatorImpl(config, capture=mock_capture)
    coord._call_vision_model = AsyncMock(
        return_value='{"affordance":"","reason":"No relevant visible control","safe_to_try":"no"}'
    )

    result = await coord.suggest_alternative_affordance(
        missing_target="Return or Replace Items",
        task_goal="Return the most recent Tylenol order on Amazon",
        expected_observation="Return options page is visible",
        screenshot_b64="fakedata",
    )

    assert result is None


# ---------------------------------------------------------------------------
# Test 8: verify_condition ambiguous response -> False (conservative)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_condition_ambiguous(mock_capture):
    """Ambiguous or unexpected response -> False (conservative default)."""
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)

    for ambiguous_response in [
        "MAYBE",
        "I'm not sure",
        "The screen shows...",
        "POSSIBLY",
        "",
    ]:
        coord._call_vision_model.return_value = ambiguous_response
        result = await coord.verify_condition("something", screenshot_b64="fakedata")
        assert result is False, f"Expected False for response: {repr(ambiguous_response)}"


# ---------------------------------------------------------------------------
# Test 9: capture_b64 returns valid base64 string (mock capture)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_b64_returns_valid_base64(mock_capture):
    """capture_screenshot() returns valid base64 via the capture object."""
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)

    result = await coord.capture_screenshot()

    # Should be a valid base64 string
    decoded = base64.b64decode(result)
    assert len(decoded) > 0
    mock_capture.capture_b64.assert_called_once()


# ---------------------------------------------------------------------------
# Test 10: Screenshot compression check (mock capture)
# ---------------------------------------------------------------------------


def test_screen_capture_returns_jpeg_bytes():
    """ScreenCapture.capture() should call subprocess and return JPEG bytes."""
    with patch("automation_agent.vision.capture.subprocess.run") as mock_run, \
         patch("automation_agent.vision.capture.Image") as mock_image_module:

        # Set up mock image
        mock_img = MagicMock()
        mock_image_module.open.return_value = mock_img
        mock_img.resize.return_value = mock_img
        mock_image_module.LANCZOS = 1

        # Make save write JPEG header bytes
        def mock_save(buf, format=None, quality=None):
            buf.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        mock_img.save.side_effect = mock_save

        capture = ScreenCapture(target_resolution=(1024, 768))
        result = capture.capture()

        # Verify screencapture was called
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "screencapture" in call_args[0][0]
        assert "-x" in call_args[0][0]

        # Verify we got bytes back starting with JPEG magic bytes
        assert isinstance(result, bytes)
        assert result[:2] == b"\xff\xd8"

        # Verify resize was called with target resolution
        mock_img.resize.assert_called_once_with((1024, 768), 1)


# ---------------------------------------------------------------------------
# Test 11: Screenshot downscaled to target resolution
# ---------------------------------------------------------------------------


def test_screen_capture_init_resolution():
    """ScreenCapture stores the target resolution."""
    capture = ScreenCapture(target_resolution=(2560, 1440))
    assert capture.target_resolution == (2560, 1440)

    capture_default = ScreenCapture()
    assert capture_default.target_resolution == (1024, 768)


# ---------------------------------------------------------------------------
# Test 12: Coordinate conversion for various screen sizes
# ---------------------------------------------------------------------------


def test_coordinate_conversion_various_sizes(mock_capture):
    """Coordinate conversion works correctly for different screen sizes."""
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)

    # Test 1440x900 (Molmo uses 0-100 range, so 50.0 = center)
    x, y = coord._convert_coordinates(50.0, 50.0, "molmo", 1440, 900)
    assert x == 720
    assert y == 450

    # Test 2560x1440
    x, y = coord._convert_coordinates(50.0, 50.0, "molmo", 2560, 1440)
    assert x == 1280
    assert y == 720

    # Test Qwen at 1440x900
    x, y = coord._convert_coordinates(500, 500, "qwen3-vl", 1440, 900)
    assert x == 720
    assert y == 450

    # Test Qwen at 2560x1440
    x, y = coord._convert_coordinates(500, 500, "qwen3-vl", 2560, 1440)
    assert x == 1280
    assert y == 720

    # Test pixel coordinates (Claude)
    x, y = coord._convert_coordinates(300.0, 400.0, "claude-sonnet-4-20250514", 1920, 1080)
    assert x == 300
    assert y == 400


# ---------------------------------------------------------------------------
# Test 13: Coordinate conversion with Retina (2x) considerations
# ---------------------------------------------------------------------------


def test_coordinate_conversion_retina(mock_capture):
    """Coordinate conversion handles Retina-like scaled resolutions correctly.

    When screenshots are taken at logical resolution (e.g., 1024x768),
    coordinates from the model should map to that logical resolution,
    not the physical 2x resolution. This test ensures that the conversion
    remains consistent regardless of whether the display is Retina.
    """
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)

    # Logical resolution (what the model sees after downscaling)
    logical_w, logical_h = 1024, 768

    # Molmo says element is at center (50.0, 50.0) of the screenshot (0-100 range)
    x, y = coord._convert_coordinates(50.0, 50.0, "molmo", logical_w, logical_h)
    assert x == 512
    assert y == 384

    # Corner cases
    x, y = coord._convert_coordinates(0.0, 0.0, "molmo", logical_w, logical_h)
    assert x == 0
    assert y == 0

    x, y = coord._convert_coordinates(100.0, 100.0, "molmo", logical_w, logical_h)
    assert x == logical_w - 1  # clamped to valid pixel index
    assert y == logical_h - 1  # clamped to valid pixel index

    # Physical Retina resolution — if user passes physical resolution,
    # conversion still works mathematically
    physical_w, physical_h = 2048, 1536
    x, y = coord._convert_coordinates(50.0, 50.0, "molmo", physical_w, physical_h)
    assert x == 1024
    assert y == 768


# ---------------------------------------------------------------------------
# Test 14: save_screenshot writes file and returns path
# ---------------------------------------------------------------------------


def test_save_screenshot(tmp_path, mock_capture):
    """ScreenCapture.save() writes file and returns the path."""
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    mock_capture.capture.return_value = fake_jpeg

    # Use the actual save method (not the mock)
    capture = ScreenCapture.__new__(ScreenCapture)
    capture.target_resolution = (1024, 768)

    # Patch the capture method to return known bytes
    with patch.object(capture, "capture", return_value=fake_jpeg):
        output_path = str(tmp_path / "test_screenshot.jpg")
        result = capture.save(output_path)

        assert result == output_path
        assert os.path.exists(output_path)

        with open(output_path, "rb") as f:
            content = f.read()
        assert content == fake_jpeg


# ---------------------------------------------------------------------------
# Additional edge case tests
# ---------------------------------------------------------------------------


class TestCoordinateSpacesRegistry:
    """Tests for the COORDINATE_SPACES registry."""

    def test_known_models_are_registered(self):
        """All expected models are in the registry."""
        assert "molmo" in COORDINATE_SPACES
        assert "qwen3-vl" in COORDINATE_SPACES
        assert "claude-sonnet-4-20250514" in COORDINATE_SPACES

    def test_coordinate_space_values(self):
        """Each model maps to a valid coordinate space type."""
        valid_spaces = {"normalized_0_1", "normalized_0_100", "normalized_0_1000", "pixel"}
        for model, space in COORDINATE_SPACES.items():
            assert space in valid_spaces, f"Model '{model}' has invalid space '{space}'"


class TestParseCoordinates:
    """Tests for the _parse_coordinates method."""

    def setup_method(self):
        config = _make_config(vision_model="molmo")
        capture = MagicMock(spec=ScreenCapture)
        capture.target_resolution = (1024, 768)
        self.coord = ScreenCoordinatorImpl(config, capture=capture)

    def test_parse_found(self):
        result = self.coord._parse_coordinates("FOUND: x=100.5, y=200.3")
        assert result == (100.5, 200.3, 0.0)

    def test_parse_not_found(self):
        result = self.coord._parse_coordinates("NOT_FOUND")
        assert result is None

    def test_parse_garbage(self):
        result = self.coord._parse_coordinates("something random")
        assert result is None

    def test_parse_found_integer(self):
        result = self.coord._parse_coordinates("FOUND: x=500, y=250")
        assert result == (500.0, 250.0, 0.0)

    def test_parse_found_with_whitespace(self):
        result = self.coord._parse_coordinates("  FOUND: x = 100 , y = 200  ")
        assert result == (100.0, 200.0, 0.0)


class TestModelModels:
    """Tests for vision data models."""

    def test_element_location_defaults(self):
        loc = ElementLocation(x=100, y=200)
        assert loc.x == 100
        assert loc.y == 200
        assert loc.confidence == 0.0
        assert loc.label == ""
        assert loc.method == ""

    def test_screen_state_defaults(self):
        state = ScreenState(description="test", screenshot_b64="abc")
        assert state.description == "test"
        assert state.resolution == (0, 0)
        assert state.elements == []
        assert state.app_name == ""
        assert state.window_title == ""

    def test_screen_state_with_elements(self):
        elems = [ElementLocation(x=1, y=2), ElementLocation(x=3, y=4)]
        state = ScreenState(
            description="screen",
            screenshot_b64="data",
            resolution=(1024, 768),
            elements=elems,
            app_name="Safari",
            window_title="Google",
        )
        assert len(state.elements) == 2
        assert state.app_name == "Safari"


class TestScreenCoordinatorProtocol:
    """Verify ScreenCoordinatorImpl satisfies the ScreenCoordinator protocol."""

    def test_implements_protocol(self):
        """ScreenCoordinatorImpl should satisfy the ScreenCoordinator protocol."""
        from automation_agent.protocols import ScreenCoordinator

        config = _make_config(vision_model="molmo")
        capture = MagicMock(spec=ScreenCapture)
        capture.target_resolution = (1024, 768)
        coord = ScreenCoordinatorImpl(config, capture=capture)

        assert isinstance(coord, ScreenCoordinator)


@pytest.mark.asyncio
async def test_find_element_captures_screenshot_if_none_provided(mock_capture):
    """find_element captures a screenshot if none is provided."""
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)
    coord._call_vision_model.return_value = "NOT_FOUND"

    await coord.find_element("some button")

    mock_capture.capture_b64.assert_called_once()


@pytest.mark.asyncio
async def test_find_element_claude_pixel_coords(mock_capture):
    """Claude returns pixel coordinates that pass through unchanged."""
    config = _make_config(vision_model="claude-sonnet-4-20250514")
    coord = _make_coordinator(config, mock_capture)
    coord._call_vision_model.return_value = "FOUND: x=350, y=200"

    result = await coord.find_element("Save button", screenshot_b64="fakedata")

    assert result is not None
    assert result.x == 350
    assert result.y == 200


# ---------------------------------------------------------------------------
# NEW TEST: find_element uses correct model for anthropic provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_element_uses_correct_model_for_anthropic(mock_capture):
    """When provider=anthropic, find_element uses anthropic_vision_model for coordinate space, not vision_model."""
    config = _make_config(
        vision_model="qwen3-vl",
        model_provider=ModelProvider.ANTHROPIC,
        anthropic_vision_model="claude-sonnet-4-20250514",
        anthropic_api_key="fake-key",
    )
    coord = _make_coordinator(config, mock_capture)
    # Claude returns pixel coordinates (e.g., x=350, y=200)
    coord._call_vision_model.return_value = "FOUND: x=350, y=200"

    result = await coord.find_element("Save button", screenshot_b64="fakedata")

    assert result is not None
    # If it incorrectly used qwen3-vl (normalized_0_1000), it would compute:
    # x = 350/1000 * 1024 = 358, y = 200/1000 * 768 = 153
    # With the correct model (claude, pixel space), coords pass through as-is:
    assert result.x == 350
    assert result.y == 200


# ---------------------------------------------------------------------------
# NEW TEST: Coordinate clamping at boundary
# ---------------------------------------------------------------------------


def test_coordinate_clamping_at_boundary(mock_capture):
    """Normalized value 1.0 should clamp to width-1 / height-1, not width/height."""
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)

    # normalized_0_100: value 100.0 -> int(100.0/100 * 1024) = 1024, clamped to 1023
    x, y = coord._convert_coordinates(100.0, 100.0, "molmo", 1024, 768)
    assert x == 1023
    assert y == 767

    # normalized_0_1000: value 1000 -> int(1000/1000 * 1024) = 1024, clamped to 1023
    x, y = coord._convert_coordinates(1000, 1000, "qwen3-vl", 1024, 768)
    assert x == 1023
    assert y == 767

    # Values below boundary should not be clamped (50.0 = 50% in 0-100 range)
    x, y = coord._convert_coordinates(50.0, 50.0, "molmo", 1024, 768)
    assert x == 512
    assert y == 384


# ---------------------------------------------------------------------------
# NEW TEST: describe_screen with desktop state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_describe_screen_with_desktop_state(mock_capture):
    """Pass desktop state dict, verify it's merged into description."""
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)
    vision_text = "A web browser showing a search page."
    coord._call_vision_model.return_value = vision_text

    hs_state = {"app_name": "Safari", "window_title": "Google"}
    result = await coord.describe_screen(
        screenshot_b64="fakedata", desktop_state=hs_state
    )

    assert result.startswith("Frontmost app: Safari (window: 'Google').")
    assert vision_text in result


@pytest.mark.asyncio
async def test_describe_screen_without_desktop_state(mock_capture):
    """Without desktop state, describe_screen returns raw vision output."""
    config = _make_config(vision_model="molmo")
    coord = _make_coordinator(config, mock_capture)
    vision_text = "A web browser showing a search page."
    coord._call_vision_model.return_value = vision_text

    result = await coord.describe_screen(screenshot_b64="fakedata")

    assert result == vision_text


# ---------------------------------------------------------------------------
# NEW TEST: Capture size enforcement
# ---------------------------------------------------------------------------


def test_capture_size_enforcement():
    """Mock a large image, verify quality reduction or resize happens."""
    from PIL import Image as PILImage

    with patch("automation_agent.vision.capture.subprocess.run") as mock_run, \
         patch("automation_agent.vision.capture.Image") as mock_image_module:

        mock_img = MagicMock()
        mock_image_module.open.return_value = mock_img
        mock_img.resize.return_value = mock_img
        mock_img.mode = "RGB"
        mock_img.size = (1024, 768)
        mock_image_module.LANCZOS = 1

        call_count = 0

        def mock_save(buf, format=None, quality=None):
            nonlocal call_count
            call_count += 1
            if quality >= 70:
                # Simulate oversized JPEG at high quality
                buf.write(b"\xff\xd8" + b"\x00" * (_MAX_JPEG_SIZE + 1000))
            else:
                # At quality=50, produce something within limits
                buf.write(b"\xff\xd8" + b"\x00" * 100)

        mock_img.save.side_effect = mock_save

        capture = ScreenCapture(target_resolution=(1024, 768))
        result = capture.capture()

        # Should have tried multiple quality levels before succeeding
        assert call_count >= 3  # tried 85, 70, then 50
        assert isinstance(result, bytes)
        assert len(result) <= _MAX_JPEG_SIZE


# ---------------------------------------------------------------------------
# NEW TEST: Unknown model raises at init (fail fast)
# ---------------------------------------------------------------------------


def test_unknown_model_raises_at_init(mock_capture):
    """Completely unknown model (no prefix match) raises ValueError at construction."""
    config = _make_config(vision_model="totally-unknown-model")

    with pytest.raises(ValueError, match="not in COORDINATE_SPACES registry"):
        ScreenCoordinatorImpl(config, capture=mock_capture)


def test_prefix_matched_model_does_not_raise(mock_capture):
    """A model that prefix-matches a known model should NOT raise, just warn."""
    # "molmo-7b" starts with "molmo" which is a known prefix
    config = _make_config(vision_model="molmo-7b")
    coord = ScreenCoordinatorImpl(config, capture=mock_capture)
    # Should construct without error
    assert coord is not None
