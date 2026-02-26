"""Main entry point for the automation agent CLI."""

import asyncio
import sys
from pathlib import Path
from typing import Any, Tuple

from .cli import parse_args, validate_args
from .config import AgentConfig, LogLevel, ModelProvider, load_config
from .logging import configure_logging, get_logger
from .llm.client import OllamaClient
from .llm import AnthropicClient, ANTHROPIC_AVAILABLE, MolmoVisionClient, MolmoLocalClient
from .workflows import run_restaurant_workflow
from .orchestrator import AutomationAgent
from .actuator import create_actuator
from .planner import ActionPlannerImpl
from .skills import SkillRegistryImpl
from .vision import ScreenCoordinatorImpl


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

    # OpenRouter settings (Molmo mode)
    if hasattr(args, "openrouter_api_key") and args.openrouter_api_key:
        config.openrouter_api_key = args.openrouter_api_key

    # Logging settings
    if args.log_level:
        level = LogLevel(args.log_level)
        config.log_level = level
        config.log_console_level = level
        config.log_file_level = level
    if args.log_dir:
        config.log_dir = args.log_dir

    # Molmo mode: force Ollama vision model for coordinate grounding
    if hasattr(args, "molmo") and args.molmo:
        config.model_provider = ModelProvider.OLLAMA
        config.vision_model = "molmo"

    if hasattr(args, "hammerspoon") and args.hammerspoon:
        config.use_hammerspoon = True


async def _resolve_molmo_vision_backend(
    config: AgentConfig,
    text_llm_client: Any,
    logger: Any,
) -> Tuple[Any, str]:
    """
    Resolve the best available Molmo-capable vision backend.

    Priority:
    1) OpenRouter Molmo via API key
    2) Local Ollama model named "molmo"
    3) Local HuggingFace Molmo model
    4) Fallback to local qwen3-vl
    """
    # 1) OpenRouter Molmo
    if config.openrouter_api_key:
        molmo_client = MolmoVisionClient(
            api_key=config.openrouter_api_key,
            model=config.molmo_model,
            timeout=config.ollama_timeout,
            base_url=config.openrouter_base_url,
        )
        if await molmo_client.test_connection():
            logger.info("using_molmo_openrouter", model=config.molmo_model)
            print(f"[INFO] Using Molmo via OpenRouter ({config.molmo_model})")
            return molmo_client, config.molmo_model
        logger.warning("molmo_openrouter_unavailable_fallback")
        print("[WARN] OpenRouter Molmo unavailable, trying local Ollama models...")

    # 2/3) Local Ollama choices
    if isinstance(text_llm_client, OllamaClient):
        if await text_llm_client.check_model_available("molmo"):
            logger.info("using_molmo_ollama", model="molmo")
            print("[INFO] Using local Ollama Molmo model (molmo)")
            return text_llm_client, "molmo"

        if MolmoLocalClient.dependencies_available():
            local_molmo = MolmoLocalClient(
                model_name=config.molmo_local_model,
                timeout=max(config.ollama_timeout, 1200),
            )
            if await local_molmo.test_connection():
                logger.info("using_molmo_local_hf", model=config.molmo_local_model)
                print(f"[INFO] Using local HuggingFace Molmo ({config.molmo_local_model})")
                return local_molmo, config.molmo_local_model
            logger.warning("molmo_local_hf_unavailable")

        if await text_llm_client.check_model_available("qwen3-vl"):
            logger.warning("molmo_not_found_fallback_qwen3_vl")
            print("[WARN] Local Molmo model not found; falling back to qwen3-vl for vision.")
            return text_llm_client, "qwen3-vl"

    logger.warning("molmo_not_available_any_backend")
    print("[WARN] Molmo backend unavailable; using configured vision model.")
    return text_llm_client, config.vision_model


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
    molmo: bool = False,
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

    # Select vision backend (default: same client as text path)
    vision_client = llm_client
    observer_vision_model = vision_model
    if molmo:
        vision_client, observer_vision_model = await _resolve_molmo_vision_backend(
            config=config,
            text_llm_client=llm_client,
            logger=logger,
        )

    # Initialize components
    planner = ActionPlannerImpl(config)
    skill_registry = SkillRegistryImpl()
    coordinator = ScreenCoordinatorImpl(config)
    actuator = create_actuator(config)
    logger.info("using_actuator", actuator=type(actuator).__name__)
    print(f"[INFO] Using actuator: {type(actuator).__name__}")

    # Create agent
    agent = AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
    )

    if dry_run:
        # Dry run - just plan without executing
        logger.info("dry_run_parsing", prompt=prompt)
        print(f"\n[DRY RUN] Planning: {prompt}")

        try:
            plan = await planner.plan(prompt)
            print(f"\nPlanned Steps:")
            for i, step in enumerate(plan.steps, 1):
                print(f"    {i}. {step.action}: {step.params}")
            return 0
        except Exception as e:
            logger.error("planning_failed", error=str(e))
            print(f"\n[ERROR] Failed to plan: {e}")
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
                for i, sr in enumerate(result.steps, 1):
                    status = "OK" if sr.success else "FAIL"
                    print(f"  {i}. [{status}] {sr.step.action}: {sr.step.params}")

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
                for i, sr in enumerate(result.steps, 1):
                    status = "OK" if sr.success else "FAIL"
                    print(f"  {i}. [{status}] {sr.step.action}: {sr.step.params}")
                    if sr.error:
                        print(f"      Error: {sr.error}")

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
                getattr(args, "molmo", False),
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
