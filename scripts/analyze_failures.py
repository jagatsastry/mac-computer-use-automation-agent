#!/usr/bin/env python3
"""Analyze failure patterns across multiple runs.

Usage: PYTHONPATH=src python3 scripts/analyze_failures.py logs/runs/
"""
import json
import sys
from pathlib import Path
from collections import Counter


def main():
    if len(sys.argv) < 2:
        runs_dir = Path("logs/runs")
    else:
        runs_dir = Path(sys.argv[1])

    if not runs_dir.exists():
        print(f"Directory not found: {runs_dir}")
        return 1

    run_dirs = sorted(runs_dir.iterdir())
    if not run_dirs:
        print("No runs found")
        return 0

    print(f"Analyzing {len(run_dirs)} runs from {runs_dir}\n")

    failure_actions = Counter()
    failure_reasons = Counter()
    total_runs = 0
    successful_runs = 0

    for run_dir in run_dirs:
        events_file = run_dir / "events.jsonl"
        if not events_file.exists():
            continue

        total_runs += 1
        events = []
        with open(events_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))

        # Check if run succeeded
        task_complete = any(e["event_type"] == "task_complete" for e in events)
        if task_complete:
            successful_runs += 1

        # Collect failures
        for e in events:
            if e["event_type"] in ("verify_fail", "action_error", "task_fail"):
                failure_actions[e.get("data", {}).get("action", "unknown")] += 1
                failure_reasons[e["message"][:80]] += 1

    print(f"Total runs: {total_runs}")
    print(f"Successful: {successful_runs} ({100*successful_runs//max(total_runs,1)}%)")
    print(f"Failed: {total_runs - successful_runs}")

    if failure_actions:
        print(f"\nTop failing actions:")
        for action, count in failure_actions.most_common(10):
            print(f"  {action}: {count}")

    if failure_reasons:
        print(f"\nTop failure reasons:")
        for reason, count in failure_reasons.most_common(10):
            print(f"  {reason}: {count}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
