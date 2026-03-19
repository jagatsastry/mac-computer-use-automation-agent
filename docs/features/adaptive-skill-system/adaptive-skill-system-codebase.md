# Adaptive Skill System: Codebase Analysis

This document is a complete analysis of the existing codebase relevant to implementing the Adaptive Skill System MVP described in `docs/features/adaptive-skill-system/adaptive-skill-system.md`.

---

## 1. Skills System

### 1.1 File Inventory

| File | Purpose |
|------|---------|
| `src/automation_agent/skills/__init__.py` | Re-exports: `ExpandedSkill`, `Skill`, `SkillObservation`, `SkillParam`, `SkillRegistryImpl`, `SkillRequirements` |
| `src/automation_agent/skills/models.py` | Dataclasses: `Skill`, `SkillParam`, `SkillRequirements`, `ExpandedSkill`, `SkillObservation` |
| `src/automation_agent/skills/registry.py` | `SkillRegistryImpl` -- main registry, implements `SkillRegistry` protocol |
| `src/automation_agent/skills/router.py` | `SkillRouter` -- LLM-driven single-skill routing |
| `src/automation_agent/skills/matcher.py` | `match_skill()` -- keyword fallback (no LLM) |
| `src/automation_agent/skills/loader.py` | `parse_skill_file()`, `load_skill_from_file()` -- YAML frontmatter parser |
| `src/automation_agent/skills/distiller.py` | `SkillDistiller` -- post-run LLM distillation of `SkillObservation` |
| `src/automation_agent/skills/experience.py` | `SkillExperienceStore` -- JSONL sidecar persistence for observations |
| `src/automation_agent/skills/self_test.py` | CLI self-test (sync, not used in production) |
| `src/automation_agent/skills/prompts/route_skill.md` | Router prompt template (single-skill output) |
| `src/automation_agent/skills/prompts/distill_skill_updates.md` | Distiller prompt template |

### 1.2 Skill Data Model (`models.py`)

```python
@dataclass
class Skill:
    name: str
    description: str
    trigger_keywords: List[str]
    parameters: Dict[str, SkillParam]
    requires: SkillRequirements      # apps, os
    success_condition: str
    max_retries: int = 3
    steps_text: str = ""             # Markdown body: Steps section
    error_recovery_text: str = ""    # Markdown body: Error Recovery section
    notes_text: str = ""             # Markdown body: Notes section
    raw_content: str = ""            # Full original file content

@dataclass
class SkillObservation:
    category: str          # "alternative_path" | "checkpoint" | "user_gate" | "anti_pattern"
    condition: str
    recommendation: str
    rationale: str = ""
    confidence: float = 0.0
    run_id: str = ""
    created_at: datetime
```

**Key point for MVP**: The `Skill` dataclass has no `skill_id`, `parent_skill_id`, `domains`, `intents`, `sites`, `tags`, or `summary` fields. These must be added (to the frontmatter schema and/or to a new `SkillCard` dataclass) for the MVP skill card system.

### 1.3 SkillRegistryImpl (`registry.py`)

This is the main entry point. Key methods:

| Method | Signature | What it does |
|--------|-----------|-------------|
| `match()` | `async (prompt) -> Optional[Dict]` | Routes prompt to a skill. Returns `{"skill_name", "expanded_steps", "skill_context", "params"}` or `None`. |
| `expand()` | `(skill_name, params) -> Optional[str]` | Substitutes `{{param}}` placeholders in steps_text. |
| `build_runtime_context()` | `(skill_name, params) -> Optional[str]` | Builds rich context string with steps, recovery, notes, and learned observations. |
| `learn_from_run()` | `async (skill_name, goal, trace, ...) -> List[SkillObservation]` | Post-run distillation via `SkillDistiller`, persisted via `SkillExperienceStore`. |
| `list_skills()` | `() -> List[Dict[str,str]]` | Returns `[{"name", "description"}]` for all skills. |
| `get_skill()` | `(skill_name) -> Optional[Skill]` | Direct access to loaded skill. |

**Current `match()` return shape** (the shape that the orchestrator consumes):
```python
{
    "skill_name": str,
    "expanded_steps": str,       # Steps with params substituted
    "skill_context": str,        # Rich context: steps + recovery + notes + observations
    "params": Dict[str, str],
}
```

**MVP change needed**: `match()` must return multiple candidates with match type labels. The orchestrator currently reads `skill_match["skill_name"]`, `skill_match.get("skill_context")`, and `skill_match.get("expanded_steps")`. All three callers must be updated.

### 1.4 SkillRouter (`router.py`)

Single-skill LLM router. Key details:

- **Prompt template** (`prompts/route_skill.md`): Sends ALL skills as a markdown summary, asks for ONE `{"skill_name", "params"}` JSON response.
- **`_build_skills_summary()`**: Iterates `self.skills.values()`, formats each as `### name\ndescription\nParameters:...`. This is what the MVP spec calls a "skill card" -- but currently it's generated inline, not stored.
- **`_parse_response()`**: Expects `{"skill_name": str|null, "params": {}}`. Returns `None` on parse failure or unknown skill name.
- **LLM backends**: `_call_local()` (OpenAI-compatible via httpx) or `_call_anthropic()` (Anthropic SDK).
- **Max tokens**: 512.

**MVP change needed**: The router prompt and parse logic must be updated to return top-k candidates with match_type labels (`direct`, `analogical`, `fallback`). The `_build_skills_summary()` method should be replaced by compact skill cards.

### 1.5 SkillDistiller (`distiller.py`)

Post-run learning via LLM. Key details:

- **Prompt template** (`prompts/distill_skill_updates.md`): Given skill name, goal, canonical skill context, and execution trace, extract generalizable `SkillObservation` items.
- **`distill()`**: Builds prompt, calls LLM, parses JSON response.
- **`_build_prompt()`**: Formats trace as numbered lines with action, success/fail, evidence, error, suggested_element, retry_strategies, reflection hints.
- **`_parse_response()`**: Expects `{"observations": [{"category", "condition", "recommendation", "rationale", "confidence"}]}`.
- **Output categories**: `alternative_path`, `checkpoint`, `user_gate`, `anti_pattern`.

**MVP compatibility**: The distiller already works for post-run observation extraction. It does NOT need changes for MVP Phase 1. However, the MVP adds a new concern: the replan should also produce a "derived skill patch" alongside revised steps. This is a planner-side change, not a distiller change.

### 1.6 SkillExperienceStore (`experience.py`)

JSONL sidecar file persistence. Key details:

- **Storage**: One `.jsonl` file per skill in `config.skill_learning_dir` (default `logs/skill_learning/`).
- **`append(skill_name, observations)`**: Appends JSONL lines.
- **`top_for_context(skill_name, limit=5, min_confidence=0.6)`**: Returns highest-confidence unique observations for injection into planning context.
- **Deduplication**: By `(category, recommendation)` tuple.

**MVP compatibility**: Works as-is. The derived skill session is a separate in-memory object, not persisted via this store.

### 1.7 Skill Library Files

8 skills in `src/automation_agent/skills/library/`:

| File | Name | Trigger Keywords |
|------|------|-----------------|
| `return_amazon_order.md` | `return-amazon-order` | return, send back, refund, amazon |
| `open_app_and_navigate.md` | `open-app-and-navigate` | open, launch, go to, navigate |
| `google_search.md` | `google-search` | (not read, likely google, search) |
| `send_imessage.md` | `send-imessage` | (not read, likely message, imessage) |
| `amazon_search.md` | `amazon-search` | (likely amazon, search) |
| `restaurant_opentable.md` | `restaurant-opentable` | (restaurant, opentable) |
| `restaurant_yelp.md` | `restaurant-yelp` | (restaurant, yelp) |
| `restaurant_google.md` | `restaurant-google` | (restaurant, google) |

**Frontmatter schema today**:
```yaml
name: return-amazon-order
description: Return an item or package on Amazon
trigger-keywords: [return, send back, refund, amazon]
parameters: { item: { type: string, required: true, description: ..., examples: [...] } }
requires: { os: darwin }
success-condition: Return confirmation with label or drop-off instructions visible
max-retries: 3
```

**MVP addition needed**: Add optional frontmatter fields: `skill-id`, `tags`, `summary` (abstraction-first). These are used to build skill cards. Existing skills should be enriched with these fields.

---

## 2. Planner

### 2.1 ActionPlannerImpl (`planner/planner.py`)

| Method | What it does |
|--------|-------------|
| `plan(goal, screen_description, skill_context, desktop_context)` | Initial planning. `skill_context` is an optional string. |
| `replan(goal, screen_description, history, retry_strategies_used, desktop_context, skill_context)` | Replanning after failure. Same `skill_context` parameter. |

**How skill_context flows in**:
- `_build_plan_prompt()` substitutes `{{skill_context}}` placeholder with the string or `"No skill context available"`.
- `_build_replan_prompt()` does the same.
- The planner does NOT distinguish between "direct skill" and "analogical prior" -- it gets a single flat string.

**MVP changes needed**:
1. `skill_context` must support multiple skills with match_type labels.
2. The plan prompt template (`plan_from_prompt.md`) needs a new section for multi-skill context with direct/analogical guidance.
3. The replan prompt template (`replan_from_state.md`) needs to accept a derived procedure and return both revised steps AND a derived skill patch.
4. `_parse_plan_response()` may need to parse both `steps` and `derived_skill_patch` from the replan response.

### 2.2 Plan Prompt Template (`planner/prompts/plan_from_prompt.md`)

Structure:
```
User Goal: {{goal}}
{{desktop_context}}
Current Screen State: {{screen_description}}
Skill Context (if available): {{skill_context}}
Available Actions: [activate_app, click, type_text, press_key, open_url, quit_app, observe, wait_for_user, done]
CRITICAL RULES: verify fields, expected_observation, on_fail
Response Format: JSON {"steps": [...]}
```

**MVP change**: Add a structured "Skill Priors" section that distinguishes direct vs analogical matches, and includes the derived procedure if one exists.

### 2.3 Replan Prompt Template (`planner/prompts/replan_from_state.md`)

Structure:
```
Original Goal: {{goal}}
{{desktop_context}}
Skill Context (if available): {{skill_context}}
Current Screen State: {{screen_description}}
Execution History: {{history}}
Strategies Already Tried: {{retry_strategies}}
CRITICAL: Try DIFFERENT approach
Response Format: same JSON
```

**MVP change**: Add `{{derived_procedure}}` placeholder. Instruct the LLM to also return `derived_skill_patch` alongside `steps`. The parse logic must handle both.

---

## 3. Orchestrator

### 3.1 AutomationAgent (`orchestrator/agent.py`)

This is the main orchestrator -- 1909 lines. Key flow:

```
execute(goal):
  1. skill_match = await skill_registry.match(goal)    # lines 85-101
  2. screen_desc = await coordinator.describe_screen()  # lines 103-115
  3. plan = await planner.plan(goal, ...)               # lines 118-146
  4. for step in plan.steps:                            # lines 168-279
       result = await _execute_step(...)
       if not result.success:
         recovery = await _handle_failure(...)
         if recovery is None:
           return await _replan_and_continue(...)
  5. await _maybe_learn_skill_run(...)                  # lines 247-253
```

**How skill context enters the orchestrator**:
- Line 86-97: `skill_match = await self.skill_registry.match(goal)` returns the dict.
- Line 89: `skill_context = skill_match.get("skill_context") or skill_match.get("expanded_steps")`
- Line 121-125: `skill_context` is passed to `planner.plan()`.
- Line 1816-1822: `skill_context` is passed to `planner.replan()` in `_replan_and_continue()`.
- Line 247-253: `skill_name` and `skill_context` are passed to `_maybe_learn_skill_run()`.

**MVP changes needed**:
1. `execute()` must handle the new multi-match return shape from `match()`.
2. A `DerivedSkillSession` object must be created after match, seeded from the best parent skill.
3. `skill_context` passed to planner must include all top-k skills + the derived procedure.
4. `_replan_and_continue()` must update the derived procedure with the replan patch.
5. `_maybe_learn_skill_run()` already works -- just needs the derived procedure passed through for richer distillation.

### 3.2 Key Orchestrator Methods for MVP Integration

| Method | Line | MVP Integration Point |
|--------|------|-----------------------|
| `execute()` | 71 | Create `DerivedSkillSession` after match |
| `_replan_and_continue()` | 1794 | Update derived procedure from replan patch |
| `_maybe_learn_skill_run()` | 1876 | Pass derived procedure for richer distillation |
| `_handle_failure()` | 1607 | No change needed |
| `_build_skill_fallback_plan()` | 439 | May need update if skill_context format changes |

### 3.3 StepVerifier (`orchestrator/verifier.py`)

Three-tier verification (Tier 0: Accessibility, Tier 1: Actuator state, Tier 2: Vision). No changes needed for MVP.

### 3.4 GroundingRouter (`orchestrator/grounding_router.py`)

Mixture-of-Grounding for element finding. No changes needed for MVP.

---

## 4. Shared Models and Protocols

### 4.1 Key Dataclasses (`shared_models.py`)

| Class | Fields | MVP Impact |
|-------|--------|------------|
| `ActionStep` | action, params, verify, expected_observation, on_fail, max_retries | No change |
| `ActionPlan` | steps, goal, skill_name, raw_llm_response, planning_duration_ms, token_usage | No change |
| `StepResult` | step, success, verification_method, evidence, error, retry_strategies_used, reflection_hint, reflection_observed, suggested_element | No change |
| `ExecutionResult` | success, message, steps, error, total_duration_ms, iterations, goal, run_id | No change |
| `FindElementResult` | x, y, confidence, source, raw_response, screen_x, screen_y, image_width, image_height | No change |

### 4.2 Protocols (`protocols.py`)

| Protocol | Key Methods | MVP Impact |
|----------|------------|------------|
| `ActionPlanner` | `plan()`, `replan()` | `skill_context` parameter type unchanged (still `Optional[str]`), but content becomes richer |
| `ScreenCoordinator` | `find_element()`, `describe_screen()`, `verify_condition()`, `capture_screenshot()` | No change |
| `Actuator` | `click()`, `type_text()`, `press_key()`, etc. | No change |
| `SkillRegistry` | `match()`, `list_skills()`, `expand()`, `validate_all()` | `match()` return type changes |
| `Verifier` | `verify()` | No change |

**MVP protocol change**: `SkillRegistry.match()` currently returns `Optional[Dict[str, Any]]`. The return shape changes but the type signature stays the same (still a dict). The orchestrator must handle new keys.

### 4.3 AgentConfig (`config.py`)

Relevant config fields for skills:

| Field | Default | Description |
|-------|---------|-------------|
| `skill_library_path` | `None` (bundled) | Path to skill library directory |
| `skill_learning_enabled` | `True` | Enable post-run observation learning |
| `skill_learning_dir` | `logs/skill_learning` | Directory for learned observations |
| `skill_learning_max_observations` | `5` | Max observations injected into context |

**MVP addition needed**: No new config fields strictly required. The skill card cache can be derived from `skill_learning_dir`. If needed later, add `skill_card_cache_dir`.

---

## 5. Test Infrastructure

### 5.1 Test Configuration (`pyproject.toml`)

- `asyncio_mode = "auto"` -- all async tests run automatically.
- Markers: `unit`, `integration`, `e2e`, `manual`, `legacy`.
- `addopts = ["-v", "--strict-markers"]`.

### 5.2 Fixtures (`tests/conftest.py`)

| Fixture | What it provides |
|---------|-----------------|
| `mock_planner` | `AsyncMock` with `plan()` and `replan()` returning valid `ActionPlan` |
| `mock_coordinator` | `AsyncMock` with `find_element()`, `describe_screen()`, `verify_condition()`, `capture_screenshot()` |
| `mock_actuator` | `MagicMock` with all actuator methods returning `{"success": True}` |
| `mock_skill_registry` | `MagicMock` with `match()` returning `None`, `expand()` returning `None` |
| `mock_verifier` | `AsyncMock` with `verify()` returning success `StepResult` |
| `tmp_log_dir` | Temporary directory for event logs |
| `tmp_skill_dir` | Temporary directory for skill files |
| `sample_action_step`, `sample_action_plan`, `sample_step_result`, `sample_failed_step_result` | Pre-built test data |

### 5.3 Test Patterns

**Pattern 1: Config creation** (critical gotcha):
```python
# ALWAYS use _env_file=None to prevent .env leaking AGENT_MODEL_PROVIDER
config = AgentConfig(_env_file=None, anthropic_api_key="test-key-not-real")
```

**Pattern 2: Mock LLM response**:
```python
def _make_llm_response(steps_data: list) -> dict:
    return {
        "content": json.dumps({"steps": steps_data}),
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))
```

**Pattern 3: Mock router**:
```python
registry._router = AsyncMock()
registry._router.route.return_value = {
    "skill_name": "return-amazon-order",
    "params": {"item": "Tylenol"},
}
```

**Pattern 4: Agent creation** (`test_orchestrator_new.py`):
```python
agent = AutomationAgent(
    planner=mock_planner,
    skill_registry=mock_skill_registry,
    coordinator=mock_coordinator,
    actuator=mock_actuator,
    config=config,
    logger=EventLogger(tmp_log_dir),
)
```

**Pattern 5: Skill registry with real loading** (`test_skill_learning.py`):
```python
skill_dir = tmp_path / "skills"
skill_dir.mkdir()
(skill_dir / "return_amazon_order.md").write_text(SAMPLE_SKILL)
registry = SkillRegistryImpl(skill_dir=skill_dir, config=config)
```

**Pattern 6: Inline skill definition**:
```python
SAMPLE_SKILL = textwrap.dedent("""\
    ---
    name: return-amazon-order
    description: Return an item or package on Amazon
    trigger-keywords: [return, amazon]
    parameters:
      item:
        type: string
        required: true
        description: What to return
    requires:
      os: darwin
    success-condition: Return confirmation visible
    ---
    ## Steps
    1. Open orders page
       - verify: Orders page visible
    ...
""")
```

### 5.4 Existing Test Files

| File | Tests | What it covers |
|------|-------|---------------|
| `tests/unit/test_planner.py` | ~15 | plan(), replan(), prompt building, response parsing, action aliases |
| `tests/unit/test_orchestrator_new.py` | ~28 | Full execute() flow, skill matching, element finding, verification, replanning, failure handling |
| `tests/unit/test_coordinator.py` | ~20 | Vision coordinator, coordinate parsing, screenshot handling |
| `tests/unit/test_verifier.py` | ~15 | Three-tier verification, edge cases |
| `tests/unit/test_skill_learning.py` | ~5 | Runtime context building, learn_from_run, distiller parsing |
| `tests/orchestrator/test_grounding_router.py` | ~20 | Grounding strategy classification, fallback chains |
| `tests/integration/test_skill_learning_integration.py` | 2 | End-to-end: observations flow into replan prompt, replanned run persists observations |
| `tests/integration/test_accuracy_architecture_integration.py` | ? | Vision arch improvements |
| `tests/integration/test_status_and_skill_fallback_integration.py` | ? | Status overlay, skill fallback plans |

---

## 6. Integration Points for MVP

### 6.1 New Files to Create

| File | Purpose |
|------|---------|
| `src/automation_agent/skills/card_builder.py` | `SkillCardBuilder` -- generates compact routing cards from `Skill` objects |
| `src/automation_agent/skills/derived_skill.py` | `DerivedSkillSession` -- in-memory run-local derived procedure |
| `tests/unit/test_skill_cards.py` | Unit tests for card builder |
| `tests/unit/test_derived_skill.py` | Unit tests for derived skill session |

### 6.2 Files to Modify

| File | Change |
|------|--------|
| `src/automation_agent/skills/models.py` | Add `SkillCard` dataclass, optional frontmatter fields on `Skill` |
| `src/automation_agent/skills/router.py` | Return top-k candidates with match_type labels |
| `src/automation_agent/skills/prompts/route_skill.md` | Update to request top-3 with match_type and confidence |
| `src/automation_agent/skills/registry.py` | Update `match()` return shape, build multi-skill context |
| `src/automation_agent/planner/prompts/plan_from_prompt.md` | Add multi-skill priors section |
| `src/automation_agent/planner/prompts/replan_from_state.md` | Add derived procedure, request patch output |
| `src/automation_agent/planner/planner.py` | Handle multi-skill context assembly, parse replan patch |
| `src/automation_agent/orchestrator/agent.py` | Create/update derived procedure, pass through context |
| `src/automation_agent/protocols.py` | No signature changes needed (dict return type is flexible) |
| `src/automation_agent/skills/library/*.md` | Add `skill-id`, `tags`, `summary` to frontmatter |

### 6.3 Backward Compatibility Constraints

1. **`skill_context` is a string**: The planner protocol takes `skill_context: Optional[str]`. The MVP must assemble all context (multiple skills, match types, derived procedure) into a single string. Do NOT change the protocol signature.
2. **`match()` returns `Optional[Dict]`**: The return dict shape changes, but the type stays the same. The orchestrator must handle both old and new keys gracefully during transition.
3. **Existing tests**: 500+ tests pass today. New code must not break them. Use `_env_file=None` in all test configs.
4. **Skill frontmatter**: New optional fields (`skill-id`, `tags`, `summary`) must be backward-compatible -- existing skills without these fields must still load.

---

## 7. Dependencies

### 7.1 Available (already in `pyproject.toml`)

- `pydantic` / `pydantic-settings` -- for config and data validation
- `structlog` -- structured logging
- `httpx` -- async HTTP client for LLM calls
- `pyyaml` -- YAML parsing (skill frontmatter)
- `pytest`, `pytest-asyncio`, `pytest-mock` -- testing
- `anthropic` -- optional, for Claude API calls

### 7.2 Not Needed

The MVP does NOT require:
- Embedding models or vector databases (no embedding retrieval in MVP)
- New LLM providers (reuse existing `_call_local()` / `_call_anthropic()`)
- Database (JSONL files suffice for observations; derived procedure is in-memory)

---

## 8. Patterns to Follow

### 8.1 Do Follow

1. **Dataclass-first models**: All data structures are `@dataclass`. Follow this for `SkillCard`, `DerivedSkillSession`, `ReplanPatch`.
2. **Protocol-based interfaces**: Components depend on protocols, not implementations. The `SkillRegistry` protocol doesn't need signature changes.
3. **Template-based prompts**: LLM prompts live in `.md` files with `{{placeholder}}` substitution. Follow this for new/modified prompts.
4. **Structured logging**: Use `structlog.get_logger(__name__)` consistently.
5. **JSONL persistence**: The experience store pattern (one `.jsonl` file per skill) is simple and works. Use it for any new persistence needs.
6. **Test isolation**: Use `_env_file=None` in `AgentConfig()` to prevent `.env` leaks. Use `tmp_path` for file-based tests.
7. **AsyncMock for LLM calls**: Mock `_call_llm` or `_router.route` rather than mocking HTTP.
8. **Graceful degradation**: The router falls back to keyword matching if LLM fails. The distiller is no-op if disabled. Follow this pattern.

### 8.2 Do NOT Follow

1. **Do NOT change protocol signatures** for `ActionPlanner.plan()` or `ActionPlanner.replan()`. The `skill_context: Optional[str]` parameter stays -- assemble richer context into that string.
2. **Do NOT add new constructor parameters to `AutomationAgent`** unless absolutely necessary. Wire the derived skill session internally.
3. **Do NOT mutate canonical skill markdown files** at runtime. This is a hard invariant.
4. **Do NOT add embedding dependencies**. The MVP uses LLM routing, not vector search.
5. **Do NOT store derived procedures persistently** in MVP. They are in-memory, per-run objects.

---

## 9. Constraints

1. **Python 3.11** target. Use `list[str]` not `List[str]` where the codebase already does (mixed usage exists).
2. **Line length 100** (Black + Ruff).
3. **Ruff rules**: E, W, F, I, B, C4, UP. Ignores E501, B008.
4. **asyncio_mode = "auto"**: All test methods in `Test*` classes are auto-detected as async.
5. **`.env` gotcha**: `.env` sets `AGENT_MODEL_PROVIDER=anthropic`. Test configs MUST use `_env_file=None` to avoid this leaking into pydantic-settings.
6. **Single replan**: The orchestrator only replans once per run (`_replan_and_continue` does not recurse). The derived procedure update happens in that single replan call.

---

## 10. RECOMMENDATION

### Build Order (matches spec's recommended order)

**Slice 1: Skill Cards + Models**
- Add `SkillCard` dataclass to `models.py`
- Add optional `skill_id`, `tags`, `summary` to `Skill` frontmatter (backward-compatible)
- Create `card_builder.py` with `SkillCardBuilder.build(skill) -> SkillCard`
- Add `DerivedSkillSession` dataclass to a new `derived_skill.py`
- Add `ReplanPatch` dataclass (simple: `replace_labels`, `add_landmarks`, `verify_improvements`)
- Unit tests for all new dataclasses and the card builder

**Slice 2: Top-K Router**
- Update `route_skill.md` prompt to request top-3 with `match_type` and `confidence`
- Update `SkillRouter._build_skills_summary()` to use compact skill cards
- Update `SkillRouter._parse_response()` to parse `{"matches": [...]}`
- Update `SkillRouter.route()` return type from `Optional[Dict]` to `Optional[List[Dict]]`
- Update `SkillRegistryImpl.match()` to return multi-match result
- Unit tests for new routing

**Slice 3: Multi-Skill Planner Context**
- Update `plan_from_prompt.md` with multi-skill priors section
- Update `replan_from_state.md` with derived procedure and patch output
- Update `ActionPlannerImpl._build_plan_prompt()` to assemble multi-skill context
- Update `ActionPlannerImpl._build_replan_prompt()` to include derived procedure
- Update `ActionPlannerImpl._parse_plan_response()` to optionally extract `derived_skill_patch`
- Unit tests for new prompt assembly and parsing

**Slice 4: Orchestrator Integration**
- Update `AutomationAgent.execute()` to create `DerivedSkillSession` after match
- Update `_replan_and_continue()` to update derived procedure from replan patch
- Update context assembly to include derived procedure in skill_context string
- Integration tests for full flow

### Key Design Decisions

1. **Skill cards are computed, not stored**: `SkillCardBuilder.build()` generates cards on the fly from `Skill` objects. No separate cache directory needed in MVP.
2. **`match()` return shape**: Return `{"primary_skill_name", "candidates": [...], "skill_context", "params"}` where `candidates` is a list of `{"skill_name", "match_type", "confidence", "reason"}`.
3. **`skill_context` assembly**: The registry's `build_runtime_context()` should be extended to handle multiple skills, producing a single string with labeled sections per skill.
4. **`DerivedSkillSession` lifecycle**: Created in `execute()` after match, updated in `_replan_and_continue()`, serialized into `skill_context` string for subsequent planner calls. Discarded when `execute()` returns.
5. **Replan patch handling**: The replan LLM response gains an optional `derived_skill_patch` key. The planner extracts it and returns it alongside the `ActionPlan`. The orchestrator applies it to the `DerivedSkillSession`.

### Risk Mitigation

1. **Test all existing tests pass after each slice**. Run `pytest tests/unit/` after every change.
2. **Keep `match()` backward-compatible**: If the orchestrator checks for `skill_match["skill_name"]`, ensure that key still exists in the new return shape.
3. **Make new frontmatter fields optional**: Existing skills without `skill-id` or `tags` must still load and work.
4. **Keep the keyword fallback**: `matcher.py` should still work as a no-LLM fallback.
