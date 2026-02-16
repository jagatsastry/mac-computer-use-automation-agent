"""Tests for Molmo vision client."""

import pytest

from automation_agent.llm.molmo_client import MolmoVisionClient


class TestMolmoVisionClient:
    """Test MolmoVisionClient behavior."""

    def test_extract_text_handles_string_content(self):
        """Test extraction from standard OpenAI-compatible string content."""
        client = MolmoVisionClient(api_key="test-key")
        data = {"choices": [{"message": {"content": "hello"}}]}
        assert client._extract_text(data) == "hello"

    def test_extract_text_handles_list_content(self):
        """Test extraction from list-form content with text chunks."""
        client = MolmoVisionClient(api_key="test-key")
        data = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "alpha "},
                            {"type": "text", "text": "beta"},
                        ]
                    }
                }
            ]
        }
        assert client._extract_text(data) == "alpha beta"

    @pytest.mark.asyncio
    async def test_generate_vision_uses_post_json(self):
        """Test vision generation uses API response extraction path."""
        client = MolmoVisionClient(api_key="test-key")

        async def fake_post(path, payload):
            assert path == "/chat/completions"
            assert payload["model"] == "demo-model"
            return {"choices": [{"message": {"content": "ok"}}]}

        client._post_json = fake_post  # type: ignore[assignment]

        result = await client.generate_vision(
            model="demo-model",
            prompt="find button",
            image_b64="abcd",
        )
        assert result == "ok"
