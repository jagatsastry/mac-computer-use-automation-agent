"""Tests for CLI override behavior in __main__."""

from automation_agent.__main__ import apply_cli_overrides
from automation_agent.cli import parse_args
from automation_agent.config import AgentConfig, ModelProvider


def test_molmo_flag_forces_ollama_vision_model():
    """--molmo should switch to Ollama vision model 'molmo'."""
    config = AgentConfig(
        model_provider=ModelProvider.ANTHROPIC,
        vision_model="qwen3-vl",
    )
    args = parse_args(["--molmo", "Find reserve button"])

    apply_cli_overrides(config, args)

    assert config.model_provider == ModelProvider.OLLAMA
    assert config.vision_model == "molmo"
