"""CLI for the vision coordinator component.

Usage:
    python -m automation_agent.vision describe
    python -m automation_agent.vision find "the Safari icon in the dock"
    python -m automation_agent.vision verify "Safari browser is open"
    python -m automation_agent.vision capture output.png
"""

import argparse
import asyncio
import sys

from automation_agent.config import AgentConfig
from automation_agent.vision.capture import ScreenCapture
from automation_agent.vision.coordinator import ScreenCoordinatorImpl


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vision Coordinator CLI",
        prog="python -m automation_agent.vision",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # describe
    subparsers.add_parser("describe", help="Describe the current screen state")

    # find
    find_parser = subparsers.add_parser("find", help="Find a UI element on screen")
    find_parser.add_argument("element", help="Description of the element to find")

    # verify
    verify_parser = subparsers.add_parser(
        "verify", help="Verify a visual condition on screen"
    )
    verify_parser.add_argument("condition", help="Condition to verify")

    # capture
    capture_parser = subparsers.add_parser(
        "capture", help="Capture a screenshot and save to file"
    )
    capture_parser.add_argument("output", help="Output file path")

    args = parser.parse_args()

    if args.command == "capture":
        config = AgentConfig()
        capture = ScreenCapture(config.screenshot_resolution)
        path = capture.save(args.output)
        print(f"Screenshot saved to: {path}")
        return

    # Commands requiring the coordinator
    config = AgentConfig()
    coordinator = ScreenCoordinatorImpl(config)

    if args.command == "describe":
        result = asyncio.run(coordinator.describe_screen())
        print(result)

    elif args.command == "find":
        result = asyncio.run(coordinator.find_element(args.element))
        if result is None:
            print("NOT_FOUND")
            sys.exit(1)
        else:
            print(f"Found at ({result['x']}, {result['y']})")

    elif args.command == "verify":
        result = asyncio.run(coordinator.verify_condition(args.condition))
        if result is True:
            print("YES")
            sys.exit(0)
        elif result is None:
            print("UNCLEAR")
            sys.exit(2)
        else:
            print("NO")
            sys.exit(1)


if __name__ == "__main__":
    main()
