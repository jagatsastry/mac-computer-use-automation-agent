"""Tests for configuration system."""

import json
import pytest
from pathlib import Path
from automation_agent.config import AgentConfig, LogLevel, ModelProvider, load_config


class TestAgentConfig:
    """Test AgentConfig class."""

    def test_default_config(self):
        """Test default configuration values."""
        config = AgentConfig()

        assert config.model_provider == ModelProvider.OLLAMA
        assert config.ollama_host == "http://localhost:11434"
        assert config.vision_model == "qwen2-vl"
        assert config.text_model == "gemma2:9b"
        assert config.ollama_timeout == 120
        assert config.log_level == LogLevel.INFO
        assert config.action_delay == 0.5
        assert config.max_retries == 3
        print("✓ Default config values correct")

    def test_config_from_dict(self):
        """Test creating config from dictionary."""
        data = {
            "ollama_host": "http://192.168.1.100:11434",
            "vision_model": "custom-vision",
            "text_model": "custom-text",
            "log_level": "DEBUG",
            "action_delay": 1.0,
        }
        config = AgentConfig(**data)

        assert config.ollama_host == "http://192.168.1.100:11434"
        assert config.vision_model == "custom-vision"
        assert config.text_model == "custom-text"
        assert config.log_level == LogLevel.DEBUG
        assert config.action_delay == 1.0
        print("✓ Config from dict works")

    def test_ollama_host_validation(self):
        """Test ollama_host validation."""
        # Valid hosts
        config1 = AgentConfig(ollama_host="http://localhost:11434")
        assert config1.ollama_host == "http://localhost:11434"

        config2 = AgentConfig(ollama_host="https://example.com:11434")
        assert config2.ollama_host == "https://example.com:11434"

        # Invalid host (no http/https)
        with pytest.raises(ValueError, match="must start with http"):
            AgentConfig(ollama_host="localhost:11434")

        print("✓ Ollama host validation works")

    def test_timeout_validation(self):
        """Test timeout validation."""
        # Valid timeout
        config = AgentConfig(ollama_timeout=60)
        assert config.ollama_timeout == 60

        # Invalid timeouts
        with pytest.raises(ValueError):
            AgentConfig(ollama_timeout=0)

        with pytest.raises(ValueError):
            AgentConfig(ollama_timeout=-1)

        print("✓ Timeout validation works")

    def test_screenshot_quality_validation(self):
        """Test screenshot quality validation."""
        # Valid quality
        config = AgentConfig(screenshot_quality=50)
        assert config.screenshot_quality == 50

        # Invalid quality (too low)
        with pytest.raises(ValueError):
            AgentConfig(screenshot_quality=0)

        # Invalid quality (too high)
        with pytest.raises(ValueError):
            AgentConfig(screenshot_quality=101)

        print("✓ Screenshot quality validation works")

    def test_log_dir_creation(self):
        """Test that log directory is created."""
        import tempfile
        import shutil

        temp_dir = Path(tempfile.mkdtemp())
        log_dir = temp_dir / "test_logs"

        try:
            config = AgentConfig(log_dir=log_dir)
            assert log_dir.exists()
            assert log_dir.is_dir()
            print("✓ Log directory created automatically")
        finally:
            shutil.rmtree(temp_dir)

    def test_get_log_file_path(self):
        """Test log file path generation."""
        config = AgentConfig(log_dir=Path("/tmp/test_logs"))
        path = config.get_log_file_path()

        assert path == Path("/tmp/test_logs/automation_agent.log")
        print("✓ Log file path correct")


class TestLoadConfig:
    """Test load_config function."""

    def test_load_config_no_file(self):
        """Test loading config without file."""
        config = load_config()
        assert isinstance(config, AgentConfig)
        print("✓ Load config without file works")

    def test_load_config_from_json_file(self, tmp_path):
        """Test loading config from JSON file."""
        config_file = tmp_path / "config.json"
        config_data = {
            "ollama_host": "http://custom:11434",
            "log_level": "DEBUG",
            "action_delay": 0.8,
        }
        config_file.write_text(json.dumps(config_data))

        config = load_config(config_file)
        assert config.ollama_host == "http://custom:11434"
        assert config.log_level == LogLevel.DEBUG
        assert config.action_delay == 0.8
        print("✓ Load config from JSON file works")

    def test_load_config_nonexistent_file(self, tmp_path):
        """Test loading config from nonexistent file."""
        config_file = tmp_path / "nonexistent.json"
        config = load_config(config_file)

        # Should return default config
        assert isinstance(config, AgentConfig)
        assert config.ollama_host == "http://localhost:11434"
        print("✓ Load config with nonexistent file returns defaults")
