"""Self-test for the orchestrator: execute 'Open Calculator' with real components.

Run:
    PYTHONPATH=src python -m automation_agent.orchestrator.self_test
"""

import asyncio
import sys

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger


async def self_test():
    """Run a real self-test: Open Calculator."""
    print("=== Orchestrator Self-Test ===")
    print("Goal: Open Calculator")
    print()

    try:
        from automation_agent.planner.planner import ActionPlannerImpl
        from automation_agent.skills.registry import SkillRegistryImpl
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl
        from automation_agent.actuator.hammerspoon import HammerspoonActuator
        from automation_agent.orchestrator.agent import AutomationAgent

        config = AgentConfig(_env_file=None)
        logger = EventLogger(config.event_log_dir)

        planner = ActionPlannerImpl(config)
        skill_registry = SkillRegistryImpl(config.skill_library_path)
        coordinator = ScreenCoordinatorImpl(config)
        actuator = HammerspoonActuator(cli_path=config.hammerspoon_cli_path)

        # Check actuator availability
        if not actuator.is_available():
            print("[SKIP] Hammerspoon not available. Install and configure it first.")
            print("       brew install hammerspoon")
            print("       Enable the IPC module in Hammerspoon config.")
            return False

        agent = AutomationAgent(
            planner=planner,
            skill_registry=skill_registry,
            coordinator=coordinator,
            actuator=actuator,
            config=config,
            logger=logger,
        )

        print(f"Run ID: {logger.run_id}")
        result = await agent.execute("Open Calculator")

        print()
        print(f"Success: {result.success}")
        print(f"Message: {result.message}")
        print(f"Iterations: {result.iterations}")
        print(f"Duration: {result.total_duration_ms}ms")
        print(f"Steps executed: {len(result.steps)}")

        for i, sr in enumerate(result.steps):
            status = "PASS" if sr.success else "FAIL"
            print(f"  Step {i}: [{status}] {sr.step.action} -- {sr.evidence}")

        print()
        print(f"Log directory: {logger.run_dir}")

        if result.success:
            print("[PASS] Self-test succeeded!")
        else:
            print(f"[FAIL] Self-test failed: {result.error or result.message}")

        return result.success

    except ImportError as e:
        print(f"[SKIP] Missing dependency: {e}")
        print("       Install all dependencies: pip install -e '.[all]'")
        return False
    except Exception as e:
        print(f"[FAIL] Self-test error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Entry point."""
    success = asyncio.run(self_test())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
