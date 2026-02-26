#!/usr/bin/env python3
"""E2E test: evaluate a math expression using the Calculator macOS app.

Usage:
    python scripts/run_calculator_test.py "188 * 234 / 50"
    python scripts/run_calculator_test.py "3 * 18"
    python scripts/run_calculator_test.py "999 + 1"

Runs entirely through the automation agent bridge — no direct OS calls.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation_agent.actuator.bridge_actuator import HammerspoonBridgeActuator


def tokenize(expr: str) -> list[tuple[str, list[str]]]:
    """Convert a math expression into a list of (label, keys) for press_key."""
    tokens = []
    for ch in expr.replace(" ", ""):
        if ch.isdigit():
            tokens.append((ch, [ch]))
        elif ch == "+":
            tokens.append(("+", ["shift", "="]))
        elif ch == "-":
            tokens.append(("-", ["-"]))
        elif ch == "*":
            tokens.append(("*", ["shift", "8"]))
        elif ch == "/":
            tokens.append(("/", ["/"]))
        elif ch == ".":
            tokens.append((".", ["."]))
        elif ch == "=":
            pass  # we add = at the end
        else:
            print(f"WARNING: unknown character '{ch}', skipping")
    return tokens


def evaluate(expr: str) -> float:
    """Compute expected result (for verification)."""
    # Safe eval for simple arithmetic
    allowed = set("0123456789.+-*/ ()")
    clean = expr.replace(" ", "")
    if not all(c in allowed for c in clean):
        raise ValueError(f"Expression contains disallowed characters: {expr}")
    return eval(clean)


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} \"<math expression>\"")
        print(f"Example: {sys.argv[0]} \"188 * 234 / 50\"")
        return 1

    expr = sys.argv[1]
    expected = evaluate(expr)

    bridge = HammerspoonBridgeActuator()

    print("=" * 70)
    print(f"E2E Calculator Test: {expr} = {expected}")
    print("=" * 70)

    # 1. Check bridge
    if not bridge.is_available():
        print("ERROR: Hammerspoon bridge not available on port 27741")
        return 1
    print(f"Bridge: available, accessibility={bridge.has_accessibility()}")

    # 2. Quit Calculator if running (clean state)
    print("\n--- Setup: Clean state ---")
    bridge.quit_app("Calculator")
    time.sleep(1)

    # 3. Launch Calculator
    print("\n--- Step 1: Launch Calculator ---")
    r = bridge.activate_app("Calculator")
    if not r["success"]:
        print(f"  FAIL: {r}")
        return 1
    time.sleep(1.5)

    state = bridge.get_state()
    app = state.get("app_name", "")
    if "Calculator" not in app:
        print(f"  FAIL: Expected Calculator as frontmost, got: {app}")
        return 1
    print(f"  Calculator is frontmost")

    # 4. Clear
    print("\n--- Step 2: Clear ---")
    bridge.press_key(["Escape"])
    time.sleep(0.3)

    # 5. Enter expression
    print(f"\n--- Step 3: Enter {expr} ---")
    keystrokes = tokenize(expr)
    for label, keys in keystrokes:
        r = bridge.press_key(keys)
        if not r["success"]:
            print(f"  FAIL: press_key '{label}' failed: {r}")
            return 1
        time.sleep(0.2)

    # Press = (Return)
    bridge.press_key(["return"])
    time.sleep(1)
    print(f"  Entered {len(keystrokes)} keystrokes + =")

    # 6. Verify state
    print("\n--- Step 4: Verify ---")
    state = bridge.get_state()
    print(f"  Frontmost: {state.get('app_name', '?')}")

    # 7. Vision verification
    # Format expected for display (Calculator may show integers without decimal)
    if expected == int(expected):
        display_expected = str(int(expected))
    else:
        display_expected = str(expected)

    # Calculator may format with commas (e.g., 43,992)
    condition = (
        f"The Calculator display shows the number {display_expected} "
        f"(possibly formatted with commas or as {expected})"
    )
    print(f"  Checking for: {display_expected}")
    has_result = bridge.check_condition(condition)
    print(f"  Vision check: {has_result}")

    if has_result:
        print("\n" + "=" * 70)
        print(f"PASS: {expr} = {display_expected}")
        print("=" * 70)
        return 0

    # Fallback: describe screen for debugging
    print("\n  Getting screen description...")
    desc = bridge.describe_screen()
    print(f"  Screen: {desc[:400]}")
    print("\n" + "=" * 70)
    print(f"FAIL: Expected {display_expected}, not confirmed on screen")
    print("=" * 70)
    return 1


if __name__ == "__main__":
    sys.exit(main())
