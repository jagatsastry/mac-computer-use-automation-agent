# Skill Librarian -- Architecture Spec

**Status**: APPROVED (DE review complete, 5 rounds, 14 improvements)
**PRD**: `docs/features/skill-librarian/skill-librarian-prd.md` (30 ACs, approved after 3 review rounds)
**Branch**: `feature/skill-librarian`
**Test command**: `.venv/bin/python -m pytest tests/unit/ -x -q`

---

## 1. System Overview

The Skill Librarian is a post-run evaluator that promotes high-confidence observations from JSONL sidecars into the canonical skill library. It runs after the existing SkillDistiller pipeline and produces three possible outcomes: `patch_parent` (append learned tips to an existing skill), `create_sibling` (generate a new skill from an analogical run), or `observation_only` (no-op).

### Data Flow

```
_maybe_learn_skill_run() -> List[SkillObservation]
    |
    v
_maybe_promote_skill()
    |
    v
SkillLibrarian.evaluate_run()
    |-- Load ALL observations for skill (experience_store.load())
    |-- Group by (category.lower(), recommendation.lower())
    |-- Filter: already promoted? -> skip
    |-- Compute Bayesian score per group
    |-- Filter: score >= 0.7 AND count >= 5 AND distinct_runs >= 3
    |
    v  (for FIRST qualifying group only -- one promotion per run)
LLM Call 1: Decide type  ->  observation_only? STOP
    |
    v
LLM Call 2: Generate content
    |
    v
Validate -> fail? fall back to observation_only
    |
    v
Commit: write file -> load_from_string -> write history -> mark promoted
```

**Key constraint**: One promotion per `evaluate_run()` call. If multiple groups qualify, pick the highest-scoring one. This keeps the blast radius small and limits LLM cost to at most 2 calls per run.

---

## 2. Component Design

### 2.1 SkillLibrarian (`src/automation_agent/skills/librarian.py`)

The core class. Stateless except for references to config, experience store, and registry.

```python
class SkillLibrarian:
    def __init__(
        self,
        config: AgentConfig,
        experience_store: SkillExperienceStore,
        registry: "SkillRegistryImpl",
    ) -> None: ...

    async def evaluate_run(
        self,
        goal: str,
        skill_name: str,
        derived_session: Optional[DerivedSkillSession],
        observations: list[SkillObservation],  # just-distilled
        trace: list[StepResult],
        run_id: str,
        had_replan: bool,
        success: bool,
    ) -> Optional[PromotionDecision]: ...

    # --- Internal methods ---

    def _group_observations(
        self, observations: list[SkillObservation],
    ) -> dict[tuple[str, str], list[SkillObservation]]: ...

    def _compute_score(
        self, group: list[SkillObservation],
    ) -> float: ...

    def _load_promotion_history(
        self, skill_name: str,
    ) -> set[tuple[str, str]]: ...

    async def _decide_promotion_type(
        self, skill_name: str, group: list[SkillObservation],
        score: float, parent_skill: Skill,
        derived_session: Optional[DerivedSkillSession],
        success: bool,
    ) -> Optional[dict]: ...  # LLM Call 1

    async def _generate_content(
        self, promotion_type: str, parent_skill: Skill,
        group: list[SkillObservation],
        derived_session: Optional[DerivedSkillSession],
    ) -> Optional[dict]: ...  # LLM Call 2

    def _validate_tips(self, tips_text: str) -> bool: ...
    def _validate_sibling_md(self, md_content: str) -> bool: ...

    def _apply_patch_parent(
        self, skill_name: str, tips_text: str,
    ) -> str: ...  # returns updated file content

    def _apply_create_sibling(
        self, md_content: str, parent_skill: Skill,
    ) -> tuple[str, Path]: ...  # returns (skill_id, file_path)

    def _mark_promoted(
        self, skill_name: str, keys: list[tuple[str, str]],
    ) -> None: ...

    def _write_history(self, decision: PromotionDecision) -> None: ...

    def _parse_response(self, response: str) -> Optional[dict]: ...
```

LLM calls delegate to the shared `call_skill_llm()` utility (see section 2.5).

### 2.2 PromotionDecision (`src/automation_agent/skills/models.py`)

```python
@dataclass
class PromotionDecision:
    skill_name: str
    promotion_type: str       # "patch_parent" | "create_sibling" | "observation_only"
    reason: str
    confidence_score: float
    observation_keys: list[list[str]]  # [[category, recommendation], ...]
    run_id: str
    timestamp: str            # ISO 8601
    generated_tips: str = ""
    new_skill_id: str = ""
    new_skill_path: str = ""
    parent_skill_id: str = ""
    observation_count: int = 0
    distinct_run_count: int = 0
    pre_promotion_baseline: dict = field(default_factory=dict)
    # {"friction_runs": N, "friction_successes": M, "friction_success_rate": float | None}
    # IMPORTANT: This baseline only reflects runs where _trace_deserves_learning()
    # returned True (i.e., runs with retries, suggested elements, reflections, or
    # wait_for_user). Clean runs that need no retries produce no observations and
    # are invisible here. This means the metric measures "friction run success rate",
    # not overall success rate. This is still useful for regression detection: if
    # friction runs start failing more often after a promotion, the tip may be harmful.
    # friction_success_rate = friction_successes / min(friction_runs, 5).
    # None if friction_runs == 0 at capture time.
```

### 2.3 Modified Data Models

**`SkillObservation`** (models.py) -- add:
```python
promoted: bool = False
```

**`Skill`** (models.py) -- add:
```python
parent_skill_id: str = ""
learned_tips_text: str = ""
```

### 2.4 Shared LLM Utility (`src/automation_agent/skills/llm_utils.py`)

Extracts the duplicated `_call_llm`/`_call_local`/`_call_anthropic` pattern into a shared function. Currently duplicated across `SkillDistiller` (distiller.py:80-109), `SkillRouter` (router.py:104-143), and would be duplicated again in the librarian.

```python
async def call_skill_llm(
    config: AgentConfig,
    prompt: str,
    max_tokens: int = 1024,
    model: str = "",  # defaults to config.text_model or config.vision_model
    temperature: float = 0.0,
) -> str:
    """Shared LLM dispatch for the skill subsystem.

    Routes to Anthropic or local OpenAI-compatible endpoint based on
    config.model_provider. Used by SkillDistiller, SkillLibrarian, and
    (optionally in a follow-up) SkillRouter.
    """
```

The librarian uses this directly instead of private `_call_llm` methods. The distiller is also migrated to use it (reducing code in distiller.py). The router migration is deferred to a follow-up to minimize blast radius.

### 2.5 LLM Prompt Templates

Two templates in `src/automation_agent/skills/prompts/`:

**`librarian_decide.md`** -- Call 1 (decide type):
- Input placeholders: `{{skill_name}}`, `{{skill_summary}}`, `{{observation_summary}}`, `{{bayesian_score}}`, `{{distinct_runs}}`, `{{derived_session_summary}}`, `{{success}}`, `{{existing_tips}}`
- System role: "You are a skill quality evaluator for a macOS automation agent."
- Output format: strict JSON only, no prose. Schema: `{"promotion_type": "patch_parent"|"create_sibling"|"observation_only", "reason": "<one sentence>"}`
- `max_tokens=256`
- Decision rules the prompt must encode:
  - `patch_parent`: observations add reusable, generalizable tips to the existing skill (default when evidence is strong)
  - `create_sibling`: `derived_session_summary` is non-empty AND shows a distinct workflow variant (different site, different flow). If `derived_session_summary` is empty/null, `create_sibling` is NOT allowed
  - `observation_only`: insufficient evidence, contradictory observations, or environment-specific behavior
- **Contrastive safety gate**: "If the observations describe timing-specific or environment-specific behavior (e.g., 'wait N seconds', 'scroll down on slow connections'), prefer `observation_only` unless the observation's `condition` field specifies the discriminating context. Observations without specific conditions should not be promoted." (v1 substitute for full contrastive refinement, deferred to v2)

**`librarian_generate.md`** -- Call 2 (generate content):
- Input placeholders: `{{promotion_type}}`, `{{parent_skill_raw}}`, `{{observations}}`, `{{derived_session_full}}`, `{{existing_tips}}`
- System role: "You are a skill content generator for a macOS automation agent."
- Output format by promotion type:
  - `patch_parent`: `{"learned_tips": "- Tip one\n- Tip two\n..."}` — markdown bullet list only, no headings, no frontmatter. Each bullet is a standalone actionable tip.
  - `create_sibling`: `{"sibling_skill_md": "---\nname: ...\nskill-id: ...\nparent-skill-id: ...\n...\n---\n\n## Steps\n...\n\n## Error Recovery\n...\n\n## Notes\n..."}` — complete skill file with YAML frontmatter (must include `parent-skill-id`) + all standard sections
- Constraints the prompt must encode:
  - Must NOT duplicate content already in `{{existing_tips}}`
  - Sibling must include `parent-skill-id: {{parent_skill_id}}` in frontmatter
  - Tips must be actionable and context-qualified ("When X, do Y"), not generic advice
- `max_tokens=1024` for `patch_parent`, `max_tokens=4096` for `create_sibling`

---

## 3. Integration Points

### 3.1 Orchestrator (`agent.py`)

**`_maybe_learn_skill_run()`** (line 2425):
- Change return type: `-> None` to `-> list[SkillObservation]`
- All early returns become `return []`
- Final path returns `observations`

**New `_maybe_promote_skill()`**:
```python
async def _maybe_promote_skill(
    self, *, goal, skill_name, observations, derived_session,
    step_results, run_id, had_replan, success,
) -> None:
    if not skill_name or not observations:
        return
    promote = getattr(self.skill_registry, "promote_from_run", None)
    if promote is None:
        return
    try:
        decision = await promote(
            goal=goal, skill_name=skill_name,
            derived_session=derived_session,
            observations=observations, trace=step_results,
            run_id=run_id, had_replan=had_replan, success=success,
        )
        if decision and decision.promotion_type != "observation_only":
            slog.info("skill_promotion_applied", ...)
    except Exception:
        slog.warning("skill_promotion_failed", exc_info=True)
```

Uses `getattr` on the public method `promote_from_run` (not the private `_librarian` attribute), consistent with the existing `learn_from_run` pattern at line 2444.

**Call sites** -- insert after each `_maybe_learn_skill_run` call:
- Success path (~line 352): `success=True` hardcoded
- Replan path (~line 2394): `success=success` from line 2379/2388

### 3.2 Registry (`registry.py`)

In `__init__`:
```python
self._librarian: Optional[SkillLibrarian] = None
if (self._config is not None
    and self._config.skill_librarian_enabled
    and self._experience_store is not None):
    from automation_agent.skills.librarian import SkillLibrarian
    self._librarian = SkillLibrarian(
        config=self._config,
        experience_store=self._experience_store,
        registry=self,
    )
```

**Public method** (delegates to internal librarian):
```python
async def promote_from_run(
    self,
    goal: str,
    skill_name: str,
    derived_session: Optional[DerivedSkillSession],
    observations: list[SkillObservation],
    trace: list[StepResult],
    run_id: str,
    had_replan: bool,
    success: bool,
) -> Optional["PromotionDecision"]:
    """Evaluate and promote accumulated observations after a run.
    No-op if librarian is disabled. Same duck-typing pattern as learn_from_run."""
    if self._librarian is None:
        return None
    return await self._librarian.evaluate_run(
        goal=goal, skill_name=skill_name,
        derived_session=derived_session,
        observations=observations, trace=trace,
        run_id=run_id, had_replan=had_replan, success=success,
    )
```

In `build_runtime_context()` (after notes, before observed variants):
```python
if skill.learned_tips_text:
    sections.extend(["", "## Learned Tips", skill.learned_tips_text])
```

### 3.3 Loader (`loader.py`)

In `parse_skill_file()`:
```python
# After existing skill-id parsing (~line 90):
parent_skill_id = meta.get("parent-skill-id", "")

# After existing section extraction (~line 87):
learned_tips_text = _extract_section(body, "Learned Tips")
```

Pass both to the `Skill()` constructor.

### 3.4 Experience Store (`experience.py`)

New method:
```python
def mark_promoted(
    self, skill_name: str, keys: set[tuple[str, str]],
) -> int:
    """Mark observations matching (category, recommendation) keys as promoted.
    Returns count marked. Uses atomic write-to-temp + rename."""
```

Update `top_for_context()` filter (line 66):
```python
if item.confidence >= min_confidence and item.recommendation.strip()
    and not getattr(item, "promoted", False)
```

Using `getattr` for backward compat with old JSONL files that lack the field.

### 3.5 Config (`config.py`)

```python
# Skill Librarian Configuration
skill_librarian_enabled: bool = Field(
    default=False,
    description="Promote high-confidence observations into canonical skills",
)
skill_librarian_min_confidence: float = Field(
    default=0.7,
    description="Minimum Bayesian score for promotion",
    gt=0.0, le=1.0,
)
skill_librarian_min_observations: int = Field(
    default=5,
    description="Minimum observation count before promotion",
    gt=0,
)
skill_librarian_min_runs: int = Field(
    default=3,
    description="Minimum distinct run_ids before promotion",
    gt=0,
)
skill_librarian_max_tips: int = Field(
    default=10,
    description="Maximum bullet entries in a skill's Learned Tips section",
    gt=0,
)
```

---

## 4. Algorithms

### 4.1 Bayesian Score (AC-2)

```python
def _compute_score(self, group: list[SkillObservation]) -> float:
    alpha = sum(1 for o in group if o.confidence >= 0.6)
    beta = sum(1 for o in group if o.confidence < 0.4)
    return (alpha + 1) / (alpha + beta + 2)
```

Neutral observations (0.4 <= conf < 0.6) excluded from alpha/beta. Score for all-neutral group = 0.5 (fails 0.7 threshold).

### 4.2 Observation Grouping (AC-3)

```python
import re as _re
_NORMALIZE_RE = _re.compile(r"[^\w\s]")  # strip punctuation
_WHITESPACE_RE = _re.compile(r"\s+")     # collapse whitespace

def _normalize_key(self, text: str) -> str:
    """Normalize observation text for grouping: lowercase, strip punctuation, collapse whitespace."""
    text = text.strip().lower()
    text = self._NORMALIZE_RE.sub("", text)
    text = self._WHITESPACE_RE.sub(" ", text).strip()
    return text

def _group_observations(self, observations):
    groups = defaultdict(list)
    for obs in observations:
        key = (self._normalize_key(obs.category), self._normalize_key(obs.recommendation))
        groups[key].append(obs)
    return dict(groups)
```

The normalization pre-pass ensures minor LLM output variations ("click the confirm button" vs "click confirm button") group together. This is not semantic dedup (deferred to v2) but catches the most common phrasing differences.

Qualification check per group:
- `not all(getattr(obs, "promoted", False) for obs in group)` — skip groups where every observation is already marked promoted (defense-in-depth against history write failures; uses `getattr` for backward compat with old JSONL)
- `len(group) >= config.skill_librarian_min_observations`
- `len({o.run_id for o in group if o.run_id}) >= config.skill_librarian_min_runs`
- `not any promoted for key in promotion_history`
- `score >= config.skill_librarian_min_confidence`

### 4.3 Learned Tips File Manipulation (AC-6)

```python
def _apply_patch_parent(self, skill_name: str, tips_text: str) -> str:
    skill = self.registry.get_skill(skill_name)
    content = skill.raw_content

    # Use same heading+section regex as loader._extract_section for consistency
    pattern = re.compile(
        r"^(##\s+Learned\s+Tips\s*\n)(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(content)

    if match:
        section_end = match.end(2)
        existing = match.group(2).rstrip()
        new_content = (
            content[:match.start(2)]
            + existing + "\n" + tips_text + "\n\n"
            + content[section_end:].lstrip("\n")
        )
    else:
        # Append new section at EOF
        new_content = content.rstrip() + "\n\n## Learned Tips\n" + tips_text + "\n"

    return new_content
```

### 4.4 Commit Sequence (AC-20)

For `patch_parent`:
1. `backup = skill.raw_content`
2. Check tip count: if existing `## Learned Tips` bullet count >= `config.skill_librarian_max_tips`, return `observation_only` with reason `"tip_limit_reached"`
3. `updated = _apply_patch_parent(skill_name, tips)`
4. `old_skill = registry.get_skill(skill_name)` -- snapshot for rollback
5. Atomic write updated content to .md
6. `registry.load_from_string(updated)` -- reload + rebuild router
7. If step 6 fails: atomic write backup to .md, then `registry.load_from_string(backup)` to restore in-memory + router. If THAT also fails: log critical("in-memory state inconsistent after rollback, restart recommended"). Return observation_only
8. `_write_history(decision)` -- if fails: atomic write backup to .md, restore `registry._skills`, return observation_only
9. `experience_store.mark_promoted(skill_name, keys)` -- if fails: log warning, continue (group in history so won't re-evaluate; observations stay injectable which is harmless redundancy)

For `create_sibling`:
1. `_validate_sibling_md(md_content)` -- parse_skill_file round-trip + field checks (trigger_keywords, steps_text, success_condition non-empty) + parent-skill-id required. If fails: return observation_only (no file written)
2. Atomic write .md to `skills/library/{name}.md`
3. `registry.load_from_string(md_content)` -- add + rebuild
4. If step 3 fails: delete file, `registry._skills.pop(new_name, None)`, return observation_only
5. `_write_history(decision)` -- if fails: delete file, `registry._skills.pop`, return observation_only
6. `experience_store.mark_promoted(skill_name, keys)` -- if fails: log warning, continue (same harmless redundancy)

**Ordering rationale**: History write (step 8/4) runs BEFORE mark_promoted (step 9/5). If history write fails, rollback is trivial — undo file + in-memory, nothing else happened. If mark_promoted fails after history succeeds, the group is already in history (won't be re-evaluated) but observations remain injectable — this is harmless redundancy, not a bug. This ordering eliminates the need for complex un-mark rollback logic.

### 4.5 Atomic File Write

```python
def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(str(tmp), str(path))
```

---

## 5. Domain Slice Decomposition

### Engineer 1: Core Librarian

**Scope**: The `SkillLibrarian` class, Bayesian evaluation, LLM calls, promotion history, and all unit tests.

**Files to create**:
- `src/automation_agent/skills/librarian.py`
- `src/automation_agent/skills/llm_utils.py` -- shared LLM dispatch (extracted from distiller pattern)
- `src/automation_agent/skills/prompts/librarian_decide.md`
- `src/automation_agent/skills/prompts/librarian_generate.md`
- `tests/unit/test_skill_librarian.py`

**Files to modify**:
- `src/automation_agent/skills/models.py` -- add `PromotionDecision`, `promoted` on `SkillObservation`, `parent_skill_id` and `learned_tips_text` on `Skill`
- `src/automation_agent/config.py` -- add 4 librarian config fields
- `src/automation_agent/skills/experience.py` -- add `mark_promoted()` method, update `top_for_context()` filter
- `src/automation_agent/skills/distiller.py` -- migrate `_call_llm`/`_call_local`/`_call_anthropic` to use `call_skill_llm` from `llm_utils.py`

**Interfaces provided to Engineer 2**:
- `SkillLibrarian.__init__(config, experience_store, registry)`
- `SkillLibrarian.evaluate_run(goal, skill_name, derived_session, observations, trace, run_id, had_replan, success) -> Optional[PromotionDecision]`

**Detailed task list**:

1. **Data model changes** (models.py)
   - Add `promoted: bool = False` to `SkillObservation`
   - Add `parent_skill_id: str = ""` to `Skill`
   - Add `learned_tips_text: str = ""` to `Skill`
   - Add `PromotionDecision` dataclass (full definition in PRD AC-9)
   - Tests: verify backward compat (SkillObservation without promoted field uses default)

2. **Config changes** (config.py)
   - Add 5 fields: `skill_librarian_enabled`, `skill_librarian_min_confidence`, `skill_librarian_min_observations`, `skill_librarian_min_runs`, `skill_librarian_max_tips`
   - Tests: verify defaults, verify env var override

3. **Experience store changes** (experience.py)
   - Add `mark_promoted(skill_name, keys)` -- read JSONL, set promoted=True on matches, atomic rewrite
   - Update `top_for_context()` to skip `promoted=True` observations
   - Tests: mark_promoted round-trip, top_for_context excludes promoted, backward compat with old JSONL

4. **SkillLibrarian class** (librarian.py)
   - `__init__`: store config, experience_store, registry references. Load promotion history template.
   - `_group_observations()`: group by (category.lower(), recommendation.lower())
   - `_compute_score()`: Bayesian formula (alpha = conf >= 0.6, beta = conf < 0.4, Laplace smoothed)
   - `_load_promotion_history()`: read `{skill_learning_dir}/promotions/history.jsonl`, return set of (category, recommendation) tuples for the given skill
   - `evaluate_run()`: main orchestration -- load all obs, group, filter by history/thresholds, pick best group, call LLM decide, optionally call LLM generate, validate, commit, return decision
   - Tests: score calculation (pure alpha, pure beta, mixed, all neutral), grouping, history dedup, threshold filtering, evaluate_run with mocked LLM returning each promotion type

5. **LLM integration** (llm_utils.py + librarian.py)
   - Create `llm_utils.py` with `call_skill_llm()` shared utility (extracted from distiller pattern)
   - Migrate `SkillDistiller._call_llm`/`_call_local`/`_call_anthropic` to delegate to `call_skill_llm()`. Add `temperature=0.0` to the Anthropic path (bug fix: local already uses 0.0, Anthropic was defaulting to 1.0).
   - Librarian uses `call_skill_llm()` directly (no private LLM methods)
   - `_parse_response()`: strip fences, json.loads, validate required fields, return None on failure
   - `_decide_promotion_type()`: build prompt from librarian_decide.md, call LLM, parse
   - `_generate_content()`: build prompt from librarian_generate.md, call LLM, parse
   - Tests: parse valid/invalid JSON, missing fields, fence stripping, call_skill_llm dispatch (local vs anthropic)

6. **Validation** (librarian.py)
   - `_validate_tips()`: non-empty, no `---`, no `## Steps`/`## Error Recovery` headings
   - `_validate_sibling_md()`: `parse_skill_file()` round-trip + same field checks as `validate_skill_file()` (trigger_keywords, steps_text, success_condition non-empty) + `parent-skill-id` required in frontmatter
   - Tests: valid tips, tips with forbidden headings, valid sibling md, sibling md missing frontmatter fields, sibling md missing parent-skill-id

7. **File operations** (librarian.py)
   - `_apply_patch_parent()`: section detection + append-within algorithm
   - `_apply_create_sibling()`: name collision check, atomic write, return (skill_id, path)
   - `_atomic_write()`: write-to-temp + os.replace
   - Tests: patch to skill without Learned Tips, patch to skill with existing Learned Tips, sibling name collision (appends suffix)

8. **Commit sequence and rollback** (librarian.py)
   - Implement AC-20 ordering for both patch_parent and create_sibling
   - On load_from_string failure: rollback file, return observation_only
   - `_mark_promoted()`: delegate to experience_store.mark_promoted
   - `_write_history()`: append to promotions/history.jsonl
   - Tests: successful commit, rollback on router rebuild failure

9. **Prompt templates**
   - `librarian_decide.md`: structured prompt for Call 1
   - `librarian_generate.md`: structured prompt for Call 2 with parent raw_content, derived session, observations
   - Must reference existing tips to avoid duplicate generation

10. **Observability** (AC-26)
    - Structured log events at each stage via structlog

### Engineer 2: Integration + Sibling Creation

**Scope**: Orchestrator wiring, loader/registry changes, and integration tests.

**Files to create**:
- `tests/unit/test_skill_librarian_integration.py` (unit tests with mocked librarian verifying wiring)

**Files to modify**:
- `src/automation_agent/skills/loader.py` -- parse `parent-skill-id`, extract `## Learned Tips`
- `src/automation_agent/skills/registry.py` -- instantiate librarian, include `learned_tips_text` in `build_runtime_context()`
- `src/automation_agent/orchestrator/agent.py` -- modify `_maybe_learn_skill_run()` return type, add `_maybe_promote_skill()`, wire both call sites

**Detailed task list**:

1. **Loader changes** (loader.py)
   - Parse `parent-skill-id` from YAML frontmatter (after line ~90)
   - Extract `## Learned Tips` via `_extract_section(body, "Learned Tips")`
   - Pass `parent_skill_id` and `learned_tips_text` to `Skill()` constructor
   - Tests: parse skill with parent-skill-id, parse skill with Learned Tips section, parse skill without either (backward compat), round-trip: write skill with learned tips then parse back

2. **Registry changes** (registry.py)
   - Instantiate `SkillLibrarian` in `__init__` when `skill_librarian_enabled=True` (lazy import)
   - Add public `promote_from_run()` method (delegates to `_librarian.evaluate_run()`, no-op if librarian is None)
   - Include `learned_tips_text` in `build_runtime_context()` between Notes and Observed Variants
   - Tests: runtime context includes learned tips, runtime context without learned tips, librarian not instantiated when disabled, `promote_from_run` delegates to librarian with correct args, `promote_from_run` returns None when librarian disabled

3. **Orchestrator changes** (agent.py)
   - Modify `_maybe_learn_skill_run()`: change `-> None` to `-> list[SkillObservation]`, all early returns become `return []`
   - Add `_maybe_promote_skill()` method with getattr pattern
   - Update success path call site (~line 352): capture observations, call promote with success=True
   - Update replan path call site (~line 2394): capture observations, call promote with success=success
   - Tests: _maybe_promote_skill called after learn, _maybe_promote_skill handles missing librarian (getattr returns None), _maybe_promote_skill swallows exceptions

4. **End-to-end wiring tests**
   - Mock librarian on registry, verify it's called with correct args from orchestrator
   - Verify _maybe_learn_skill_run returns observations (not None)
   - Verify _maybe_promote_skill is no-op when librarian_enabled=False
   - Verify _maybe_promote_skill is no-op when observations is empty

---

## 6. Error Handling

Every boundary in the librarian is wrapped in try/except:

| Boundary | On failure | Behavior |
|----------|-----------|----------|
| `evaluate_run()` top-level | Log warning, return None | Agent continues normally |
| LLM Call 1 (decide type) | Return observation_only | No file writes |
| LLM Call 2 (generate content) | Return observation_only | No file writes |
| `_parse_response()` | Return None | Falls back to observation_only |
| `_validate_tips()` | Return False | Falls back to observation_only |
| `_validate_sibling_md()` | Return False | Falls back to observation_only |
| Atomic file write | Raise | Caught by commit sequence rollback |
| `load_from_string()` | Raise | Triggers rollback: restore file, return observation_only |
| `mark_promoted()` | Log warning, continue | Observations stay injectable (safe -- worst case is redundant context) |
| `_write_history()` | Raise | Caught by commit sequence -- triggers rollback of file write, in-memory state, and mark_promoted |

---

## 7. Invariants

1. **Canonical skills never mutated during a live run** (Hard Invariant #1, #5). The librarian runs only from `_maybe_promote_skill()`, which is called after `_maybe_learn_skill_run()`, which is called after all step execution is complete.

2. **Observations never marked promoted unless file write, router rebuild, AND history write have all succeeded** (AC-20 ordering invariant).

3. **At most one promotion per `evaluate_run()` call**. Prevents cascading mutations and bounds LLM cost.

4. **Disabled by default** (AC-13). `skill_librarian_enabled=False`. No runtime cost when disabled -- `_maybe_promote_skill` returns immediately when `getattr` yields None.

5. **Additive only** (AC-6). Only `## Learned Tips` is touched. `## Steps`, `## Error Recovery`, `## Notes` are never modified programmatically.

6. **Single-process assumption** (AC-17a). No file locking. No concurrent access guards.

7. **Tip count cap** (`skill_librarian_max_tips`, default 10). A skill with 10+ learned tips is considered mature; further promotions are downgraded to `observation_only` until a human reviews and consolidates.

### Known Scaling Limitations

- **Observation file growth**: `evaluate_run()` loads the entire JSONL sidecar for the skill on every invocation. For v1 this is acceptable (JSONL parsing of a few hundred lines is <10ms, dominated by the 2-10s LLM calls). When observation files exceed 1000 lines (requiring hundreds of friction runs over months), a future optimization should add either incremental evaluation (load only observations since last evaluation timestamp) or periodic compaction (remove promoted observations from the JSONL file).
- **Learned Tips context cost**: Each learned tip bullet adds ~20-50 tokens to `build_runtime_context()`. The max_tips cap (default 10) bounds this to ~200-500 tokens per skill, which is acceptable for planner context windows.

---

## 8. Testing Strategy

### Unit Tests (Engineer 1: `test_skill_librarian.py`)

| Category | Tests | What's tested |
|----------|-------|---------------|
| Score computation | 5+ | Pure corroborating, pure contradicting, mixed, all neutral, empty group |
| Grouping | 3+ | Multi-group, case-insensitive dedup, empty observations |
| History dedup | 3+ | Skip already-promoted keys, handle missing history file, handle corrupt JSONL line |
| Threshold filtering | 4+ | Below min_obs, below min_runs, below min_confidence, exactly at threshold |
| LLM parse | 5+ | Valid JSON, fenced JSON, missing fields, invalid promotion_type, non-JSON response |
| Tips validation | 4+ | Valid tips, empty tips, tips with `---`, tips with `## Steps` heading |
| Sibling validation | 4+ | Valid .md, missing frontmatter field, missing Steps section, duplicate name |
| File manipulation | 4+ | Patch without existing tips, patch with existing tips, sibling write, atomic write crash safety |
| Commit sequence | 3+ | Successful patch_parent commit, successful create_sibling commit, rollback on router failure |
| evaluate_run flow | 5+ | No qualifying groups (returns None), observation_only decision, patch_parent end-to-end, create_sibling end-to-end, exception during LLM call |
| Experience store | 4+ | mark_promoted round-trip, top_for_context excludes promoted, backward compat, mark with no matching keys |
| Config | 2+ | Default values, env var overrides |

**Estimated: ~46 unit tests**

### Integration Tests (Engineer 2: `test_skill_librarian_integration.py`)

| Category | Tests | What's tested |
|----------|-------|---------------|
| Orchestrator wiring | 4+ | Librarian called with correct args, not called when disabled, not called when no observations, exception swallowed |
| Return type change | 2+ | _maybe_learn_skill_run returns list, returns [] on early exit |
| Loader changes | 4+ | parent-skill-id parsed, learned_tips_text parsed, backward compat, round-trip |
| Registry changes | 5+ | learned_tips in runtime context, librarian instantiated when enabled, not instantiated when disabled, promote_from_run delegates to librarian with correct args, promote_from_run returns None when disabled |

**Estimated: ~15 integration tests**

### Test Patterns

- **Config fixture**: `AgentConfig(_env_file=None, anthropic_api_key="test-key", model_provider="local", skill_learning_dir=tmp_path / "learning", skill_librarian_enabled=True)`
- **LLM mocking**: `librarian._call_llm = AsyncMock(return_value='{"promotion_type": "observation_only", "reason": "..."}')`
- **Registry mocking for orchestrator tests**: `mock_skill_registry._librarian = AsyncMock()` with `evaluate_run = AsyncMock(return_value=None)`
- **Pin `model_provider="local"`** in all test configs to avoid `.env` leaking `AGENT_MODEL_PROVIDER=anthropic`

### Regression Protection

All existing tests must pass. Key risk areas:
- `SkillObservation` adding `promoted` field -- backward compat via default value
- `Skill` adding `parent_skill_id` and `learned_tips_text` -- backward compat via default values
- `_maybe_learn_skill_run` return type change from None to list -- callers that `await` without capturing return are unaffected
- `top_for_context()` filter change -- uses `getattr` for backward compat with old JSONL

---

## 9. File List Per Slice

### Engineer 1 (Core Librarian)

| File | Action | Lines (est.) |
|------|--------|-------------|
| `src/automation_agent/skills/librarian.py` | CREATE | ~350 |
| `src/automation_agent/skills/prompts/librarian_decide.md` | CREATE | ~40 |
| `src/automation_agent/skills/prompts/librarian_generate.md` | CREATE | ~60 |
| `src/automation_agent/skills/models.py` | MODIFY | +30 |
| `src/automation_agent/config.py` | MODIFY | +20 |
| `src/automation_agent/skills/experience.py` | MODIFY | +35 |
| `tests/unit/test_skill_librarian.py` | CREATE | ~500 |

### Engineer 2 (Integration + Wiring)

| File | Action | Lines (est.) |
|------|--------|-------------|
| `src/automation_agent/skills/loader.py` | MODIFY | +10 |
| `src/automation_agent/skills/registry.py` | MODIFY | +15 |
| `src/automation_agent/orchestrator/agent.py` | MODIFY | +40 |
| `tests/unit/test_skill_librarian_integration.py` | CREATE | ~200 |

---

## 10. Dependency Order

Engineer 2 depends on Engineer 1's data model changes (step 1: `PromotionDecision`, `promoted`, `parent_skill_id`, `learned_tips_text` on models.py). Engineer 1 should complete step 1 first so Engineer 2 can start immediately on loader and registry changes.

After that, both engineers can work in parallel:
- Engineer 1: librarian.py + experience.py + config.py + prompts + tests
- Engineer 2: loader.py + registry.py + agent.py + wiring tests

Merge order: Engineer 1 first (provides `SkillLibrarian` class), then Engineer 2 (wires it up).

---

## 11. References

- **PRD**: `docs/features/skill-librarian/skill-librarian-prd.md` (30 ACs)
- **SOTA research**: `docs/features/skill-librarian/skill-librarian-sota.md` (MACLA, ReMe, ExpeL, EvolveR, Voyager)
- **Codebase analysis**: `docs/features/skill-librarian/skill-librarian-codebase.md`
- **Design doc**: `docs/features/adaptive-skill-system/adaptive-skill-system.md` (Layer 5: Librarian / Promotion Pipeline)
- **Key source files**: experience.py, models.py, distiller.py, registry.py, loader.py, agent.py, derived_skill.py, protocols.py
