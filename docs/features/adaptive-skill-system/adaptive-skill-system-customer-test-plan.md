# Adaptive Skill System: Customer Testing Plan

## Context

The adaptive skill system learns from execution traces and promotes patterns into canonical skills. It has been implemented and unit-tested (1411 tests passing), but never validated end-to-end with real agent runs demonstrating the full learning loop. This plan designs test prompts and verification strategies to prove 4 properties:

- **(a)** Existing skills are matched and used
- **(b)** New skills are created when none exist (or analogical matches produce siblings)
- **(c)** Replans cause skill updates (observations → learned tips)
- **(d)** Subsequent runs have reduced duration/failures

## Critical Design Constraints

1. `_maybe_learn_skill_run()` at `agent.py:4007` guards with `if not skill_name: return []`. **Learning only happens when a skill was matched.**
2. `create_sibling` writes new skills with `trusted: false`. Embedding retrieval ignores untrusted skills (`registry.py:137`). So newly created siblings are only reachable via keyword fallback, not embedding.
3. Replan observations are capped at confidence `0.6` (`registry.py:723`), which still exceeds the `min_confidence=0.5` threshold.
4. Promotion groups use exact normalized `(category, recommendation)` buckets. LLM wording drift across runs can prevent observations from grouping together for promotion.
5. **Promotion thresholds are loose**: `min_observations=2`, `min_runs=1`, `min_confidence=0.5`. Two observations at 0.6 confidence already qualify. Promotion can fire as early as round 2.
6. **Property (b) — create_sibling — is hard to test live**: Same-site/same-intent matches yield `patch_parent` (tip additions). Cross-site analogical matches are filtered below usable confidence by the router prompt. `create_sibling` requires significant workflow divergence from a matched parent, which is unlikely in a same-intent match. **The reliable test for property (b) is a deterministic integration harness, not live prompts.**

---

## Consultant Review (DONE — PASS after 3 rounds)

- **Round 1 (v1)**: 6 findings (3 High, 3 Medium), 8 answers. All addressed.
- **Round 2 (v2→v3)**: 5 findings (1 High, 4 Medium). All addressed: added deterministic integration test, full block isolation, stronger assertions.
- **Round 3 (v4)**: **PASS**. No blocking findings. 3 non-blocking hardening suggestions accepted.

### Consultant Findings Addressed

| # | Finding | Severity | Resolution |
|---|---------|----------|------------|
| 1 | B1 won't match — `return-walmart-order` requires return/refund keywords; sub-0.5 matches discarded; no `skill_name` → no learning | High | **Replaced B1** with `"Return a pair of shoes I bought on Walmart"` — matches `return-walmart-order` by intent, but shoe-specific return flow diverges from the general template |
| 2 | A2 is a mismatch — `buy-on-target` is add-to-cart, not search | High | **Changed A2** prompt to explicit add-to-cart: `"Add a coffee maker to my cart on Target"` |
| 3 | Verification reads ambiguous `skill_match` log (site extraction vs real match) | High | **Fixed verification** to filter by `e['data'].get('skill_name')` being non-None. Duration computed from first/last event timestamps. |
| 4 | B2 doesn't exercise site-entity path — `restaurant-google` has no `site:` metadata, `google` not a seed site | Medium | **Replaced B2** with `"Find the cheapest laptop on Best Buy"`. `bestbuy` is a seed site. |
| 5 | Promotion grouping relies on exact wording — LLM drift prevents grouping | Medium | **Documented as risk**. Increased runs to 5 per prompt. |
| 6 | `create_sibling` output is `trusted: false` → invisible to embedding retrieval | Medium | **Documented in constraints**. Property (b) acceptance is: sibling `.md` file created — not that it's immediately usable via embedding. |

Additional answers incorporated:
- **Duration expectations lowered**: Primary metrics are retry count, replan count, and failed-step count (not wall-clock).
- **C2 changed**: Gift wrapping instead of size/color (already in skill template).
- **Full block isolation**: Wipe skill_learning + restore skill library between every block.

---

## Test Prompts

### Group A — Existing Skill Reuse (property a)

| ID | Prompt | Expected Skill | Rationale |
|----|--------|---------------|-----------|
| A1 | `"Search Amazon for wireless earbuds under $25"` | `amazon-search` | Direct match via site:amazon + keywords. Tests parameter extraction. |
| A2 | `"Add a coffee maker to my cart on Target"` | `buy-on-target` | Direct match via site:target + add-to-cart intent. |

### Group B — Observation Accumulation + Limitation Documentation (property b)

**Design note**: Live prompts cannot reliably produce `create_sibling`. B-group tests validate `patch_parent` from matched skills with workflow variation. The authoritative `create_sibling` test is the **deterministic integration harness** (`tests/integration/test_sibling_promotion.py`).

| ID | Prompt | Expected Behavior | Rationale |
|----|--------|-------------------|-----------|
| B1 | `"Return a pair of shoes I bought on Walmart"` | Matches `return-walmart-order`. Shoe-specific return may produce replan observations. Most likely: `patch_parent`. | Tests observation accumulation from matched skill with variant workflow. |
| B2 | `"Find the cheapest laptop on Best Buy"` | Site extraction returns `bestbuy` (seed site). No bestbuy skill. **Expected: `skill_no_match`**. | Documents "no-match = no-learning" limitation. |

### Group C — Replan-Driven Skill Updates (properties c, d)

| ID | Prompt | Expected Behavior | Rationale |
|----|--------|-------------------|-----------|
| C1 | `"Search Amazon for USB-C hub sorted by customer reviews"` | Matches `amazon-search` but skill only sorts by price. Must replan for review-sort UI. | Core learning loop test: match → fail → replan → distill → promote. |
| C2 | `"Add a phone case to my cart on Target and select gift wrapping"` | Matches `buy-on-target`. Gift wrapping NOT in skill template. Must replan. | Tests tip promotion for uncovered workflow. |

---

## Run Sequence

**Total: 6 unique prompts x 5 runs = 30 runs. Estimated ~50 minutes.**

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

# 3. Isolate state — FULL reset
mkdir -p /tmp/adaptive_test_backup
cp -r logs/skill_learning /tmp/adaptive_test_backup/skill_learning_$(date +%s) 2>/dev/null || true
rm -rf logs/skill_learning/
mkdir -p logs/skill_learning/promotions
cp -r src/automation_agent/skills/library/ /tmp/adaptive_test_backup/skill_library_clean/

# 4. Snapshot skill files
find src/automation_agent/skills/library/ -name "*.md" | sort | xargs md5 > /tmp/skill_md5_before.txt
```

### Execution order (fully isolated blocks)

A1/C1 share `amazon-search`; A2/C2 share `buy-on-target`. **Full isolation requires resetting both `logs/skill_learning/` and the skill library between blocks.**

```
Block 1 — A1 x5 (amazon-search baseline + learning)
  [RESET]
Block 2 — C1 x5 (amazon-search replan + promotion)
  [RESET]
Block 3 — A2 x5 (buy-on-target baseline + learning)
  [RESET]
Block 4 — C2 x5 (buy-on-target replan + promotion)
  [RESET]
Block 5 — B1 x5 (walmart return — observation accumulation)
  [RESET]
Block 6 — B2 x5 (bestbuy — expected no-match)
```

**Between-block reset:**
```bash
rm -rf logs/skill_learning/ && mkdir -p logs/skill_learning/promotions
rm -rf src/automation_agent/skills/library/
cp -r /tmp/adaptive_test_backup/skill_library_clean/ src/automation_agent/skills/library/
```

Each block runs as a separate `automation-agent` invocation (fresh Python process per run).

---

## Verification Strategy

### Property (a): Existing Skill Reuse

```bash
# Filter for REAL matches (have skill_name in data, not just site extraction)
python3 -c "
import json; from pathlib import Path
for line in (Path('logs/runs/$LATEST_RUN/events.jsonl')).read_text().splitlines():
    e = json.loads(line)
    if e['event_type'] == 'skill_match' and e.get('data', {}).get('skill_name'):
        print(f\"MATCH: {e['data']['skill_name']} (conf={e['data'].get('confidence','')})\")
    elif e['event_type'] == 'skill_no_match':
        print('NO MATCH')
"
```

**Pass**: A1 → `amazon-search`, A2 → `buy-on-target`, C1 → `amazon-search`, C2 → `buy-on-target`

### Property (b): Observation Accumulation

**Authoritative artifacts** (not log proxies):
- `logs/skill_learning/promotions/history.jsonl` — `promotion_type`, `run_id`, promoted keys
- `src/automation_agent/skills/library/` — diff against snapshot for patched/new `.md` files
- `skill_no_match` event for B2 confirms limitation

**Deterministic test**: `tests/integration/test_sibling_promotion.py` (7 tests, all passing) proves the full `create_sibling` commit contract.

### Property (c): Replans Update Skills

**Pass**: Observations in `logs/skill_learning/{skill}.jsonl` with distinct `run_id` values. After 3+ runs: `patch_parent` in promotion history. Skill `.md` gains `## Learned Tips`.

### Property (d): Reduced Effort

**Primary metrics**: Replan count and failed-step count decrease across runs (measured AFTER first promotion point). Wall-clock is informational only.

---

## Deterministic Integration Test (Property b)

**File**: `tests/integration/test_sibling_promotion.py` — 7 tests covering:

1. **Full commit contract**: history.jsonl entry, new .md in configured dir, parent-skill-id, trusted:false, registry reload, observations marked promoted, no dual-write to default library
2. **Name collision**: suffix -2 appended
3. **Rollback on reload failure**: file deleted, registry clean
4. **Rollback on history write failure**: file deleted, registry clean
5. **Below-threshold**: 1 observation → no promotion
6. **Already-promoted**: same group not re-promoted
7. **Invalid MD rejection**: missing sections → observation_only

---

## Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| Existing learned state contaminates results | High | Pre-run wipes skill_learning + snapshots library |
| Prior patch_parent edits bias results | High | Restore clean library from snapshot between blocks |
| Browser warming affects timing | Medium | First run is baseline; trends from run 2+ |
| Login walls / captchas | Medium | Run from logged-in session |
| LLM wording drift prevents grouping | Medium | 5 runs per prompt; inspect wording manually if needed |
| create_sibling downgrades to patch_parent | Medium | Document actual promotion_type |
| B2 no-match → no learning | Expected | Documents the limitation |
| Shared skill cross-contamination | Medium | Full block isolation with reset |
| Early promotion (run 2) | Low | Record promotion point; measure (d) after |
| C2 gift wrapping unavailable | Low | C1 is primary; C2 supplementary |
