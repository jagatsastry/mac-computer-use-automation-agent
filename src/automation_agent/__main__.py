"""Main entry point for the automation agent CLI."""

import sys

from .cli import parse_args, validate_args
from .config import AgentConfig, LogLevel, load_config
from .logging import configure_logging, get_logger


def apply_cli_overrides(config: AgentConfig, args) -> None:
    """Apply command-line argument overrides to configuration."""
    if args.ollama_host:
        config.ollama_host = args.ollama_host
    if args.ollama_model:
        config.vision_model = args.ollama_model
    if args.ollama_timeout:
        config.ollama_timeout = args.ollama_timeout

    if args.log_level:
        level = LogLevel(args.log_level)
        config.log_level = level
        config.log_console_level = level
        config.log_file_level = level
    if args.log_dir:
        config.log_dir = args.log_dir


def main() -> None:
    """Main entry point for the automation agent."""
    args = parse_args()
    validate_args(args)

    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"Error loading configuration: {e}", file=sys.stderr)
        sys.exit(1)

    apply_cli_overrides(config, args)
    configure_logging(config)
    logger = get_logger(__name__)

    logger.info(
        "automation_agent_started",
        version="0.1.0",
        prompt=args.prompt,
        dry_run=args.dry_run,
    )

    try:
        logger.info("received_prompt", prompt=args.prompt, dry_run=args.dry_run)

        if args.dry_run:
            logger.info("dry_run_mode_enabled")
            print(f"\n[DRY RUN] Would execute: {args.prompt}")
            print("\nConfiguration:")
            print(f"  Ollama Host: {config.ollama_host}")
            print(f"  Vision Model: {config.vision_model}")
            print(f"  Text Model: {config.text_model}")
            print(f"  Log Directory: {config.log_dir}")
        else:
            print(f"\n[INFO] Received prompt: {args.prompt}")
            print("\n[INFO] Phase 1: Setup complete. Full agent implementation in progress.")
            print(f"[INFO] Logs written to: {config.get_log_file_path()}")

        logger.info("automation_agent_completed", success=True)
        sys.exit(0)

    except KeyboardInterrupt:
        logger.warning("automation_agent_interrupted")
        print("\n\n[INTERRUPTED] Automation cancelled by user", file=sys.stderr)
        sys.exit(130)

    except Exception as e:
        logger.exception("automation_agent_failed", error=str(e))
        print(f"\n[ERROR] Automation failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
