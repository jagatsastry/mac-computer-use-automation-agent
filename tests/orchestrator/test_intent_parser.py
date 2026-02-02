"""Tests for intent parser component."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from automation_agent.orchestrator.intent_parser import IntentParser


class TestIntentParserInit:
    """Test IntentParser initialization."""

    def test_init_with_defaults(self, mock_ollama_client):
        """Test parser initialization with default model."""
        parser = IntentParser(mock_ollama_client)
        assert parser.client == mock_ollama_client
        assert parser.model == "gemma2:9b"

    def test_init_with_custom_model(self, mock_ollama_client):
        """Test parser initialization with custom model."""
        parser = IntentParser(mock_ollama_client, model="llama3:8b")
        assert parser.model == "llama3:8b"


class TestIntentParserParse:
    """Test IntentParser.parse() method."""

    @pytest.mark.asyncio
    async def test_parse_simple_command(self, mock_ollama_client, sample_intent_json):
        """Test parsing a simple command."""
        mock_ollama_client.generate = AsyncMock(return_value=json.dumps(sample_intent_json))
        parser = IntentParser(mock_ollama_client)

        intent = await parser.parse("Open Safari and go to youtube.com")

        assert len(intent.steps) == 2
        assert intent.steps[0].action == "activate_app"
        assert intent.steps[0].params["app_name"] == "Safari"
        assert intent.steps[1].action == "open_url"
        assert intent.requires_observation is False

    @pytest.mark.asyncio
    async def test_parse_complex_command(self, mock_ollama_client, sample_complex_intent_json):
        """Test parsing a complex command requiring observation."""
        mock_ollama_client.generate = AsyncMock(return_value=json.dumps(sample_complex_intent_json))
        parser = IntentParser(mock_ollama_client)

        intent = await parser.parse("Search YouTube for cooking tutorials and click most popular")

        assert len(intent.steps) == 4
        assert intent.requires_observation is True
        assert intent.steps[3].action == "click_element"

    @pytest.mark.asyncio
    async def test_parse_preserves_raw_prompt(self, mock_ollama_client, sample_intent_json):
        """Test that raw prompt is preserved in intent."""
        mock_ollama_client.generate = AsyncMock(return_value=json.dumps(sample_intent_json))
        parser = IntentParser(mock_ollama_client)

        prompt = "Open Safari"
        intent = await parser.parse(prompt)

        assert intent.raw_prompt == prompt

    @pytest.mark.asyncio
    async def test_parse_calls_llm_correctly(self, mock_ollama_client, sample_intent_json):
        """Test that LLM is called with correct parameters."""
        mock_ollama_client.generate = AsyncMock(return_value=json.dumps(sample_intent_json))
        parser = IntentParser(mock_ollama_client, model="test-model")

        await parser.parse("Test command")

        mock_ollama_client.generate.assert_called_once()
        call_kwargs = mock_ollama_client.generate.call_args
        assert call_kwargs.kwargs["model"] == "test-model"
        assert "Test command" in call_kwargs.kwargs["prompt"]
        assert call_kwargs.kwargs["format"] == "json"

    @pytest.mark.asyncio
    async def test_parse_quit_app_command(self, mock_ollama_client):
        """Test parsing quit app command."""
        response = {
            "steps": [{"action": "quit_app", "params": {"app_name": "Google Chrome"}}],
            "requires_observation": False
        }
        mock_ollama_client.generate = AsyncMock(return_value=json.dumps(response))
        parser = IntentParser(mock_ollama_client)

        intent = await parser.parse("Close Chrome")

        assert intent.steps[0].action == "quit_app"
        assert intent.steps[0].params["app_name"] == "Google Chrome"

    @pytest.mark.asyncio
    async def test_parse_type_and_press_key(self, mock_ollama_client):
        """Test parsing type and press key commands."""
        response = {
            "steps": [
                {"action": "type_text", "params": {"text": "hello world"}},
                {"action": "press_key", "params": {"keys": ["return"]}}
            ],
            "requires_observation": False
        }
        mock_ollama_client.generate = AsyncMock(return_value=json.dumps(response))
        parser = IntentParser(mock_ollama_client)

        intent = await parser.parse("Type hello world and press enter")

        assert len(intent.steps) == 2
        assert intent.steps[0].action == "type_text"
        assert intent.steps[1].action == "press_key"


class TestIntentParserJsonParsing:
    """Test JSON parsing edge cases."""

    @pytest.mark.asyncio
    async def test_parse_json_with_markdown_wrapper(self, mock_ollama_client):
        """Test parsing JSON wrapped in markdown code blocks."""
        json_content = {"steps": [{"action": "activate_app", "params": {"app_name": "Safari"}}], "requires_observation": False}
        wrapped_response = f"```json\n{json.dumps(json_content)}\n```"
        mock_ollama_client.generate = AsyncMock(return_value=wrapped_response)
        parser = IntentParser(mock_ollama_client)

        intent = await parser.parse("Open Safari")

        assert intent.steps[0].action == "activate_app"

    @pytest.mark.asyncio
    async def test_parse_json_with_extra_text(self, mock_ollama_client):
        """Test parsing JSON with extra text around it."""
        json_content = {"steps": [{"action": "quit_app", "params": {"app_name": "Safari"}}], "requires_observation": False}
        response = f"Here is the result: {json.dumps(json_content)} That's the answer."
        mock_ollama_client.generate = AsyncMock(return_value=response)
        parser = IntentParser(mock_ollama_client)

        intent = await parser.parse("Close Safari")

        assert intent.steps[0].action == "quit_app"

    @pytest.mark.asyncio
    async def test_parse_invalid_json_raises_error(self, mock_ollama_client):
        """Test that invalid JSON raises ValueError."""
        mock_ollama_client.generate = AsyncMock(return_value="This is not JSON at all")
        parser = IntentParser(mock_ollama_client)

        with pytest.raises(ValueError) as exc_info:
            await parser.parse("Invalid command")

        assert "Could not parse JSON" in str(exc_info.value)


class TestIntentParserComplexityClassification:
    """Test complexity classification."""

    @pytest.mark.asyncio
    async def test_classify_simple_open_command(self, mock_ollama_client):
        """Test classifying simple open command."""
        parser = IntentParser(mock_ollama_client)

        result = await parser.classify_complexity("Open Safari")
        assert result is False  # Simple task

    @pytest.mark.asyncio
    async def test_classify_simple_go_to_command(self, mock_ollama_client):
        """Test classifying simple navigation command."""
        parser = IntentParser(mock_ollama_client)

        result = await parser.classify_complexity("Go to youtube.com")
        assert result is False  # Simple task

    @pytest.mark.asyncio
    async def test_classify_simple_quit_command(self, mock_ollama_client):
        """Test classifying simple quit command."""
        parser = IntentParser(mock_ollama_client)

        result = await parser.classify_complexity("Close Chrome")
        assert result is False  # Simple task

    @pytest.mark.asyncio
    async def test_classify_complex_click_command(self, mock_ollama_client):
        """Test classifying click command as complex."""
        parser = IntentParser(mock_ollama_client)

        result = await parser.classify_complexity("Click the login button")
        assert result is True  # Complex task

    @pytest.mark.asyncio
    async def test_classify_complex_find_command(self, mock_ollama_client):
        """Test classifying find command as complex."""
        parser = IntentParser(mock_ollama_client)

        result = await parser.classify_complexity("Find the search box and type hello")
        assert result is True  # Complex task

    @pytest.mark.asyncio
    async def test_classify_complex_most_popular(self, mock_ollama_client):
        """Test classifying 'most popular' command as complex."""
        parser = IntentParser(mock_ollama_client)

        result = await parser.classify_complexity("Click on the most popular video")
        assert result is True  # Complex task

    @pytest.mark.asyncio
    async def test_classify_complex_select_command(self, mock_ollama_client):
        """Test classifying select command as complex."""
        parser = IntentParser(mock_ollama_client)

        result = await parser.classify_complexity("Select the first result")
        assert result is True  # Complex task

    @pytest.mark.asyncio
    async def test_classify_ambiguous_defaults_to_complex(self, mock_ollama_client):
        """Test that ambiguous commands default to complex."""
        parser = IntentParser(mock_ollama_client)

        result = await parser.classify_complexity("Do something with the screen")
        assert result is True  # Default to complex for safety


class TestIntentParserVariousCommands:
    """Test parsing various command types."""

    @pytest.mark.asyncio
    async def test_parse_keyboard_shortcut(self, mock_ollama_client):
        """Test parsing keyboard shortcut command."""
        response = {
            "steps": [{"action": "press_key", "params": {"keys": ["command", "c"]}}],
            "requires_observation": False
        }
        mock_ollama_client.generate = AsyncMock(return_value=json.dumps(response))
        parser = IntentParser(mock_ollama_client)

        intent = await parser.parse("Copy the selected text")

        assert intent.steps[0].action == "press_key"
        assert intent.steps[0].params["keys"] == ["command", "c"]

    @pytest.mark.asyncio
    async def test_parse_multi_step_command(self, mock_ollama_client):
        """Test parsing multi-step command."""
        response = {
            "steps": [
                {"action": "activate_app", "params": {"app_name": "Safari"}},
                {"action": "open_url", "params": {"url": "https://google.com", "browser": "Safari"}},
                {"action": "type_text", "params": {"text": "weather today"}},
                {"action": "press_key", "params": {"keys": ["return"]}}
            ],
            "requires_observation": False
        }
        mock_ollama_client.generate = AsyncMock(return_value=json.dumps(response))
        parser = IntentParser(mock_ollama_client)

        intent = await parser.parse("Open Safari, go to Google, and search for weather today")

        assert len(intent.steps) == 4
        assert intent.steps[2].params["text"] == "weather today"

    @pytest.mark.asyncio
    async def test_parse_url_normalization(self, mock_ollama_client):
        """Test that URLs are normalized in response."""
        response = {
            "steps": [{"action": "open_url", "params": {"url": "https://www.youtube.com", "browser": "Safari"}}],
            "requires_observation": False
        }
        mock_ollama_client.generate = AsyncMock(return_value=json.dumps(response))
        parser = IntentParser(mock_ollama_client)

        intent = await parser.parse("Open YouTube")

        assert "youtube.com" in intent.steps[0].params["url"]

    @pytest.mark.asyncio
    async def test_parse_app_name_normalization(self, mock_ollama_client):
        """Test that app names are normalized."""
        response = {
            "steps": [{"action": "activate_app", "params": {"app_name": "Google Chrome"}}],
            "requires_observation": False
        }
        mock_ollama_client.generate = AsyncMock(return_value=json.dumps(response))
        parser = IntentParser(mock_ollama_client)

        intent = await parser.parse("Open Chrome")

        assert intent.steps[0].params["app_name"] == "Google Chrome"
