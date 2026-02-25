"""Self-test for the planner — calls real Claude API.

Usage:
    PYTHONPATH=src python -m automation_agent.planner.self_test

Requires ANTHROPIC_API_KEY to be set.
"""

import asyncio
import sys
import time

from automation_agent.config import AgentConfig
from automation_agent.planner.planner import ActionPlannerImpl


async def run_self_test() -> bool:
    """Run planner self-test with real API call.

    Returns True if all checks pass, False otherwise.
    """
    config = AgentConfig(_env_file=None)
    if not config.anthropic_api_key:
        print("SKIP: ANTHROPIC_API_KEY not set")
        return True

    planner = ActionPlannerImpl(config)
    goal = "Open Calculator"

    print(f"Planning goal: {goal!r}")
    start = time.monotonic()

    try:
        plan = await planner.plan(goal)
    except Exception as e:
        print(f"FAIL: plan() raised {type(e).__name__}: {e}")
        return False

    latency = time.monotonic() - start
    print(f"Latency: {latency:.2f}s ({plan.planning_duration_ms}ms internal)")

    # Check: plan has steps
    if not plan.steps:
        print("FAIL: plan has no steps")
        return False
    print(f"Steps: {len(plan.steps)}")

    # Check: all non-terminal steps have verify
    errors = plan.validate()
    if errors:
        print(f"FAIL: validation errors: {errors}")
        return False
    print("Validation: PASS (all steps have verify)")

    # Check: token usage recorded
    if plan.token_usage:
        print(
            f"Token usage: {plan.token_usage.get('input_tokens', 0)} in / "
            f"{plan.token_usage.get('output_tokens', 0)} out"
        )
    else:
        print("WARNING: no token usage recorded")

    # Print steps
    for i, step in enumerate(plan.steps):
        status = "OK" if step.verify or step.action in ("done", "wait_for_user") else "MISSING VERIFY"
        print(f"  [{status}] Step {i}: {step.action}({step.params}) verify={step.verify!r}")

    print("\nSelf-test PASSED")
    return True


def main() -> None:
    success = asyncio.run(run_self_test())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
