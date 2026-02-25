"""CLI for the Actuator component.

Usage:
    python -m automation_agent.actuator status
    python -m automation_agent.actuator click 500 300
    python -m automation_agent.actuator type "hello world"
    python -m automation_agent.actuator key cmd+c
    python -m automation_agent.actuator activate "Safari"
    python -m automation_agent.actuator open-url "https://example.com"
    python -m automation_agent.actuator quit "Safari"
    python -m automation_agent.actuator state
"""

import json
import sys

from automation_agent.actuator.actuator import HammerspoonActuator


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__.strip())
        sys.exit(1)

    actuator = HammerspoonActuator()
    command = args[0].lower()

    if command == "status":
        available = actuator.is_available()
        print(f"Hammerspoon hs CLI: {'available' if available else 'NOT FOUND'}")
        if available:
            print(f"  Path: {actuator._hs_path}")
        sys.exit(0 if available else 1)

    if not actuator.is_available():
        print("ERROR: Hammerspoon 'hs' CLI not found. Install Hammerspoon and enable the CLI.")
        sys.exit(1)

    if command == "click":
        if len(args) < 3:
            print("Usage: click X Y")
            sys.exit(1)
        x, y = int(args[1]), int(args[2])
        result = actuator.click(x, y)
        print(json.dumps(result, indent=2))

    elif command == "type":
        if len(args) < 2:
            print("Usage: type \"text\"")
            sys.exit(1)
        text = args[1]
        result = actuator.type_text(text)
        print(json.dumps(result, indent=2))

    elif command == "key":
        if len(args) < 2:
            print("Usage: key cmd+c")
            sys.exit(1)
        keys = args[1].split("+")
        result = actuator.press_key(keys)
        print(json.dumps(result, indent=2))

    elif command == "activate":
        if len(args) < 2:
            print("Usage: activate \"App Name\"")
            sys.exit(1)
        result = actuator.activate_app(args[1])
        print(json.dumps(result, indent=2))

    elif command == "open-url":
        if len(args) < 2:
            print("Usage: open-url \"https://example.com\"")
            sys.exit(1)
        result = actuator.open_url(args[1])
        print(json.dumps(result, indent=2))

    elif command == "quit":
        if len(args) < 2:
            print("Usage: quit \"App Name\"")
            sys.exit(1)
        result = actuator.quit_app(args[1])
        print(json.dumps(result, indent=2))

    elif command == "state":
        result = actuator.get_state()
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {command}")
        print(__doc__.strip())
        sys.exit(1)


if __name__ == "__main__":
    main()
