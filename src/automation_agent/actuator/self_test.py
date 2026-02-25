"""Self-test for the Actuator component.

Performs real integration tests against Hammerspoon.
Only meaningful when Hammerspoon is installed and running.

Usage:
    PYTHONPATH=src python -m automation_agent.actuator.self_test
"""

import json
import sys

from automation_agent.actuator.actuator import HammerspoonActuator


def self_test() -> bool:
    """Run self-test against real Hammerspoon. Returns True if all checks pass."""
    actuator = HammerspoonActuator()
    passed = 0
    failed = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal passed, failed
        status = "PASS" if condition else "FAIL"
        msg = f"  [{status}] {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        if condition:
            passed += 1
        else:
            failed += 1

    print("Actuator Self-Test")
    print("=" * 50)

    # Check 1: hs CLI available
    available = actuator.is_available()
    check("hs CLI available", available, f"path={actuator._hs_path}")
    if not available:
        print("\nCannot proceed: Hammerspoon 'hs' CLI not found.")
        print("Install Hammerspoon and enable CLI: Hammerspoon > Preferences > Enable CLI")
        return False

    # Check 2: get_state returns valid data
    state = actuator.get_state()
    has_keys = all(k in state for k in ("app_name", "app_bundle", "window_title"))
    check("get_state() returns expected keys", has_keys, json.dumps(state, indent=2))

    # Check 3: activate Calculator
    result = actuator.activate_app("Calculator")
    check("activate_app('Calculator')", result.get("success", False), str(result))

    # Check 4: get_state shows Calculator
    import time
    time.sleep(1)  # Wait for app to activate
    state2 = actuator.get_state()
    is_calculator = state2.get("app_name", "") == "Calculator"
    check(
        "get_state() shows Calculator as frontmost",
        is_calculator,
        f"app_name={state2.get('app_name', '')}",
    )

    # Check 5: quit Calculator
    quit_result = actuator.quit_app("Calculator")
    check("quit_app('Calculator')", quit_result.get("success", False), str(quit_result))

    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = self_test()
    sys.exit(0 if success else 1)
