#!/usr/bin/env python3
"""Post-hoc analysis of a run's events.jsonl file.

Usage: PYTHONPATH=src python3 scripts/analyze_run.py logs/runs/<run_id>/events.jsonl
"""
import json
import sys
from pathlib import Path
from collections import Counter


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/analyze_run.py <events.jsonl>")
        return 1

    events_file = Path(sys.argv[1])
    if not events_file.exists():
        print(f"File not found: {events_file}")
        return 1

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
        print(f"  {ts} {e['event_type']}: {e['message'][:60]}{dur}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
