"""Unit tests for the OpenAI GPT client (llm/openai_client.py).

All tests are fully mocked -- no real API calls.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from automation_agent.config import AgentConfig, ModelProvider
from automation_agent.llm.openai_client import (
    OpenAIClient,
    _MAX_COMPUTER_CALL_ITERATIONS,
    _parse_point_from_text,
    _to_data_url,
    extract_computer_call_id,
    extract_computer_point,
    extract_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    """Create an AgentConfig with test defaults."""
    defaults = {
        "model_provider": "local",
        "vision_model": "molmo",
        "log_dir": "/tmp/test_agent_logs",
        "openai_api_key": "sk-test-key-12345",
        "openai_model": "gpt-4.1",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _fake_response(status_code: int = 200, json_body: dict = None) -> httpx.Response:
    """Build a fake httpx.Response."""
    body = json.dumps(json_body or {}).encode()
    return httpx.Response(
        status_code=status_code,
        content=body,
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
    )


# ---------------------------------------------------------------------------
# 1. extract_text: output_text field
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_output_text_field(self):
        resp = {"output_text": "Hello world"}
        assert extract_text(resp) == "Hello world"

    def test_output_array_content(self):
        resp = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "text", "text": "Line one"},
                        {"type": "text", "text": "Line two"},
                    ],
                }
            ]
        }
        assert "Line one" in extract_text(resp)
        assert "Line two" in extract_text(resp)

    def test_empty_response(self):
        assert extract_text({}) == ""

    def test_output_text_takes_priority(self):
        resp = {
            "output_text": "Priority text",
            "output": [{"text": "Other text"}],
        }
        assert extract_text(resp) == "Priority text"

    def test_output_item_text(self):
        resp = {"output": [{"text": "Direct text item"}]}
        assert extract_text(resp) == "Direct text item"


# ---------------------------------------------------------------------------
# 2. extract_computer_point
# ---------------------------------------------------------------------------


class TestExtractComputerPoint:
    def test_click_action(self):
        resp = {
            "output": [
                {
                    "type": "computer_call",
                    "call_id": "call_123",
                    "actions": [{"type": "click", "x": 512, "y": 384}],
                }
            ]
        }
        assert extract_computer_point(resp) == (512, 384)

    def test_double_click_action(self):
        resp = {
            "output": [
                {
                    "type": "computer_call",
                    "call_id": "call_456",
                    "actions": [{"type": "double_click", "x": 100, "y": 200}],
                }
            ]
        }
        assert extract_computer_point(resp) == (100, 200)

    def test_no_computer_call(self):
        resp = {"output": [{"type": "message", "text": "No click needed"}]}
        assert extract_computer_point(resp) is None

    def test_non_click_action_ignored(self):
        resp = {
            "output": [
                {
                    "type": "computer_call",
                    "call_id": "call_789",
                    "actions": [{"type": "scroll", "x": 0, "y": 100}],
                }
            ]
        }
        assert extract_computer_point(resp) is None

    def test_multiple_actions_picks_first_click(self):
        resp = {
            "output": [
                {
                    "type": "computer_call",
                    "call_id": "call_multi",
                    "actions": [
                        {"type": "scroll", "x": 0, "y": 100},
                        {"type": "click", "x": 42, "y": 99},
                    ],
                }
            ]
        }
        assert extract_computer_point(resp) == (42, 99)

    def test_float_coordinates_truncated(self):
        resp = {
            "output": [
                {
                    "type": "computer_call",
                    "call_id": "call_float",
                    "actions": [{"type": "click", "x": 512.7, "y": 384.2}],
                }
            ]
        }
        assert extract_computer_point(resp) == (512, 384)

    def test_empty_output(self):
        assert extract_computer_point({"output": []}) is None
        assert extract_computer_point({}) is None


# ---------------------------------------------------------------------------
# 3. extract_computer_call_id
# ---------------------------------------------------------------------------


class TestExtractComputerCallId:
    def test_extracts_call_id(self):
        resp = {
            "output": [
                {
                    "type": "computer_call",
                    "call_id": "call_abc123",
                    "actions": [{"type": "screenshot"}],
                }
            ]
        }
        assert extract_computer_call_id(resp) == "call_abc123"

    def test_no_computer_call(self):
        resp = {"output": [{"type": "message"}]}
        assert extract_computer_call_id(resp) is None

    def test_empty(self):
        assert extract_computer_call_id({}) is None


# ---------------------------------------------------------------------------
# 4. _parse_point_from_text
# ---------------------------------------------------------------------------


class TestParsePointFromText:
    def test_keyed_format(self):
        assert _parse_point_from_text("FOUND: x=100, y=200") == (100, 200)

    def test_keyed_format_with_quotes(self):
        assert _parse_point_from_text('x="350" y="175"') == (350, 175)

    def test_tuple_format(self):
        assert _parse_point_from_text("(512, 384)") == (512, 384)

    def test_no_match(self):
        assert _parse_point_from_text("NOT_FOUND") is None

    def test_float_coords(self):
        assert _parse_point_from_text("x=512.5, y=384.9") == (512, 384)


# ---------------------------------------------------------------------------
# 5. _to_data_url
# ---------------------------------------------------------------------------


def test_to_data_url():
    assert _to_data_url("abc123") == "data:image/jpeg;base64,abc123"
    assert _to_data_url("abc", "image/png") == "data:image/png;base64,abc"


# ---------------------------------------------------------------------------
# 6. OpenAIClient construction
# ---------------------------------------------------------------------------


class TestOpenAIClientInit:
    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="API key"):
            OpenAIClient(api_key="", model="gpt-4.1")

    def test_default_model(self):
        client = OpenAIClient(api_key="sk-test")
        assert client.model == "gpt-4.1"

    def test_custom_model(self):
        client = OpenAIClient(api_key="sk-test", model="gpt-5.4")
        assert client.model == "gpt-5.4"


# ---------------------------------------------------------------------------
# 7. find_element -- immediate computer point
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_element_immediate_point():
    """GPT returns a click action on the first call."""
    client = OpenAIClient(api_key="sk-test")
    mock_resp = {
        "id": "resp_123",
        "output": [
            {
                "type": "computer_call",
                "call_id": "call_1",
                "actions": [{"type": "click", "x": 500, "y": 300}],
            }
        ],
    }
    client._post = AsyncMock(return_value=mock_resp)

    result = await client.find_element("Search button", "fakeimg", 1024, 768)
    assert result is not None
    assert result == (500, 300, 0.8)


# ---------------------------------------------------------------------------
# 8. find_element -- computer call loop (screenshot needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_element_computer_call_loop():
    """GPT requests a screenshot first, then returns a click on second call."""
    client = OpenAIClient(api_key="sk-test")

    # First call: computer_call with no click (screenshot request)
    first_resp = {
        "id": "resp_1",
        "output": [
            {
                "type": "computer_call",
                "call_id": "call_screenshot",
                "actions": [{"type": "screenshot"}],
            }
        ],
    }
    # Second call: actual click
    second_resp = {
        "id": "resp_2",
        "output": [
            {
                "type": "computer_call",
                "call_id": "call_click",
                "actions": [{"type": "click", "x": 250, "y": 150}],
            }
        ],
    }
    client._post = AsyncMock(side_effect=[first_resp, second_resp])

    result = await client.find_element("OK button", "fakeimg", 1024, 768)
    assert result == (250, 150, 0.8)
    assert client._post.call_count == 2

    # Verify the second call includes computer_call_output
    second_call_payload = client._post.call_args_list[1][0][0]
    assert second_call_payload["previous_response_id"] == "resp_1"
    assert second_call_payload["input"][0]["type"] == "computer_call_output"
    assert second_call_payload["input"][0]["call_id"] == "call_screenshot"


# ---------------------------------------------------------------------------
# 9. find_element -- NOT_FOUND
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_element_not_found():
    """GPT text response says NOT_FOUND."""
    client = OpenAIClient(api_key="sk-test")
    mock_resp = {
        "id": "resp_nf",
        "output_text": "NOT_FOUND - the element is not visible on screen.",
    }
    client._post = AsyncMock(return_value=mock_resp)

    result = await client.find_element("Nonexistent button", "fakeimg", 1024, 768)
    assert result is None


# ---------------------------------------------------------------------------
# 10. find_element -- text fallback coordinates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_element_text_fallback():
    """GPT returns coordinates in text instead of computer_call."""
    client = OpenAIClient(api_key="sk-test")
    mock_resp = {
        "id": "resp_text",
        "output_text": "The button is at approximately x=400, y=250.",
    }
    client._post = AsyncMock(return_value=mock_resp)

    result = await client.find_element("Submit", "fakeimg", 1024, 768)
    assert result is not None
    assert result == (400, 250, 0.5)


# ---------------------------------------------------------------------------
# 11. describe_screen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_describe_screen():
    client = OpenAIClient(api_key="sk-test")
    mock_resp = {
        "output_text": "Safari browser is open showing google.com with a search bar.",
    }
    client._post = AsyncMock(return_value=mock_resp)

    desc = await client.describe_screen("fakeimg")
    assert "Safari" in desc
    assert "google" in desc


# ---------------------------------------------------------------------------
# 12. verify_condition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_condition_yes():
    client = OpenAIClient(api_key="sk-test")
    client._post = AsyncMock(return_value={"output_text": "YES"})
    result = await client.verify_condition("Calculator app is visible", "fakeimg")
    assert result is True


@pytest.mark.asyncio
async def test_verify_condition_no():
    client = OpenAIClient(api_key="sk-test")
    client._post = AsyncMock(return_value={"output_text": "NO"})
    result = await client.verify_condition("Calculator app is visible", "fakeimg")
    assert result is False


@pytest.mark.asyncio
async def test_verify_condition_unclear():
    client = OpenAIClient(api_key="sk-test")
    client._post = AsyncMock(return_value={"output_text": "UNCLEAR"})
    result = await client.verify_condition("Calculator app is visible", "fakeimg")
    assert result is None


# ---------------------------------------------------------------------------
# 13. generate_text (for planning)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_text():
    client = OpenAIClient(api_key="sk-test")
    mock_resp = {
        "output_text": '{"steps":[{"action":"click","params":{"target":"OK"},"verify":"done"}]}',
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
    client._post = AsyncMock(return_value=mock_resp)

    result = await client.generate_text("Plan steps to click OK")
    assert "steps" in result["content"]
    assert result["usage"]["input_tokens"] == 100
    assert result["usage"]["output_tokens"] == 50


# ---------------------------------------------------------------------------
# 14. generate_vision (multi-image)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_vision():
    client = OpenAIClient(api_key="sk-test")
    mock_resp = {"output_text": "Two screenshots showing the same browser window."}
    client._post = AsyncMock(return_value=mock_resp)

    result = await client.generate_vision("Compare these", ["img1b64", "img2b64"])
    assert "Two screenshots" in result

    # Verify both images are in the payload
    payload = client._post.call_args[0][0]
    content = payload["input"][0]["content"]
    image_items = [c for c in content if c.get("type") == "input_image"]
    assert len(image_items) == 2


# ---------------------------------------------------------------------------
# 15. HTTP error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_rate_limit():
    """429 raises ModelTimeoutError."""
    from automation_agent.llm.exceptions import ModelTimeoutError

    client = OpenAIClient(api_key="sk-test", timeout=5.0)
    resp = _fake_response(429, {"error": {"message": "rate limited"}})

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp):
        with pytest.raises(ModelTimeoutError, match="429"):
            await client._post({"input": "test"})


@pytest.mark.asyncio
async def test_post_server_error():
    """500 raises InvalidResponseError."""
    from automation_agent.llm.exceptions import InvalidResponseError

    client = OpenAIClient(api_key="sk-test", timeout=5.0)
    resp = _fake_response(500, {"error": {"message": "internal error"}})

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp):
        with pytest.raises(InvalidResponseError, match="500"):
            await client._post({"input": "test"})


# ---------------------------------------------------------------------------
# 16. Config integration: openai provider
# ---------------------------------------------------------------------------


def test_config_openai_provider():
    """AgentConfig accepts model_provider='openai'."""
    config = _make_config(model_provider="openai")
    assert config.model_provider == ModelProvider.OPENAI
    assert config.openai_api_key == "sk-test-key-12345"
    assert config.openai_model == "gpt-4.1"


def test_config_openai_model_override():
    config = _make_config(model_provider="openai", openai_model="gpt-5.4")
    assert config.openai_model == "gpt-5.4"


# ---------------------------------------------------------------------------
# 17. find_element -- max iterations exhausted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_element_max_iterations_exhausted():
    """When the computer-call loop exhausts all iterations, try text fallback."""
    client = OpenAIClient(api_key="sk-test")

    # Each iteration returns a screenshot request with no click
    loop_resp = {
        "id": "resp_loop",
        "output": [
            {
                "type": "computer_call",
                "call_id": "call_loop",
                "actions": [{"type": "screenshot"}],
            }
        ],
    }
    # Final response has text fallback
    final_resp = {
        "id": "resp_final",
        "output_text": "x=800, y=600",
    }
    # 1 initial + 3 loop iterations = 4 total; the last returns text
    responses = [loop_resp] * _MAX_COMPUTER_CALL_ITERATIONS + [final_resp]
    client._post = AsyncMock(side_effect=responses)

    result = await client.find_element("Target", "fakeimg", 1024, 768)
    assert result == (800, 600, 0.5)


# ---------------------------------------------------------------------------
# 18. Planner routes to OpenAI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_routes_to_openai():
    """ActionPlannerImpl._call_llm dispatches to _call_openai_llm."""
    from automation_agent.planner.planner import ActionPlannerImpl

    config = _make_config(model_provider="openai")
    planner = ActionPlannerImpl(config)

    mock_result = {
        "content": '{"steps":[{"action":"click","params":{"target":"btn"},"verify":"ok"}]}',
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    planner._call_openai_llm = AsyncMock(return_value=mock_result)
    planner._call_anthropic_llm = AsyncMock()
    planner._call_local_llm = AsyncMock()

    result = await planner._call_llm("test prompt")
    assert result["content"] == mock_result["content"]
    planner._call_openai_llm.assert_awaited_once()
    planner._call_anthropic_llm.assert_not_awaited()
    planner._call_local_llm.assert_not_awaited()


# ---------------------------------------------------------------------------
# 19. Coordinator routes to OpenAI vision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_openai_vision_dispatch():
    """ScreenCoordinatorImpl dispatches to _call_openai_vision for openai provider."""
    from unittest.mock import MagicMock

    from automation_agent.vision.capture import ScreenCapture
    from automation_agent.vision.coordinator import ScreenCoordinatorImpl

    config = _make_config(model_provider="openai")
    capture = MagicMock(spec=ScreenCapture)
    capture.target_resolution = (1024, 768)

    coord = ScreenCoordinatorImpl(config, capture=capture)
    coord._call_openai_vision = AsyncMock(return_value="FOUND: x=500, y=300")

    result = await coord._call_vision_model_with_images("find element", ["fakeimg"])
    assert result == "FOUND: x=500, y=300"
    coord._call_openai_vision.assert_awaited_once()


# ---------------------------------------------------------------------------
# 20. Coordinator model validation skips for openai
# ---------------------------------------------------------------------------


def test_coordinator_skips_model_validation_for_openai():
    """OpenAI provider should not fail model validation."""
    from unittest.mock import MagicMock

    from automation_agent.vision.capture import ScreenCapture
    from automation_agent.vision.coordinator import ScreenCoordinatorImpl

    config = _make_config(model_provider="openai")
    capture = MagicMock(spec=ScreenCapture)
    capture.target_resolution = (1024, 768)

    # Should not raise ValueError
    coord = ScreenCoordinatorImpl(config, capture=capture)
    assert coord._get_active_model() == "gpt-4.1"
