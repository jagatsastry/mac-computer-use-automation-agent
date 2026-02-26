"""Tests for CLI override behavior in __main__."""

from automation_agent.__main__ import apply_cli_overrides
from automation_agent.cli import parse_args
from automation_agent.config import AgentConfig, ModelProvider


def test_molmo_flag_forces_local_vision_model():
    """--molmo should switch to local vision model 'molmo'."""
    config = AgentConfig(
        model_provider=ModelProvider.ANTHROPIC,
        vision_model="qwen3-vl",
    )
    args = parse_args(["--molmo", "Find reserve button"])

    apply_cli_overrides(config, args)

    assert config.model_provider == ModelProvider.LOCAL
    assert config.vision_model == "molmo"


def test_openrouter_key_override():
    """--openrouter-api-key should update config for Molmo mode."""
    config = AgentConfig(_env_file=None)
    args = parse_args(["--openrouter-api-key", "sk-test", "Find reserve button"])

    apply_cli_overrides(config, args)

    assert config.openrouter_api_key == "sk-test"
