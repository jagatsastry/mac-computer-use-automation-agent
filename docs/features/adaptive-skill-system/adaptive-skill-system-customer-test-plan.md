# Adaptive Skill System: Customer Testing Plan

## Context

The adaptive skill system learns from execution traces and promotes patterns into canonical skills. It has been implemented and unit-tested (1411 tests passing), but never validated end-to-end with real agent runs demonstrating the full learning loop. This plan designs test prompts and verification strategies to prove 4 properties:

- **(a)** Existing skills are matched and used
- **(b)** New skills are created when none exist (or analogical matches produce siblings)
- **(c)** Replans cause skill updates (observations → learned tips)
- **(d)** Subsequent runs have reduced duration/failures

## Critical Design Constraint

`_maybe_learn_skill_run()` at `agent.py:4007` guards with `if not skill_name: return []`. **Learning only happens when a skill was matched.** For property (b), we can't test "no skill at all → new skill created." Instead, we test analogical/partial matches that produce siblings via `create_sibling` promotion.

---

## Step 1: Send Plan to Consultant for Review

Before executing any tests, send this plan to `codex exec` for an independent review. Iterate until the consultant approves.

**Consultant prompt** (write to `/tmp/adaptive-skill-test-plan-review.md`):

```markdown
# Review Request: Adaptive Skill System Customer Testing Plan

You are reviewing a customer testing plan for a macOS automation agent's adaptive skill system. The system learns from execution traces and promotes patterns into canonical skills.

## The 4 Properties Being Tested
(a) Existing skills are matched and used
(b) New skills are created via analogical matching + sibling promotion
(c) Replans cause skill updates (learned tips appended to skill files)
(d) Subsequent runs show reduced duration/failures

## System Architecture (key facts)
- Skills are .md files in src/automation_agent/skills/library/
- Distillation: After each run, SkillDistiller extracts observations → stored in logs/skill_learning/{skill_name}.jsonl
- Promotion: When 2+ observations accumulate with Bayesian score >= 0.5, SkillLibrarian promotes: patch_parent (add tips) or create_sibling (new .md file)
- Feedback: Promoted tips + high-confidence observations injected into planner context on subsequent runs
- CRITICAL: Learning ONLY happens when a skill was matched (agent.py:4007)

## Proposed Test Prompts
[paste the test prompts section below]

## Proposed Verification Strategy
[paste the verification section below]

## What to review:
1. Are the prompts well-chosen to exercise all 4 properties?
2. Are there gaps in the verification strategy?
3. Is the "no-skill-match = no learning" limitation adequately tested?
4. Are the expected outcomes realistic?
5. Any risks or failure modes not accounted for?
```

Run: `codex exec "$(cat /tmp/adaptive-skill-test-plan-review.md)" > /tmp/consultant-review-output.txt 2>/tmp/consultant-review-err.txt`

Address all comments, update this plan, re-submit if needed.

---

## Step 2: Test Prompts

### Group A — Existing Skill Reuse (property a)

| ID | Prompt | Expected Skill | Rationale |
|----|--------|---------------|-----------|
| A1 | `"Search Amazon for wireless earbuds under $25"` | `amazon-search` | Direct match via site:amazon + keywords. Tests parameter extraction (product, max_price). |
| A2 | `"Find a coffee maker on Target"` | `buy-on-target` | Direct match via site:target + required-keywords. Tests site-entity filtering. |

### Group B — Analogical Match → Sibling Creation (property b)

| ID | Prompt | Expected Behavior | Rationale |
|----|--------|-------------------|-----------|
| B1 | `"Search for running shoes on Walmart"` | Matches `return-walmart-order` weakly (site:walmart), but intent is "search/buy" not "return". Replan produces distinct workflow. After 2 runs, `create_sibling` may fire. | Tests whether a wrong-intent match produces sibling skill via derived session divergence. |
| B2 | `"Look up restaurants near me on Google Maps"` | May match `restaurant-google` or `google-search`. Neither has Google Maps steps. Replans produce observations about Maps-specific UI. | Tests analogical matching + observation accumulation for a near-miss domain. |

### Group C — Replan-Driven Skill Updates (properties c, d)

| ID | Prompt | Expected Behavior | Rationale |
|----|--------|-------------------|-----------|
| C1 | `"Search Amazon for USB-C hub sorted by customer reviews"` | Matches `amazon-search` but skill only sorts by price. Agent must replan to find review-sort UI. Observations about sort-by-reviews accumulate. After 2 runs, `patch_parent` adds tips. | Tests the core learning loop: match → fail → replan → distill → promote → improved next run. |
| C2 | `"Buy a phone case under $15 on Target"` | Matches `buy-on-target`. Variant selection (color/size) may require replan. Observations about variant handling accumulate. | Tests incremental tip promotion on an existing well-tested skill. |

### Group D — Duration Reduction Baseline (property d)

| ID | Prompt | Rationale |
|----|--------|-----------|
| D1 | `"Search Amazon for wireless earbuds under $25"` | Same as A1. Run 3 times. Compare duration, replan count, step failures across runs. |

**Note**: D1 reuses A1's prompt. Every prompt in groups A-C is also run 3 times to measure property (d).

---

## Step 3: Run Sequence

**Total: 7 unique prompts x 3 runs = 21 runs. Estimated ~35 minutes.**

### Pre-run setup

```bash
# 1. Verify vision server
curl -s http://localhost:8091/v1/models | python3 -c "import sys,json; print('OK:', json.load(sys.stdin)['data'][0]['id'])"

# 2. Verify config
.venv/bin/python -c "
from automation_agent.config import AgentConfig
c = AgentConfig()
assert c.skill_learning_enabled, 'Learning disabled!'
assert c.skill_librarian_enabled, 'Librarian disabled!'
print(f'min_obs={c.skill_librarian_min_observations}, min_runs={c.skill_librarian_min_runs}, min_conf={c.skill_librarian_min_confidence}')
"

# 3. Snapshot skill files for before/after diff
find src/automation_agent/skills/library/ -name "*.md" | sort | xargs md5 > /tmp/skill_md5_before.txt

# 4. Record baseline observation counts
for f in logs/skill_learning/*.jsonl 2>/dev/null; do
    echo "$(basename $f .jsonl): $(wc -l < $f | tr -d ' ') obs"
done > /tmp/obs_baseline.txt
cat /tmp/obs_baseline.txt
```

### Execution order (sequential, grouped by round)

```
Round 1 (baseline):  A1, A2, B1, B2, C1, C2, A1
Round 2 (learning):  A1, A2, B1, B2, C1, C2, A1
Round 3 (promotion): A1, A2, B1, B2, C1, C2, A1
```

A1 appears 3 extra times (9 total runs for that prompt) to provide the strongest duration-trend signal for property (d).

Run command per prompt:
```bash
.venv/bin/python -m automation_agent --status-ui overlay --verbose-overlay "<prompt>"
```

Capture each run's ID:
```bash
LATEST_RUN=$(ls -t logs/runs/ | head -1)
echo "$PROMPT_ID,$ROUND,$LATEST_RUN" >> /tmp/adaptive_test_runs.csv
```

---

## Step 4: Verification Strategy

### Property (a): Existing Skill Reuse

**What to check (per run of A1, A2, C1, C2):**

```bash
# Check events.jsonl for skill_match
python3 -c "
import json; from pathlib import Path
for line in (Path('logs/runs/$LATEST_RUN/events.jsonl')).read_text().splitlines():
    e = json.loads(line)
    if e['event_type'] == 'skill_match':
        print(f\"MATCH: {e['data']['skill_name']} (conf={e['data'].get('confidence','')})\")
    elif e['event_type'] == 'skill_no_match':
        print('NO MATCH')
"
```

**Pass criteria:**
- A1 → `amazon-search` matched
- A2 → `buy-on-target` matched
- C1 → `amazon-search` matched
- C2 → `buy-on-target` matched
- Plan steps follow skill template structure (open_url to correct domain, etc.)

### Property (b): New Skill Created

**What to check (after round 2+ of B1, B2):**

```bash
# 1. Check if observations accumulated for a near-match skill
for skill in return-walmart-order restaurant-google google-search; do
    f="logs/skill_learning/${skill}.jsonl"
    [ -f "$f" ] && echo "$skill: $(wc -l < $f) obs" || echo "$skill: none"
done

# 2. Check promotion history for create_sibling
grep -c "create_sibling" logs/skill_learning/promotions/history.jsonl 2>/dev/null || echo "0 siblings"

# 3. Check for new .md files
diff <(cat /tmp/skill_md5_before.txt) <(find src/automation_agent/skills/library/ -name "*.md" | sort | xargs md5)
```

**Expected outcomes:**
- B1: If `return-walmart-order` matches (wrong intent), observations about "search" vs "return" accumulate. After 2 runs, `create_sibling` may create a `search-walmart` or similar skill. If no match, no learning occurs — document this as the "no-match = no-learning" limitation.
- B2: Similar — if `restaurant-google` or `google-search` matches, observations about Maps-specific UI accumulate.

### Property (c): Replans Update Skills

**What to check (per run of C1, C2):**

```bash
# 1. Replan count
python3 -c "
import json; from pathlib import Path
events = [json.loads(l) for l in Path('logs/runs/$LATEST_RUN/events.jsonl').read_text().splitlines()]
replans = sum(1 for e in events if e['event_type'] == 'replan_complete')
learns = [e for e in events if e['event_type'] == 'skill_expand']
print(f'Replans: {replans}')
for l in learns: print(f\"Learned: {l['data']['count']} obs for {l['data']['skill_name']}\")
"

# 2. Check observation file for the matched skill
tail -3 logs/skill_learning/amazon-search.jsonl | python3 -c "
import sys,json
for l in sys.stdin:
    d=json.loads(l)
    print(f\"{d['category']}: {d['recommendation'][:60]} (conf={d['confidence']}, run={d['run_id'][:8]})\")
"

# 3. After round 2+: check if Learned Tips were added
grep -A 20 '## Learned Tips' src/automation_agent/skills/library/amazon_search.md 2>/dev/null || echo 'No tips yet'

# 4. Check promotion history
tail -3 logs/skill_learning/promotions/history.jsonl 2>/dev/null | python3 -c "
import sys,json
for l in sys.stdin:
    d=json.loads(l)
    print(f\"{d['skill_name']}: {d['promotion_type']} (score={d['confidence_score']:.2f}, obs={d['observation_count']})\")
"
```

**Pass criteria:**
- Observations written to `logs/skill_learning/{skill}.jsonl` after replan runs
- After 2+ runs: `patch_parent` entry in promotion history
- Skill `.md` file gains `## Learned Tips` section with relevant bullets

### Property (d): Reduced Duration

**Cross-run analysis (after all 21 runs):**

```bash
python3 << 'PYEOF'
import json, csv
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Load run mappings
runs = []
with open("/tmp/adaptive_test_runs.csv") as f:
    for row in csv.reader(f):
        prompt_id, round_num, run_id = row
        runs.append((prompt_id, int(round_num), run_id))

# Analyze each
prompt_data = defaultdict(list)
for prompt_id, round_num, run_id in runs:
    ef = Path(f"logs/runs/{run_id}/events.jsonl")
    if not ef.exists(): continue
    events = [json.loads(l) for l in ef.read_text().splitlines() if l.strip()]
    if not events: continue
    t0 = datetime.fromisoformat(events[0]["timestamp"])
    t1 = datetime.fromisoformat(events[-1]["timestamp"])
    duration = (t1 - t0).total_seconds()
    replans = sum(1 for e in events if e["event_type"] == "replan_complete")
    fails = sum(1 for e in events if e["event_type"] == "step_complete" and not e.get("data",{}).get("success"))
    success = any(e["event_type"] == "task_complete" for e in events)
    prompt_data[prompt_id].append({
        "round": round_num, "duration": duration,
        "replans": replans, "fails": fails, "success": success
    })

# Report trends
for pid in sorted(prompt_data):
    runs = sorted(prompt_data[pid], key=lambda r: r["round"])
    print(f"\n{pid}:")
    print(f"  {'Round':<6} {'Duration':>10} {'Replans':>8} {'Fails':>6} {'OK':>4}")
    for r in runs:
        print(f"  {r['round']:<6} {r['duration']:>8.1f}s {r['replans']:>8} {r['fails']:>6} {'Y' if r['success'] else 'N':>4}")
    d = [r["duration"] for r in runs]
    if len(d) >= 2:
        delta = ((d[-1] - d[0]) / d[0]) * 100
        print(f"  Trend: {delta:+.0f}% ({'IMPROVING' if delta < 0 else 'FLAT/WORSE'})")
PYEOF
```

**Pass criteria:**
- For prompts that trigger replans (C1, C2): replan count should decrease across rounds (e.g., 2 → 1 → 0)
- Step failure count should decrease
- Duration should decrease (10-30% improvement expected; vision latency is constant overhead)
- Success rate should increase

---

## Step 5: Write Final Report

After all runs, produce `docs/adaptive-skill-customer-report.md` with:

1. **Summary table**: prompt, rounds, duration trend, observation count, promotions
2. **Per-property verdict**: PASS/PARTIAL/FAIL with evidence
3. **Skill file diffs**: before/after for any skill that gained tips or siblings
4. **Limitation documented**: "no-match = no-learning" design constraint
5. **Recommendations**: gaps found, suggested improvements

---

## Files Involved

| File | Role |
|------|------|
| `src/automation_agent/orchestrator/agent.py:3987-4079` | `_maybe_learn_skill_run`, `_maybe_promote_skill` |
| `src/automation_agent/skills/registry.py:244,695,733` | `match()`, `learn_from_run()`, `promote_from_run()` |
| `src/automation_agent/skills/librarian.py:86-286` | `evaluate_run()`, promotion pipeline |
| `src/automation_agent/skills/experience.py:38-120` | `load()`, `append()`, `top_for_context()`, `mark_promoted()` |
| `src/automation_agent/skills/distiller.py:27` | `distill()` |
| `src/automation_agent/skills/derived_skill.py:31,82` | `apply_patch()`, `serialize_for_context()` |
| `src/automation_agent/config.py:182-221` | All `skill_*` config flags |
| `scripts/run_log.py` | Run inspection tool |
| `logs/skill_learning/*.jsonl` | Observation persistence |
| `logs/skill_learning/promotions/history.jsonl` | Promotion history |
| `src/automation_agent/skills/library/*.md` | Canonical skill files |

## Verification

After all runs complete:
1. Run the cross-run duration analysis script (Step 4, property d)
2. Run the observation accumulation check (Step 4, property c)
3. Diff skill files: `diff /tmp/skill_md5_before.txt <(find ... | xargs md5)`
4. Run all unit tests: `.venv/bin/python -m pytest tests/unit/ -x -q` (should still pass)
5. Consultant reviews the final report via `codex exec`
