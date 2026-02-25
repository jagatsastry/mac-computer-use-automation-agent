#!/usr/bin/env python3
"""E2E test: calculate 3 * 18 using the Calculator macOS app.

Tests the full pipeline:
  1. Bridge actuator (activate app, keyboard input via osascript fallback)
  2. Vision verification (Hammerspoon → Claude API screenshot analysis)
  3. Closed-loop verification (check expected result on screen)

Requires Hammerspoon running with hs.claude.server on port 27741.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation_agent.actuator.bridge_actuator import HammerspoonBridgeActuator


def main():
    bridge = HammerspoonBridgeActuator()

    print("=" * 70)
    print("E2E Calculator Test: 3 * 18 = 54")
    print("=" * 70)

    # 1. Check bridge
    if not bridge.is_available():
        print("ERROR: Hammerspoon bridge not available on port 27741")
        return 1
    print(f"Bridge: available, accessibility={bridge.has_accessibility()}")

    # 2. Activate Calculator
    print("\n--- Step 1: Activate Calculator ---")
    r = bridge.activate_app("Calculator")
    if not r["success"]:
        print(f"ERROR: Failed to activate Calculator: {r}")
        return 1
    time.sleep(1.5)

    state = bridge.get_state()
    app = state.get("app_name", "")
    if "Calculator" not in app:
        print(f"ERROR: Expected Calculator as frontmost, got: {app}")
        return 1
    print(f"  PASS: Calculator is frontmost")

    # 3. Clear Calculator
    print("\n--- Step 2: Clear Calculator ---")
    bridge.press_key(["Escape"])
    time.sleep(0.3)

    # 4. Enter calculation: 3 * 18 =
    print("\n--- Step 3: Enter 3 * 18 = ---")
    keystrokes = [
        ("3", ["3"]),
        ("*", ["shift", "8"]),  # shift+8 → * (osascript fallback handles this)
        ("1", ["1"]),
        ("8", ["8"]),
        ("=", ["return"]),
    ]

    for label, keys in keystrokes:
        r = bridge.press_key(keys)
        if not r["success"]:
            print(f"  FAIL: press_key {label} failed: {r}")
            return 1
        print(f"  Pressed {label}: OK")
        time.sleep(0.3)

    time.sleep(1)

    # 5. Verify frontmost app
    print("\n--- Step 4: Verify State ---")
    state = bridge.get_state()
    print(f"  Frontmost: {state.get('app_name', '?')}")

    # 6. Vision verification
    print("\n--- Step 5: Vision Verification ---")
    print("  Calling Claude API to check screen...")
    has_54 = bridge.check_condition(
        "The number 54 is displayed on the Calculator screen"
    )
    print(f"  Vision check (54 visible): {has_54}")

    if has_54:
        print("\n" + "=" * 70)
        print("RESULT: PASS")
        print("Calculator shows 54 (3 * 18)")
        print("Pipeline: Python → osascript fallback → Calculator → Claude vision")
        print("=" * 70)
        return 0
    else:
        print("\n  Getting screen description for debugging...")
        desc = bridge.describe_screen()
        print(f"  Screen: {desc[:300]}")
        print("\n" + "=" * 70)
        print("RESULT: FAIL - 54 not confirmed on screen")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
