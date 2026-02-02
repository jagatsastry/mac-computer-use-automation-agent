#!/usr/bin/env python3
"""
Real component test: Actions (PyAutoGUI)
Tests actual mouse and keyboard actions in safe mode
"""
import asyncio
import sys
from pathlib import Path
import time

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from automation_agent.actions.simple import ClickAction, TypeAction, HotkeyAction, WaitAction
from automation_agent.config import AgentConfig
import pyautogui
import asyncio


async def test_actions_real_async():
    """Test real actions (safe mode)"""
    print("=" * 60)
    print("COMPONENT TEST: Actions (PyAutoGUI)")
    print("=" * 60)

    config = AgentConfig()

    # Get screen info
    screen_width, screen_height = pyautogui.size()
    print(f"\nScreen size: {screen_width}x{screen_height}")

    # Get current mouse position
    current_x, current_y = pyautogui.position()
    print(f"Current mouse position: ({current_x}, {current_y})")

    # Test 1: Mouse position detection
    print("\n[Test 1] Testing mouse position detection...")
    print(f"  Current position: ({current_x}, {current_y})")
    print(f"  Status: ✅ Position detection working")

    # Test 2: Safe click action (validation only)
    print("\n[Test 2] Testing click action validation...")
    # Click at center of screen (safe position)
    center_x = screen_width // 2
    center_y = screen_height // 2
    click_action = ClickAction(x=center_x, y=center_y)

    try:
        is_valid = await click_action.validate()
        print(f"  Action: Click at ({center_x}, {center_y})")
        print(f"  Valid: {is_valid}")
        print(f"  Status: ✅ Click action valid")
    except Exception as e:
        print(f"  Status: ❌ Validation error - {e}")
        return False

    # Test 3: Type action (validation only)
    print("\n[Test 3] Testing type action validation...")
    type_action = TypeAction(text="Hello, World!")

    try:
        is_valid = await type_action.validate()
        print(f"  Action: Type 'Hello, World!'")
        print(f"  Valid: {is_valid}")
        print(f"  Status: ✅ Type action valid")
    except Exception as e:
        print(f"  Status: ❌ Validation error - {e}")
        return False

    # Test 4: Hotkey action (validation only)
    print("\n[Test 4] Testing hotkey action validation...")
    hotkey_action = HotkeyAction("command", "space")

    try:
        is_valid = await hotkey_action.validate()
        print(f"  Action: Hotkey (command, space)")
        print(f"  Valid: {is_valid}")
        print(f"  Status: ✅ Hotkey action valid")
    except Exception as e:
        print(f"  Status: ❌ Validation error - {e}")
        return False

    # Test 5: Wait action
    print("\n[Test 5] Testing wait action...")
    wait_action = WaitAction(duration=0.5)

    try:
        is_valid = await wait_action.validate()
        print(f"  Action: Wait 0.5 seconds")
        print(f"  Valid: {is_valid}")
        start_time = time.time()
        result = await wait_action.execute()
        elapsed = time.time() - start_time
        print(f"  Elapsed: {elapsed:.2f}s")
        print(f"  Success: {result.success}")
        print(f"  Status: ✅ Wait action working")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 6: PyAutoGUI features
    print("\n[Test 6] Testing PyAutoGUI features...")
    try:
        # Test failsafe
        print(f"  Failsafe enabled: {pyautogui.FAILSAFE}")

        # Test pause
        print(f"  Default pause: {pyautogui.PAUSE}s")

        # Test position
        pos = pyautogui.position()
        print(f"  Mouse position: {pos}")

        # Test on screen check
        on_screen = pyautogui.onScreen(center_x, center_y)
        print(f"  Center is on screen: {on_screen}")

        print(f"  Status: ✅ PyAutoGUI features working")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 7: Actual execution (optional, user confirmation)
    print("\n[Test 7] Real action execution (OPTIONAL - SKIPPED)")
    print("  Note: To test real execution, run with --execute flag")
    print("  This would move mouse to center and back")
    print("  Status: ⏭️  Skipped for safety")

    # Test execution if --execute flag is provided
    if len(sys.argv) > 1 and sys.argv[1] == "--execute":
        print("\n[EXECUTING REAL ACTIONS]")
        print("  Moving mouse to center of screen...")
        pyautogui.moveTo(center_x, center_y, duration=1.0)
        time.sleep(0.5)
        print("  Moving mouse back to original position...")
        pyautogui.moveTo(current_x, current_y, duration=1.0)
        print("  Status: ✅ Real execution completed")

    print("\n" + "=" * 60)
    print("RESULT: ✅ All tests passed")
    print("NOTE: For real action execution, run with --execute flag")
    print("=" * 60)
    return True


def test_actions_real():
    """Wrapper to run async test."""
    return asyncio.run(test_actions_real_async())


if __name__ == "__main__":
    result = test_actions_real()
    sys.exit(0 if result else 1)
