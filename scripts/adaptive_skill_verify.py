#!/usr/bin/env python3
"""
Adaptive Skill Customer Test Verification Script.

Analyzes run logs from the 30-run customer test to verify properties (a)-(d).
Reads /tmp/adaptive_test_runs.csv and logs/runs/*/events.jsonl.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJ = Path("/Users/jagatp/workspace/macos-automation-agent")
RUNS_DIR = PROJ / "logs" / "runs"
CSV_FILE = Path("/tmp/adaptive_test_runs.csv")
LEARNING_DIR = PROJ / "logs" / "skill_learning"
SKILL_LIB = PROJ / "src" / "automation_agent" / "skills" / "library"


def load_events(run_id: str) -> list[dict]:
    ef = RUNS_DIR / run_id / "events.jsonl"
    if not ef.exists():
        return []
    events = []
    for line in ef.read_text().splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def load_runs() -> list[tuple[str, int, str]]:
    if not CSV_FILE.exists():
        print(f"ERROR: {CSV_FILE} not found. Run the test runner first.")
        sys.exit(1)
    runs = []
    with open(CSV_FILE) as f:
        for row in csv.reader(f):
            if len(row) >= 3:
                runs.append((row[0], int(row[1]), row[2]))
    return runs


def verify_property_a(runs: list[tuple[str, int, str]]):
    """Property (a): Existing skills are matched and used."""
    print("\n" + "=" * 60)
    print("PROPERTY (a): Existing Skill Reuse")
    print("=" * 60)

    expected = {
        "A1": "amazon-search",
        "A2": "buy-on-target",
        "C1": "amazon-search",
        "C2": "buy-on-target",
    }

    results = defaultdict(list)
    for pid, rnd, run_id in runs:
        if pid not in expected:
            continue
        events = load_events(run_id)
        matched = None
        for e in events:
            if e.get("event_type") == "skill_match" and e.get("data", {}).get("skill_name"):
                matched = e["data"]["skill_name"]
                break
        results[pid].append((rnd, matched))

    all_pass = True
    for pid in sorted(results):
        print(f"\n  {pid} (expected: {expected[pid]}):")
        for rnd, matched in sorted(results[pid]):
            status = "PASS" if matched == expected[pid] else "FAIL"
            if status == "FAIL":
                all_pass = False
            print(f"    Round {rnd}: {matched or 'NO MATCH'} [{status}]")

    print(f"\n  VERDICT: {'PASS' if all_pass else 'FAIL'}")
    return all_pass


def verify_property_b(runs: list[tuple[str, int, str]]):
    """Property (b): Observation accumulation + create_sibling limitation."""
    print("\n" + "=" * 60)
    print("PROPERTY (b): Observation Accumulation / create_sibling")
    print("=" * 60)

    # B1: should match return-walmart-order
    b1_runs = [(rnd, rid) for pid, rnd, rid in runs if pid == "B1"]
    b2_runs = [(rnd, rid) for pid, rnd, rid in runs if pid == "B2"]

    print("\n  B1 (Return shoes on Walmart):")
    b1_matched = False
    for rnd, run_id in sorted(b1_runs):
        events = load_events(run_id)
        matched = None
        for e in events:
            if e.get("event_type") == "skill_match" and e.get("data", {}).get("skill_name"):
                matched = e["data"]["skill_name"]
                break
            elif e.get("event_type") == "skill_no_match":
                matched = "NO_MATCH"
                break
        if matched and matched != "NO_MATCH":
            b1_matched = True
        print(f"    Round {rnd}: {matched or 'unknown'}")

    # Check observations
    obs_file = LEARNING_DIR / "return-walmart-order.jsonl"
    obs_count = 0
    if obs_file.exists():
        obs_count = sum(1 for l in obs_file.read_text().splitlines() if l.strip())
    print(f"    Observations: {obs_count}")

    print(f"\n  B2 (Cheapest laptop on Best Buy):")
    b2_no_match_count = 0
    for rnd, run_id in sorted(b2_runs):
        events = load_events(run_id)
        matched = None
        for e in events:
            if e.get("event_type") == "skill_match" and e.get("data", {}).get("skill_name"):
                matched = e["data"]["skill_name"]
                break
            elif e.get("event_type") == "skill_no_match":
                matched = "NO_MATCH"
                b2_no_match_count += 1
                break
        print(f"    Round {rnd}: {matched or 'unknown'}")

    # Check promotion history for create_sibling
    history_file = LEARNING_DIR / "promotions" / "history.jsonl"
    sibling_count = 0
    if history_file.exists():
        for line in history_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                if d.get("promotion_type") == "create_sibling":
                    sibling_count += 1
            except json.JSONDecodeError:
                pass
    print(f"\n  create_sibling promotions: {sibling_count}")
    print(f"  B1 matched: {b1_matched}")
    print(f"  B2 no-match (documented limitation): {b2_no_match_count}/{len(b2_runs)}")

    # PASS criteria: B1 matches and accumulates observations; B2 documents limitation
    b1_pass = b1_matched and obs_count >= 1
    b2_pass = b2_no_match_count > 0 or len(b2_runs) == 0
    verdict = "PASS" if (b1_pass and b2_pass) else "PARTIAL" if (b1_pass or b2_pass) else "FAIL"
    print(f"\n  VERDICT: {verdict}")
    return verdict == "PASS"


def verify_property_c(runs: list[tuple[str, int, str]]):
    """Property (c): Replans cause skill updates."""
    print("\n" + "=" * 60)
    print("PROPERTY (c): Replan-Driven Skill Updates")
    print("=" * 60)

    c_prompts = {"C1": "amazon-search", "C2": "buy-on-target"}
    any_promotion = False

    for pid, skill_name in c_prompts.items():
        c_runs = [(rnd, rid) for p, rnd, rid in runs if p == pid]
        if not c_runs:
            print(f"\n  {pid}: No runs found")
            continue

        print(f"\n  {pid} ({skill_name}):")
        for rnd, run_id in sorted(c_runs):
            events = load_events(run_id)
            replans = sum(1 for e in events if e.get("event_type") == "replan_complete")
            learns = [
                e for e in events
                if e.get("event_type") == "skill_expand"
            ]
            learn_count = sum(e.get("data", {}).get("count", 0) for e in learns)
            print(f"    Round {rnd}: replans={replans}, observations_learned={learn_count}")

        # Check observations
        obs_file = LEARNING_DIR / f"{skill_name}.jsonl"
        obs_count = 0
        run_ids = set()
        if obs_file.exists():
            for line in obs_file.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    obs_count += 1
                    if d.get("run_id"):
                        run_ids.add(d["run_id"])
                except json.JSONDecodeError:
                    pass
        print(f"    Total observations: {obs_count} across {len(run_ids)} distinct runs")

        # Check promotions
        history_file = LEARNING_DIR / "promotions" / "history.jsonl"
        promotions = []
        if history_file.exists():
            for line in history_file.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    if d.get("skill_name") == skill_name:
                        promotions.append(d)
                except json.JSONDecodeError:
                    pass
        if promotions:
            any_promotion = True
            for p in promotions:
                print(
                    f"    PROMOTION: {p['promotion_type']} "
                    f"(score={p.get('confidence_score', '?'):.2f}, "
                    f"obs={p.get('observation_count', '?')})"
                )

        # Check skill file for Learned Tips
        skill_file = SKILL_LIB / f"{skill_name.replace('-', '_')}.md"
        if skill_file.exists():
            content = skill_file.read_text()
            if "## Learned Tips" in content:
                print("    Skill file: ## Learned Tips section PRESENT")
            else:
                print("    Skill file: No Learned Tips section yet")

    verdict = "PASS" if any_promotion else "PARTIAL"
    print(f"\n  VERDICT: {verdict}")
    return any_promotion


def verify_property_d(runs: list[tuple[str, int, str]]):
    """Property (d): Reduced duration/effort across runs."""
    print("\n" + "=" * 60)
    print("PROPERTY (d): Reduced Duration / Effort")
    print("=" * 60)

    prompt_data = defaultdict(list)
    for pid, rnd, run_id in runs:
        events = load_events(run_id)
        if not events:
            continue
        try:
            t0 = datetime.fromisoformat(events[0]["timestamp"])
            t1 = datetime.fromisoformat(events[-1]["timestamp"])
        except (KeyError, ValueError):
            continue
        duration = (t1 - t0).total_seconds()
        replans = sum(1 for e in events if e.get("event_type") == "replan_complete")
        fails = sum(
            1 for e in events
            if e.get("event_type") == "step_complete"
            and not e.get("data", {}).get("success")
        )
        success = any(e.get("event_type") == "task_complete" for e in events)
        prompt_data[pid].append({
            "round": rnd, "duration": duration,
            "replans": replans, "fails": fails, "success": success
        })

    improving_count = 0
    total_replan_prompts = 0

    for pid in sorted(prompt_data):
        items = sorted(prompt_data[pid], key=lambda r: r["round"])
        print(f"\n  {pid}:")
        print(f"    {'Round':<6} {'Duration':>10} {'Replans':>8} {'Fails':>6} {'OK':>4}")
        for r in items:
            print(
                f"    {r['round']:<6} {r['duration']:>8.1f}s "
                f"{r['replans']:>8} {r['fails']:>6} "
                f"{'Y' if r['success'] else 'N':>4}"
            )

        rp = [r["replans"] for r in items]
        fl = [r["fails"] for r in items]
        d = [r["duration"] for r in items]

        if len(rp) >= 2:
            rp_trend = "IMPROVING" if rp[-1] < rp[0] else "FLAT/WORSE"
            fl_trend = "IMPROVING" if fl[-1] < fl[0] else "FLAT/WORSE"
            delta = ((d[-1] - d[0]) / d[0]) * 100 if d[0] > 0 else 0
            print(f"    Replans: {rp[0]} -> {rp[-1]} ({rp_trend})")
            print(f"    Fails:   {fl[0]} -> {fl[-1]} ({fl_trend})")
            print(f"    Duration: {delta:+.0f}% (wall-clock, informational only)")

            if pid in ("C1", "C2"):
                total_replan_prompts += 1
                if rp[-1] <= rp[0] and fl[-1] <= fl[0]:
                    improving_count += 1

    if total_replan_prompts > 0:
        verdict = "PASS" if improving_count == total_replan_prompts else "PARTIAL"
    else:
        verdict = "N/A"
    print(f"\n  VERDICT: {verdict} ({improving_count}/{total_replan_prompts} replan prompts improving)")
    return verdict == "PASS"


def main():
    runs = load_runs()
    print(f"Loaded {len(runs)} runs from {CSV_FILE}")

    a = verify_property_a(runs)
    b = verify_property_b(runs)
    c = verify_property_c(runs)
    d = verify_property_d(runs)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  (a) Existing skill reuse:    {'PASS' if a else 'FAIL'}")
    print(f"  (b) Observation accumulation: {'PASS' if b else 'PARTIAL/FAIL'}")
    print(f"  (c) Replan → skill updates:   {'PASS' if c else 'PARTIAL/FAIL'}")
    print(f"  (d) Reduced effort:           {'PASS' if d else 'PARTIAL/FAIL'}")
    print()


if __name__ == "__main__":
    main()
