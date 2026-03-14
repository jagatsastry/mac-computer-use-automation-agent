#!/usr/bin/env python3
"""Post-hoc analysis of a run's events.jsonl file.

Usage:
  python scripts/analyze_run.py --most-recent          # latest run
  python scripts/analyze_run.py logs/runs/<run_id>/events.jsonl
"""
import argparse
import json
import sys
from pathlib import Path
from collections import Counter

DEFAULT_RUNS_DIR = Path(__file__).resolve().parent.parent / "logs" / "runs"


def _find_most_recent(runs_dir: Path) -> Path:
    """Return the events.jsonl from the most recently modified run directory."""
    candidates = [
        d / "events.jsonl"
        for d in runs_dir.iterdir()
        if d.is_dir() and (d / "events.jsonl").exists()
    ]
    if not candidates:
        print(f"No runs found in {runs_dir}", file=sys.stderr)
        sys.exit(1)
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main():
    parser = argparse.ArgumentParser(description="Analyze an agent run's event log")
    parser.add_argument("events_file", nargs="?", help="Path to events.jsonl")
    parser.add_argument(
        "--most-recent", action="store_true", default=False,
        help="Analyze the most recent run (default if no file given)",
    )
    parser.add_argument(
        "--runs-dir", type=Path, default=DEFAULT_RUNS_DIR,
        help=f"Runs directory (default: {DEFAULT_RUNS_DIR})",
    )
    args = parser.parse_args()

    if args.events_file:
        events_file = Path(args.events_file)
    elif args.most_recent or args.events_file is None:
        events_file = _find_most_recent(args.runs_dir)
    else:
        parser.print_help()
        return 1

    if not events_file.exists():
        print(f"File not found: {events_file}")
        return 1

    print(f"File: {events_file}")

    events = []
    with open(events_file) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    print(f"Run: {events[0].get('run_id', 'unknown') if events else 'empty'}")
    print(f"Total events: {len(events)}")

    # Event type distribution
    type_counts = Counter(e["event_type"] for e in events)
    print("\nEvent types:")
    for etype, count in type_counts.most_common():
        print(f"  {etype}: {count}")

    # Duration analysis
    durations = [e.get("duration_ms") for e in events if e.get("duration_ms")]
    if durations:
        print(f"\nDurations:")
        print(f"  Total: {sum(durations)}ms")
        print(f"  Avg: {sum(durations) // len(durations)}ms")
        print(f"  Max: {max(durations)}ms")

    # Verification results
    verify_events = [e for e in events if e["event_type"].startswith("verify_")]
    if verify_events:
        passes = sum(1 for e in verify_events if e["event_type"] == "verify_pass")
        fails = sum(1 for e in verify_events if e["event_type"] == "verify_fail")
        print(f"\nVerification: {passes} pass, {fails} fail")

    # Screenshots
    screenshots = [e.get("screenshot_path") for e in events if e.get("screenshot_path")]
    if screenshots:
        print(f"\nScreenshots: {len(screenshots)}")
        for s in screenshots:
            print(f"  {s}")

    # Timeline
    print("\nTimeline:")
    for e in events:
        ts = e.get("timestamp", "?")
        if "T" in ts:
            ts = ts.split("T")[1][:12]
        dur = f" ({e['duration_ms']}ms)" if e.get("duration_ms") else ""
        print(f"  {ts} {e['event_type']}: {e['message'][:80]}{dur}")
        # Expand plan steps inline
        if e["event_type"] in ("plan_complete", "replan_complete"):
            data = e.get("data", {})
            for i, step in enumerate(data.get("steps_summary", []), 1):
                print(f"           {i}. {step}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
