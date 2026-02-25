"""CLI entry point for the orchestrator.

Usage:
    python -m automation_agent.orchestrator --goal "Open Calculator"
    python -m automation_agent.orchestrator --goal "Open Calculator" --dry-run
"""

import argparse
import asyncio
import sys
from pathlib import Path

from automation_agent.config import AgentConfig


def main(argv=None):
    """Run the automation agent from the command line."""
    parser = argparse.ArgumentParser(
        description="macOS Automation Agent Orchestrator",
    )
    parser.add_argument(
        "--goal",
        type=str,
        required=True,
        help="Natural language goal to execute (e.g. 'Open Calculator')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without executing",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Override max iterations (default: from config)",
    )

    args = parser.parse_args(argv)

    config = AgentConfig(_env_file=None)
    if args.max_iterations is not None:
        config = AgentConfig(_env_file=None, max_iterations=args.max_iterations)

    if args.dry_run:
        print("=== DRY RUN ===")
        print(f"Goal: {args.goal}")
        print(f"Max iterations: {config.max_iterations}")
        print("Would initialize: Planner, SkillRegistry, Coordinator, Actuator")
        print("Would execute goal through AutomationAgent.execute()")
        return

    # Full execution requires real components
    asyncio.run(_run_agent(args.goal, config))


async def _run_agent(goal: str, config: AgentConfig):
    """Run the agent with real components."""
    # Import components
    from automation_agent.planner.planner import ActionPlannerImpl
    from automation_agent.skills.registry import SkillRegistryImpl
    from automation_agent.vision.coordinator import ScreenCoordinatorImpl
    from automation_agent.actuator.hammerspoon import HammerspoonActuator
    from automation_agent.logging.event_logger import EventLogger
    from automation_agent.orchestrator.agent import AutomationAgent

    # Initialize components
    planner = ActionPlannerImpl(config)
    skill_registry = SkillRegistryImpl(config.skill_library_path)
    coordinator = ScreenCoordinatorImpl(config)
    actuator = HammerspoonActuator(cli_path=config.hammerspoon_cli_path)
    logger = EventLogger(config.event_log_dir)

    agent = AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
    )

    print(f"Executing goal: {goal}")
    print(f"Run ID: {logger.run_id}")
    print("---")

    result = await agent.execute(goal)

    print("---")
    print(f"Success: {result.success}")
    print(f"Message: {result.message}")
    print(f"Iterations: {result.iterations}")
    print(f"Duration: {result.total_duration_ms}ms")
    print(f"Steps: {len(result.steps)}")

    if not result.success:
        print(f"Error: {result.error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
