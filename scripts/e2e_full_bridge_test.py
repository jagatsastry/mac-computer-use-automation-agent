#!/usr/bin/env python3
"""Full end-to-end test of the Hammerspoon bridge actuator.

Tests both synchronous actions AND async vision operations.
Requires Hammerspoon to be running with hs.claude.server on port 27741.
"""

import sys
import time

sys.path.insert(0, "src")

from automation_agent.actuator.bridge_actuator import HammerspoonBridgeActuator


def main():
    bridge = HammerspoonBridgeActuator()
    results = []
    total = 0
    passed = 0

    def test(name, fn):
        nonlocal total, passed
        total += 1
        try:
            ok, detail = fn()
            if ok:
                passed += 1
                print(f"  PASS  {name}: {detail}")
            else:
                print(f"  FAIL  {name}: {detail}")
            results.append((name, ok, detail))
        except Exception as e:
            print(f"  ERROR {name}: {e}")
            results.append((name, False, str(e)))

    print("=" * 70)
    print("Full E2E Bridge Actuator Test (sync + async vision)")
    print("=" * 70)

    # --- Sync action tests ---
    print("\n--- Synchronous Actions ---")

    def test_health():
        available = bridge.is_available()
        return available, "Bridge is available"
    test("1. Health check", test_health)

    def test_get_state():
        state = bridge.get_state()
        app = state.get("app_name", "")
        return bool(app), f"Frontmost app: {app}"
    test("2. Get state", test_get_state)

    def test_activate_calc():
        result = bridge.activate_app("Calculator")
        time.sleep(1)
        state = bridge.get_state()
        return result["success"] and "Calculator" in state.get("app_name", ""), \
            f"success={result['success']}, app={state.get('app_name')}"
    test("3. Activate Calculator", test_activate_calc)

    def test_click():
        result = bridge.click(400, 400)
        return result["success"], f"click(400,400)={result['success']}"
    test("4. Click", test_click)

    def test_press_key():
        # Press Escape to dismiss any dialog
        result = bridge.press_key(["Escape"])
        return result["success"], f"press_key(Escape)={result['success']}"
    test("5. Press key", test_press_key)

    def test_open_url():
        result = bridge.open_url("https://example.com")
        time.sleep(2)
        state = bridge.get_state()
        return result["success"], f"success={result['success']}, app={state.get('app_name')}"
    test("6. Open URL", test_open_url)

    def test_activate_back():
        result = bridge.activate_app("iTerm2")
        time.sleep(0.5)
        state = bridge.get_state()
        return "iTerm" in state.get("app_name", ""), f"app={state.get('app_name')}"
    test("7. Return to iTerm2", test_activate_back)

    # --- Async vision tests ---
    print("\n--- Async Vision Operations (calls Claude API) ---")

    def test_describe():
        print("    (calling Claude API, may take ~5-15s...)")
        desc = bridge.describe_screen()
        has_content = len(desc) > 20
        preview = desc[:100].replace('\n', ' ') + "..." if len(desc) > 100 else desc
        return has_content, f"len={len(desc)}, preview: {preview}"
    test("8. Describe screen", test_describe)

    def test_check_true():
        print("    (calling Claude API...)")
        result = bridge.check_condition("Is iTerm2 or a terminal application visible?")
        return result is True, f"check_condition('iTerm visible')={result}"
    test("9. Check condition (expect True)", test_check_true)

    def test_check_false():
        print("    (calling Claude API...)")
        result = bridge.check_condition("Is Microsoft Excel currently in the foreground?")
        return result is False, f"check_condition('Excel foreground')={result}"
    test("10. Check condition (expect False)", test_check_false)

    # --- Summary ---
    print()
    print("=" * 70)
    print(f"Results: {passed}/{total} passed")
    print("=" * 70)

    failures = [(n, d) for n, ok, d in results if not ok]
    if failures:
        print("\nFailed tests:")
        for name, detail in failures:
            print(f"  - {name}: {detail}")
        return 1

    print("\nAll tests passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
