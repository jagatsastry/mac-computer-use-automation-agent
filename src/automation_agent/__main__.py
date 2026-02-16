"""Main entry point for the automation agent CLI."""

import asyncio
import sys
from pathlib import Path

from .cli import parse_args, validate_args
from .config import AgentConfig, LogLevel, ModelProvider, load_config
from .logging import configure_logging, get_logger
from .llm.client import OllamaClient
from .llm import AnthropicClient, ANTHROPIC_AVAILABLE
from .perception.capture import ScreenCapturer
from .workflows import run_restaurant_workflow
from .orchestrator import (
    AutomationAgent,
    IntentParser,
    ActionRegistry,
    ScreenObserver,
)


def apply_cli_overrides(config: AgentConfig, args) -> None:
    """Apply command-line argument overrides to configuration."""
    # Provider selection
    if hasattr(args, 'provider') and args.provider:
        config.model_provider = ModelProvider(args.provider)

    # Ollama settings
    if args.ollama_host:
        config.ollama_host = args.ollama_host
    if args.ollama_model:
        config.vision_model = args.ollama_model
    if args.ollama_timeout:
        config.ollama_timeout = args.ollama_timeout

    # Anthropic settings
    if hasattr(args, 'anthropic_api_key') and args.anthropic_api_key:
        config.anthropic_api_key = args.anthropic_api_key
    if hasattr(args, 'anthropic_model') and args.anthropic_model:
        config.anthropic_model = args.anthropic_model
        config.anthropic_vision_model = args.anthropic_model

    # Logging settings
    if args.log_level:
        level = LogLevel(args.log_level)
        config.log_level = level
        config.log_console_level = level
        config.log_file_level = level
    if args.log_dir:
        config.log_dir = args.log_dir


def _is_restaurant_prompt(prompt: str) -> bool:
    """Best-effort intent check for restaurant reservation workflows."""
    lowered = prompt.lower()
    keywords = [
        "restaurant",
        "reservation",
        "reserve",
        "book a table",
        "opentable",
        "yelp",
        "dinner",
        "lunch",
        "cuisine",
    ]
    return any(token in lowered for token in keywords)


async def run_agent(
    prompt: str,
    config: AgentConfig,
    dry_run: bool = False,
    restaurant_only: bool = False,
) -> int:
    """
    Run the automation agent with the given prompt.

    Args:
        prompt: User's natural language command
        config: Agent configuration
        dry_run: If True, only parse intent without executing

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    logger = get_logger(__name__)

    # Initialize LLM client based on provider
    if config.model_provider == ModelProvider.ANTHROPIC:
        if not ANTHROPIC_AVAILABLE:
            logger.error("anthropic_not_installed")
            print("\n[ERROR] Anthropic provider selected but 'anthropic' package not installed")
            print("Install with: pip install anthropic")
            return 1

        if not config.anthropic_api_key:
            logger.error("anthropic_api_key_missing")
            print("\n[ERROR] Anthropic API key not configured")
            print("Set AGENT_ANTHROPIC_API_KEY environment variable or add to config")
            return 1

        llm_client = AnthropicClient(
            api_key=config.anthropic_api_key,
            timeout=config.ollama_timeout,
            model=config.anthropic_model,
            vision_model=config.anthropic_vision_model,
        )
        text_model = config.anthropic_model
        vision_model = config.anthropic_vision_model

        # Test connection
        if not await llm_client.test_connection():
            logger.error("anthropic_connection_failed")
            print("\n[ERROR] Could not connect to Anthropic API")
            print("Check your API key and network connection")
            return 1

        logger.info("using_anthropic_provider", model=config.anthropic_model)
        print(f"[INFO] Using Anthropic Claude ({config.anthropic_model})")

    else:
        # Default: Ollama
        llm_client = OllamaClient(
            host=config.ollama_host,
            timeout=config.ollama_timeout,
        )
        text_model = config.text_model
        vision_model = config.vision_model

        # Test connection
        if not await llm_client.test_connection():
            logger.error("ollama_connection_failed", host=config.ollama_host)
            print(f"\n[ERROR] Could not connect to Ollama at {config.ollama_host}")
            print("Make sure Ollama is running: ollama serve")
            return 1

    # Initialize components
    capturer = ScreenCapturer(config)
    parser = IntentParser(llm_client, model=text_model)
    observer = ScreenObserver(llm_client, capturer, model=vision_model)
    registry = ActionRegistry()

    # Create agent
    agent = AutomationAgent(
        parser=parser,
        observer=observer,
        registry=registry,
        llm_client=llm_client,
        text_model=text_model,
        max_iterations=20,
        action_delay=config.action_delay,
    )

    if dry_run:
        # Dry run - just parse the intent
        logger.info("dry_run_parsing", prompt=prompt)
        print(f"\n[DRY RUN] Parsing: {prompt}")

        try:
            intent = await parser.parse(prompt)
            print(f"\nParsed Intent:")
            print(f"  Requires Observation: {intent.requires_observation}")
            print(f"  Steps:")
            for i, step in enumerate(intent.steps, 1):
                print(f"    {i}. {step.action}: {step.params}")
            return 0
        except Exception as e:
            logger.error("intent_parsing_failed", error=str(e))
            print(f"\n[ERROR] Failed to parse intent: {e}")
            return 1

    if restaurant_only or _is_restaurant_prompt(prompt):
        logger.info("restaurant_workflow_enabled")
        print("\n[INFO] Running restaurant-focused workflow")
        try:
            result = await run_restaurant_workflow(
                prompt=prompt,
                agent=agent,
                repo_root=Path.cwd(),
            )
        except Exception as e:
            logger.exception("restaurant_workflow_failed", error=str(e))
            print(f"\n[ERROR] Restaurant workflow failed: {e}")
            return 1

        if result.success:
            print(f"\n[SUCCESS] {result.message}")
            return 0

        print(f"\n[FAILED] {result.message}")
        if result.error:
            print(f"Error: {result.error}")
        return 1

    # Execute the command
    logger.info("executing_prompt", prompt=prompt)
    print(f"\n[INFO] Executing: {prompt}")

    try:
        result = await agent.execute(prompt)

        if result.success:
            logger.info("execution_succeeded", message=result.message)
            print(f"\n[SUCCESS] {result.message}")

            if result.steps:
                print(f"\nActions executed:")
                for i, step in enumerate(result.steps, 1):
                    status = "OK" if step.success else "FAIL"
                    print(f"  {i}. [{status}] {step.action}: {step.params}")

            if result.iterations > 0:
                print(f"\nCompleted in {result.iterations} iteration(s)")

            return 0
        else:
            logger.error("execution_failed", error=result.error, message=result.message)
            print(f"\n[FAILED] {result.message}")

            if result.error:
                print(f"Error: {result.error}")

            if result.steps:
                print(f"\nActions attempted:")
                for i, step in enumerate(result.steps, 1):
                    status = "OK" if step.success else "FAIL"
                    print(f"  {i}. [{status}] {step.action}: {step.params}")
                    if step.error:
                        print(f"      Error: {step.error}")

            return 1

    except Exception as e:
        logger.exception("agent_execution_error", error=str(e))
        print(f"\n[ERROR] Agent execution failed: {e}")
        return 1


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
        # Run the async agent
        exit_code = asyncio.run(
            run_agent(
                args.prompt,
                config,
                args.dry_run,
                getattr(args, "restaurant_only", False),
            )
        )
        logger.info("automation_agent_completed", success=(exit_code == 0))
        sys.exit(exit_code)

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
