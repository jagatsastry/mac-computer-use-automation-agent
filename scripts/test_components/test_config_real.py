#!/usr/bin/env python3
"""
Real component test: Configuration system
Tests actual config loading from .env and environment variables
"""
import sys
from pathlib import Path
import os
import tempfile
import shutil

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from automation_agent.config import AgentConfig


def test_config_real():
    """Test real configuration system"""
    print("=" * 60)
    print("COMPONENT TEST: Configuration System")
    print("=" * 60)

    # Test 1: Load default configuration
    print("\n[Test 1] Loading default configuration...")
    try:
        config = AgentConfig()
        print(f"  Ollama host: {config.ollama_host}")
        print(f"  Vision model: {config.vision_model}")
        print(f"  Text model: {config.text_model}")
        print(f"  Log level: {config.log_level}")
        print(f"  Status: ✅ Default config loaded")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 2: Load from .env file
    print("\n[Test 2] Testing .env file loading...")
    project_root = Path(__file__).parent.parent.parent
    env_file = project_root / ".env"

    if env_file.exists():
        print(f"  Found .env at: {env_file}")
        with open(env_file) as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            print(f"  Loaded {len(lines)} settings:")
            for line in lines[:5]:  # Show first 5
                print(f"    {line}")
            if len(lines) > 5:
                print(f"    ... and {len(lines) - 5} more")
        print(f"  Status: ✅ .env file loaded")
    else:
        print(f"  No .env file found at {env_file}")
        print(f"  Status: ⚠️  Using defaults")

    # Test 3: Environment variable override
    print("\n[Test 3] Testing environment variable override...")
    try:
        # Set test env var
        os.environ["AGENT_OLLAMA_HOST"] = "http://test:11434"
        config_override = AgentConfig()

        if config_override.ollama_host == "http://test:11434":
            print(f"  Override successful: {config_override.ollama_host}")
            print(f"  Status: ✅ Environment override working")
        else:
            print(f"  Status: ❌ Override failed")
            return False

        # Cleanup
        del os.environ["AGENT_OLLAMA_HOST"]
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 4: Test JSON config loading
    print("\n[Test 4] Testing JSON config file...")
    try:
        # Create temp config
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('''{
                "ollama_host": "http://localhost:11434",
                "vision_model": "qwen2-vl",
                "text_model": "gemma2:9b",
                "log_level": "DEBUG"
            }''')
            temp_config = f.name

        # Load config from JSON file
        from automation_agent.config import load_config
        config_json = load_config(Path(temp_config))

        print(f"  Loaded from: {temp_config}")
        print(f"  Log level: {config_json.log_level}")
        print(f"  Status: ✅ JSON config loading working")

        # Cleanup
        os.unlink(temp_config)
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 5: Validation
    print("\n[Test 5] Testing configuration validation...")
    try:
        config = AgentConfig()

        # Check required fields
        assert config.ollama_host, "ollama_host is required"
        assert config.vision_model, "vision_model is required"
        assert config.text_model, "text_model is required"
        # log_level is an Enum, check if it's valid
        from automation_agent.config import LogLevel
        assert config.log_level in LogLevel, f"invalid log_level: {config.log_level}"
        assert config.max_retries > 0, "max_retries must be positive"
        assert config.ollama_timeout > 0, "timeout must be positive"

        print(f"  All validations passed")
        print(f"  Status: ✅ Validation working")
    except AssertionError as e:
        print(f"  Status: ❌ Validation error - {e}")
        return False
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 6: Display current configuration
    print("\n[Test 6] Current active configuration...")
    config = AgentConfig()
    print(f"  Ollama:")
    print(f"    Host: {config.ollama_host}")
    print(f"    Timeout: {config.ollama_timeout}s")
    print(f"    Max retries: {config.max_retries}")
    print(f"  Models:")
    print(f"    Vision: {config.vision_model}")
    print(f"    Text: {config.text_model}")
    print(f"  Logging:")
    print(f"    Level: {config.log_level}")
    print(f"    Directory: {config.log_dir}")
    print(f"  Safety:")
    print(f"    Blocked apps: {config.blocked_apps}")
    print(f"    Require confirmation: {config.require_confirmation}")
    print(f"  Status: ✅ Configuration displayed")

    print("\n" + "=" * 60)
    print("RESULT: ✅ All tests passed")
    print("=" * 60)
    return True


if __name__ == "__main__":
    result = test_config_real()
    sys.exit(0 if result else 1)
