"""Configuration system for the automation agent."""

from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Tuple

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogLevel(str, Enum):
    """Available log levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ModelProvider(str, Enum):
    """Supported LLM providers."""
    LOCAL = "local"
    ANTHROPIC = "anthropic"


class StatusUIMode(str, Enum):
    """Supported live status UI modes."""

    OFF = "off"
    OVERLAY = "overlay"


class AgentConfig(BaseSettings):
    """
    Main configuration for the automation agent.

    Configuration is loaded in this order (later sources override earlier):
    1. Default values
    2. Environment variables (with AGENT_ prefix)
    3. .env file in current directory
    4. config.json file (if specified)
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM Configuration
    model_provider: ModelProvider = Field(
        default=ModelProvider.LOCAL,
        description="LLM provider to use (local or anthropic)",
    )
    vision_server_url: str = Field(
        default="http://localhost:8080",
        description="Vision server URL (any OpenAI-compatible endpoint, e.g. llama.cpp)",
    )
    vision_model: str = Field(
        default="qwen3-vl",
        description="Vision model name for screen analysis",
    )
    text_model: str = Field(
        default="gemma2:9b",
        description="Text model for planning and reasoning",
    )
    vision_server_timeout: int = Field(
        default=300,
        description="Timeout in seconds for vision server API calls",
        gt=0,
    )

    # Grounding Model Configuration
    grounding_model: str = Field(
        default="",
        description="Specialized grounding model name. If set, used for find_element calls.",
    )
    grounding_server_url: str = Field(
        default="",
        description="Server URL for grounding model (if different from vision server)",
    )
    grounding_llm_routing_enabled: bool = Field(
        default=True,
        description="Use an LLM tie-breaker for ambiguous grounding decisions",
    )
    grounding_llm_max_candidates: int = Field(
        default=12,
        description="Maximum number of accessibility candidates included in LLM routing context",
        gt=0,
    )

    # Anthropic (Claude) Configuration
    anthropic_api_key: Optional[str] = Field(
        default=None,
        description="Anthropic API key (required if model_provider is anthropic). Also checks ANTHROPIC_API_KEY env var.",
    )

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def get_anthropic_key(cls, v: Optional[str]) -> Optional[str]:
        """Check multiple env vars for Anthropic API key."""
        import os
        if v:
            return v
        # Fallback to common ANTHROPIC_API_KEY env var
        return os.environ.get("ANTHROPIC_API_KEY")
    anthropic_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Anthropic model to use for text generation",
    )
    anthropic_vision_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Anthropic model to use for vision tasks",
    )

    # Molmo/OpenRouter configuration
    openrouter_api_key: Optional[str] = Field(
        default=None,
        description="OpenRouter API key for Molmo vision mode. Also checks OPENROUTER_API_KEY env var.",
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="Base URL for OpenRouter-compatible API",
    )
    molmo_model: str = Field(
        default="allenai/molmo-2-8b:free",
        description="Molmo model identifier for OpenRouter vision mode",
    )
    molmo_local_model: str = Field(
        default="allenai/MolmoE-1B-0924",
        description="Local HuggingFace Molmo model identifier",
    )
    use_accessibility: bool = Field(
        default=False,
        description="Use macOS Accessibility API for fast UI element lookup",
    )
    # Screenshot / Vision Configuration
    screenshot_resolution: Tuple[int, int] = Field(
        default=(1024, 768),
        description="Target resolution (width, height) for screenshots sent to vision models",
    )

    # Skill Library Configuration
    skill_library_path: Optional[Path] = Field(
        default=None,
        description="Path to skill library directory. Defaults to bundled skills.",
    )
    skill_learning_enabled: bool = Field(
        default=True,
        description="Learn generalized skill observations from prior runs",
    )
    skill_learning_dir: Path = Field(
        default=Path("logs/skill_learning"),
        description="Directory for generalized skill observations learned from prior runs",
    )
    skill_learning_max_observations: int = Field(
        default=5,
        description="Maximum learned observations injected into skill context",
        gt=0,
    )

    # Event Logger Configuration
    event_log_dir: Path = Field(
        default=Path("logs/runs"),
        description="Directory for structured event logs (per-run JSONL + traces)",
    )

    # Orchestrator Configuration
    max_iterations: int = Field(
        default=20,
        description="Maximum number of iterations (steps + retries) before aborting",
        gt=0,
    )

    @field_validator("openrouter_api_key", mode="before")
    @classmethod
    def get_openrouter_key(cls, v: Optional[str]) -> Optional[str]:
        """Check multiple env vars for OpenRouter API key."""
        import os

        if v:
            return v
        return os.environ.get("OPENROUTER_API_KEY")

    # Automation Configuration
    screenshot_quality: int = Field(
        default=85,
        description="JPEG quality for screenshots (1-100)",
        ge=1,
        le=100,
    )
    action_delay: float = Field(
        default=0.5,
        description="Delay in seconds between automation actions",
        ge=0.0,
    )
    max_retries: int = Field(
        default=3,
        description="Maximum number of retries for failed actions",
        ge=0,
    )
    retry_delay: float = Field(
        default=1.0,
        description="Delay in seconds between retries",
        ge=0.0,
    )

    # Logging Configuration
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Logging level",
    )
    log_console_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Console logging level",
    )
    log_file_level: LogLevel = Field(
        default=LogLevel.DEBUG,
        description="File logging level",
    )
    log_dir: Path = Field(
        default=Path("logs"),
        description="Directory for log files",
    )
    log_file_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum size of log file before rotation",
        gt=0,
    )
    log_file_backup_count: int = Field(
        default=5,
        description="Number of rotated log files to keep",
        ge=0,
    )
    log_structured: bool = Field(
        default=True,
        description="Enable structured JSON logging",
    )

    # Live status UI
    status_ui: StatusUIMode = Field(
        default=StatusUIMode.OFF,
        description="Show a live on-screen status UI during execution",
    )
    status_overlay_poll_interval: float = Field(
        default=0.25,
        description="How often the floating status overlay polls for new events",
        gt=0.0,
    )
    status_overlay_max_lines: int = Field(
        default=200,
        description="Maximum number of log lines retained in the status overlay",
        gt=0,
    )
    status_overlay_linger_seconds: float = Field(
        default=15.0,
        description="How long the status overlay stays visible after completion",
        ge=0.0,
    )

    # Safety Configuration
    require_confirmation: bool = Field(
        default=False,
        description="Require user confirmation for destructive actions",
    )
    blocked_apps: List[str] = Field(
        default_factory=lambda: ["System Preferences", "System Settings"],
        description="List of blocked application names",
    )

    @field_validator("log_dir")
    @classmethod
    def create_log_dir(cls, v: Path) -> Path:
        """Ensure log directory exists."""
        v.mkdir(parents=True, exist_ok=True)
        return v

    @field_validator("vision_server_url")
    @classmethod
    def validate_vision_server_url(cls, v: str) -> str:
        """Ensure vision server URL has proper format."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("vision_server_url must start with http:// or https://")
        return v.rstrip("/")

    @field_validator("grounding_server_url")
    @classmethod
    def validate_grounding_server_url(cls, v: str) -> str:
        """Validate grounding server URL when non-empty."""
        if not v:
            return v
        if not v.startswith(("http://", "https://")):
            raise ValueError("grounding_server_url must start with http:// or https://")
        return v.rstrip("/")

    def get_log_file_path(self) -> Path:
        """Get the current log file path."""
        return self.log_dir / "automation_agent.log"


def load_config(config_file: Optional[Path] = None) -> AgentConfig:
    """
    Load configuration from environment and optional config file.

    Args:
        config_file: Optional path to JSON config file

    Returns:
        Loaded configuration
    """
    if config_file and config_file.exists():
        import json
        with open(config_file) as f:
            config_data = json.load(f)
        return AgentConfig(**config_data)

    return AgentConfig()
