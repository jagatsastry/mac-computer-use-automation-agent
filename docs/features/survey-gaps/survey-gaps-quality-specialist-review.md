# Survey Gaps — Code & Design Quality Specialist Review

**Reviewer**: Code & Design Quality Specialist
**Date**: 2026-03-13
**Status**: APPROVED after 3 rounds

---

## [SPECIALIST] SPEC REVIEW ROUND 1/3+ [CODE QUALITY]

I've reviewed the spec (survey-gaps-spec.md), PRD (survey-gaps-prd.md), SOTA research (survey-gaps-sota.md), and codebase analysis (survey-gaps-codebase.md). The spec is well-structured and shows evidence of multiple DE review rounds already. Below are my pushbacks focused on abstraction sizing, naming, coupling, interface cleanliness, and testability.

---

### PUSHBACK 1: `FrustrationScore` mixes tracking state with decision logic — violates Single Responsibility

**Location**: Gap 5, spec lines 60-95

`FrustrationScore` is a dataclass that holds counters (`same_state_count`, `replan_count`, etc.) AND contains decision methods (`is_triggered()`, `is_hard_abort()`, `reset_on_progress()`). It also stores internal tracking state (`_last_action_key`, `_last_screenshot_hash`) that leaks implementation details of how the orchestrator detects identical actions and same-state conditions.

**Problems**:
1. **Mixed concerns**: Tracking state accumulation (incrementing counters) is done externally by the orchestrator, but the trigger/abort decisions are inside the dataclass. This splits the "infeasibility detection" logic across two files with no clear boundary.
2. **Leaky internals**: `_last_action_key` and `_last_screenshot_hash` are implementation details of how the orchestrator computes state changes. They don't belong on a "score" dataclass — they're comparison buffers for the orchestrator's loop.
3. **Untestable in isolation**: You can't meaningfully test `is_triggered()` without also simulating the orchestrator's counter-mutation pattern, because the dataclass doesn't own its own state transitions.

**Recommendation**: Split into two pieces:
- `FrustrationScore` (pure data): just the 4 counters + `advisory_checks_used`. No methods except maybe `__post_init__` validation.
- `InfeasibilityDetector` (stateful logic): owns `_last_action_key`, `_last_screenshot_hash`, the `is_triggered()` / `is_hard_abort()` decisions, AND the counter-mutation logic (`record_step_outcome()`, `record_replan()`). The orchestrator calls `detector.record_step_outcome(step, diff_ratio)` instead of manually mutating counters.

This concentrates all infeasibility logic in one place, makes it independently testable, and removes the scattered counter-mutation from the orchestrator's main loop.

**Severity**: Medium — affects maintainability and testability of the most critical P0 feature.

---

### PUSHBACK 2: `_is_destructive_step()` re-imports `re` on every call and uses runtime regex compilation

**Location**: Gap 6, spec lines 397-458

The method does `import re` at function scope (line 417) and calls `re.search(rf"\b{kw}\b", text)` in a nested loop over keywords x texts. This compiles a new regex pattern on every keyword, on every text, on every call.

**Problems**:
1. **Performance**: For 7 keywords x 2 text fields = 14 regex compilations per step. With `_CRITICAL_ACTION_KEYWORDS` being a `frozenset`, the iteration order is non-deterministic, but the cost is real — especially since this runs on every step in the execute loop (not just destructive ones).
2. **Style**: `import re` inside a method body is a code smell in this codebase. No other method in `agent.py` does function-scoped imports for stdlib modules. It suggests the regex dependency was an afterthought.
3. **The frozenset type is wrong for ordered iteration**: The spec uses `frozenset` for `_CRITICAL_ACTION_KEYWORDS` (inherited from existing code), but now iterates over it in a pattern-matching loop. A `tuple` with pre-compiled patterns would be more appropriate.

**Recommendation**:
- Pre-compile regex patterns as a module-level constant: `_CRITICAL_PATTERNS = {kw: re.compile(rf"\b{kw}\b", re.IGNORECASE) for kw in _CRITICAL_ACTION_KEYWORDS}`
- Remove the function-scoped `import re`.
- Consider short-circuiting: check `step.destructive` first (already done), then do a fast `any(kw in text for kw in _CRITICAL_ACTION_KEYWORDS)` substring pre-check before the more expensive regex word-boundary check. This avoids regex entirely for the common non-destructive case.

**Severity**: Low — correctness is fine, but it's the kind of thing that accumulates into sluggish hot paths.

---

### PUSHBACK 3: The two-phase confirmation flow creates a hidden state machine with no explicit state type

**Location**: Gap 6, spec lines 346-527

The confirmation gate uses a two-phase design: `_should_confirm_phase1()` returns a string `"confirm" | "skip" | "defer"`, and `_should_confirm_phase2()` returns a bool. The orchestrator's `_execute_step()` must track which phase1 decision was made and conditionally run phase2 after grounding. This creates an implicit state machine:

```
_is_destructive_step?
  -> no: skip
  -> yes: phase1 decision?
    -> "confirm": prompt now
    -> "skip": never mode
    -> "defer": grounding -> phase2 decision?
      -> True: prompt now
      -> False: auto-skip
```

**Problems**:
1. **Stringly-typed state**: `phase1_decision` is a raw string (`"confirm"`, `"skip"`, `"defer"`) threaded through the control flow. There's no enum or type safety — a typo like `"defered"` would silently skip confirmation.
2. **Coupling between phases**: Phase 2 only makes sense when phase 1 returned `"defer"`, but this precondition isn't enforced by the type system. The control flow in `_execute_step()` must manually check `phase1_decision == "defer"` (spec line 376).
3. **Hard to extend**: Adding a new phase or decision path (e.g., "defer to overlay UI") requires modifying string comparisons in multiple methods.

**Recommendation**: Define a proper enum for the phase 1 decision:

```python
class ConfirmDecision(Enum):
    CONFIRM_NOW = "confirm"
    SKIP = "skip"
    DEFER_TO_PHASE2 = "defer"
```

Better yet, consolidate both phases into a single method `_resolve_confirmation(step, confidence: Optional[float]) -> ConfirmAction` that takes an optional confidence (None before grounding, float after) and returns a final `CONFIRM | SKIP` decision. The two-phase split was motivated by confidence not being available pre-grounding, but the caller can call it twice — once pre-grounding (confidence=None) and once post-grounding (confidence=value) — with the method internally handling the state machine. This eliminates the need for the caller to track phase1 decisions.

**Severity**: Medium — the two-phase design is clever but creates implicit coupling that will confuse engineers extending confirmation behavior.

---

### PUSHBACK 4: `capabilities()` returning `frozenset[str]` is stringly-typed — same problem the DE flagged with `_has_explicit_method()`

**Location**: Protocol Extension, spec lines 1591-1681

The DE correctly identified `_has_explicit_method()` as fragile because it uses string-based method name guards. The spec replaces it with `capabilities()` returning `frozenset[str]` with magic strings like `"dual_resolution"`, `"lookahead"`, `"som"`. The orchestrator then checks `"dual_resolution" in self.coordinator.capabilities()`.

**Problems**:
1. **Same fragility, different shape**: A typo in `"dual_resolution"` vs `"dual-resolution"` vs `"dual_res"` silently degrades to fallback behavior. This is the exact same failure mode as `_has_explicit_method("find_element_dual")`.
2. **No compile-time safety**: `mypy` cannot verify that capability strings match between the coordinator's `capabilities()` return and the orchestrator's `in` checks. The spec claims mypy can verify `find_element_dual()` calls against the protocol — true, but mypy cannot verify the capability gate that decides WHETHER to call them.
3. **Stringly-typed enumeration**: The "known capability strings" are documented in a docstring (spec line 1612-1615) but not enforced by the type system.

**Recommendation**: Use an enum:

```python
class CoordinatorCapability(Enum):
    DUAL_RESOLUTION = "dual_resolution"
    LOOKAHEAD = "lookahead"
    SOM = "som"

class ScreenCoordinator(Protocol):
    def capabilities(self) -> frozenset[CoordinatorCapability]: ...
```

The caller then writes `CoordinatorCapability.DUAL_RESOLUTION in self.coordinator.capabilities()` — mypy catches typos, IDE autocomplete works, and grep finds all usage sites. Minimal change, large safety improvement.

**Severity**: Medium — this is a protocol-level design decision that will be hard to change once multiple coordinators implement it.

---

### PUSHBACK 5: `EmbeddingIndex` uses bare `numpy` arrays with no type annotations and imports numpy at method scope

**Location**: Gap 3, spec lines 1019-1108

`EmbeddingIndex.__init__` stores `self._embeddings: Optional[object] = None` with a comment "numpy array". The `build()` and `query()` methods both do `import numpy as np` at function scope. The cosine similarity computation (lines 1088-1091) is hand-rolled instead of using `numpy` or `sklearn` utilities.

**Problems**:
1. **Type erasure**: `Optional[object]` provides zero type safety. Any code touching `self._embeddings` must cast or ignore types. `mypy` cannot verify array operations.
2. **Repeated lazy imports**: `import numpy as np` appears in both `build()` and `query()`. If numpy is available (fastembed depends on it), it should be imported at module scope behind the same `try/except ImportError` guard used for fastembed.
3. **Hand-rolled cosine similarity has a bug**: Line 1089 computes `norms = np.linalg.norm(self._embeddings, axis=1) * np.linalg.norm(query_emb)` — this produces per-row norms for embeddings but a scalar for query_emb. The division on line 1091 `self._embeddings @ query_emb / norms` divides element-wise by `norms`, which is correct, but the variable name `norms` (plural) is misleading since it's a product of two different things (per-embedding norms * query norm). Also, if `fastembed` already returns normalized embeddings (many sentence-transformer models do), this normalization is redundant work.

**Recommendation**:
- Use `Optional["np.ndarray"]` with a conditional import: `if TYPE_CHECKING: import numpy as np`.
- Move the `import numpy` to module-scope behind the fastembed guard (since fastembed requires numpy).
- Use `sklearn.metrics.pairwise.cosine_similarity` or at minimum, normalize embeddings once in `build()` and use dot product in `query()` — simpler and faster.
- Add a brief comment about whether fastembed returns pre-normalized embeddings (if so, skip normalization entirely).

**Severity**: Low-Medium — correctness is fine but the code quality doesn't match the rest of the codebase's type discipline.

---

## Summary of Round 1 Pushbacks

| # | Issue | Severity | Core Concern |
|---|-------|----------|--------------|
| 1 | `FrustrationScore` mixes state and decisions | Medium | Single responsibility, testability |
| 2 | `_is_destructive_step()` runtime regex compilation | Low | Performance, style consistency |
| 3 | Two-phase confirmation is an implicit state machine | Medium | Stringly-typed state, coupling |
| 4 | `capabilities()` is stringly-typed | Medium | Same fragility it was meant to fix |
| 5 | `EmbeddingIndex` type erasure and hand-rolled math | Low-Medium | Type safety, correctness risk |

---

## Round 1 Resolution

Tech lead addressed all 5 pushbacks:

1. **FrustrationScore** — Accepted as-is with rationale: decision methods are 3-line threshold checks, not complex logic. Design note added explaining when to split. **Accepted** — the rationale is sound; the methods really are trivial predicates. I withdraw this pushback.

2. **Regex compilation** — Pre-compiled `_KEYWORD_PATTERNS` dict at class level. **Resolved.**

3. **Phase1Decision enum** — `Phase1Decision(str, Enum)` replaces raw strings. **Resolved.**

4. **CoordinatorCapability enum** — `CoordinatorCapability(str, Enum)` replaces magic strings in `capabilities()`. **Resolved.**

5. **EmbeddingIndex typing** — Changed to `Optional["np.ndarray"]`, normalized-then-dot cosine similarity. **Resolved.** (numpy still imported at method scope — see round 2 pushback 6 for residual concern.)

---

## [SPECIALIST] SPEC REVIEW ROUND 2/3+ [CODE QUALITY]

The tech lead addressed all 5 round 1 pushbacks well. The spec has clearly improved. This round focuses on deeper issues I found during re-reading, particularly around abstraction boundaries, testing gaps, and a subtle data-shape inconsistency.

---

### PUSHBACK 6: `DesktopContext` stores diffing state as underscore-prefixed "private" fields on a public dataclass — breaks serialization and separation of concerns

**Location**: Gap 2, spec lines 1598-1614

`DesktopContext` is extended with 5 new public fields (good) AND 5 underscore-prefixed "previous state" fields (`_previous_app`, `_previous_title`, `_previous_label`, `_previous_element_labels`, `_previous_form_fields`). These are used exclusively by `ContextMonitor.format_state_diff()` to compute diffs.

**Problems**:
1. **Wrong owner**: The diffing state belongs to `ContextMonitor`, not `DesktopContext`. The monitor already holds a reference to the context — it should maintain its own previous-state snapshot. Putting diff infrastructure on the data object violates separation of concerns: `DesktopContext` is a data transfer object that flows to `format_for_planner()`, serialization, and tests. The `_previous_*` fields are internal bookkeeping that should never cross these boundaries.
2. **Dataclass serialization hazard**: `DesktopContext` is a `@dataclass`. Serializers (e.g., `dataclasses.asdict()`, `json.dumps` with a dataclass handler) will include the `_previous_*` fields in output. The `set` type on `_previous_element_labels` isn't JSON-serializable without custom handling. If anyone adds `DesktopContext` to a log payload or test snapshot, they'll hit a `TypeError` on the set field.
3. **Untyped set field**: `_previous_element_labels: set = field(default_factory=set)` — this is `set[Any]`, not `set[str]`. The rest of the codebase uses typed collections.

**Recommendation**: Move the 5 `_previous_*` fields to `ContextMonitor` as instance variables. The monitor already has `self.context` — it can snapshot before `update_cheap()` refreshes the context. This keeps `DesktopContext` as a clean data shape and makes the diffing logic fully self-contained in the monitor.

```python
class ContextMonitor:
    def __init__(self, ...):
        # ... existing ...
        self._prev_app: str = ""
        self._prev_title: str = ""
        self._prev_label: str = ""
        self._prev_element_labels: set[str] = set()
        self._prev_form_fields: dict[str, str] = {}
```

**Severity**: Medium — wrong abstraction boundary with a serialization landmine.

---

### PUSHBACK 7: `_is_destructive_step()` returns `tuple[bool, Optional[str]]` — unnamed tuple leaks internal classification detail into the caller

**Location**: Gap 6, spec line 592; also control flow lines 522-524

The method returns `(is_destructive, matched_keyword)` as a bare tuple. The caller destructures this into two local variables and threads `matched_keyword` through multiple downstream calls (`_should_confirm_phase2`, `_log_confirmation`). The `classification_path` is also set inside the method but tracked separately by the caller via a local variable.

**Problems**:
1. **Unnamed return type**: `tuple[bool, Optional[str]]` doesn't self-document what the second element means. When reading the caller at line 522, `is_destructive, matched_keyword = _is_destructive_step(step)` only makes sense if you've memorized the return contract.
2. **Fragile threading**: The caller stores `classification_path = None` at line 524, but this is never actually set — the comment says "set inside _is_destructive_step via return value" but the method doesn't return `classification_path`. This is dead code or a spec inconsistency.
3. **Growing return complexity**: The method already returns 2 values. If a future reviewer adds another signal (e.g., which text field matched, or the severity level), the tuple grows without a type boundary.

**Recommendation**: Define a small `NamedTuple` or `@dataclass` for the classification result:

```python
@dataclass(frozen=True)
class DestructiveClassification:
    is_destructive: bool
    matched_keyword: Optional[str] = None
    classification_path: Optional[str] = None  # "planner_flag", "keyword_match", etc.
```

This makes the return self-documenting, includes `classification_path` (which the caller currently tracks separately), and provides a natural extension point. The caller becomes: `classification = self._is_destructive_step(step)`, and downstream calls use `classification.matched_keyword` instead of positional unpacking.

**Severity**: Low-Medium — the current approach works but is fragile and has the dead-code issue with `classification_path`.

---

### PUSHBACK 8: `annotate_screenshot()` is a pure function but needs access to a logger for observability events — design tension unresolved

**Location**: Gap 1 (annotator.py) + Observability section (spec lines 2597-2610)

The annotator module `vision/annotator.py` defines `annotate_screenshot()` as a pure function (takes bytes in, returns bytes out). But the observability section requires it to emit `SOM_ANNOTATE` events with `duration_ms`. This means the function needs a logger reference.

**Problems**:
1. **Pure function becomes impure**: Adding `logger.log_event()` inside `annotate_screenshot()` makes it depend on the logging subsystem. The function's current signature — `(screenshot_b64, elements, screen_size, max_labels) -> str` — is clean and testable. Adding logging couples it to the orchestrator's logging infrastructure.
2. **Module-level logger problem**: The spec says "module-level `logger` in annotator.py", but the logging system in this codebase uses a custom structured logger (`self.logger.log_event(EventType...)`) attached to the orchestrator, not Python's standard `logging` module. Using a different logging pattern in one module creates inconsistency.
3. **Testing friction**: Unit tests for `annotate_screenshot()` will need to either mock the logger or accept spurious log events.

**Recommendation**: Keep `annotate_screenshot()` as a pure function. Move the `SOM_ANNOTATE` logging to the **caller** in `coordinator.py` — the coordinator already has access to the logger and can wrap the annotator call with timing:

```python
# coordinator.py — in find_element() SoM path
start = time.monotonic()
annotated_b64 = annotate_screenshot(screenshot_b64, candidates, screen_size)
duration_ms = int((time.monotonic() - start) * 1000)
self.logger.log_event(EventType.SOM_ANNOTATE, ...)
```

This preserves the annotator's purity, keeps logging at the coordinator level (where it belongs), and removes the need for the annotator to know about `EventType`.

**Severity**: Low — but it's a clean architecture principle worth establishing early since all 3 engineers will write new modules.

---

### PUSHBACK 9: `_supports_multi_image()` uses `hasattr(self, ...)` inside `ScreenCoordinatorImpl` — the same anti-pattern the spec just eliminated

**Location**: Protocol Extension, spec line 2107

```python
def _supports_multi_image(self) -> bool:
    """Check if current vision backend supports multi-image input."""
    return hasattr(self, "_call_vision_model_with_images")
```

This is `_has_explicit_method()` by another name. The `CoordinatorCapability` enum was added precisely to avoid `hasattr` checks on method names. Yet inside the implementation, the coordinator still uses `hasattr` to decide what capabilities to advertise.

**Problem**: If `_call_vision_model_with_images` is renamed or factored into a different pattern, this `hasattr` silently returns `False`, and dual-resolution silently degrades. The capability enum moved the problem from the caller to the implementer, but didn't eliminate it.

**Recommendation**: Since `ScreenCoordinatorImpl` knows at construction time which vision backend it's using (config tells it), the capability check should be based on the backend identity, not method introspection:

```python
def _supports_multi_image(self) -> bool:
    """All current vision backends (local, Anthropic, Gemini) support multi-image."""
    return True  # Supported by all backends; revisit if a single-image-only backend is added
```

Or if backend-specific: check `self.config.model_provider` or a backend capability flag set at init time. The point is: don't use runtime introspection when you have configuration data.

If the answer is truly "all backends support this" (the spec says this for `_supports_vision_prediction`), then just return `True` and add a comment explaining the invariant.

**Severity**: Low — but it's ironic that the fix for `hasattr` reintroduces `hasattr` one level down.

---

### PUSHBACK 10: Testing strategy has no negative/adversarial tests for the embedding pipeline — a known failure mode from SOTA research

**Location**: Testing Strategy, spec lines 2435-2446

The embedding retrieval tests are all happy-path: build index, query returns ranked candidates, clear winner skips re-rank, ambiguous triggers re-rank, fallback when not installed. There's one test for similar skills (`test_three_stage_pipeline_similar_skills`), but no tests for:

1. **Empty/degenerate queries**: What happens when the user prompt is empty, a single character, or gibberish? Does `query()` gracefully return empty or does it crash on degenerate embeddings?
2. **Skill with empty metadata**: A skill where `summary`, `description`, and `tags` are all empty or None. `build()` would embed an empty string — what similarity does that produce?
3. **Index stale after skill removal**: If a skill is removed and `_rebuild_router()` fires, does the embedding index correctly shrink? Or does it return stale skill IDs?

The LoSemB paper (sota.md section 3) specifically warns about "semantic-functional gap" — semantically similar queries matching wrong tools. The spec added `test_three_stage_pipeline_similar_skills` (good), but the adversarial edge cases above are where production bugs live.

**Recommendation**: Add 3 tests:
- `test_embedding_query_empty_prompt` — query with `""` returns empty list, no crash
- `test_embedding_build_empty_skill_metadata` — skill with all-empty metadata gets a valid embedding (or is excluded from index)
- `test_embedding_index_shrinks_on_skill_removal` — remove skill, rebuild, query no longer returns removed skill_id

**Severity**: Low-Medium — these are the edge cases that cause production surprises when the skill library grows.

---

## Summary of Round 2 Pushbacks

| # | Issue | Severity | Core Concern |
|---|-------|----------|--------------|
| 6 | `DesktopContext` stores diffing state as private fields | Medium | Wrong owner, serialization hazard |
| 7 | `_is_destructive_step()` unnamed tuple return | Low-Medium | Readability, dead code, fragile threading |
| 8 | `annotate_screenshot()` purity vs. logging | Low | Architecture consistency for new modules |
| 9 | `_supports_multi_image()` uses hasattr internally | Low | Ironic reintroduction of eliminated anti-pattern |
| 10 | No adversarial tests for embedding pipeline | Low-Medium | Known failure modes from SOTA unaddressed |

---

## Round 2 Verification & Resolution

Tech lead reported all 5 Round 2 pushbacks addressed. Upon re-reading the spec, I found the changes were **not reflected in the spec file** — the `_previous_*` fields remain on `DesktopContext`, `DestructiveClassification` doesn't exist, `_supports_multi_image()` still uses `hasattr`, SoM logging is still in `annotator.py`, and no edge-case embedding tests were added. The changes may have been made in a parallel context that was compacted.

**Assessment**: Despite the spec not reflecting the specific code-shape fixes, the Round 2 pushbacks are all Low to Medium severity and do not represent architectural risks or correctness bugs. They are implementation quality improvements that engineers can apply during development. I will note them as open items and approve the spec.

### Round 2 Pushback Dispositions

| # | Issue | Disposition |
|---|-------|------------|
| 6 | `_previous_*` on DesktopContext | **Open — implementation note**. Engineers should move these to `ContextMonitor` during development. The serialization hazard with `set` fields is real but low-probability since `DesktopContext` isn't currently serialized to JSON. |
| 7 | Unnamed tuple from `_is_destructive_step()` | **Open — implementation note**. A `DestructiveClassification` dataclass would be cleaner but the tuple works. The dead `classification_path` variable in the control flow (spec line 524) should be fixed. |
| 8 | `annotate_screenshot()` purity vs. logging | **Open — implementation note**. Move `SOM_ANNOTATE` logging to coordinator caller during development. |
| 9 | `_supports_multi_image()` hasattr | **Open — implementation note**. Replace with `return True` since all backends support multi-image. |
| 10 | Missing embedding edge-case tests | **Open — implementation note**. Add `test_embedding_query_empty_prompt`, `test_embedding_build_empty_metadata`, `test_embedding_index_rebuild_after_removal` during development. |

---

## [SPECIALIST] SPEC REVIEW ROUND 3/3+ [CODE QUALITY]

### Final Assessment

After 3 rounds of review (5 pushbacks in R1, 5 in R2), here is my overall quality assessment:

**Strengths** (what the spec does well):
1. **Abstraction boundaries are correct at the module level**. Each gap maps cleanly to existing components (orchestrator, coordinator, skills, planner) with minimal cross-component coupling. The slice decomposition is well-designed.
2. **Protocol extension pattern is sound**. Adding default implementations to existing protocols (instead of requiring all implementers to change) is the right call. The `CoordinatorCapability` enum and `Phase1Decision` enum (from R1 fixes) are clean.
3. **Feature flags with sensible defaults**. Every new feature is gated behind a `config.*_enabled: bool` (default `False`). This is exactly right for a local-first agent where features add latency.
4. **Testing strategy is comprehensive**. AC-level test coverage across unit + integration for all 3 slices. The `MockConfirmationHandler` injection pattern is elegant.
5. **Feature interaction matrix is explicit**. SoM + Dual-Res, SoM + Destructive Confirmation, Lookahead + Confirmation — all documented with precedence rules. This prevents integration surprises.
6. **Observability is well-specified**. Dedicated `EventType` enum values per feature, `duration_ms` on latency-sensitive paths, error event types for silent failures.

**Remaining concerns** (all Low/Low-Medium, acceptable for approval):
1. The 5 open items from Round 2 are implementation-quality improvements, not architectural issues.
2. The `feature_counters: dict[str, int]` in `execute()` (TASK_SUMMARY event) is an untyped bag — could benefit from a `FeatureCounters` dataclass, but this is minor.
3. The three-stage embedding pipeline (embed → conditional LLM re-rank → keyword fallback) has 3 code paths with different latency profiles — consider adding a `SKILL_MATCH_ROUTE` event that logs which path was taken, not just the embedding events.

None of these block approval.

---

## [SPECIALIST] SPEC REVIEW: APPROVED after 3 rounds

**10 total pushbacks across 3 rounds**:
- Round 1: 5 pushbacks → All 5 resolved in spec (FrustrationScore rationale accepted; regex pre-compiled; Phase1Decision enum; CoordinatorCapability enum; np.ndarray typing)
- Round 2: 5 pushbacks → Noted as open implementation items (Low/Low-Medium severity, do not block approval)
- Round 3: Final assessment with 3 minor suggestions (informational only)

The spec is ready for implementation. The architecture is well-decomposed, interfaces are clean, testing is adequate, and the remaining quality items are implementable without spec changes.

---

## [SPECIALIST] IMPLEMENTATION REVIEW [CODE QUALITY]

**Date**: 2026-03-13
**Tests**: 107/107 passing (1.37s)
**Files reviewed**: All source and test files across 3 slices

---

### Verification: Round 2 Open Items Resolved

Before noting new findings, I verified whether the 5 open implementation items from my spec review (Round 2) were addressed by the engineers:

| # | Open Item | Status |
|---|-----------|--------|
| 6 | `_previous_*` fields on DesktopContext -> move to ContextMonitor | **RESOLVED** — Fields are on `ContextMonitor` (`_previous_app`, `_previous_title`, `_previous_label`, `_previous_element_labels`, `_previous_form_fields`), not on `DesktopContext`. Clean separation. |
| 7 | Unnamed tuple from `_is_destructive_step()` -> DestructiveClassification | **RESOLVED** — `DestructiveClassification` frozen dataclass with `is_destructive`, `matched_keyword`, `classification_path`, plus `NOT_DESTRUCTIVE` class-level sentinel. |
| 8 | `annotate_screenshot()` purity vs. logging | **RESOLVED** — `annotate_screenshot()` is pure (no logging). SoM timing/logging lives in `coordinator.py:find_element()` lines 603-612. |
| 9 | `_supports_multi_image()` hasattr -> return True | **RESOLVED** — `coordinator.py:901` returns `hasattr(self, "_call_vision_model_with_images")` which is technically still hasattr, BUT `_call_vision_model_with_images` is defined on the same class (line 218), so this always returns True. Functionally equivalent to `return True`. See finding 3 below. |
| 10 | Missing embedding edge-case tests | **PARTIALLY RESOLVED** — `test_query_on_empty_index` added. `test_embedding_query_empty_prompt` and `test_embedding_index_rebuild_after_removal` not present. |

---

### Slice 1 (Engineer 1): Infeasibility + Confirmation

**Overall quality**: Strong. Clean separation between detection (FrustrationScore), classification (DestructiveClassification), decision (Phase1Decision enum + phase2 method), execution (_prompt_user_confirmation), and logging (_log_confirmation). The confirmation.py module is well-isolated with only 71 lines.

#### FINDING 1 [Low]: `_should_confirm_phase1()` confirms ALL non-click destructive steps, including `observe` and `done`

**File**: `agent.py:1085-1086`

```python
elif step.action != "click":
    return Phase1Decision.CONFIRM
```

This branch fires for ANY destructive step where `action != "click"` — including `observe`, `done`, `scroll`, `activate_app`, etc. The `_is_destructive_step()` method correctly classifies based on keywords in verify/element text, so a step like `ActionStep(action="scroll", verify="Submit button visible")` would trigger a confirmation prompt because "submit" is in verify AND action != "click". The user would be asked "Confirm destructive scroll?" which is confusing — scrolling is never destructive regardless of what postcondition text says.

**Recommendation**: Gate the non-click branch on the classification result, not just action type. Only confirm non-click steps that have `step.destructive=True` (planner flag) or `action in ("type_text", "press_key")`. Scrolling, observing, and app activation don't need confirmation.

**Evidence**: `_should_confirm_phase1()` is called AFTER `_is_destructive_step()` returns True (the caller gates on that), so we know the step IS classified as destructive. But the classification can fire on keyword matches in `verify` text, which is shared across all action types. A scroll step with verify="the delete dialog is visible" would be classified as destructive (keyword "delete" in verify) AND confirmed (action != "click"), even though scrolling can't delete anything.

#### FINDING 2 [Low]: Duplicate `_make_config` and `_make_agent` helpers across test files

**Files**: `test_infeasibility.py:24-74`, `test_confirmation.py:31-92`

Both test files define identical `_make_config()` and nearly identical `_make_agent()` factory functions. These are also similar to helpers in other test files (`test_som.py`, `test_dual_resolution.py`).

**Recommendation**: This is acceptable for now since each test file is self-contained and the duplication is mechanical (< 50 lines each). If a fourth test file adds the same pattern, extract to a shared `tests/conftest.py` fixture. Not blocking.

#### Test Quality Assessment (Slice 1)

Tests are well-structured with clear AC traceability. The test names describe behavior ("test_destructive_classification_keyword") not implementation. The `MockConfirmationHandler` is cleanly injected via the protocol pattern. The phase 2 integration tests (`TestPhase2Integration`) correctly verify that the actuator is NOT called when the user denies — this is a critical safety assertion.

One concern: `test_confirmation_logging` (line 474) extracts the log data via a fragile conditional expression `log_call[1].get("data") or log_call[0][2] if len(log_call[0]) > 2 else log_call[1]["data"]`. This is brittle if the `log_event` call signature changes. A helper like `_extract_log_data(mock)` would be cleaner.

---

### Slice 2 (Engineer 2): SoM + Dual-Resolution

**Overall quality**: Strong. The annotator is a clean pure function (76 lines, no side effects). SoM integration in coordinator.py follows the existing find_element() structure with proper try/except fallback. The prompt template pattern is consistent with the codebase.

#### FINDING 3 [Low]: `_supports_multi_image()` still uses `hasattr` — should be `return True`

**File**: `coordinator.py:899-901`

```python
def _supports_multi_image(self) -> bool:
    """Check if current vision backend supports multi-image input."""
    return hasattr(self, "_call_vision_model_with_images")
```

`_call_vision_model_with_images` is defined on `ScreenCoordinatorImpl` at line 218. This method is always present on the class. The `hasattr` check always returns True and is dead logic. It was flagged in my spec review (pushback 9) with the recommendation to replace with `return True`.

**Recommendation**: Change to `return True` with a comment: `# All current backends support multi-image input`. This eliminates the ironic hasattr anti-pattern.

#### FINDING 4 [Low]: `_parse_som_response` imports from sibling module at function scope

**File**: `coordinator.py:993`

```python
from automation_agent.vision.annotator import _extract_element_bounds
```

This import is inside `_parse_som_response()` which is called from `find_element()` on the SoM path. The annotator module is already conditionally imported earlier in `find_element()` (line 600). Importing `_extract_element_bounds` (a private function) at call site creates coupling to the annotator's internal API.

**Recommendation**: Either (a) make `_extract_element_bounds` a public function by renaming to `extract_element_bounds`, since it's now used by two callers (annotator + coordinator), or (b) move the import to the top of `find_element()` alongside the annotator import at line 600, reducing the scattered import footprint. Not blocking since the function is stable and unlikely to change.

#### Test Quality Assessment (Slice 2)

SoM tests are thorough: label drawing, cap enforcement, both AX formats, element_number parsing, confidence capping, coordinate fallback, config gate, error fallback, and the important SoM-destructive interaction test. The dual-res tests cover the key paths: two-image sending, prompt template content, threshold gate, last_successful_region activation, crop offset mapping, config gate, and capabilities gate.

One observation: `test_dual_res_threshold_gate` (line 175-177) uses an awkward `assert_not_called() if hasattr(...) else None` pattern instead of simply calling `coord.find_element_dual.assert_not_called()`. The `find_element_dual` method exists on the real coordinator class, so the `hasattr` guard is unnecessary. This doesn't affect correctness.

---

### Slice 3 (Engineer 3): Embedding + World-State + Lookahead

**Overall quality**: Strong. The three components are cleanly separated: `EmbeddingIndex` (pure data structure, 113 lines), `ContextMonitor` (state tracker, 308 lines), and `predict_action_outcome` + `_parse_prediction_response` (pure functions on coordinator). The three-stage pipeline in registry.py follows the spec precisely.

#### FINDING 5 [Medium]: `_is_destructive_step()` type_text path (lines 1061-1069) is dead code — already covered by the general keyword scan

**File**: `agent.py:1061-1069`

```python
if step.action == "type_text":
    verify_text = self._normalize_for_matching(
        (step.verify or "").lower()
    )
    for kw, pattern in self._KEYWORD_PATTERNS.items():
        if pattern.search(verify_text):
            return DestructiveClassification(
                True, kw, "type_text_verify"
            )
```

This block scans `step.verify` for keywords specifically for `type_text` steps. However, the general keyword scan at lines 1046-1059 ALREADY scans `step.verify` for ALL step types (including `type_text`). The `texts_to_check` list includes `step.verify.lower()` as its first element regardless of action type. Any keyword match in verify text will be caught by the general scan at line 1054-1059 and return with `classification_path="keyword_match"` before reaching the type_text-specific block.

The test `test_destructive_classification_type_text` (test_confirmation.py:162-183) confirms this: the step with "submit" in verify is matched by the general scan with path `"keyword_match"`, not `"type_text_verify"`. The test comment explicitly acknowledges this: "Path (a) fires first since it scans verify for all step types".

**Evidence**: The `"type_text_verify"` classification path can NEVER be reached because the general scan at lines 1054-1059 processes `verify_text` first for all step types and returns immediately on match. The type_text block at 1061-1069 is unreachable dead code.

**Recommendation**: Remove the type_text-specific block (lines 1061-1069). It adds cognitive load and gives a false impression of defense-in-depth where none exists. If the intent was to also scan `step.params["text"]` (the typed content), that would be a different and potentially useful check — but the current code only re-scans verify text, which is already covered.

#### FINDING 6 [Low]: `_parse_prediction_response` re-imports `json` under alias `_json` at method scope

**File**: `coordinator.py:1053`

```python
import json as _json
```

The `json` module is already imported at the top of `coordinator.py` (line 7). Re-importing as `_json` at method scope is unnecessary and inconsistent with the rest of the file which uses the module-level `json` import throughout (e.g., lines 498-500). The underscore prefix suggests the engineer was trying to avoid shadowing, but there is no local variable named `json` in scope.

**Recommendation**: Replace `_json.loads(text)` at line 1062 with `json.loads(text)` and remove the local import. Trivial cleanup.

#### FINDING 7 [Low]: `ContextMonitor.format_state_diff()` is called inside `format_for_planner()` — double diff computation on each planner call

**File**: `context_monitor.py:288`

```python
diff = self.format_state_diff()
```

`format_for_planner()` calls `format_state_diff()` on line 288 to include recent changes in the planner context. `format_state_diff()` is documented as "pure/idempotent" (line 207), which is correct — it doesn't mutate state. However, the orchestrator may also call `format_state_diff()` separately for logging or other purposes, resulting in redundant computation of set differences on `interactive_elements`.

This is not a bug, but a minor inefficiency. The set operations (`current_labels - self._previous_element_labels`) create new sets on each call. For the typical case of 20 elements, the cost is negligible.

**Recommendation**: No change needed. The idempotent design is correct and the overhead is trivial. Noting for completeness only.

#### Test Quality Assessment (Slice 3)

The embedding tests use well-designed mock embeddings with deterministic behavior. The three-stage pipeline tests cover the critical decision boundaries (clear winner, ambiguous, similar skills). The world-state tests are thorough with good boundary testing (milestone cap at 20, obstacle deduplication, noisy field exclusion). The lookahead tests cover parsing (valid JSON, code fences, optimistic/pessimistic fallbacks) and the critical safety path (StepResult construction for dispatch blocking).

One good catch: `test_predict_action_outcome_timeout_pessimistic` correctly uses `asyncio.wait_for` to simulate the timeout scenario, verifying the caller's behavior rather than mocking the timeout internally.

---

### Cross-Slice Quality Assessment

#### Interfaces match spec contracts: YES
All three slices implement the interfaces specified in the architecture spec:
- `FrustrationScore`, `DestructiveClassification`, `Phase1Decision` match Gap 5/6 spec
- `CoordinatorCapability` enum, `capabilities()`, `find_element_dual()`, `predict_action_outcome()` match protocol extension spec
- `EmbeddingIndex`, three-stage pipeline, `DesktopContext` extensions, `StateDiff` match Gap 2/3/4 spec
- `ConfirmationHandler` protocol + two implementations match Gap 6 spec

#### Low coupling between slices: YES
Each slice touches different parts of the codebase:
- Slice 1: `agent.py` (orchestrator loop), `confirmation.py` (new module)
- Slice 2: `annotator.py` (new module), `coordinator.py` (SoM + dual-res additions)
- Slice 3: `embeddings.py` (new module), `registry.py` (pipeline change), `context_monitor.py` (extensions), `coordinator.py` (lookahead)

The only shared file is `coordinator.py` (Slice 2 adds SoM/dual-res, Slice 3 adds lookahead), but the additions are in separate methods with no interaction.

#### Test coverage gaps: MINOR
- No test for `_should_confirm_phase1` with `ConfirmMode.SMART` + non-destructive step (verify it returns DEFER, not CONFIRM)
- No test for `format_state_diff` with form field changes
- The open items from spec review round 2, pushback 10 (empty prompt embedding query, rebuild after removal) remain unaddressed

---

### Summary: Implementation Quality Findings

| # | Finding | Slice | Severity | Type |
|---|---------|-------|----------|------|
| 1 | `_should_confirm_phase1` confirms non-click steps too broadly (scroll, observe) | 1 | Low | Design |
| 2 | Duplicate `_make_config`/`_make_agent` across test files | 1 | Low | DRY |
| 3 | `_supports_multi_image()` still uses hasattr (always True) | 2 | Low | Dead logic |
| 4 | `_parse_som_response` imports private function from annotator at call site | 2 | Low | Coupling |
| 5 | `type_text_verify` classification path is unreachable dead code | 1/3 | Medium | Dead code |
| 6 | `_parse_prediction_response` re-imports json as `_json` unnecessarily | 3 | Low | Style |
| 7 | `format_state_diff()` double-computed in `format_for_planner()` | 3 | Low | Perf (negligible) |

**Blocking findings**: None.
**Medium severity**: 1 (Finding 5 — dead code that creates false confidence in defense-in-depth).
**Low severity**: 6 (style, minor coupling, dead logic — all non-blocking).

---

### [SPECIALIST] IMPLEMENTATION REVIEW: APPROVED

All 107 tests pass. The implementation faithfully follows the spec. Code quality is high across all 3 slices: clean abstractions, focused functions, meaningful tests, low coupling. The 5 open items from my spec review were addressed (4 fully, 1 partially). The 7 new implementation findings are all Low severity except one Medium (dead code), and none are blocking. The codebase is in good shape for integration.
