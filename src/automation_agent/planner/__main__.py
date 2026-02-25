"""CLI entry point for the planner component.

Usage:
    python -m automation_agent.planner --plan "Open Calculator"
    python -m automation_agent.planner --dry-run "Open Calculator"
"""

import argparse
import asyncio
import json
import sys

from automation_agent.config import AgentConfig
from automation_agent.planner.planner import ActionPlannerImpl


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="macOS Automation Planner — generate action plans from goals"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--plan",
        type=str,
        metavar="GOAL",
        help="Plan a goal and print the resulting action plan",
    )
    group.add_argument(
        "--dry-run",
        type=str,
        metavar="GOAL",
        help="Print the prompt that would be sent to the LLM (no API call)",
    )
    parser.add_argument(
        "--screen",
        type=str,
        default="",
        help="Screen description to include in the prompt",
    )
    parser.add_argument(
        "--skill-context",
        type=str,
        default=None,
        help="Skill context to include in the prompt",
    )
    return parser


def main(argv: list = None) -> None:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    config = AgentConfig(_env_file=None)
    planner = ActionPlannerImpl(config)

    if args.dry_run:
        prompt = planner._build_plan_prompt(
            args.dry_run, args.screen, args.skill_context
        )
        print("=== DRY RUN: Prompt that would be sent to LLM ===")
        print(prompt)
        return

    if args.plan:
        if not config.anthropic_api_key:
            print(
                "ERROR: ANTHROPIC_API_KEY not set. "
                "Set it via environment variable or config.",
                file=sys.stderr,
            )
            sys.exit(1)

        plan = asyncio.run(
            planner.plan(args.plan, args.screen, args.skill_context)
        )
        print(f"Goal: {plan.goal}")
        print(f"Steps: {len(plan.steps)}")
        print(f"Planning duration: {plan.planning_duration_ms}ms")
        if plan.token_usage:
            print(
                f"Token usage: {plan.token_usage.get('input_tokens', 0)} in / "
                f"{plan.token_usage.get('output_tokens', 0)} out"
            )
        print("\nPlan:")
        for i, step in enumerate(plan.steps):
            print(
                f"  {i}: {step.action}({step.params}) "
                f"[verify: {step.verify!r}, on_fail: {step.on_fail}]"
            )


if __name__ == "__main__":
    main()
