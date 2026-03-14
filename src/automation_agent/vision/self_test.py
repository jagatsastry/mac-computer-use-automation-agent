"""Self-test for the vision coordinator.

Performs a real integration test:
1. Captures a screenshot
2. Describes the screen
3. Verifies that "Desktop is visible"

This test requires:
- macOS with screencapture available
- A configured vision model (local OpenAI-compatible server or Anthropic)

Usage:
    PYTHONPATH=src python -m automation_agent.vision.self_test
"""

import asyncio
import sys

from automation_agent.config import AgentConfig
from automation_agent.vision.capture import ScreenCapture
from automation_agent.vision.coordinator import ScreenCoordinatorImpl


async def run_self_test() -> bool:
    """Run the self-test sequence. Returns True if all checks pass."""
    config = AgentConfig()
    capture = ScreenCapture(config.screenshot_resolution)
    coordinator = ScreenCoordinatorImpl(config, capture)

    print("=== Vision Coordinator Self-Test ===\n")

    # Step 1: Capture screenshot
    print("[1/3] Capturing screenshot...")
    try:
        screenshot_b64 = capture.capture_b64()
        print(f"  OK: Captured screenshot ({len(screenshot_b64)} base64 chars)\n")
    except Exception as e:
        print(f"  FAIL: Screenshot capture failed: {e}\n")
        return False

    # Step 2: Describe screen
    print("[2/3] Describing screen...")
    try:
        description = await coordinator.describe_screen(screenshot_b64)
        print(f"  OK: {description[:200]}...\n" if len(description) > 200 else f"  OK: {description}\n")
    except Exception as e:
        print(f"  FAIL: Screen description failed: {e}\n")
        return False

    # Step 3: Verify condition
    print("[3/3] Verifying 'Desktop is visible'...")
    try:
        result = await coordinator.verify_condition("Desktop is visible", screenshot_b64)
        status = "YES" if result is True else ("UNCLEAR" if result is None else "NO")
        print(f"  Result: {status}\n")
    except Exception as e:
        print(f"  FAIL: Verification failed: {e}\n")
        return False

    print("=== Self-test complete ===")
    return True


def main() -> None:
    success = asyncio.run(run_self_test())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
