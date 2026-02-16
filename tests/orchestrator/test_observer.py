"""Tests for screen observer component."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime
from automation_agent.orchestrator.observer import ScreenObserver
from automation_agent.orchestrator.models import Coordinates, Observation


class TestScreenObserverInit:
    """Test ScreenObserver initialization."""

    def test_init_with_defaults(self, mock_ollama_client, mock_screen_capturer):
        """Test observer initialization with default model."""
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        assert observer.vision == mock_ollama_client
        assert observer.capturer == mock_screen_capturer
        assert observer.model == "qwen3-vl"

    def test_init_with_custom_model(self, mock_ollama_client, mock_screen_capturer):
        """Test observer initialization with custom model."""
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer, model="llava:13b")

        assert observer.model == "llava:13b"


class TestScreenObserverObserve:
    """Test ScreenObserver.observe() method."""

    @pytest.mark.asyncio
    async def test_observe_with_default_question(self, mock_ollama_client, mock_screen_capturer):
        """Test observe with no specific question."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="Safari browser showing YouTube")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        observation = await observer.observe()

        assert isinstance(observation, Observation)
        assert observation.description == "Safari browser showing YouTube"
        assert observation.screenshot_b64 == "base64_screenshot_data"
        assert isinstance(observation.timestamp, datetime)

    @pytest.mark.asyncio
    async def test_observe_with_custom_question(self, mock_ollama_client, mock_screen_capturer):
        """Test observe with specific question."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="Yes, there is a search bar")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        observation = await observer.observe("Is there a search bar visible?")

        mock_ollama_client.generate_vision.assert_called_once()
        call_kwargs = mock_ollama_client.generate_vision.call_args.kwargs
        assert "search bar" in call_kwargs["prompt"]
        assert observation.description == "Yes, there is a search bar"

    @pytest.mark.asyncio
    async def test_observe_calls_capturer(self, mock_ollama_client, mock_screen_capturer):
        """Test that observe calls screen capturer."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="Screen captured")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        await observer.observe()

        mock_screen_capturer.capture_screen_b64.assert_called_once()

    @pytest.mark.asyncio
    async def test_observe_uses_correct_model(self, mock_ollama_client, mock_screen_capturer):
        """Test that observe uses the configured model."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="Result")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer, model="test-vision")

        await observer.observe()

        call_kwargs = mock_ollama_client.generate_vision.call_args.kwargs
        assert call_kwargs["model"] == "test-vision"

    @pytest.mark.asyncio
    async def test_observe_passes_screenshot_to_vision(self, mock_ollama_client, mock_screen_capturer):
        """Test that screenshot is passed to vision model."""
        mock_screen_capturer.capture_screen_b64.return_value = "test_screenshot_data"
        mock_ollama_client.generate_vision = AsyncMock(return_value="Result")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        await observer.observe()

        call_kwargs = mock_ollama_client.generate_vision.call_args.kwargs
        assert call_kwargs["image_b64"] == "test_screenshot_data"


class TestScreenObserverFindElement:
    """Test ScreenObserver.find_element() method."""

    @pytest.mark.asyncio
    async def test_find_element_success(self, mock_ollama_client, mock_screen_capturer):
        """Test successfully finding an element."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="<box>(100,200,300,400)</box>")
        mock_screen_capturer.get_screen_size.return_value = (1920, 1080)
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        coords = await observer.find_element("the search button")

        assert isinstance(coords, Coordinates)
        # Coordinates should be converted from normalized (0-1000) to actual pixels
        # x1=100/1000*1920=192, y1=200/1000*1080=216
        # x2=300/1000*1920=576, y2=400/1000*1080=432
        assert coords.x == 192
        assert coords.y == 216

    @pytest.mark.asyncio
    async def test_find_element_not_found(self, mock_ollama_client, mock_screen_capturer):
        """Test element not found returns None."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="<box>NOT_FOUND</box>")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        coords = await observer.find_element("nonexistent button")

        assert coords is None

    @pytest.mark.asyncio
    async def test_find_element_invalid_response(self, mock_ollama_client, mock_screen_capturer):
        """Test invalid response returns None."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="I cannot find that element")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        coords = await observer.find_element("some element")

        assert coords is None

    @pytest.mark.asyncio
    async def test_find_element_prompt_format(self, mock_ollama_client, mock_screen_capturer):
        """Test that find_element uses correct prompt format."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="<box>NOT_FOUND</box>")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        await observer.find_element("the login button")

        call_kwargs = mock_ollama_client.generate_vision.call_args.kwargs
        assert "login button" in call_kwargs["prompt"]
        assert "bounding box" in call_kwargs["prompt"].lower()

    @pytest.mark.asyncio
    async def test_find_element_calculates_center(self, mock_ollama_client, mock_screen_capturer):
        """Test that returned coordinates have correct center."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="<box>(100,100,200,200)</box>")
        mock_screen_capturer.get_screen_size.return_value = (1000, 1000)  # 1:1 mapping for easy math
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        coords = await observer.find_element("button")

        # With 1:1 mapping, coords should be same as normalized
        assert coords.x == 100
        assert coords.y == 100
        assert coords.width == 100
        assert coords.height == 100
        assert coords.center_x == 150
        assert coords.center_y == 150


class TestScreenObserverCheckCondition:
    """Test ScreenObserver.check_condition() method."""

    @pytest.mark.asyncio
    async def test_check_condition_yes(self, mock_ollama_client, mock_screen_capturer):
        """Test check_condition returns True for YES."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="YES")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        result = await observer.check_condition("Is YouTube loaded?")

        assert result is True

    @pytest.mark.asyncio
    async def test_check_condition_no(self, mock_ollama_client, mock_screen_capturer):
        """Test check_condition returns False for NO."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="NO")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        result = await observer.check_condition("Is there an error?")

        assert result is False

    @pytest.mark.asyncio
    async def test_check_condition_yes_lowercase(self, mock_ollama_client, mock_screen_capturer):
        """Test check_condition handles lowercase yes."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="yes")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        result = await observer.check_condition("Is the page loaded?")

        assert result is True

    @pytest.mark.asyncio
    async def test_check_condition_yes_in_sentence(self, mock_ollama_client, mock_screen_capturer):
        """Test check_condition handles YES in a sentence."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="Yes, the video is playing")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        result = await observer.check_condition("Is the video playing?")

        assert result is True

    @pytest.mark.asyncio
    async def test_check_condition_ambiguous_response(self, mock_ollama_client, mock_screen_capturer):
        """Test check_condition with ambiguous response."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="Maybe, I'm not sure")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        result = await observer.check_condition("Is something visible?")

        assert result is False  # No YES means False


class TestScreenObserverExtractElements:
    """Test ScreenObserver.extract_elements() method."""

    @pytest.mark.asyncio
    async def test_extract_elements_returns_list(self, mock_ollama_client, mock_screen_capturer):
        """Test extract_elements returns a list."""
        mock_ollama_client.generate_vision = AsyncMock(
            return_value="1. Video: Cat compilation - 1M views\n2. Video: Dog tricks - 500K views"
        )
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        elements = await observer.extract_elements("videos")

        assert isinstance(elements, list)
        assert len(elements) >= 1

    @pytest.mark.asyncio
    async def test_extract_elements_includes_raw_description(self, mock_ollama_client, mock_screen_capturer):
        """Test that raw description is included."""
        response = "1. Button: Submit\n2. Button: Cancel"
        mock_ollama_client.generate_vision = AsyncMock(return_value=response)
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        elements = await observer.extract_elements("buttons")

        assert elements[0]["raw_description"] == response

    @pytest.mark.asyncio
    async def test_extract_elements_prompt_format(self, mock_ollama_client, mock_screen_capturer):
        """Test extract_elements uses correct prompt format."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="No elements found")
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        await observer.extract_elements("links")

        call_kwargs = mock_ollama_client.generate_vision.call_args.kwargs
        assert "links" in call_kwargs["prompt"]


class TestScreenObserverCoordinateParsing:
    """Test coordinate parsing edge cases."""

    @pytest.mark.asyncio
    async def test_parse_coordinates_without_parentheses(self, mock_ollama_client, mock_screen_capturer):
        """Test parsing coordinates without parentheses."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="<box>100,200,300,400</box>")
        mock_screen_capturer.get_screen_size.return_value = (1000, 1000)
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        coords = await observer.find_element("button")

        assert coords is not None
        assert coords.x == 100

    @pytest.mark.asyncio
    async def test_parse_coordinates_with_spaces(self, mock_ollama_client, mock_screen_capturer):
        """Test parsing coordinates with extra spaces."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="<box>( 100 , 200 , 300 , 400 )</box>")
        mock_screen_capturer.get_screen_size.return_value = (1000, 1000)
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        coords = await observer.find_element("button")

        assert coords is not None
        assert coords.x == 100

    @pytest.mark.asyncio
    async def test_screen_size_caching(self, mock_ollama_client, mock_screen_capturer):
        """Test that screen size is cached."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="<box>(100,200,300,400)</box>")
        mock_screen_capturer.get_screen_size.return_value = (1920, 1080)
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        # Call find_element multiple times
        await observer.find_element("button1")
        await observer.find_element("button2")

        # get_screen_size should only be called once due to caching
        assert mock_screen_capturer.get_screen_size.call_count == 1

    @pytest.mark.asyncio
    async def test_coordinate_scaling_large_screen(self, mock_ollama_client, mock_screen_capturer):
        """Test coordinate scaling for large screens."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="<box>(500,500,600,600)</box>")
        mock_screen_capturer.get_screen_size.return_value = (3840, 2160)  # 4K
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        coords = await observer.find_element("button")

        # 500/1000 * 3840 = 1920
        # 500/1000 * 2160 = 1080
        assert coords.x == 1920
        assert coords.y == 1080

    @pytest.mark.asyncio
    async def test_coordinate_scaling_small_screen(self, mock_ollama_client, mock_screen_capturer):
        """Test coordinate scaling for small screens."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="<box>(500,500,600,600)</box>")
        mock_screen_capturer.get_screen_size.return_value = (1280, 720)  # HD
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer)

        coords = await observer.find_element("button")

        # 500/1000 * 1280 = 640
        # 500/1000 * 720 = 360
        assert coords.x == 640
        assert coords.y == 360

    @pytest.mark.asyncio
    async def test_coordinate_range_detection_pixel_space(self, mock_ollama_client, mock_screen_capturer):
        """Test auto-detection of pixel-space coordinates."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="<box>(1200,500,1500,700)</box>")
        mock_screen_capturer.get_screen_size.return_value = (1920, 1080)
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer, model="molmo")

        coords = await observer.find_element("reserve button")

        assert coords is not None
        assert coords.x == 1200
        assert coords.y == 500
        assert coords.width == 300
        assert coords.height == 200

    @pytest.mark.asyncio
    async def test_coordinate_range_detection_normalized_space(self, mock_ollama_client, mock_screen_capturer):
        """Test auto-detection of normalized coordinates."""
        mock_ollama_client.generate_vision = AsyncMock(return_value="<box>(500,500,600,600)</box>")
        mock_screen_capturer.get_screen_size.return_value = (1920, 1080)
        observer = ScreenObserver(mock_ollama_client, mock_screen_capturer, model="molmo")

        coords = await observer.find_element("reserve button")

        assert coords is not None
        # Normalized -> pixel conversion on 1920x1080
        assert coords.x == 960
        assert coords.y == 540
