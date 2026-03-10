"""Main entry point for the automation agent CLI."""

import asyncio
import sys
from pathlib import Path

from .cli import parse_args, validate_args
from .config import AgentConfig, LogLevel, ModelProvider, load_config
from .logging import configure_logging, get_logger
from .workflows import run_restaurant_workflow
from .orchestrator import AutomationAgent
from .actuator import create_actuator
from .planner import ActionPlannerImpl
from .skills import SkillRegistryImpl
from .status import StatusOverlayController
from .vision import ScreenCoordinatorImpl


def apply_cli_overrides(config: AgentConfig, args) -> None:
    """Apply command-line argument overrides to configuration."""
    # Provider selection
    if hasattr(args, 'provider') and args.provider:
        config.model_provider = ModelProvider(args.provider)

    # Vision server settings
    if args.vision_server_url:
        config.vision_server_url = args.vision_server_url
    if args.vision_model:
        config.vision_model = args.vision_model
    if args.vision_timeout:
        config.vision_server_timeout = args.vision_timeout

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
    if hasattr(args, "status_ui") and args.status_ui:
        config.status_ui = args.status_ui

    # Molmo mode: force local vision model for coordinate grounding
    if hasattr(args, "molmo") and args.molmo:
        config.model_provider = ModelProvider.LOCAL
        config.vision_model = "molmo"



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

    # Initialize components
    planner = ActionPlannerImpl(config)
    skill_registry = SkillRegistryImpl(config=config)
    coordinator = ScreenCoordinatorImpl(config)
    actuator = create_actuator(config)
    logger.info("using_actuator", actuator=type(actuator).__name__)
    print(f"[INFO] Using actuator: {type(actuator).__name__}")

    # Initialize optional next-gen modules
    screenshot_diff = None
    context_monitor = None
    grounding_router = None

    if config.use_accessibility and coordinator.accessibility:
        try:
            from .orchestrator.context_monitor import ContextMonitor
            context_monitor = ContextMonitor(accessibility=coordinator.accessibility)
            logger.info("context_monitor_enabled")
        except Exception:
            pass

    try:
        from .orchestrator.grounding_router import GroundingRouter
        grounding_router = GroundingRouter(
            accessibility=coordinator.accessibility,
            vision_coordinator=coordinator,
        )
        logger.info(
            "grounding_router_enabled",
            accessibility=bool(coordinator.accessibility),
        )
    except Exception:
        pass

    try:
        from .orchestrator.screenshot_diff import ScreenshotDiffVerifier
        screenshot_diff = ScreenshotDiffVerifier(capturer=coordinator.capture)
        logger.info("screenshot_diff_enabled")
    except Exception:
        pass

    # Create agent
    agent = AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        screenshot_diff=screenshot_diff,
        context_monitor=context_monitor,
        grounding_router=grounding_router,
    )
    status_overlay = StatusOverlayController(config=config, events_file=agent.logger.events_file)

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
        status_overlay.start()
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
