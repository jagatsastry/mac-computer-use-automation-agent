#!/usr/bin/env python3
"""Calculator 2+2=4 test with vision verification.

Usage: PYTHONPATH=src python3 scripts/run_calculator_test.py
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
    config = AgentConfig()
    log_dir = Path("logs/runs")
    logger = EventLogger(log_dir)

    print(f"Run ID: {logger.run_id}")
    print(f"Log dir: {logger.run_dir}")

    agent = AutomationAgent(
        planner=ActionPlannerImpl(config),
        skill_registry=SkillRegistryImpl(),
        coordinator=ScreenCoordinatorImpl(config),
        actuator=HammerspoonActuator(config),
        config=config,
        logger=logger,
    )

    print("\n--- Executing: Open Calculator and compute 2 + 2 ---\n")
    result = await agent.execute("Open Calculator and compute 2 + 2")

    print(f"\nSuccess: {result.success}")
    print(f"Message: {result.message}")
    print(f"Steps: {len(result.steps)}")
    print(f"Duration: {result.total_duration_ms}ms")

    for i, sr in enumerate(result.steps):
        status = "+" if sr.success else "x"
        print(f"  {status} Step {i}: {sr.step.action} -- {sr.evidence[:80]}")

    print(f"\nTrace: {logger.trace_file}")
    print(f"Events: {logger.events_file}")
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
