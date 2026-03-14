# Skill Librarian — Product Requirements Document

## Problem Statement

The automation agent's skill learning system extracts structured observations (`SkillObservation`) from execution traces via `SkillDistiller` and stores them as JSONL sidecar files in `SkillExperienceStore`. These observations are injected into LLM context at runtime but **never update the canonical skill `.md` files**.

This is the "passive accumulation" anti-pattern: observations pile up in sidecars, context windows bloat (capped at 5 per skill, but never pruned), and proven insights remain second-class citizens forever. The user expectation is clear: **the same task should get easier and faster over time**. Today it doesn't — skills are frozen at authoring time regardless of how many successful runs refine them.

### What This Costs

1. **Context waste**: Observations that should be part of the skill text are injected as separate context every run, consuming tokens without the structural benefit of being inline.
2. **No skill evolution**: A skill that works 80% of the time never improves to 95%, even after dozens of runs that identify the missing 20%.
3. **No cross-site learning**: A successful Walmart return run using `amazon_return` as an analogical prior produces observations but never creates a `walmart_return` skill — the next Walmart return starts from scratch.
4. **Observation rot**: Old observations accumulate without curation. Low-quality or contradicted observations are never pruned.

---

## Acceptance Criteria

### Core Evaluation

**AC-1**: The Librarian must evaluate accumulated observations for a skill after each completed run (success or partial success). Evaluation must happen post-run, never during execution. This preserves Hard Invariants #1 and #5 from the design doc.

**AC-2**: The Librarian must compute a Bayesian confidence score for each observation group using Laplace-smoothed success rate: `score = (alpha + 1) / (alpha + beta + 2)`, where `alpha` = corroborating observations (confidence >= 0.6) and `beta` = contradicting observations (confidence < 0.4). Observations with 0.4 <= confidence < 0.6 ("neutral") are intentionally excluded from the alpha/beta counts — they contribute to the minimum observation count (AC-3) but do not move the confidence score in either direction. This means a group of entirely neutral observations will score 0.5 (= 1/2) and fail the 0.7 promotion threshold, which is the desired behavior: ambiguous evidence should not trigger promotion. This formula is derived from MACLA (theta_conf=0.7), EvolveR (theta_prune=0.3), and ReMe (beta=0.5).

**AC-3**: The Librarian must require a minimum of 5 observations across at least 3 distinct `run_id` values before considering promotion. Single-run flukes must not pollute the canonical library. (Thresholds from ReMe alpha=5 and MACLA n_min=3+3.)

**Grouping algorithm**: Group observations by exact string match on `(category.strip().lower(), recommendation.strip().lower())`. This is the same deduplication key used by `SkillExperienceStore.top_for_context()` (experience.py line 72). Within each group, count distinct non-empty `run_id` values. A group qualifies for promotion consideration only when `len(group) >= min_observations` AND `len(distinct_run_ids_in_group) >= min_runs`.

Semantic deduplication (e.g., treating "click the confirm button" and "click confirm" as equivalent) is explicitly out of scope for v1. The distiller already produces observations with consistent phrasing because they come from the same LLM prompt template. If semantic dedup becomes necessary, it can be added as a pre-grouping normalization step in a future phase.

**AC-4**: The Librarian must only promote observation groups with a Bayesian confidence score >= 0.7. This threshold is conservative per MACLA (theta_conf=0.7) and the SOTA recommendation of >= 0.75 relaxed slightly for initial adoption.

### Promotion Types

**AC-5**: The Librarian must support three promotion types:
- `patch_parent` — modify the parent skill's `## Learned Tips` section (additive only; never edit human-authored `## Steps` or `## Error Recovery`)
- `create_sibling` — generate a new `.md` skill file derived from the parent (e.g., `walmart_return` from `amazon_return`)
- `observation_only` — retain observations in JSONL, do not touch the library

The `create_ancestor` type (e.g., `commerce_return` generalizing multiple siblings) is explicitly deferred to a future phase.

**AC-6**: For `patch_parent` promotions, the Librarian must use an LLM to generate new learned tips text, considering all qualifying observations in the group.

**Repeated patch_parent behavior**: New tips are APPENDED within the existing `## Learned Tips` section, never replacing it. This avoids semantic drift from LLM regeneration of previously-promoted tips.

**File manipulation algorithm**:
1. Read the skill `.md` file content.
2. Search for the `## Learned Tips` heading using the same regex pattern as `_extract_section()` (loader.py line 183): `^##\s+Learned Tips\s*\n`.
3. **If found**: locate the end of the section (the position of the next `^## ` heading, or EOF if none). Insert the new bullet lines before that boundary, separated by a blank line from existing bullets.
4. **If not found**: append `\n\n## Learned Tips\n` + new bullet lines at EOF.
5. Write back atomically (AC-17b).

The LLM Call 2 prompt for repeated `patch_parent` must include the existing `## Learned Tips` text as context so the LLM can avoid generating duplicates of already-promoted tips. The LLM output is only the NEW bullets to append, not a replacement of the whole section.

The original `## Steps`, `## Error Recovery`, and `## Notes` sections must never be modified programmatically. Only `## Learned Tips` is touched. (Follows the Intervention Paradox mitigation and SOTA additive-only pattern.)

**AC-7**: For `create_sibling` promotions, the Librarian must generate a complete `.md` skill file with valid YAML frontmatter including `name`, `skill-id`, `description`, `summary`, `tags`, `trigger-keywords`, `parameters`, `requires`, `success-condition`, and `parent-skill-id`. The generated file must pass `parse_skill_file()` validation before being written to disk.

**Prerequisites** (must be implemented as part of this feature):

1. **`parent_skill_id` on Skill**: Add `parent_skill_id: str = ""` to the `Skill` dataclass in `models.py`. Update `parse_skill_file()` in `loader.py` to read `parent-skill-id` from YAML frontmatter (line ~90, alongside existing `skill-id` parsing). Currently neither field nor parser support exists.

2. **`learned_tips_text` on Skill**: Add `learned_tips_text: str = ""` to the `Skill` dataclass. Update `parse_skill_file()` to extract `## Learned Tips` using the existing `_extract_section(body, "Learned Tips")` helper (loader.py line 172). Update `build_runtime_context()` (registry.py line 361) to include `learned_tips_text` in the planner context, after `## Notes` and before `## Observed Variants`:
    ```python
    if skill.learned_tips_text:
        sections.extend(["", "## Learned Tips", skill.learned_tips_text])
    ```
    **Why this is required**: Without this, `patch_parent` promotions write `## Learned Tips` to the .md file on disk, but `load_from_string()` -> `parse_skill_file()` does not parse it into any structured field. The in-memory `Skill` object would lack the tips, making them invisible to the planner until the agent restarts (a regression window). Adding `learned_tips_text` ensures tips are immediately available to the planner after promotion via `load_from_string()`.

**AC-8**: For `create_sibling`, the Librarian must derive the new skill content from the `DerivedSkillSession` data combined with qualifying observations. The field mapping from `DerivedSkillSession` (derived_skill.py) to sibling `.md` content is:

| DerivedSkillSession field | Sibling .md target | How |
|---------------------------|-------------------|-----|
| `parent_skill_ids[0]` | frontmatter `parent-skill-id` | Direct copy |
| `current_steps` | `## Steps` section | Apply `replaced_labels` substitutions to produce site-specific steps |
| `replaced_labels` | `## Notes` section | Include as "Label adaptations from parent: X -> Y (reason)" |
| `discovered_landmarks` | `## Steps` verify conditions | LLM incorporates as verify targets where relevant |
| `successful_adaptations` | `## Notes` section | Include as site-specific tips |
| `failed_assumptions` | `## Error Recovery` section | Include as "If X (which failed on parent): do Y instead" |
| `verification_notes` | `## Steps` verify conditions | LLM incorporates into step verify clauses |

The LLM prompt must receive the full `DerivedSkillSession` serialization (via `serialize_for_context()`) plus the parent skill's `raw_content` as reference. The LLM generates the complete sibling .md; the field mapping above is guidance for the prompt, not rigid code-level extraction.

### Promotion History and Deduplication

**AC-9**: The Librarian must track promotion history in a JSONL file at `{skill_learning_dir}/promotions/history.jsonl`. Each entry is a serialized `PromotionDecision`.

**PromotionDecision dataclass** (to be added in `models.py`):

```python
@dataclass
class PromotionDecision:
    skill_name: str                          # Parent skill that was evaluated
    promotion_type: str                      # "patch_parent" | "create_sibling" | "observation_only"
    reason: str                              # LLM-generated rationale
    confidence_score: float                  # Bayesian score of the promoted group
    observation_keys: list[list[str]]         # [[category, recommendation], ...] keys that were promoted
    # Note: list[list[str]] not list[tuple[str,str]] because JSON has no tuple type.
    # Each inner list is always length 2: [category, recommendation].
    # Reconstructed as tuples in code when needed for set lookups.
    run_id: str                              # Run that triggered this evaluation
    timestamp: str                           # ISO 8601
    # patch_parent fields
    generated_tips: str = ""                 # The ## Learned Tips text that was appended
    # create_sibling fields
    new_skill_id: str = ""                   # skill-id of the created sibling
    new_skill_path: str = ""                 # Relative path to the new .md file
    parent_skill_id: str = ""                # Parent skill-id for lineage
    # metadata
    observation_count: int = 0               # Total observations in the group
    distinct_run_count: int = 0              # Distinct run_ids in the group
```

**AC-10**: The Librarian must not re-promote observations that have already been promoted. Before promoting, it must check the promotion history and skip observation groups whose (category, recommendation) key has already been promoted for the same skill.

**AC-11**: After a successful promotion, the Librarian must mark promoted observations in the JSONL sidecar so they are no longer injected at runtime via `top_for_context()`. (Promoted observations are now part of the skill text — injecting them separately is redundant context.)

**Implementation**: Add a `promoted: bool = False` field to `SkillObservation` (models.py). Update `SkillExperienceStore.top_for_context()` to filter out observations where `promoted=True`. The marking operation rewrites the JSONL file in place (read all lines, set `promoted=True` on matching observations, write back atomically via write-to-temp + rename). Existing JSONL files without the `promoted` field remain backward-compatible: `SkillObservation(**data)` will use the default `promoted=False` since the field has a default value.

### Safety and Correctness

**AC-12**: All 1459 existing tests (as of 2026-03-12) must continue to pass after the Librarian is integrated. The Librarian must not introduce regressions in any existing component.

**AC-13**: The Librarian must be gated behind a configuration flag `skill_librarian_enabled: bool = False` (disabled by default). It must be a no-op when disabled.

**AC-14**: Promotion thresholds must be configurable via `AgentConfig`:
- `skill_librarian_enabled: bool = False`
- `skill_librarian_min_confidence: float = 0.7`
- `skill_librarian_min_observations: int = 5`
- `skill_librarian_min_runs: int = 3`

**AC-15**: The Librarian must never crash the agent. All librarian logic must be wrapped in try/except with structured logging, following the same best-effort pattern as `_maybe_learn_skill_run()`.

**AC-16**: The Librarian must use the same LLM backend dispatch pattern as `SkillDistiller` (supporting both Anthropic and local OpenAI-compatible endpoints based on `config.model_provider`).

**AC-17**: Generated sibling skill names must not collide with existing skills. The Librarian must check `registry._skills` before writing and append a numeric suffix if needed.

**AC-17a**: **Hard constraint: single-process, single-run assumption.** The automation agent executes one run at a time in a single process. Concurrent access to `skill_learning_dir` or `skills/library/` from multiple agent processes is not supported. This means: no file-level locking is required for JSONL rewrites or .md writes; no async-concurrent reads within the same event loop need to be guarded (the librarian runs post-run after all step execution is complete, so no concurrent `top_for_context()` reads can occur). If multi-process support is needed in the future, file locking must be added as a separate effort.

**AC-17b**: All file writes (both `patch_parent` to existing .md and `create_sibling` to new .md) must use atomic write-to-temp-then-rename: write to a temporary file in the same directory (e.g., `skill_name.md.tmp`), then `os.replace()` to the final path. This protects against partial writes from process crashes mid-write, not against concurrent access.

### Integration

**AC-18**: The Librarian must be invoked from a new `_maybe_promote_skill()` method in the orchestrator, called after `_maybe_learn_skill_run()` completes in both the success path and the replan path.

**Wiring**: The `SkillLibrarian` instance lives on `SkillRegistryImpl` (instantiated in `__init__` when `skill_librarian_enabled=True`, stored as `self._librarian`). The orchestrator accesses it via `getattr(self.skill_registry, "_librarian", None)`, consistent with the existing pattern for `learn_from_run` (agent.py line 2444: `getattr(self.skill_registry, "learn_from_run", None)`). The `SkillRegistry` protocol in `protocols.py` is NOT modified -- the librarian is an implementation detail of `SkillRegistryImpl`, not a protocol-level contract. If `_librarian` is `None` (learning disabled or librarian disabled), `_maybe_promote_skill()` returns immediately.

**Required changes to `_maybe_learn_skill_run()`** (agent.py line 2425):
- Change return type from `-> None` to `-> list[SkillObservation]`
- Return the `observations` list (currently used only for logging at lines 2468-2473)
- All early-exit paths must return `[]` (empty list), never `None`:
  - Line 2442: `if not skill_name or not step_results: return []`
  - Line 2445-2446: `if learn is None: return []`
  - Line 2465-2467: `except Exception: ... return []`
  - Line 2468: `if observations:` branch still logs, then falls through to `return observations`
  - After the logging block: `return observations` (or `[]` if observations was never assigned)

**Success path call site** (agent.py line ~352):
```python
# Current:
await self._maybe_learn_skill_run(goal=goal, skill_name=skill_name, ...)
# New:
observations = await self._maybe_learn_skill_run(goal=goal, skill_name=skill_name, ...)
await self._maybe_promote_skill(
    goal=goal, skill_name=skill_name, observations=observations,
    derived_session=derived_session, step_results=step_results,
    run_id=self.logger.run_id, had_replan=False, success=True,
)
```
`success=True` is hardcoded because this code path only executes on success (the preceding loop broke with all steps passing).

**Replan path call site** (agent.py line ~2394):
```python
# `success` is computed at lines 2378-2388 BEFORE this point:
#   success = last_result.success (line 2379)
#   success = False if abort (line 2388)
observations = await self._maybe_learn_skill_run(goal=goal, skill_name=skill_name, ...)
await self._maybe_promote_skill(
    goal=goal, skill_name=skill_name, observations=observations,
    derived_session=derived_session, step_results=step_results,
    run_id=self.logger.run_id, had_replan=True, success=success,
)
```
`success` comes from line 2379 (with possible override at 2388 for abort). It is available before the learn call at line 2394.

**AC-19**: The Librarian must receive: `goal`, `skill_name`, `derived_session` (DerivedSkillSession), `observations` (just-distilled list), `trace` (List[StepResult]), `run_id`, `had_replan` (bool), and `success` (bool).

**When evaluation runs**: The librarian evaluates whenever `observations` is non-empty (i.e., the distiller produced observations for this run), regardless of `success`. A failed run (`success=False`) can still produce valid observations -- for example, the distiller extracts an `anti_pattern` observation from a failure trace. The `success` flag is passed to the LLM in Call 1 as context for the promotion decision (failed runs are less likely to produce `create_sibling` candidates but may still contribute to `patch_parent` tips). If `observations` is empty, the librarian is a no-op -- there is nothing new to evaluate.

**AC-20**: The post-decision commit sequence must follow this exact order:

**For `create_sibling`:**
1. Write new .md file to `skills/library/` (atomic: temp + rename, AC-17b)
2. Call `registry.load_from_string(content)` to add the skill to `_skills` and rebuild the router. Use `load_from_string()`, NOT `load_from_directory()`, because it adds only the new skill without reloading all skills from disk.
3. If step 2 succeeds: mark observations as promoted in JSONL (AC-11)
4. If step 2 succeeds: write promotion history entry (AC-9)
5. If step 2 FAILS: delete the newly written .md file, log the error, fall back to `observation_only`. Do NOT execute steps 3-4.

**For `patch_parent`:**
1. Read the current .md content into a backup string
2. Append `## Learned Tips` text to the .md file (atomic: temp + rename, AC-17b)
3. Call `registry.load_from_string(updated_content)` to reload the modified skill and rebuild the router
4. If step 3 succeeds: mark observations as promoted in JSONL (AC-11)
5. If step 3 succeeds: write promotion history entry (AC-9)
6. If step 3 FAILS: restore the .md file from the backup string (atomic write), log the error, fall back to `observation_only`. Do NOT execute steps 4-5.

**Critical ordering invariant**: Observations must NEVER be marked as promoted unless the skill file write AND router rebuild have both succeeded. Marking first then failing on rebuild would leave observations invisible to both `top_for_context()` and the canonical library.

### LLM Call Strategy

**AC-21**: The Librarian uses TWO sequential LLM calls, not one.

**Call 1 — Decide promotion type** (cheap, ~200 output tokens): Receives the observation group summary, Bayesian score, parent skill summary, and DerivedSkillSession summary. Returns:

```json
{
  "promotion_type": "patch_parent" | "create_sibling" | "observation_only",
  "reason": "string (required, non-empty)"
}
```

If `observation_only`, stop. No second call.

**Call 2 — Generate content** (expensive, only if Call 1 chose `patch_parent` or `create_sibling`): Receives the full parent skill `raw_content`, full DerivedSkillSession serialization, all qualifying observations, and the promotion type from Call 1. Returns type-specific content:

For `patch_parent`:
```json
{
  "learned_tips": "string (markdown bullet list, required, non-empty)"
}
```

For `create_sibling`:
```json
{
  "sibling_skill_md": "string (complete .md file content with YAML frontmatter + body)"
}
```

The `sibling_skill_md` string must be a complete, self-contained `.md` file. The YAML frontmatter must include all required fields: `name`, `skill-id`, `description`, `summary`, `tags`, `trigger-keywords`, `parameters`, `requires` (with `apps` and `os`), `success-condition`, `max-retries`, and `parent-skill-id`. The body must include `## Steps` (with `- verify:` conditions on each step), `## Error Recovery`, and `## Notes` sections. The LLM prompt must explicitly list these required fields and reference the parent skill's `raw_content` as a structural template. Validation via AC-25 will catch any missing fields.

**Rationale**: Two calls avoids the speculative generation problem where the LLM invests tokens generating a full sibling .md only to decide `observation_only`. It also avoids the sunk-cost bias where the LLM prefers `create_sibling` because it already generated the content. Call 1 is lightweight (the observation summary + parent summary fit in ~500 tokens of input). Call 2 only runs when needed. Total cost for `observation_only` decisions is ~one cheap call. The `SkillDistiller` already makes one LLM call per run; adding one more for the decision is acceptable latency.

### LLM Output Validation

**AC-22**: Both LLM calls must return structured JSON as specified above.

**AC-23**: The Librarian must implement a `_parse_response()` method following the same pattern as `SkillDistiller._parse_response()`: strip markdown fences, `json.loads()`, validate required fields per promotion type, return `None` (triggering `observation_only` fallback) on parse failure. Malformed LLM output must never result in a file write.

**AC-24**: For `patch_parent`, the generated `## Learned Tips` text must be validated: non-empty, does not contain YAML frontmatter delimiters (`---`), and does not contain `## Steps` or `## Error Recovery` headings (which would corrupt section parsing in `loader.py:_extract_section()`).

**AC-25**: For `create_sibling`, the complete generated `.md` content must be validated in two steps before writing: (1) round-trip through `parse_skill_file()` to verify YAML frontmatter + body structure, then (2) pass the parsed `Skill` through `validate_skill_file()`-equivalent checks (non-empty `trigger_keywords`, non-empty `steps_text`, non-empty `success_condition`) matching the same checks in `SkillRegistryImpl.validate_all()` (registry.py lines 341-355). If either validation step fails, the promotion falls back to `observation_only` and logs a warning.

### Observability

**AC-26**: The Librarian must emit structured log events for: evaluation started, promotion decision made (with type and confidence), promotion applied, promotion skipped (with reason), LLM parse failures, and any errors.

---

## Out of Scope

- **`create_ancestor` promotion type** — Phase 5 of the design doc. Requires detecting structural commonality across multiple sibling skills. Too complex for initial implementation.
- **Human-in-the-loop approval UI** — The graduated autonomy model (manual -> semi-auto -> full-auto) is the right long-term path, but building a review UI is out of scope. The `skill_librarian_enabled=False` default and conservative thresholds serve as the gate.
- **Embedding-based retrieval** — Not needed until the library exceeds ~50 skills. The current full-card LLM routing is sufficient.
- **Post-promotion monitoring and auto-rollback** — Tracking success rate before/after promotion and auto-reverting degraded skills is valuable but deferred. Git provides manual rollback.
- **Contrastive refinement** — MACLA's pattern of comparing success vs. failure contexts before promoting is deferred to v2. v1 uses threshold-based promotion with LLM-generated text.
- **Observation pruning/decay** — Utility-based pruning (ReMe) and downvoting (ExpeL) are deferred. Observations accumulate; only promotion marks them as consumed.
- **Modifying `## Steps` or `## Error Recovery` sections** — Per the Intervention Paradox findings, programmatic edits to human-authored sections are too risky. Promotions are additive only (`## Learned Tips`).
- **Creating skills from scratch (no parent)** — The Librarian only operates when a skill was matched (`skill_name` is non-None). Runs with no matching skill produce no observations (the distiller is gated behind `if not skill_name` in `_maybe_learn_skill_run` line 2442) and therefore the librarian is never invoked. Creating a brand-new skill from repeated skillless runs (e.g., "book a flight on United" with no flight-booking skill) would require a separate `create_new` promotion type with its own observation pipeline. Deferred to a future phase.

---

## Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Promotion accuracy | >= 8:1 improvement-to-regression ratio | Track skill success rate before/after promotion over 10+ runs (per ReMe's 8.5:1 benchmark) |
| Observation-to-promotion rate | 10-30% of observation groups promoted | Count promoted vs. total observation groups per skill |
| Context token reduction | >= 20% fewer injected observations | Compare `top_for_context()` results before/after promotions mark observations consumed |
| Sibling skill creation | >= 1 sibling created after 3+ analogical runs | Count sibling skills generated in the library |
| Test suite stability | 0 regressions | All existing tests pass on every PR |
| Librarian crash rate | 0 agent crashes from librarian code | Monitor structured error logs |

---

## Architecture Summary

```
Post-run flow:

  _maybe_learn_skill_run()  [MODIFIED: returns List[SkillObservation]]
      |
      v
  SkillDistiller.distill() -> List[SkillObservation]
  SkillExperienceStore.append()
      |
      v
  _maybe_promote_skill()           [NEW - AC-18]
      |
      v
  SkillLibrarian.evaluate_run()    [NEW - AC-1]
      |-- Load all observations for skill (experience_store.load())
      |-- Group by (category, recommendation key)
      |-- Filter: already promoted? (AC-10) -> skip
      |-- Compute Bayesian score per group (AC-2)
      |-- Filter: score >= threshold AND count >= min AND runs >= min (AC-3, AC-4)
      |
      v
  LLM Call 1: Decide promotion type  [AC-21]
      |-- observation_only -> STOP, no-op
      |-- patch_parent or create_sibling -> continue
      |
      v
  LLM Call 2: Generate content  [AC-21]
      |-- patch_parent -> generate ## Learned Tips text (AC-6)
      |-- create_sibling -> generate full .md content (AC-7, AC-8)
      |
      v
  Validate output (AC-23, AC-24, AC-25)
      |-- validation fails -> fall back to observation_only, STOP
      |
      v
  Commit sequence (AC-20, strict ordering):
      1. Write file (atomic temp+rename)
      2. Load into registry + rebuild router (load_from_string)
      3. Mark observations as promoted (AC-11)
      4. Write promotion history (AC-9)
      [If step 2 fails: rollback step 1, skip steps 3-4]
      |
      v
  Log events (AC-26)
```

### New Files

- `src/automation_agent/skills/librarian.py` — `SkillLibrarian` class
- `src/automation_agent/skills/prompts/librarian_evaluate.md` — LLM prompt template
- `tests/unit/test_skill_librarian.py` — Unit tests

### Modified Files

- `src/automation_agent/config.py` — Add librarian config fields (AC-14)
- `src/automation_agent/skills/models.py` — Add `PromotionDecision` dataclass + `parent_skill_id: str = ""` and `learned_tips_text: str = ""` on `Skill` + `promoted: bool = False` on `SkillObservation` (AC-7, AC-11 prereqs)
- `src/automation_agent/skills/loader.py` — Parse `parent-skill-id` from YAML frontmatter + extract `## Learned Tips` section (AC-7 prereq)
- `src/automation_agent/skills/registry.py` — Include `learned_tips_text` in `build_runtime_context()` + instantiate librarian, expose for orchestrator
- `src/automation_agent/orchestrator/agent.py` — Add `_maybe_promote_skill()` call site after `_maybe_learn_skill_run()` (AC-18); modify `_maybe_learn_skill_run()` to return observations for forwarding
- `src/automation_agent/skills/experience.py` — Add method to mark observations as promoted (AC-11)

---

## Research References

| Concept | Source | How It Applies |
|---------|--------|----------------|
| Bayesian confidence tracking (Beta posterior) | MACLA (arxiv 2512.18950) | AC-2: Laplace-smoothed score formula |
| Minimum evidence threshold | ReMe (arxiv 2512.10696), alpha=5 | AC-3: min 5 observations, 3 runs |
| Confidence threshold for promotion | MACLA theta_conf=0.7 | AC-4: score >= 0.7 |
| Additive-only modifications | Intervention Paradox (arxiv 2602.03338) | AC-6: only append `## Learned Tips`, never edit `## Steps` |
| Improvement-to-regression ratio | ReMe: 8.5:1 | Success metrics target |
| Promotion gating | OpenAI Self-Evolving Agents Cookbook | AC-13, AC-14: config-gated, conservative defaults |
| Skill verification before promotion | Voyager (voyager.minedojo.org) | AC-7: validate with `parse_skill_file()` |
| ExpeL insight operations | ExpeL (arxiv 2308.10144) | AC-10: deduplication, promotion tracking |
| Semantic drift prevention | SOTA doc, Failure Mode #5 | AC-6: immutable `## Steps`, additive `## Learned Tips` |
| Agent lineage management | ALE (danieltan.weblog.lol) | AC-9: promotion history with lineage tracking |
| Context window pollution | Agent Drift (arxiv 2601.04170) | AC-11: mark promoted observations to stop re-injection |
| Graduated autonomy | OpenAI Cookbook | AC-13: disabled by default, thresholds configurable |
| Derived skill as promotion input | Design doc Layer 4 | AC-8, AC-19: DerivedSkillSession feeds sibling creation |
| Structured output validation | SkillDistiller._parse_response() (distiller.py) | AC-22, AC-23: same strip-fences + json.loads + validate pattern |
| Round-trip validation for generated skills | Voyager self-verification | AC-25: parse_skill_file() round-trip before write |
