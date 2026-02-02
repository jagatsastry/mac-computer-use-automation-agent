"""Tests for LLM client."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from automation_agent.llm.client import OllamaClient
from automation_agent.llm.exceptions import ModelNotFoundError, ModelTimeoutError


class TestOllamaClient:
    """Test OllamaClient class."""

    def test_client_initialization(self):
        """Test client initialization."""
        client = OllamaClient(host="http://localhost:11434", timeout=120.0)

        assert client.host == "http://localhost:11434"
        assert client.timeout == 120.0
        print("✓ Client initialization works")

    @pytest.mark.asyncio
    async def test_check_model_available_found(self):
        """Test checking if model is available (found)."""
        client = OllamaClient()

        # Mock the list response
        mock_response = {
            'models': [
                {'name': 'qwen2-vl:latest'},
                {'name': 'gemma2:9b'},
            ]
        }

        with patch.object(client._client, 'list', new=AsyncMock(return_value=mock_response)):
            available = await client.check_model_available('qwen2-vl')
            assert available is True
            print("✓ Model availability check (found) works")

    @pytest.mark.asyncio
    async def test_check_model_available_not_found(self):
        """Test checking if model is available (not found)."""
        client = OllamaClient()

        mock_response = {
            'models': [
                {'name': 'other-model:latest'},
            ]
        }

        with patch.object(client._client, 'list', new=AsyncMock(return_value=mock_response)):
            available = await client.check_model_available('nonexistent-model')
            assert available is False
            print("✓ Model availability check (not found) works")

    @pytest.mark.asyncio
    async def test_generate_success(self):
        """Test successful generation."""
        client = OllamaClient()

        mock_response = {
            'message': {'content': 'Generated response'}
        }

        with patch.object(client._client, 'chat', new=AsyncMock(return_value=mock_response)):
            result = await client.generate(
                model='test-model',
                prompt='Test prompt'
            )

            assert result == 'Generated response'
            print("✓ Generate success works")

    @pytest.mark.asyncio
    async def test_generate_with_system_prompt(self):
        """Test generation with system prompt."""
        client = OllamaClient()

        mock_response = {
            'message': {'content': 'Response with system'}
        }

        with patch.object(client._client, 'chat', new=AsyncMock(return_value=mock_response)):
            result = await client.generate(
                model='test-model',
                prompt='User prompt',
                system='System prompt'
            )

            assert result == 'Response with system'
            print("✓ Generate with system prompt works")

    @pytest.mark.asyncio
    async def test_generate_model_not_found(self):
        """Test generation with model not found."""
        client = OllamaClient()

        with patch.object(client._client, 'chat', new=AsyncMock(side_effect=Exception("model not found"))):
            with pytest.raises(ModelNotFoundError):
                await client.generate(model='nonexistent', prompt='Test')

            print("✓ Model not found exception raised")

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        """Test connection test (success)."""
        client = OllamaClient()

        mock_response = {'models': []}

        with patch.object(client._client, 'list', new=AsyncMock(return_value=mock_response)):
            connected = await client.test_connection()
            assert connected is True
            print("✓ Connection test (success) works")

    @pytest.mark.asyncio
    async def test_test_connection_failure(self):
        """Test connection test (failure)."""
        client = OllamaClient()

        with patch.object(client._client, 'list', new=AsyncMock(side_effect=Exception("Connection error"))):
            connected = await client.test_connection()
            assert connected is False
            print("✓ Connection test (failure) works")
