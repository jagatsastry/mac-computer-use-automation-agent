"""CLI for the skill registry.

Usage:
    python -m automation_agent.skills list
    python -m automation_agent.skills match "return my blue headphones on Amazon"
    python -m automation_agent.skills expand return-amazon-order --param item="blue headphones"
    python -m automation_agent.skills validate
"""

import argparse
import sys

from automation_agent.skills.registry import SkillRegistryImpl


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(
        prog="automation_agent.skills",
        description="Skill registry CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    sub.add_parser("list", help="List all loaded skills")

    # match
    p_match = sub.add_parser("match", help="Find a matching skill for a prompt")
    p_match.add_argument("prompt", help="User prompt to match")

    # expand
    p_expand = sub.add_parser("expand", help="Expand a skill with parameters")
    p_expand.add_argument("skill_name", help="Skill name")
    p_expand.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Parameter in key=value format (repeatable)",
    )

    # validate
    sub.add_parser("validate", help="Validate all loaded skills")

    args = parser.parse_args(argv)
    registry = SkillRegistryImpl()

    if args.command == "list":
        skills = registry.list_skills()
        if not skills:
            print("No skills loaded.")
            return 0
        for s in skills:
            print(f"  {s['name']:30s}  {s['description']}")
        return 0

    if args.command == "match":
        result = registry.match(args.prompt)
        if result is None:
            print("No matching skill found.")
            return 1
        print(f"Matched skill: {result['skill_name']}")
        print(f"Params: {result['params']}")
        print(f"\nExpanded steps:\n{result['expanded_steps']}")
        return 0

    if args.command == "expand":
        params = {}
        for p in args.param:
            if "=" not in p:
                print(f"Invalid param format: {p!r}  (expected KEY=VALUE)")
                return 2
            k, v = p.split("=", 1)
            params[k] = v
        try:
            text = registry.expand(args.skill_name, params)
        except ValueError as exc:
            print(f"Error: {exc}")
            return 2
        if text is None:
            print(f"Skill not found: {args.skill_name}")
            return 1
        print(text)
        return 0

    if args.command == "validate":
        errors = registry.validate_all()
        if not errors:
            print("All skills valid.")
            return 0
        print(f"{len(errors)} validation error(s):")
        for e in errors:
            print(f"  - {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
