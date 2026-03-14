#!/usr/bin/env python3
"""Pretty-print a human-readable log from an automation run's events.jsonl.

Usage:
    python scripts/run_log.py                          # latest run
    python scripts/run_log.py <run_id>                 # specific run
    python scripts/run_log.py --list                   # list recent runs
    python scripts/run_log.py --verbose                # include LLM responses
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs" / "runs"

ICONS = {
    "task_start": "\u250c",
    "skill_match": "\u2502 \U0001f3af",
    "plan_start": "\u2502 \U0001f9e0",
    "plan_complete": "\u2502 \U0001f4cb",
    "step_start": "\u251c\u2500",
    "action_start": "\u2502   \u25b6",
    "action_complete": "\u2502   \u2714",
    "element_found": "\u2502   \U0001f50d",
    "element_not_found": "\u2502   \u2718",
    "verify_start": "\u2502   \u2026",
    "verify_pass": "\u2502   \u2705",
    "verify_fail": "\u2502   \u274c",
    "verify_escalate": "\u2502   \u2191",
    "step_complete": "\u2502  ",
    "step_retry": "\u2502   \U0001f504",
    "step_replan": "\u2502 \U0001f501",
    "replan_start": "\u2502 \U0001f9e0",
    "replan_complete": "\u2502 \U0001f4cb",
    "user_wait": "\u2502 \u23f8",
    "skill_expand": "\u2502 \U0001f4da",
}


def ts(raw: str) -> str:
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%H:%M:%S")
    except Exception:
        return raw[:8]


def format_event(e: dict, verbose: bool = False) -> list[str]:
    et = e.get("event_type", "")
    icon = ICONS.get(et, "\u2502")
    time = ts(e.get("timestamp", ""))
    msg = e.get("message", "")
    lines = []

    if et == "task_start":
        goal = e.get("data", {}).get("goal", msg)
        lines.append(f"\n{icon} [{time}] AUTOMATION RUN")
        lines.append(f"\u2502  Goal: {goal}")
        lines.append(f"\u2502  Run:  {e.get('run_id', '?')}")
        lines.append("\u2502")

    elif et == "skill_match":
        d = e.get("data", {})
        lines.append(f"{icon} [{time}] Skill matched: {d.get('skill_name', '?')}")
        params = d.get("params", {})
        if params:
            lines.append(f"\u2502        params: {params}")

    elif et == "plan_complete":
        d = e.get("data", {})
        count = d.get("step_count", "?")
        lines.append(f"{icon} [{time}] Plan ready: {count} steps")
        steps = d.get("steps_summary", [])
        for i, s in enumerate(steps):
            lines.append(f"\u2502       {i:>2}. {s}")
        if verbose and d.get("llm_response"):
            lines.append(f"\u2502")
            lines.append(f"\u2502    [LLM Response]")
            for ln in d["llm_response"].split("\n")[:30]:
                lines.append(f"\u2502      {ln}")

    elif et == "plan_start":
        lines.append(f"{icon} [{time}] Planning...")

    elif et == "step_start":
        idx = e.get("step_index", "?")
        d = e.get("data", {})
        action = d.get("action", "?")
        params = d.get("params", {})
        param_str = ""
        if params:
            parts = []
            for k, v in params.items():
                sv = repr(v) if isinstance(v, str) else str(v)
                if len(sv) > 60:
                    sv = sv[:57] + "..."
                parts.append(f"{k}={sv}")
            param_str = ", ".join(parts)
        lines.append(f"{icon} [{time}] Step {idx}: {action}({param_str})")

    elif et == "element_found":
        d = e.get("data", {})
        el = d.get("element", "?")
        x, y = d.get("x", "?"), d.get("y", "?")
        conf = d.get("confidence", "?")
        src = d.get("source", "?")
        lines.append(f"{icon} [{time}] Found \"{el}\" at ({x},{y}) conf={conf} via {src}")

    elif et == "element_not_found":
        lines.append(f"{icon} [{time}] NOT FOUND: {msg.replace('Element not found: ', '')}")

    elif et == "action_complete":
        # Simplify the output
        clean = msg.replace("action_complete", "").strip()
        if "-> " in clean:
            result = clean.split("-> ", 1)[1]
            # Truncate long results
            if len(result) > 100:
                result = result[:97] + "..."
            lines.append(f"{icon} [{time}] {result}")

    elif et == "verify_pass":
        evidence = msg
        lines.append(f"{icon} [{time}] {evidence}")

    elif et == "verify_fail":
        lines.append(f"{icon} [{time}] {msg}")

    elif et == "verify_escalate":
        lines.append(f"{icon} [{time}] Escalating to vision verification...")

    elif et == "step_complete":
        d = e.get("data", {})
        idx = e.get("step_index", "?")
        ok = d.get("success", False)
        evidence = d.get("evidence", "")
        status = "\u2705 PASS" if ok else "\u274c FAIL"
        lines.append(f"{icon}  [{time}] Step {idx} {status}")
        if evidence and not ok:
            # Truncate long evidence for readability
            ev = evidence[:120] + "..." if len(evidence) > 120 else evidence
            lines.append(f"\u2502          {ev}")

    elif et == "step_retry":
        d = e.get("data", {})
        strategy = d.get("strategy", "?")
        attempt = d.get("attempt", "?")
        lines.append(f"{icon} [{time}] Retrying (strategy: {strategy}, attempt {attempt})")

    elif et == "step_replan":
        lines.append(f"{icon} [{time}] {msg}")

    elif et == "replan_start":
        lines.append(f"\u2502")
        lines.append(f"{icon} [{time}] Replanning after failure...")

    elif et == "replan_complete":
        d = e.get("data", {})
        count = d.get("step_count", "?")
        lines.append(f"{icon} [{time}] New plan: {count} steps")
        steps = d.get("steps_summary", [])
        for i, s in enumerate(steps):
            lines.append(f"\u2502       {i:>2}. {s}")
        # Show derived_skill_patch if present
        llm = d.get("llm_response", "")
        if "derived_skill_patch" in llm and verbose:
            lines.append(f"\u2502")
            lines.append(f"\u2502    [Skill Patch]")
            try:
                blob = llm.split("```json")[1].split("```")[0]
                parsed = json.loads(blob)
                patch = parsed.get("derived_skill_patch", {})
                for rl in patch.get("replace_labels", []):
                    lines.append(f"\u2502      \"{rl['old']}\" \u2192 \"{rl['new']}\"")
                    lines.append(f"\u2502        reason: {rl.get('reason', '')}")
                for fa in patch.get("failed_assumptions", []):
                    lines.append(f"\u2502      \u2718 {fa}")
                for sa in patch.get("successful_adaptations", []):
                    lines.append(f"\u2502      \u2714 {sa}")
            except Exception:
                pass

    elif et == "user_wait":
        prompt = msg.replace("Waiting for user: ", "")
        lines.append(f"{icon} [{time}] WAITING: {prompt}")

    elif et == "skill_expand":
        d = e.get("data", {})
        lines.append(f"{icon} [{time}] Learned {d.get('count', '?')} observations for skill \"{d.get('skill_name', '?')}\"")

    elif et == "action_start":
        pass  # step_start already shows this

    elif et == "verify_start":
        pass  # verify result shows this

    else:
        lines.append(f"{icon} [{time}] {msg}")

    return lines


def print_run(run_dir: Path, verbose: bool = False):
    events_file = run_dir / "events.jsonl"
    if not events_file.exists():
        print(f"No events.jsonl in {run_dir}")
        return

    events = []
    for line in events_file.read_text().splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))

    if not events:
        print("Empty run.")
        return

    # Compute duration
    t0 = events[0].get("timestamp", "")
    t1 = events[-1].get("timestamp", "")
    try:
        dt0 = datetime.fromisoformat(t0)
        dt1 = datetime.fromisoformat(t1)
        duration = dt1 - dt0
        dur_str = f"{duration.total_seconds():.1f}s"
    except Exception:
        dur_str = "?"

    for e in events:
        for line in format_event(e, verbose):
            print(line)

    # Summary
    passes = sum(1 for e in events if e.get("event_type") == "step_complete" and e.get("data", {}).get("success"))
    fails = sum(1 for e in events if e.get("event_type") == "step_complete" and not e.get("data", {}).get("success"))
    replans = sum(1 for e in events if e.get("event_type") == "replan_complete")

    print()
    print(f"\u2514\u2500 DONE ({dur_str})")
    print(f"   Steps passed: {passes}")
    print(f"   Steps failed: {fails}")
    print(f"   Replans:      {replans}")

    # Final status
    last_task = [e for e in events if e.get("event_type") in ("task_complete", "skill_expand", "step_replan")]
    if last_task:
        last = last_task[-1]
        if last.get("event_type") == "step_replan" and "exhausted" in last.get("message", ""):
            print(f"   Result:       FAILED (exhausted retries)")
        elif last.get("event_type") == "task_complete":
            print(f"   Result:       SUCCESS")
        else:
            print(f"   Result:       PARTIAL (skill learned from failure)")


def list_runs():
    if not LOGS_DIR.exists():
        print("No runs directory found.")
        return
    runs = sorted(LOGS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    print(f"{'Run ID':<16} {'Time':<20} {'Goal'}")
    print("-" * 70)
    for d in runs[:20]:
        ef = d / "events.jsonl"
        if not ef.exists():
            continue
        first_line = ef.read_text().split("\n", 1)[0].strip()
        if not first_line:
            continue
        e = json.loads(first_line)
        t = ts(e.get("timestamp", ""))
        goal = e.get("data", {}).get("goal", e.get("message", "?"))
        print(f"{d.name:<16} {t:<20} {goal[:40]}")


def latest_run() -> Optional[Path]:
    if not LOGS_DIR.exists():
        return None
    runs = sorted(LOGS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for d in runs:
        if (d / "events.jsonl").exists():
            return d
    return None


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    if "--list" in sys.argv:
        list_runs()
        return

    if args:
        run_id = args[0]
        run_dir = LOGS_DIR / run_id
        if not run_dir.exists():
            print(f"Run '{run_id}' not found in {LOGS_DIR}")
            sys.exit(1)
    else:
        run_dir = latest_run()
        if not run_dir:
            print("No runs found.")
            sys.exit(1)

    print_run(run_dir, verbose)


if __name__ == "__main__":
    main()
