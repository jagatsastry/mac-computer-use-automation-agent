#!/usr/bin/env python3
"""Expand a skill template and print the plan without executing.

Usage: PYTHONPATH=src python3 scripts/run_skill_dry_run.py "return my blue headphones on Amazon"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation_agent.skills import SkillRegistryImpl


def main():
    prompt = sys.argv[1] if len(sys.argv) > 1 else "return my blue headphones on Amazon"
    registry = SkillRegistryImpl()

    print(f"Prompt: {prompt}\n")

    match = registry.match(prompt)
    if match:
        print(f"Matched skill: {match['skill_name']}")
        print(f"Parameters: {match.get('params', {})}")
        print(f"\n--- Expanded Steps ---\n")
        print(match.get("expanded_steps", "(no expansion)"))
    else:
        print("No matching skill found.")
        print("\nAvailable skills:")
        for skill in registry.list_skills():
            print(f"  - {skill['name']}: {skill['description']}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
