#!/usr/bin/env python3
"""End-to-end test of the Hammerspoon bridge actuator.

Requires Hammerspoon to be running with hs.claude.server on port 27741.
"""

import sys
import time

# Add src to path
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

    print("=" * 60)
    print("E2E Bridge Actuator Test")
    print("=" * 60)

    # 1. Health check
    def test_health():
        available = bridge.is_available()
        return available, "Bridge is available" if available else "Bridge not available"
    test("Health check", test_health)

    # 2. Get state
    def test_get_state():
        state = bridge.get_state()
        app = state.get("app_name", "")
        return bool(app), f"Frontmost app: {app}"
    test("Get state", test_get_state)

    # 3. Activate Calculator
    def test_activate_calc():
        result = bridge.activate_app("Calculator")
        time.sleep(1)
        state = bridge.get_state()
        is_calc = "Calculator" in state.get("app_name", "")
        return result["success"] and is_calc, f"activate_app={result['success']}, frontmost={state.get('app_name')}"
    test("Activate Calculator", test_activate_calc)

    # 4. Click on a key (5 key is roughly at center of Calculator)
    def test_click():
        # Click somewhere safe on Calculator
        result = bridge.click(400, 400)
        return result["success"], f"click(400,400) success={result['success']}"
    test("Click action", test_click)

    # 5. Type text (switch to a text app first)
    def test_type_text():
        # Activate TextEdit
        bridge.activate_app("TextEdit")
        time.sleep(1)
        # Press Cmd+N for new document
        bridge.press_key(["cmd", "n"])
        time.sleep(1)
        result = bridge.type_text("Hello from bridge!")
        time.sleep(0.5)
        return result["success"], f"type_text success={result['success']}"
    test("Type text in TextEdit", test_type_text)

    # 6. Press key
    def test_press_key():
        result = bridge.press_key(["cmd", "a"])  # Select all
        return result["success"], f"press_key(cmd+a) success={result['success']}"
    test("Press key (Cmd+A)", test_press_key)

    # 7. Open URL
    def test_open_url():
        result = bridge.open_url("https://example.com")
        time.sleep(2)
        state = bridge.get_state()
        return result["success"], f"open_url success={result['success']}, frontmost={state.get('app_name')}"
    test("Open URL", test_open_url)

    # 8. Get state after URL open
    def test_state_after_url():
        state = bridge.get_state()
        app = state.get("app_name", "")
        # Should be a browser
        is_browser = any(b in app for b in ["Safari", "Chrome", "Firefox", "Arc"])
        return is_browser, f"Frontmost app after open_url: {app}"
    test("Browser is frontmost after open_url", test_state_after_url)

    # 9. Quit TextEdit (clean up)
    def test_quit_textedit():
        result = bridge.quit_app("TextEdit")
        return result["success"], f"quit_app(TextEdit) success={result['success']}"
    test("Quit TextEdit", test_quit_textedit)

    # 10. Activate back to terminal
    def test_return_to_terminal():
        result = bridge.activate_app("iTerm2")
        time.sleep(0.5)
        state = bridge.get_state()
        return "iTerm" in state.get("app_name", ""), f"Returned to {state.get('app_name')}"
    test("Return to iTerm2", test_return_to_terminal)

    print()
    print("=" * 60)
    print(f"Results: {passed}/{total} passed")
    print("=" * 60)

    # Summary of failures
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
