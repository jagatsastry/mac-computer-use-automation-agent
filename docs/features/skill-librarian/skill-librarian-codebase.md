# Skill Librarian Codebase Analysis

Deep analysis of the existing skill learning system, written for the implementor of the Librarian/Promotion Pipeline (Layer 5 from the design doc).

---

## 1. Existing Code Components

### 1.1 SkillDistiller (`src/automation_agent/skills/distiller.py`)

**Purpose**: LLM-backed post-run analysis that extracts generalized observations from execution traces.

**Key method**: `async distill(skill, goal, skill_context, trace, run_id) -> List[SkillObservation]`

**How it works**:
1. `_build_prompt()` — substitutes `{{skill_name}}`, `{{goal}}`, `{{skill_context}}`, `{{trace}}` into the prompt template at `skills/prompts/distill_skill_updates.md`.
2. Trace is serialized as numbered lines: `1. click({element: "X"}) -> SUCCESS/FAIL` plus evidence/error/suggested_element/retry_strategies/reflection fields.
3. `_call_llm()` — dispatches to Anthropic or local OpenAI-compatible endpoint based on `config.model_provider`.
4. `_parse_response()` — expects JSON `{"observations": [...]}`. Each observation needs `category`, `condition`, `recommendation` (all non-empty) plus optional `rationale` and `confidence` (0.0-1.0). Strips markdown fences. Returns `[]` on parse failure.

**LLM Prompt** (`skills/prompts/distill_skill_updates.md`):
- Four observation categories: `alternative_path`, `checkpoint`, `user_gate`, `anti_pattern`
- Warns about being skeptical of alternative_path observations during replan runs
- Returns empty array if nothing reusable

**Landmine**: The distiller uses the same LLM backend as the vision/text models. Token limits are 1024 for distiller calls. If the trace is very long, it may be truncated by the LLM.

---

### 1.2 SkillExperienceStore (`src/automation_agent/skills/experience.py`)

**Purpose**: JSONL file-based persistence for SkillObservation objects.

**Storage format**: One JSONL file per skill at `{root}/{skill_name}.jsonl`. Skill names with `/` are converted to `_`.

**Key methods**:
- `load(skill_name) -> List[SkillObservation]` — reads all lines, parses JSON, reconstitutes `created_at` via `datetime.fromisoformat()`.
- `append(skill_name, observations) -> int` — appends observations as JSON lines. Creates parent dirs as needed. Returns count written.
- `top_for_context(skill_name, limit=5, min_confidence=0.6) -> List[SkillObservation]` — filters by min_confidence, sorts by (confidence DESC, created_at DESC), deduplicates by (category, recommendation) key (case-insensitive), returns top `limit`.

**Data shape**: Each JSONL line is `asdict(SkillObservation)` with `created_at` serialized as ISO format string.

**Integration point for librarian**: The experience store is the input substrate. The librarian will read observations via `load()` to decide promotion. It may also want to read across ALL skill observations (currently no method for this — `_path_for()` is internal).

---

### 1.3 SkillRegistryImpl (`src/automation_agent/skills/registry.py`)

**Purpose**: Central registry that loads, matches, expands, and learns from skills.

**Constructor**: Takes optional `skill_dir` (defaults to `skills/library/`) and `config`. If `config.skill_learning_enabled`, creates `SkillExperienceStore` and `SkillDistiller`.

**Key methods**:

- `load_from_directory(path)` — loads all `*.md` files, filters by OS, rebuilds router.
- `async match(prompt) -> Optional[SkillMatchResult]` — Primary path: LLM router (top-k candidates). Fallback: keyword matching. Returns `SkillMatchResult` with `skill_name`, `expanded_steps`, `skill_context`, `params`, `candidates`.
- `expand(skill_name, params) -> Optional[str]` — Single-pass `{{param}}` replacement (prevents template injection).
- `build_runtime_context(skill_name, params) -> Optional[str]` — Builds rich context: skill metadata + expanded steps + error recovery + notes + observed variants from experience store.
- `async learn_from_run(skill_name, goal, trace, *, skill_context, run_id, had_replan) -> List[SkillObservation]` — Calls distiller, caps confidence to 0.6 if `had_replan=True`, persists via experience store.
- `_trace_deserves_learning(trace)` — Static method. Returns True if trace has retry_strategies, suggested_element, reflection hints, or wait_for_user actions.
- `_load_observations_for_context(skill_name)` — Loads top observations via experience store, capped by `config.skill_learning_max_observations`.

**`_build_multi_skill_context(candidates, primary_params)`**: Builds multi-skill context with headers like `## Skill Priors`, section separators `---`, and match type labels (`[direct]`, `[analogical]`, `[generic]`).

**Integration point for librarian**: The librarian will need access to:
- `self._skills` (all loaded skills)
- `self._experience_store` (observations)
- `DerivedSkillSession` data (passed from orchestrator after run)
- The execution trace (`List[StepResult]`)

The librarian could be called from a new method `promote_from_run()` on the registry, or as a standalone component invoked by the orchestrator.

---

### 1.4 Skill Data Model (`src/automation_agent/skills/models.py`)

```python
@dataclass
class SkillParam:
    type: str
    required: bool = True
    description: str = ""
    examples: List[str] = field(default_factory=list)

@dataclass
class SkillRequirements:
    apps: List[str] = field(default_factory=list)
    os: str = ""

@dataclass
class Skill:
    name: str
    description: str
    trigger_keywords: List[str]
    parameters: Dict[str, SkillParam]
    requires: SkillRequirements
    success_condition: str
    max_retries: int = 3
    steps_text: str = ""
    error_recovery_text: str = ""
    notes_text: str = ""
    raw_content: str = ""
    skill_id: str = ""          # From frontmatter `skill-id`
    tags: List[str] = field(default_factory=list)
    summary: str = ""           # Abstraction-first description for routing

@dataclass
class SkillCard:
    skill_id: str
    title: str
    summary: str
    tags: list[str]
    required_apps: list[str]
    required_os: str
    param_names: list[str] = field(default_factory=list)

@dataclass
class SkillObservation:
    category: str               # alternative_path | checkpoint | user_gate | anti_pattern
    condition: str              # "When this situation holds"
    recommendation: str         # "Reusable guidance for future runs"
    rationale: str = ""
    confidence: float = 0.0     # 0.0 = unknown, 1.0 = certain
    run_id: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
```

**Librarian implication**: The `Skill` dataclass already has `skill_id`, `tags`, and `summary` — all fields needed for the librarian to reason about skill lineage. But there is no `parent_skill_id` field yet. The design doc recommends adding optional frontmatter for `parent-skill-id`, `domains`, `intents`, `sites`, `status`.

---

### 1.5 DerivedSkillSession (`src/automation_agent/skills/derived_skill.py`)

**Purpose**: Run-local derived procedure. In-memory only, discarded after run.

```python
@dataclass
class DerivedSkillSession:
    parent_skill_ids: list[str]
    match_types: list[str]
    current_steps: str
    replaced_labels: list[dict]          # [{"old": "X", "new": "Y", "reason": "..."}]
    discovered_landmarks: list[str]
    verification_notes: list[str]
    failed_assumptions: list[str]
    successful_adaptations: list[str]
```

**Key methods**:
- `seed(parent_skill_ids, match_types, steps_text)` — Factory: creates session from routing candidates.
- `apply_patch(patch: ReplanPatch)` — Merges replan corrections. Deduplicates labels by `old` key. Uses set semantics for string lists. All lists capped at 20 items.
- `serialize_for_context()` — Renders to markdown string with sections: "## Derived Procedure (run-local, current best hypothesis)", Label Replacements, Discovered Landmarks, Verification Notes, Failed Assumptions, Successful Adaptations.

**Integration point for librarian**: This is the KEY input. After a run, the derived session contains:
- Which parent skills were used
- What labels were replaced (site-specific adaptations)
- What landmarks were discovered
- What assumptions failed
- What adaptations succeeded
- The final step sequence

The librarian should receive the DerivedSkillSession along with the execution trace and observations.

---

### 1.6 ReplanPatch (`src/automation_agent/shared_models.py`)

```python
@dataclass
class ReplanPatch:
    replace_labels: List[Dict[str, str]]     # [{"old": "X", "new": "Y", "reason": "..."}]
    add_landmarks: List[str]
    verify_improvements: List[str]
    failed_assumptions: List[str]
    successful_adaptations: List[str]
    revised_steps: str = ""
```

Parsed from `derived_skill_patch` key in the LLM replan response. `from_dict()` is tolerant of missing/malformed fields.

---

### 1.7 Orchestrator Integration (`src/automation_agent/orchestrator/agent.py`)

**`execute(goal)` flow** (lines 83-386):
1. Match skill via `skill_registry.match(goal)` -> creates `DerivedSkillSession.seed()` from candidates
2. Appends `derived_session.serialize_for_context()` to `skill_context`
3. Plans via `planner.plan(goal, skill_context=...)`
4. Executes steps in loop with verification
5. On failure -> `_replan_and_continue()`
6. On success -> `_maybe_learn_skill_run()`

**`_replan_and_continue()` flow** (lines 2221-2423):
1. Re-assembles skill_context with updated derived procedure (strips old, appends fresh)
2. Collects absent elements from step results
3. Calls `planner.replan()` with updated context
4. Applies `new_plan.replan_patch` to `derived_session` via `derived_session.apply_patch()`
5. Executes new plan steps
6. Calls `_maybe_learn_skill_run()` with `had_replan=True`

**`_maybe_learn_skill_run()` flow** (lines 2425-2473):
1. Guards: returns if no skill_name, no step_results, no `learn_from_run` method on registry
2. Builds distiller-specific context: `expanded_steps_for_distiller` + derived_session serialization (NOT the full multi-skill context)
3. Calls `registry.learn_from_run()` with the built context
4. Logs observation count

**Integration point for librarian**: The librarian should be called AFTER `_maybe_learn_skill_run()` in both success and replan paths. It needs:
- `goal` (original prompt)
- `skill_name` (if matched)
- `derived_session` (run-local corrections)
- `step_results` (full trace)
- `had_replan` (bool)
- `run_id`
- The observations that were just distilled

A natural insertion point would be a new `_maybe_promote_skill()` method called right after `_maybe_learn_skill_run()`.

---

### 1.8 SkillRouter (`src/automation_agent/skills/router.py`)

Top-k routing via LLM. Returns `SkillRouteResult` with up to 3 `SkillRouteCandidate` objects. Each candidate has `skill_id`, `match_type` (direct/analogical/generic), `confidence` (0-1), `reason`.

Prompt template at `skills/prompts/route_skill.md` asks for JSON with `matches` array.

`MIN_USEFUL_CONFIDENCE = 0.5` — candidates below this are filtered out.

---

### 1.9 SkillCardBuilder (`src/automation_agent/skills/card_builder.py`)

Builds `SkillCard` objects from `Skill` dataclasses. Cards are computed on-the-fly, not persisted. Used by the router to build compact LLM prompts.

---

### 1.10 Skill Loader (`src/automation_agent/skills/loader.py`)

Parses YAML frontmatter + Markdown body. Extracts `## Steps`, `## Error Recovery`, `## Notes` sections. Handles optional adaptive-skill fields: `skill-id`, `tags`, `summary`.

---

### 1.11 Config (`src/automation_agent/config.py`)

Relevant fields:
```python
skill_learning_enabled: bool = True      # Master switch
skill_learning_dir: Path = Path("logs/skill_learning")  # JSONL observation store
skill_learning_max_observations: int = 5  # Cap for context injection
```

The librarian would likely need its own config fields, e.g.:
- `skill_librarian_enabled: bool`
- `skill_promotion_min_confidence: float`
- `skill_promotion_min_runs: int`

---

## 2. Existing Skill Library

7 skills in `src/automation_agent/skills/library/`:
- `return_amazon_order.md` — ecommerce return (most complex, 9 steps)
- `google_search.md` — web search (3 steps)
- `amazon_search.md` — Amazon product search
- `send_imessage.md` — send iMessage
- `restaurant_opentable.md` — OpenTable reservation (10 steps)
- `restaurant_yelp.md` — Yelp reservation
- `restaurant_google.md` — Google Maps reservation

All skills have:
- `skill-id` and `summary` fields (adaptive-skill ready)
- `tags` array
- Error recovery section
- Numbered steps with `- verify:` conditions

---

## 3. Existing Observations

No observations JSONL files exist in `logs/skill_learning/` at the moment. The directory may not have been created yet (it's created on first `append()` call).

---

## 4. Skill File Format

```yaml
---
name: skill-name
skill-id: skill-name                    # Optional, defaults to name
description: Short description
summary: Abstraction-first description  # For routing cards
tags: [tag1, tag2]                      # For routing
trigger-keywords: [kw1, kw2]
parameters:
  param_name:
    type: string
    required: true
    description: What it is
    examples: ["example1"]
requires:
  apps: [Safari]
  os: darwin
success-condition: What success looks like
max-retries: 3
---

## Steps
1. Do thing
   - verify: Thing done

## Error Recovery
- If X: do Y

## Notes
- Additional notes
```

---

## 5. Data Flow Diagram

```
User prompt
  -> SkillRouter.route() -> SkillRouteResult (top-k candidates)
  -> SkillRegistryImpl.match() -> SkillMatchResult
  -> DerivedSkillSession.seed() (from candidates)
  -> Planner.plan() (with skill_context + derived procedure)
  -> Execute loop
     -> On failure: Planner.replan() -> ReplanPatch
     -> DerivedSkillSession.apply_patch()
  -> _maybe_learn_skill_run()
     -> SkillDistiller.distill() -> List[SkillObservation]
     -> SkillExperienceStore.append()
  -> [LIBRARIAN GOES HERE]
     -> Reads: DerivedSkillSession, observations, trace, goal
     -> Decides: patch_parent | create_sibling | create_ancestor | episodic_only | discard
```

---

## 6. Test Infrastructure

### Unit Tests
- `tests/unit/test_skill_registry.py` — 15+ tests covering: loading, parsing, expansion, match (router + keyword fallback), validation, multi-skill context, template injection prevention.
- `tests/unit/test_skill_learning.py` — 11 tests covering: runtime context with observations, learn_from_run persistence, distiller parse/validate, confidence capping on replan, replan prompt strength, error recovery hint integration.

### Integration Tests
- `tests/integration/test_skill_learning_integration.py` — 2 tests: learned observations flowing into replan prompt, replanned run persisting observations via full orchestrator flow.

### Test Patterns
- Fixtures in `tests/conftest.py`: `mock_planner`, `mock_coordinator`, `mock_actuator`, `mock_skill_registry`, `tmp_log_dir`, `tmp_skill_dir`.
- Config fixture pattern: `AgentConfig(_env_file=None, anthropic_api_key="test-key-not-real", skill_learning_dir=tmp_path/"skill-learning")`.
- Registry fixture pattern: create tmp skill dir, write skill .md file, create `SkillRegistryImpl(skill_dir=..., config=...)`.
- Distiller mocking: `registry._distiller = AsyncMock()`, set `distill.return_value`.
- Router mocking: `registry._router = AsyncMock()`, set `route.return_value`.

### Key test gotcha
From memory: `.env` leaks `AGENT_MODEL_PROVIDER=anthropic` into pydantic-settings. Pin `model_provider="local"` in `_make_config()` in test files.

---

## 7. Integration Points for the Librarian

### Where it plugs in

1. **Call site**: After `_maybe_learn_skill_run()` in `orchestrator/agent.py` (both success path ~line 361 and replan path ~line 2402).

2. **New file**: `src/automation_agent/skills/librarian.py` (as recommended in design doc line 970).

3. **Registry integration**: The librarian needs access to the skill registry to:
   - Read existing skills (`registry.get_skill()`, `registry._skills`)
   - Write new skill files (via file system, then `registry.load_from_directory()` or `registry.load_from_string()`)
   - Read observations (`registry._experience_store.load()`)

4. **Config**: New fields on `AgentConfig` for librarian settings.

### What it receives

```python
# Proposed interface
class SkillLibrarian:
    async def evaluate_run(
        self,
        goal: str,
        skill_name: Optional[str],
        derived_session: Optional[DerivedSkillSession],
        observations: List[SkillObservation],
        trace: List[StepResult],
        run_id: str,
        had_replan: bool,
        success: bool,
    ) -> PromotionDecision:
        ...
```

### What it produces

From the design doc (lines 785-820), allowed outputs:
- **patch_parent**: Modify the parent skill's error recovery, notes, or steps
- **create_sibling**: Write a new skill .md file derived from the parent
- **create_ancestor**: Write a more abstract skill that generalizes multiple siblings
- **episodic_only**: Keep observations, don't touch the library
- **discard**: Do nothing

### Storage locations (from design doc)
- Canonical skills: `src/automation_agent/skills/library/`
- Generated skill cards: `logs/skill_learning/cards/`
- Episodic traces and candidates: `logs/skill_learning/episodes/`
- Promotion queue: `logs/skill_learning/promotions/`

---

## 8. Patterns to Follow

1. **LLM-backed decisions**: Use the same `_call_llm()` pattern as SkillDistiller — support both Anthropic and local backends.
2. **Prompt template**: Put the librarian prompt in `src/automation_agent/skills/prompts/` as a .md file with `{{}}` placeholders.
3. **JSON output parsing**: Use the same `_parse_response()` pattern — strip markdown fences, `json.loads()`, validate required fields, return empty/None on failure.
4. **Dataclass for decisions**: Create a `PromotionDecision` dataclass in `models.py`.
5. **Config gating**: Add `skill_librarian_enabled` to `AgentConfig`, check before running.
6. **Logging**: Use `structlog.get_logger(__name__)` for structured logging.
7. **Best-effort**: Wrap in try/except like `_maybe_learn_skill_run()` — never let librarian failures crash the agent.
8. **Test patterns**: Mock the LLM, assert on the prompt content, verify file output.

---

## 9. Landmines and Constraints

1. **Hard Invariant #1**: Canonical markdown skills are NEVER mutated during a live run. Librarian runs AFTER the run completes.

2. **Hard Invariant #5**: Promotion into the canonical library happens after a run, never during a run.

3. **Confidence capping**: Observations from replan runs have confidence capped at 0.6 (in `learn_from_run`). The librarian should weight these lower.

4. **No observations yet**: The JSONL store may be empty for most skills. The librarian needs a minimum evidence threshold before promoting.

5. **DerivedSkillSession is in-memory only**: It's discarded after the run. If the librarian needs it, it must be captured before the orchestrator returns. Currently `_maybe_learn_skill_run` receives it but doesn't persist it.

6. **File writing safety**: If creating sibling skills, the librarian must write valid YAML frontmatter + Markdown. Use the same format as existing skills. Validate with `parse_skill_file()` before persisting.

7. **Router rebuild**: After writing a new skill file to `library/`, the registry's router must be rebuilt (`_rebuild_router()`). This happens automatically in `load_from_directory()` and `load_from_string()`.

8. **OS gating**: New skills must have `requires.os` set correctly or they'll be loaded on all platforms.

9. **Skill name uniqueness**: The registry warns on duplicates but the last-loaded wins. Generated skill names must not collide.

10. **Token budget**: The router prompt includes ALL skill cards. The design doc warns at 25 skills (line 90-93). The librarian should not create too many skills.

---

## 10. Recommendation

**PROCEED**

The codebase is well-structured for the librarian addition:
- Clear insertion points exist (after `_maybe_learn_skill_run()`)
- Data shapes are defined (DerivedSkillSession, SkillObservation, StepResult)
- The LLM calling pattern is established and can be reused
- Test infrastructure is solid with good fixture patterns
- The design doc (Layer 5, lines 584-614) provides clear requirements

The main implementation work is:
1. `SkillLibrarian` class with LLM prompt for promotion decisions
2. `PromotionDecision` dataclass
3. Skill file generation (for siblings/ancestors)
4. Orchestrator integration (new `_maybe_promote_skill()` call)
5. Config fields
6. Tests
