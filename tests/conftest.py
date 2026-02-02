"""Pytest configuration and fixtures."""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def sample_config_data():
    """Sample configuration data for testing."""
    return {
        "ollama_host": "http://test:11434",
        "vision_model": "test-vision",
        "text_model": "test-text",
        "log_level": "DEBUG",
    }


@pytest.fixture
def mock_ollama_response():
    """Mock Ollama API response."""
    return {
        'message': {'content': 'Mock response'},
        'model': 'test-model',
    }
