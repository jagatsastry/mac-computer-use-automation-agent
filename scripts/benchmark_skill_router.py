#!/usr/bin/env python3
"""Benchmark the SkillRouter across local (Qwen) and Anthropic (Claude) backends.

Measures speed and accuracy by:
1. Running 10 test prompts through both backends
2. Using Claude Opus as a judge to score accuracy
3. Printing a comparison table

Usage:
    .venv/bin/python scripts/benchmark_skill_router.py
    .venv/bin/python scripts/benchmark_skill_router.py --backend local
    .venv/bin/python scripts/benchmark_skill_router.py --backend anthropic
    .venv/bin/python scripts/benchmark_skill_router.py --backend both
"""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation_agent.config import AgentConfig, ModelProvider
from automation_agent.skills.registry import SkillRegistryImpl
from automation_agent.skills.router import SkillRouter


# ---------------------------------------------------------------------------
# Test cases with ground truth
# ---------------------------------------------------------------------------

TEST_CASES: List[Dict[str, Any]] = [
    {
        "prompt": "Return the blue headphones I bought on Amazon",
        "expected_skill": "return-amazon-order",
        "expected_params": {"item": "blue headphones"},
    },
    {
        "prompt": "Search for the cheapest wireless mouse on Amazon",
        "expected_skill": "amazon-search",
        "expected_params": {"product": "wireless mouse"},
    },
    {
        "prompt": "Open Safari and go to news.ycombinator.com",
        "expected_skill": "open-app-and-navigate",
        "expected_params": {"app_name": "Safari", "destination": "news.ycombinator.com"},
    },
    {
        "prompt": "Send a text message to Mom saying I'll be late",
        "expected_skill": "send-imessage",
        "expected_params": {"recipient": "Mom", "message": "I'll be late"},
    },
    {
        "prompt": "What's the weather today",
        "expected_skill": None,
        "expected_params": {},
    },
    {
        "prompt": "Google how to make sourdough bread",
        "expected_skill": "google-search",
        "expected_params": {"query": "how to make sourdough bread"},
    },
    {
        "prompt": "I need to send back the laptop stand I ordered",
        "expected_skill": "return-amazon-order",
        "expected_params": {"item": "laptop stand"},
    },
    {
        "prompt": "Find me the best deals on USB-C cables on Amazon",
        "expected_skill": "amazon-search",
        "expected_params": {"product": "USB-C cables"},
    },
    {
        "prompt": "Launch Finder and navigate to Downloads",
        "expected_skill": "open-app-and-navigate",
        "expected_params": {"app_name": "Finder", "destination": "Downloads"},
    },
    {
        "prompt": "Text John saying the meeting is moved to 3pm",
        "expected_skill": "send-imessage",
        "expected_params": {"recipient": "John", "message": "the meeting is moved to 3pm"},
    },
]


@dataclass
class BenchmarkResult:
    prompt: str
    backend: str
    expected_skill: Optional[str]
    expected_params: Dict[str, str]
    actual_skill: Optional[str]
    actual_params: Dict[str, str]
    time_s: float
    skill_correct: bool = False
    judge_score: Optional[int] = None
    judge_notes: str = ""


# ---------------------------------------------------------------------------
# Judging via Opus
# ---------------------------------------------------------------------------

async def judge_with_opus(result: BenchmarkResult, config: AgentConfig) -> BenchmarkResult:
    """Use Claude Opus to judge accuracy of routing result."""
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

        prompt = f"""You are an accuracy judge for a skill routing system.

Given the user prompt, expected output, and actual output, score the accuracy from 0-10.

User prompt: "{result.prompt}"

Expected:
- Skill: {result.expected_skill}
- Params: {json.dumps(result.expected_params)}

Actual:
- Skill: {result.actual_skill}
- Params: {json.dumps(result.actual_params)}

Scoring:
- 10/10: Skill correct AND all params semantically equivalent
- 7-9: Skill correct, params mostly right (minor wording differences OK)
- 4-6: Skill correct but params significantly wrong
- 1-3: Wrong skill
- 0: Completely wrong

Respond with ONLY JSON: {{"score": <0-10>, "notes": "<brief explanation>"}}"""

        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        response = message.content[0].text.strip()
        # Parse JSON
        if response.startswith("```"):
            lines = response.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            response = "\n".join(lines).strip()
        data = json.loads(response)
        result.judge_score = data.get("score", 0)
        result.judge_notes = data.get("notes", "")
    except Exception as e:
        result.judge_notes = f"Judge error: {e}"
    return result


# ---------------------------------------------------------------------------
# Run benchmark
# ---------------------------------------------------------------------------

async def run_benchmark(
    backend: str,
    config: AgentConfig,
    registry: SkillRegistryImpl,
    skills: dict,
) -> List[BenchmarkResult]:
    """Run all test cases through one backend."""
    # Override provider
    if backend == "local":
        config.model_provider = ModelProvider.LOCAL
    else:
        config.model_provider = ModelProvider.ANTHROPIC

    router = SkillRouter(config, skills)
    results: List[BenchmarkResult] = []

    for tc in TEST_CASES:
        prompt = tc["prompt"]
        expected_skill = tc["expected_skill"]
        expected_params = tc["expected_params"]

        start = time.monotonic()
        try:
            route_result = await router.route(prompt)
        except Exception as e:
            route_result = None
            print(f"  ERROR for '{prompt[:40]}...': {e}")
        elapsed = time.monotonic() - start

        actual_skill = route_result["skill_name"] if route_result else None
        actual_params = route_result.get("params", {}) if route_result else {}

        br = BenchmarkResult(
            prompt=prompt,
            backend=backend,
            expected_skill=expected_skill,
            expected_params=expected_params,
            actual_skill=actual_skill,
            actual_params=actual_params,
            time_s=round(elapsed, 2),
            skill_correct=(actual_skill == expected_skill),
        )
        results.append(br)
        status = "OK" if br.skill_correct else "WRONG"
        print(f"  [{status}] {prompt[:50]:50s} -> {actual_skill or 'None':25s} ({elapsed:.2f}s)")

    return results


def print_table(results: List[BenchmarkResult]) -> None:
    """Print a formatted comparison table."""
    print("\n" + "=" * 110)
    print(f"{'Prompt':<30s} | {'Backend':<10s} | {'Skill Match':^12s} | {'Params':^20s} | {'Time':>6s} | {'Judge':>6s}")
    print("-" * 110)
    for r in results:
        prompt_short = r.prompt[:28] + ".." if len(r.prompt) > 30 else r.prompt
        skill_ok = "OK" if r.skill_correct else "WRONG"
        params_str = json.dumps(r.actual_params)[:18] if r.actual_params else "{}"
        judge = f"{r.judge_score}/10" if r.judge_score is not None else "N/A"
        print(f"{prompt_short:<30s} | {r.backend:<10s} | {skill_ok:^12s} | {params_str:<20s} | {r.time_s:5.2f}s | {judge:>6s}")
    print("=" * 110)


def print_summary(results: List[BenchmarkResult]) -> None:
    """Print summary stats per backend."""
    backends = set(r.backend for r in results)
    print("\n--- Summary ---")
    for backend in sorted(backends):
        br = [r for r in results if r.backend == backend]
        total = len(br)
        skill_correct = sum(1 for r in br if r.skill_correct)
        avg_time = sum(r.time_s for r in br) / total if total else 0
        judged = [r for r in br if r.judge_score is not None]
        avg_judge = sum(r.judge_score for r in judged) / len(judged) if judged else 0
        print(f"  {backend:10s}: skill accuracy={skill_correct}/{total} "
              f"({100 * skill_correct / total:.0f}%), "
              f"avg latency={avg_time:.2f}s, "
              f"avg judge={avg_judge:.1f}/10")


async def main():
    parser = argparse.ArgumentParser(description="Benchmark SkillRouter")
    parser.add_argument(
        "--backend",
        choices=["local", "anthropic", "both"],
        default="both",
        help="Which backend(s) to benchmark",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Skip Opus judging (faster, no API cost for judging)",
    )
    args = parser.parse_args()

    config = AgentConfig()
    registry = SkillRegistryImpl()
    skills = registry._skills

    if not skills:
        print("ERROR: No skills loaded. Check skill library path.")
        sys.exit(1)

    print(f"Loaded {len(skills)} skills: {', '.join(skills.keys())}")
    print()

    all_results: List[BenchmarkResult] = []

    backends = []
    if args.backend in ("local", "both"):
        backends.append("local")
    if args.backend in ("anthropic", "both"):
        backends.append("anthropic")

    for backend in backends:
        print(f"\n=== Benchmarking: {backend.upper()} ===")
        results = await run_benchmark(backend, config, registry, skills)
        all_results.extend(results)

    # Judge all results with Opus
    if not args.no_judge and config.anthropic_api_key:
        print("\n--- Judging with Claude ---")
        for i, r in enumerate(all_results):
            await judge_with_opus(r, config)
            print(f"  Judged {i + 1}/{len(all_results)}: {r.judge_score}/10 - {r.judge_notes}")

    print_table(all_results)
    print_summary(all_results)


if __name__ == "__main__":
    asyncio.run(main())
