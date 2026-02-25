"""Self-test: load bundled skills, match a sample prompt, and expand."""

from automation_agent.skills.registry import SkillRegistryImpl


def self_test() -> None:
    """Run a quick self-test of the skill registry."""
    registry = SkillRegistryImpl()

    print("=== Loaded skills ===")
    for s in registry.list_skills():
        print(f"  {s['name']:30s}  {s['description']}")

    prompt = "return my blue headphones on Amazon"
    print(f"\n=== Matching prompt: {prompt!r} ===")
    result = registry.match(prompt)
    if result is None:
        print("  No match found.")
    else:
        print(f"  Matched: {result['skill_name']}")
        print(f"  Params:  {result['params']}")
        print(f"\n  Expanded steps:\n{result['expanded_steps']}")

    # Direct expand
    print("\n=== Direct expand: return-amazon-order with item='laptop stand' ===")
    try:
        expanded = registry.expand("return-amazon-order", {"item": "laptop stand"})
        if expanded:
            print(expanded)
    except ValueError as exc:
        print(f"  Error: {exc}")

    print("\n=== Validation ===")
    errors = registry.validate_all()
    if errors:
        for e in errors:
            print(f"  WARNING: {e}")
    else:
        print("  All skills valid.")

    print("\nSelf-test complete.")


if __name__ == "__main__":
    self_test()
