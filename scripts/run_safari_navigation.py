#!/usr/bin/env python3
"""Navigate to URL in Safari, verify page title.

Usage: PYTHONPATH=src python3 scripts/run_safari_navigation.py [url]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation_agent.config import AgentConfig
from automation_agent.planner import ActionPlannerImpl
from automation_agent.skills import SkillRegistryImpl
from automation_agent.vision import ScreenCoordinatorImpl
from automation_agent.actuator import HammerspoonActuator
from automation_agent.orchestrator import AutomationAgent
from automation_agent.logging.event_logger import EventLogger


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    config = AgentConfig()
    logger = EventLogger(Path("logs/runs"))

    print(f"Run ID: {logger.run_id}")

    agent = AutomationAgent(
        planner=ActionPlannerImpl(config),
        skill_registry=SkillRegistryImpl(),
        coordinator=ScreenCoordinatorImpl(config),
        actuator=HammerspoonActuator(config),
        config=config,
        logger=logger,
    )

    goal = f"Open Safari and navigate to {url}"
    print(f"\n--- Executing: {goal} ---\n")
    result = await agent.execute(goal)

    print(f"\nSuccess: {result.success}")
    print(f"Steps: {len(result.steps)}")
    for i, sr in enumerate(result.steps):
        status = "+" if sr.success else "x"
        print(f"  {status} Step {i}: {sr.step.action} -- {sr.evidence[:80]}")

    print(f"\nTrace: {logger.trace_file}")
    return 0 if result.success else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
