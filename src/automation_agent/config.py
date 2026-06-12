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
    GEMINI = "gemini"
    OPENAI = "openai"


class ConfirmMode(str, Enum):
    """Confirmation mode for destructive actions."""
    ALWAYS = "always"
    SMART = "smart"
    NEVER = "never"


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
    text_server_url: str = Field(
        default="http://localhost:11434",
        description="Server URL for text/planning model (any OpenAI-compatible endpoint, e.g. Ollama)",
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

    # Gemini (Google) Configuration
    gemini_api_key: Optional[str] = Field(
        default=None,
        description="Google Gemini API key (required if model_provider is gemini). Also checks GEMINI_API_KEY env var.",
    )

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def get_gemini_key(cls, v: Optional[str]) -> Optional[str]:
        """Check multiple env vars for Gemini API key."""
        import os
        if v:
            return v
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model to use for text and vision tasks",
    )

    # OpenAI (GPT) Configuration
    openai_api_key: Optional[str] = Field(
        default=None,
        description="OpenAI API key (required if model_provider is openai). "
        "Also checks OPENAI_API_KEY env var.",
    )

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def get_openai_key(cls, v: Optional[str]) -> Optional[str]:
        """Check multiple env vars for OpenAI API key.

        Checks: AGENT_OPENAI_API_KEY (via pydantic prefix), then
        OPENAI_API_KEY (standard), then reads .env file directly.
        """
        import os

        if v:
            return v
        key = os.environ.get("OPENAI_API_KEY")
        if key:
            return key
        # Also try reading from .env file directly (pydantic prefix
        # means AGENT_OPENAI_API_KEY, but users set OPENAI_API_KEY)
        try:
            from pathlib import Path
            env_path = Path(".env")
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("OPENAI_API_KEY="):
                        return line.split("=", 1)[1].strip().strip("'\"")
        except Exception:
            pass
        return None

    openai_model: str = Field(
        default="gpt-5.4",
        description="OpenAI model to use for text, vision, and grounding tasks",
    )

    # Per-step model routing — override model_provider for specific steps.
    # Each accepts "provider:model" (e.g., "openai:gpt-5.4", "gemini:gemini-2.5-flash")
    # or just a provider name (uses that provider's default model).
    # If unset, falls back to the global model_provider.
    planning_model: Optional[str] = Field(
        default=None,
        description="Model for plan generation (e.g., 'gemini:gemini-2.5-flash')",
    )
    grounding_model_provider: Optional[str] = Field(
        default=None,
        description="Model for element grounding (e.g., 'openai:gpt-5.4')",
    )
    verification_model: Optional[str] = Field(
        default=None,
        description="Model for visual verification (e.g., 'gemini:gemini-2.5-flash')",
    )
    screen_description_model: Optional[str] = Field(
        default=None,
        description="Model for screen description (e.g., 'local:molmo')",
    )
    reflection_model: Optional[str] = Field(
        default=None,
        description="Model for async post-run reflection (e.g., 'anthropic:claude-opus-4-20250514')",
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
        default=True,
        description="Use macOS Accessibility API for fast UI element lookup",
    )

    # Actuator backend selection
    actuator_backend: str = Field(
        default="applescript",
        description="Actuator backend: 'applescript' (live macOS desktop) or "
        "'sandbox' (Docker X11 container — never touches the host desktop)",
    )
    sandbox_container: str = Field(
        default="agent-sandbox",
        description="Docker container name for the sandbox desktop",
    )
    sandbox_cdp_port: int = Field(
        default=19222,
        description="Host port publishing the sandbox browser's DevTools endpoint",
        gt=0,
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

    # Skill Librarian Configuration
    skill_librarian_enabled: bool = Field(
        default=True,
        description="Promote high-confidence observations into canonical skills",
    )
    skill_librarian_min_confidence: float = Field(
        default=0.5,
        description="Minimum Bayesian score for promotion",
        gt=0.0,
        le=1.0,
    )
    skill_librarian_min_observations: int = Field(
        default=2,
        description="Minimum observation count before promotion",
        gt=0,
    )
    skill_librarian_min_runs: int = Field(
        default=1,
        description="Minimum distinct run_ids before promotion",
        gt=0,
    )
    skill_librarian_max_tips: int = Field(
        default=10,
        description="Maximum bullet entries in a skill's Learned Tips section",
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

    # --- Gap 5: Infeasibility Detection ---
    infeasibility_same_state_limit: int = Field(
        default=3,
        description="Consecutive same-state steps before triggering infeasibility check",
        gt=0,
    )
    infeasibility_replan_limit: int = Field(
        default=2,
        description="Total replans before triggering infeasibility check",
        gt=0,
    )
    infeasibility_max_advisory_checks: int = Field(
        default=2,
        description="Max 'still achievable' responses before hard abort",
        gt=0,
    )
    infeasibility_timeout_s: float = Field(
        default=30.0,
        description="Timeout in seconds for infeasibility LLM call",
        gt=0.0,
    )
    infeasibility_same_state_threshold: float = Field(
        default=0.05,
        description="Fraction of differing pixels below which a screen is 'same state'",
        gt=0.0,
        lt=1.0,
    )

    # --- Gap 6: User Confirmation ---
    confirm_destructive: ConfirmMode = Field(
        default=ConfirmMode.SMART,
        description="Confirmation mode for destructive actions: always, smart, never",
    )
    dry_run: bool = Field(
        default=False,
        description="If True, skip actual destructive confirmations and log instead",
    )

    @field_validator("confirm_destructive", mode="after")
    @classmethod
    def _validate_confirm_mode(cls, v: ConfirmMode) -> ConfirmMode:
        """NEVER mode requires AGENT_CONFIRM_DESTRUCTIVE=never env var.

        Falls back to SMART with a warning if the env var is missing or
        has a different value — so the agent still runs.
        """
        import os
        import warnings

        if v == ConfirmMode.NEVER:
            env_val = (
                os.environ.get("AGENT_CONFIRM_DESTRUCTIVE", "").lower()
            )
            if env_val != "never":
                warnings.warn(
                    "confirm_destructive=never requires AGENT_CONFIRM_DESTRUCTIVE"
                    " env var set to 'never'. Falling back to SMART mode.",
                    UserWarning,
                    stacklevel=2,
                )
                return ConfirmMode.SMART
        return v

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
    verbose_overlay: bool = Field(
        default=False,
        description="Show detailed LLM responses and reasoning in the status overlay",
    )

    # Gap 1: Set-of-Mark
    som_enabled: bool = Field(
        default=False,
        description="Enable Set-of-Mark numbered label overlay on screenshots",
    )

    # Gap 7: Dual-Resolution Grounding
    dual_resolution_grounding: bool = Field(
        default=False,
        description="Enable dual-resolution grounding (full + crop to VLM)",
    )
    dual_res_threshold: int = Field(
        default=1440,
        description="Screenshot width threshold (px) for dual-res activation",
        gt=0,
    )
    dual_res_timeout_s: float = Field(
        default=30.0,
        description="Timeout in seconds for dual-resolution VLM call. "
                    "Falls back to single-image find_element() on timeout.",
        gt=0.0,
    )

    # Gap 3: Embedding-Based Skill Retrieval
    skill_matching_enabled: bool = Field(
        default=False,
        description="Enable skill template matching. When False, the planner generates "
        "steps purely from the LLM without skill priors.",
    )
    skill_embedding_enabled: bool = Field(
        default=False,
        description="Enable embedding-based skill retrieval",
    )
    skill_embedding_model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        description="Embedding model for skill retrieval (fastembed model name)",
    )
    skill_embedding_rerank_threshold: float = Field(
        default=0.92,
        description="Skip LLM re-rank when top embedding similarity exceeds this",
        gt=0.0,
        le=1.0,
    )
    skill_embedding_min_gap: float = Field(
        default=0.15,
        description="Minimum gap between top-1 and top-2 embedding similarity "
                    "required to skip LLM re-rank",
        gt=0.0,
        le=1.0,
    )

    # Gap 4: Lookahead/Simulation
    lookahead_enabled: bool = Field(
        default=False,
        description="Enable pre-action lookahead for destructive steps",
    )
    lookahead_skip_when_confirmed: bool = Field(
        default=True,
        description="Skip lookahead when user confirmation is already active",
    )
    lookahead_timeout_s: float = Field(
        default=15.0,
        description="Timeout in seconds for lookahead VLM call",
        gt=0.0,
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

    # AC-6: Screenshot persistence
    save_step_screenshots: bool = Field(
        default=True,
        description="Save screenshots at each step (observe, NOT_FOUND, post-action)",
    )

    # JS injection for browser state verification
    js_verification_enabled: bool = Field(
        default=True,
        description="Enable JS injection for browser state verification (type_text, page state)",
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

    def resolve_step_model(self, step: str) -> Tuple[str, str]:
        """Resolve the provider and model for a specific step.

        Args:
            step: One of 'planning', 'grounding', 'verification',
                  'screen_description', 'reflection'.

        Returns:
            (provider_name, model_name) tuple. Falls back to global
            model_provider and its default model if no per-step override.
        """
        step_field_map = {
            "planning": self.planning_model,
            "grounding": self.grounding_model_provider,
            "verification": self.verification_model,
            "screen_description": self.screen_description_model,
            "reflection": self.reflection_model,
        }
        override = step_field_map.get(step)
        if override:
            if ":" in override:
                provider, model = override.split(":", 1)
                return provider.strip(), model.strip()
            # Just a provider name — use its default model
            return override.strip(), self._default_model_for(override.strip())

        # Fall back to global provider
        provider = getattr(self.model_provider, "value", self.model_provider)
        return provider, self._default_model_for(provider)

    def _default_model_for(self, provider: str) -> str:
        """Return the default model name for a provider."""
        defaults = {
            "local": self.vision_model,
            "anthropic": self.anthropic_model,
            "gemini": self.gemini_model,
            "openai": self.openai_model,
        }
        return defaults.get(provider, self.vision_model)

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
