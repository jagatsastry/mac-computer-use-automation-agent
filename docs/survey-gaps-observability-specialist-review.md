# Survey Gaps — Observability Specialist Review

**Reviewer**: Observability Specialist
**Date**: 2026-03-13
**Status**: In Progress
**Spec version reviewed**: survey-gaps-spec.md (Draft — pending DE review)

---

## Review Summary

The spec adds 11 new `EventType` enum values and a dedicated Observability section (spec line 2006-2068) mapping each event to its call site, data payload, and owning slice. This is a good foundation. However, the observability story has significant gaps that would leave operators blind in several failure scenarios and make it difficult to measure feature adoption or diagnose production issues.

---

## Round 1 Findings

### PUSHBACK 1: No latency metrics on any new code path

**Severity**: High
**Location**: All 7 gaps — no latency instrumentation anywhere in the spec

The spec defines event types for "what happened" but never captures "how long it took." Every new code path introduces latency-sensitive operations:

- **Infeasibility check** (Gap 5): LLM call to `check_infeasibility()` — could be 3-10s. No duration logged.
- **User confirmation** (Gap 6): Blocking on user input — could be 5-60s. No wait-time logged.
- **SoM annotation** (Gap 1): PIL drawing on screenshot — expected <200ms but unverified. No duration logged.
- **Embedding query** (Gap 3): fastembed inference — claimed <10ms but no measurement. No duration logged.
- **LLM re-rank** (Gap 3): Conditional LLM call in the skill matching pipeline — 3-10s. No duration logged.
- **Dual-res grounding** (Gap 7): Extra VLM call with two images — 3-10s. No duration logged.
- **Lookahead prediction** (Gap 4): VLM call before dispatch — 3-5s per the PRD. No duration logged.
- **State diff computation** (Gap 2): Potentially O(n) on interactive elements. No duration logged.

**Impact**: Without duration data, operators cannot:
1. Detect performance regressions (e.g., embedding model swap doubles latency).
2. Validate the PRD's latency claims (e.g., "sub-10ms embedding inference").
3. Set SLOs or alerts on per-step execution time.
4. Diagnose why a task took 90 seconds instead of the expected 15.

**Recommendation**: Every `log_event()` call should include a `duration_ms` field in its data payload. Wrap each operation in a timer:

```python
import time

t0 = time.monotonic()
result = await self.planner.check_infeasibility(...)
duration_ms = (time.monotonic() - t0) * 1000

self.logger.log_event(
    EventType.INFEASIBILITY_CHECK,
    f"Infeasibility check: force={force}",
    data={
        "frustration": frustration.__dict__,
        "force": force,
        "duration_ms": round(duration_ms, 1),
    },
)
```

This pattern should be applied to all 11 event types.

---

### PUSHBACK 2: Error paths are silent — no structured error events for new features

**Severity**: High
**Location**: Gap 1 (SoM), Gap 3 (Embeddings), Gap 4 (Lookahead), Gap 7 (Dual-Res)

The spec logs success/decision events but has no structured logging for error paths within the new features:

1. **SoM annotation failure**: If `annotate_screenshot()` raises (PIL decode error, element format mismatch, coordinate out of bounds), the spec doesn't specify what happens. No error event, no fallback logging. The `_extract_element_bounds()` fallback to `(el.get("x", 0), el.get("y", 0), 40, 20)` silently returns zeros for unknown formats — an operator would have no idea SoM was producing garbage coordinates.

2. **Embedding index build failure**: `_rebuild_router()` catches `ImportError` with `pass` (spec line 1146). If fastembed is installed but the model download fails, or if `build()` raises a numpy error, this is silently swallowed. The operator sees no event, no metric — embedding just silently stops working.

3. **Lookahead parse failure**: `_parse_prediction_response()` has an optimistic default on parse failure (spec line 1463-1499). The `logger.debug()` call is at debug level — not a structured event. An operator monitoring JSONL event logs won't see that lookahead predictions are consistently unparseable (which could indicate a model regression).

4. **Dual-res coordinate mapping errors**: If `crop_offset` is incorrect or coordinates overflow after mapping, no error event is emitted. The result just silently returns wrong coordinates.

5. **Infeasibility LLM parse failure**: `_parse_infeasibility_response()` is not shown in the spec. If the LLM returns malformed JSON, is there a structured error event? The spec is silent.

**Impact**: Silent failures in optional features are the hardest to diagnose. The feature appears "enabled" in config but is producing no useful results. Without error events, operators have no signal that something is wrong.

**Recommendation**: Add a new `EventType.FEATURE_ERROR` (or per-feature error types like `SOM_ERROR`, `EMBEDDING_ERROR`, `LOOKAHEAD_PARSE_ERROR`) and emit at every catch/fallback site. Each error event should include: `feature`, `error_type`, `error_message`, `fallback_used`. Example:

```python
except Exception as e:
    self.logger.log_event(
        EventType.SOM_ERROR,
        f"SoM annotation failed, falling back to raw coordinates: {e}",
        data={"error": str(e), "element_count": len(elements), "fallback": "raw_coordinate"},
    )
```

---

### PUSHBACK 3: No aggregate/summary event at task completion for new feature usage

**Severity**: Medium
**Location**: Spec Observability section (line 2006-2068) — missing task-level rollup

The spec logs per-event data but never aggregates feature usage at the task level. When `execute()` completes, there is no summary event that says: "This task used SoM 3 times, dual-res 2 times, embedding retrieval once, frustration score reached 2, confirmation was triggered 1 time, user approved."

Currently, to answer "how often is SoM being used in production?" or "what percentage of tasks trigger infeasibility checks?", an operator would need to:
1. Parse all JSONL events for a given run.
2. Group by run ID.
3. Count event types per run.
4. Aggregate across runs.

This is table-stakes for any new feature rollout — you need adoption metrics without post-hoc log analysis.

**Recommendation**: Add a `TASK_SUMMARY` event emitted at the end of `execute()` (both success and failure paths) with a rollup of all new feature activations:

```python
self.logger.log_event(
    EventType.TASK_SUMMARY,
    f"Task completed: success={result.success}",
    data={
        "success": result.success,
        "total_steps": len(step_results),
        "features_used": {
            "infeasibility_checks": frustration.advisory_checks_used,
            "infeasibility_aborted": result.infeasibility_reason is not None,
            "destructive_confirmations": confirmation_count,
            "som_annotations": som_count,
            "dual_res_groundings": dual_res_count,
            "embedding_queries": embedding_count,
            "embedding_rerank_skips": rerank_skip_count,
            "lookahead_predictions": lookahead_count,
            "lookahead_blocks": lookahead_block_count,
            "state_diffs": state_diff_count,
        },
        "final_frustration": frustration.__dict__ if frustration else None,
        "duration_ms": total_duration_ms,
    },
)
```

This requires adding simple counters in the execute() loop (increment when each feature fires), which is low-cost but high-value for adoption tracking.

---

### PUSHBACK 4: STATE_DIFF event is specified but has no call site in the execute() loop

**Severity**: Medium
**Location**: Spec line 2030-2046 (state diff logging)

The spec shows a code snippet for state diff logging "after each `context_monitor.update_cheap()` call in the execute() loop" but this is only in the Observability section — it is NOT reflected in the Gap 2 control flow or the orchestrator integration pseudocode. The Gap 2 section (spec line 1407-1421) modifies `_record_context()` to call `record_step_outcome()` but says nothing about emitting `STATE_DIFF` events.

This means Engineer 3 implementing Gap 2 could easily miss this because the observability section looks like supplementary documentation, not a spec requirement.

**Recommendation**: Add the `STATE_DIFF` logging to the Gap 2 "Orchestrator Integration" section explicitly, alongside the `_record_context()` modification. Make it clear this is a spec requirement, not a suggestion.

---

### PUSHBACK 5: Confirmation logging (AC-10) is missing the classification path that triggered it

**Severity**: Medium
**Location**: Gap 6 `_log_confirmation()` data payload (spec line 625-647)

The `DESTRUCTIVE_CONFIRM` event logs `action`, `params`, `verify`, `destructive_flag`, and `decision`. But it does NOT log:
- **Which classification path triggered the destructive classification**: Was it `planner_flag` (AC-6b), `keyword_match` (AC-6a), or `type_text_field` (AC-6c)?
- **Which keyword matched** (for keyword_match path).
- **Which phase confirmed**: Phase 1 (pre-grounding) or Phase 2 (post-grounding)?
- **The grounding confidence** (for phase 2 decisions).

Without this, an operator cannot answer:
- "Are we over-prompting users because of false-positive keyword matches?" (approval fatigue — a cited failure mode in sota.md section 6)
- "Is the planner reliably setting the destructive flag, or are we relying entirely on keyword fallback?"
- "What's the confidence distribution when we skip confirmation in smart mode?"

**Recommendation**: Extend the `_log_confirmation()` data payload:

```python
data={
    "action": step.action,
    "params": step.params,
    "verify": step.verify,
    "destructive_flag": step.destructive,
    "decision": decision_str,
    "classification_path": classification_path,  # "planner_flag", "keyword_match", "type_text_field"
    "matched_keyword": matched_keyword,           # null if planner_flag
    "confirmation_phase": phase,                  # 1 or 2
    "grounding_confidence": confidence,           # null if phase 1
}
```

This requires `_is_destructive_step()` to return a richer result (e.g., a named tuple with `is_destructive`, `classification_path`, `matched_keyword`) instead of a bare `bool`. The debug logging in `_is_destructive_step()` already computes this data — it just doesn't propagate it to the confirmation event.

---

## Round 1 Resolution

All 5 pushbacks addressed by tech lead:
1. `duration_ms` added to all 7 code paths via `time.monotonic()` wrapping.
2. Four new error event types added: `DESTRUCTIVE_CONFIRM_ERROR`, `SOM_ERROR`, `EMBEDDING_ERROR`, `LOOKAHEAD_ERROR`.
3. `TASK_SUMMARY` event added with 10 feature counters + frustration snapshot.
4. `STATE_DIFF` call site moved into Gap 2's `_record_context()` section.
5. `DESTRUCTIVE_CONFIRM` payload enriched with `classification_path`, `matched_keyword`, `phase`, `confidence`, `duration_ms`.

**Status**: Round 1 resolved.

---

## Round 2 Findings

### PUSHBACK 6: TASK_SUMMARY does not include task outcome context needed for feature effectiveness analysis

**Severity**: Medium
**Location**: `_emit_task_summary()` (spec lines 158-200)

The `TASK_SUMMARY` event captures *which* features fired and *how often*, but does not include the task's outcome (`success`, `infeasibility_reason`, `error`, `total_steps_attempted` vs `total_steps_succeeded`). The `data` payload has `feature_usage` and `frustration_final`, but no result context.

This matters because feature adoption counts alone don't tell you whether the features are *helping*. An operator needs to correlate feature usage with outcomes:
- "Tasks where SoM fired 3+ times: what's the success rate vs. tasks where SoM never fired?"
- "Tasks where infeasibility detection aborted: how many steps were wasted before abort?"
- "Tasks where lookahead blocked dispatch: did the task ultimately succeed after replan?"

Without outcome context in the same event, answering these questions requires joining `TASK_SUMMARY` events with `TASK_COMPLETE` / `TASK_FAIL` events by `run_id` — doable but unnecessary friction.

**Recommendation**: Add outcome fields to `TASK_SUMMARY`:

```python
data={
    "feature_usage": feature_counters,
    "frustration_final": {...},
    "outcome": {
        "success": result.success,
        "infeasibility_reason": result.infeasibility_reason,
        "error": result.error[:200] if result.error else None,
        "steps_attempted": len(step_results),
        "steps_succeeded": sum(1 for sr in step_results if sr.success),
    },
},
```

---

### PUSHBACK 7: Embedding index build/rebuild has no observability — silent cold-start and rebuild latency

**Severity**: Medium
**Location**: Gap 3 `_rebuild_router()` (spec lines 1120-1146)

The spec adds `EMBEDDING_QUERY` for runtime queries and `EMBEDDING_ERROR` for query failures, but the *build* phase of the embedding index has no observability:

1. **Initial index build** at startup: `EmbeddingIndex.build(skills)` embeds all skills. For 10 skills with fastembed, this is ~100ms. For 50 skills, ~500ms. No event is emitted to indicate the index was built, how many skills were indexed, or how long it took. An operator cannot tell from logs whether the embedding index is actually populated.

2. **Index rebuild** on skill add/remove: `_rebuild_router()` re-calls `build()`. No event distinguishes initial build from rebuild, or tracks what triggered the rebuild.

3. **Model download**: The first time `fastembed` is initialized, it downloads the model (~50MB). This can take 10-60 seconds and has failure modes (network errors, disk space). No event covers this.

This means if the embedding index is silently empty (build failed, model download failed, zero skills matched the filter), the three-stage pipeline falls through to LLM routing on every query, and the operator sees `EMBEDDING_QUERY` events with zero results but has no idea *why*.

**Recommendation**: Add an `EMBEDDING_BUILD` event type emitted at the end of `EmbeddingIndex.build()`:

```python
self.logger.log_event(
    EventType.EMBEDDING_BUILD,
    f"Embedding index built: {len(self._skill_ids)} skills",
    data={
        "skill_count": len(self._skill_ids),
        "model": self._model_name,
        "trigger": "init" | "skill_add" | "skill_remove",
    },
    duration_ms=build_duration_ms,
)
```

---

### PUSHBACK 8: No observability for the `_is_element_absent()` -> infeasibility shortcut path (AC-4)

**Severity**: Medium
**Location**: Gap 5 control flow (spec lines 129-134), `_check_infeasibility()` (spec lines 153-213)

The AC-4 critical-path absence shortcut is a key decision point: when a click target is confirmed absent, the orchestrator *immediately* triggers an infeasibility check (bypassing the frustration threshold). This is a significant control flow branch that currently has no dedicated observability event.

The `INFEASIBILITY_CHECK` event does include `force=True` in the data payload, which distinguishes threshold-triggered from force-triggered checks. However, the *reason* for the force trigger — which element was absent, which AX method confirmed it, and whether `_is_element_absent()` fell back to "True because AX unavailable" — is not captured.

This is important because `_is_element_absent()` has a known fragility (codebase.md section 5): it falls back to `True` when AX is unavailable, which means the shortcut can fire spuriously on machines without AX access. An operator would see `force=True` but wouldn't know if it was a genuine absence or an AX fallback.

**Recommendation**: Add `absent_element`, `absence_method` ("ax_confirmed" | "ax_fallback"), and `element_description` to the `INFEASIBILITY_CHECK` data payload when `force=True`:

```python
self.logger.log_event(
    EventType.INFEASIBILITY_CHECK,
    f"Infeasibility check: force={force}, absent_element={element_desc}",
    data={
        "frustration": frustration.__dict__,
        "force": force,
        "absent_element": element_desc,         # NEW
        "absence_method": absence_method,       # NEW: "ax_confirmed" or "ax_fallback"
        "duration_ms": round(duration_ms, 1),
    },
)
```

---

### PUSHBACK 9: Testing strategy for observability events is underspecified

**Severity**: Medium
**Location**: Observability section, test coverage paragraph (spec line 2466-2468)

The spec says: "Each event type should be asserted in the corresponding unit test. Add assertions like `assert any(e.event_type == EventType.INFEASIBILITY_CHECK for e in captured_events)`."

This is the right idea but is too vague for 17 event types across 3 slices. Specific gaps:

1. **No test for `duration_ms` being populated**: The tests should verify that `duration_ms` is a positive integer, not just that the event exists. A test that passes with `duration_ms=None` would silently miss the latency instrumentation.

2. **No test for error event types**: The testing strategy tables (spec lines 1867-1961) list tests for success-path events but none of the 4 error event types (`DESTRUCTIVE_CONFIRM_ERROR`, `SOM_ERROR`, `EMBEDDING_ERROR`, `LOOKAHEAD_ERROR`) appear in any test table.

3. **No test for `TASK_SUMMARY`**: Not listed in any test table. No test verifies that the feature counters are accurately incremented.

4. **No test for `EMBEDDING_BUILD`** (if accepted from pushback 7): No test verifies the build event is emitted with correct skill count.

**Recommendation**: Add explicit test rows to each slice's testing strategy:

Slice 1:
- `test_infeasibility_check_emits_event_with_duration` — verify `INFEASIBILITY_CHECK` event has `duration_ms > 0`
- `test_confirmation_error_emits_event` — inject handler that raises, verify `DESTRUCTIVE_CONFIRM_ERROR`
- `test_task_summary_emits_feature_counters` — run 3 steps with 1 confirmation, verify `TASK_SUMMARY` counters

Slice 2:
- `test_som_error_emits_event` — pass invalid base64 to annotator, verify `SOM_ERROR`
- `test_som_annotate_event_has_duration` — verify `SOM_ANNOTATE` has `duration_ms > 0`

Slice 3:
- `test_embedding_error_emits_event` — mock fastembed to raise, verify `EMBEDDING_ERROR`
- `test_lookahead_error_emits_event` — mock VLM to raise, verify `LOOKAHEAD_ERROR`
- `test_embedding_build_event` — verify `EMBEDDING_BUILD` after `_rebuild_router()`

---

## Round 2 Verification

Re-read the spec to verify the 4 Round 2 fixes. **Result: None of the 4 pushbacks were addressed in the spec.**

Specifically:
1. **Pushback 6 (TASK_SUMMARY outcome)**: `_emit_task_summary()` at spec lines 206-219 still has only `feature_usage` and `frustration_final` in the data payload. No `outcome` dict with `success`, `infeasibility_reason`, `steps_attempted`, `steps_succeeded`.
2. **Pushback 7 (EMBEDDING_BUILD event)**: No `EMBEDDING_BUILD` event type in the EventType enum. The `_rebuild_router()` build failure at spec line 1710 uses `logging.getLogger(__name__).warning()` (stdlib), not a structured `log_event()`.
3. **Pushback 8 (AC-4 absence diagnostics)**: `INFEASIBILITY_CHECK` event at spec lines 306-311 has `{"frustration": ..., "force": force}` but no `absent_element` or `absence_method`.
4. **Pushback 9 (Observability test coverage)**: No new test rows in any slice's testing strategy table for error events, `duration_ms` assertions, `TASK_SUMMARY`, or `EMBEDDING_BUILD`.

---

## Round 3 Findings

### Re-assertion of Round 2 pushbacks (6-9) — STILL UNRESOLVED

All 4 Round 2 pushbacks remain unaddressed in the current spec. See Round 2 section above for full details. Summarized:

- **PB6**: Add `outcome` dict to `TASK_SUMMARY` data payload.
- **PB7**: Add `EMBEDDING_BUILD` event type; emit from `_rebuild_router()` after `build()`.
- **PB8**: Add `absent_element` and `absence_method` to `INFEASIBILITY_CHECK` when `force=True`.
- **PB9**: Add observability-specific test rows to each slice's testing strategy table.

### PUSHBACK 10: Embedding build failure uses stdlib logging, not structured event logging

**Severity**: Low-Medium
**Location**: Gap 3 `_rebuild_router()` (spec lines 1703-1713)

Related to pushback 7 but distinct: the `_rebuild_router()` catch block at spec line 1710 uses:
```python
logging.getLogger(__name__).warning("Embedding index build failed: %s...", e)
```

This is stdlib `logging.warning()`, not the structured `EventLogger.log_event()` API. The agent's structured event log (`events.jsonl`) will not contain this failure. Only the Python log (stdout/stderr depending on handler config) will show it.

The spec already established the pattern: error paths should use the dedicated `*_ERROR` event types (pushback 2, resolved in Round 1). `EMBEDDING_ERROR` exists but is specified only for *query* failures in `match()`. The *build* failure is the more critical case (it silently disables the entire feature) and should also use a structured event.

**Recommendation**: Replace the stdlib logger call with:
```python
logger.log_event(
    EventType.EMBEDDING_ERROR,
    f"Embedding index build failed: {e}",
    data={"error": str(e), "trigger": "rebuild", "skill_count": len(self._skills)},
)
self._embedding_index = None
```

Or if pushback 7 is accepted, use `EMBEDDING_BUILD` with an `error` field to distinguish success from failure builds.

---

### Conditional Approval Position

The spec's observability story is solid for Round 1 items (duration_ms, error events, task summary, state diff, confirmation enrichment). These were properly addressed and verified.

The 5 outstanding items (pushbacks 6-10) are all Medium or Low-Medium severity. They improve diagnostic depth and adoption measurement but are not blocking for a safe initial rollout. The core observability framework (17 event types, latency instrumentation, error event types) is sound.

**I can approve conditionally** if the tech lead acknowledges pushbacks 6-10 as post-merge follow-ups (tracked as issues). Or I can hold for another round if the team prefers full resolution before approval.

---

## Verdict: CONDITIONALLY APPROVED

Observability foundation is solid (Round 1 items verified). 5 outstanding medium-severity items (pushbacks 6-10) should be tracked as follow-up issues. No blocking concerns remain.

[SPECIALIST] SPEC REVIEW: APPROVED (conditional) after 3 rounds

---

## Changelog

| Round | Date | Pushbacks | Status |
|-------|------|-----------|--------|
| 1 | 2026-03-13 | 5 (latency metrics, error events, task summary, state diff call site, confirmation logging enrichment) | Resolved |
| 2 | 2026-03-13 | 4 (task summary outcome context, embedding build event, AC-4 absence method, observability test coverage) | Unresolved — not in spec |
| 3 | 2026-03-13 | 1 new (embedding build uses stdlib not structured logging) + 4 re-asserted from R2 | Conditionally approved |

## Follow-up Issues (post-merge)

| Issue | Pushback | Priority |
|-------|----------|----------|
| Add `outcome` dict to `TASK_SUMMARY` | PB6 | Medium |
| Add `EMBEDDING_BUILD` event type | PB7 | Medium |
| Add `absent_element`/`absence_method` to `INFEASIBILITY_CHECK` | PB8 | Medium |
| Add observability test rows to testing strategy | PB9 | Medium |
| Use structured `log_event()` for embedding build failure | PB10 | Low-Medium |

---

## Implementation Review

**Date**: 2026-03-13
**Tests**: All 107 tests pass (7 test files across 3 slices).

### Methodology

Read every source file listed in the review scope completely. Ran all tests. Evaluated each slice against the observability criteria: structured logging at decision points, EventType enum usage, `duration_ms` capture, error event emission, and diagnostic richness.

---

### Slice 1 (Engineer 1): Infeasibility + Confirmation

**Files reviewed**: `orchestrator/agent.py` (FrustrationScore, `_check_infeasibility`, `_is_destructive_step`, `_prompt_user_confirmation`, `_log_confirmation`), `orchestrator/confirmation.py`, `planner/planner.py` (`check_infeasibility`), `logging/models.py`, `tests/unit/test_infeasibility.py`, `tests/unit/test_confirmation.py`

#### What is done well

1. **EventType enums defined and used**: `INFEASIBILITY_CHECK`, `INFEASIBILITY_ABORT`, `DESTRUCTIVE_CONFIRM`, `DESTRUCTIVE_CONFIRM_ERROR` — all 4 are defined in `models.py:57-63` and emitted at the correct call sites.

2. **`duration_ms` captured for infeasibility checks**: `_check_infeasibility()` at `agent.py:963-1001` wraps the LLM call in `time.monotonic()` and passes `duration_ms=_dur` to `log_event()`. Both the timeout path (line 984) and the normal path (line 1001) emit duration. This directly addresses spec round 1 pushback 1.

3. **`duration_ms` captured for confirmation phase 1**: At `agent.py:687-698`, the `_confirm_start`/`_confirm_dur` pair captures user wait time for phase 1 confirmation, and `duration_ms=_confirm_dur` is passed to `_log_confirmation()`.

4. **`duration_ms` captured for confirmation phase 2**: At `agent.py:738-751`, same pattern for phase 2 confirmation.

5. **Rich confirmation logging** (`_log_confirmation` at `agent.py:1120-1151`): The data payload includes `action`, `params` (redacted via `_redact_params_for_log`), `verify`, `destructive_flag`, `decision`, `classification_path`, `matched_keyword`, `phase`, `confidence`, and `duration_ms`. This fully addresses spec round 1 pushback 5 and round 2 pushback 5.

6. **Error events on failure paths**: `_prompt_user_confirmation()` at `agent.py:1106-1118` catches handler exceptions and emits `DESTRUCTIVE_CONFIRM_ERROR` with action and error in the data payload. This prevents silent handler failures.

7. **Infeasibility timeout emits structured event**: At `agent.py:978-994`, `TimeoutError` is caught and an `INFEASIBILITY_CHECK` event is emitted with `{"force": force, "timeout": True}` and `duration_ms`.

8. **Infeasibility abort includes rich context**: At `agent.py:1005-1019`, `INFEASIBILITY_ABORT` includes `reason` and `absent_elements` in the data payload — sufficient for an operator to diagnose why the task was declared infeasible.

9. **Planner infeasibility parse failure logs warning**: `planner.py:546-550` logs a structlog warning with the raw response when JSON parsing fails. Not a structured event, but acceptable since this is a rare edge case and the planner is a separate component.

10. **Tests verify logging**: `test_confirmation.py:447-507` asserts that `DESTRUCTIVE_CONFIRM` events are emitted with correct `decision`, `classification_path`, `matched_keyword`, and `phase` fields. `test_confirmation.py:542-563` verifies error handler emits event.

#### PUSHBACK 1 (Slice 1): Infeasibility check event missing frustration context in data payload

**Severity**: Low-Medium
**Location**: `agent.py:997-1002`

The `INFEASIBILITY_CHECK` event on the normal (non-timeout) path emits:
```python
data={"force": force},
```

But does NOT include the frustration metrics that triggered the check (`same_state_count`, `replan_count`, `identical_action_count`). The timeout path at line 983 also omits these. The `INFEASIBILITY_ABORT` event at line 1008 includes `absent_elements` but not the frustration state.

An operator seeing `INFEASIBILITY_CHECK` in logs cannot tell what triggered it (same-state=3? replan=2? force=True from AC-4?) without cross-referencing other events. The data is available — `frustration` is a parameter to `_check_infeasibility()`.

**Evidence**: `agent.py:997-1002` — `data={"force": force}` with no frustration fields.

**Recommendation**: Add frustration snapshot:
```python
data={
    "force": force,
    "same_state_count": frustration.same_state_count,
    "replan_count": frustration.replan_count,
    "identical_action_count": frustration.identical_action_count,
    "advisory_checks_used": frustration.advisory_checks_used,
},
```

#### PUSHBACK 2 (Slice 1): No test asserts `duration_ms > 0` on infeasibility or confirmation events

**Severity**: Low
**Location**: `test_infeasibility.py`, `test_confirmation.py`

The tests verify that events are emitted and that data fields are correct, but none assert that `duration_ms` is a positive integer. The `test_infeasibility_timeout_returns_infeasible` test verifies the timeout path's ExecutionResult but does not check the emitted event's `duration_ms`. The `test_confirmation_logging` test verifies data fields but not `duration_ms`.

This means a regression that silently drops `duration_ms` (e.g., passing `None` instead of the measured value) would not be caught by tests.

**Evidence**: `test_infeasibility.py:133-153` checks `result.infeasibility_reason` but not event duration. `test_confirmation.py:447-478` checks `data["decision"]` but not `duration_ms`.

**Recommendation**: Add assertions like:
```python
assert log_call[1].get("duration_ms") is not None or log_call[0][-1] is not None
```

---

### Slice 2 (Engineer 2): SoM + Dual-Resolution

**Files reviewed**: `vision/annotator.py`, `vision/coordinator.py` (SoM path, `_parse_som_response`, `capabilities`, `find_element_dual`), `vision/prompts/find_element_som.md`, `vision/prompts/find_element_dual.md`, `logging/models.py`, `tests/unit/test_som.py`, `tests/unit/test_dual_resolution.py`

#### What is done well

1. **EventType enums defined**: `SOM_ANNOTATE`, `SOM_PARSE`, `SOM_ERROR`, `DUAL_RES_GROUNDING` — all 4 defined in `models.py:64-70`.

2. **SoM annotation duration captured**: At `coordinator.py:603-612`, `_ann_start`/`_ann_ms` wraps `annotate_screenshot()` and the `"som_annotate"` log message includes `duration_ms=_ann_ms` and `element_count`.

3. **SoM parse result logged**: At `coordinator.py:627-635`, `"som_parse"` log includes `element_number`, `confidence`, and `source="som"`.

4. **SoM error path emits warning**: At `coordinator.py:650-656`, the `except Exception` block logs `"som_error"` with `error=str(e)` and `element_count`. This prevents silent SoM failures.

5. **Dual-res timeout logged**: At `coordinator.py:937-942`, `asyncio.TimeoutError` on the dual-res VLM call emits a structlog warning with `timeout_s` and `element` description.

6. **Capability advertising works**: `coordinator.py:885-893` returns `frozenset` with `SOM`, `DUAL_RESOLUTION`, and `LOOKAHEAD` as appropriate.

#### PUSHBACK 3 (Slice 2): SoM and dual-res logs use structlog string keys, not EventType enums

**Severity**: Medium
**Location**: `coordinator.py:609-612`, `coordinator.py:627-635`, `coordinator.py:650-656`

The SoM logging at `coordinator.py:609` uses:
```python
logger.info("som_annotate", element_count=len(candidates), duration_ms=_ann_ms)
```

This is a **structlog** call with a string message, not a `self.logger.log_event(EventType.SOM_ANNOTATE, ...)` call via the EventLogger. The EventType enums `SOM_ANNOTATE`, `SOM_PARSE`, `SOM_ERROR`, and `DUAL_RES_GROUNDING` are defined in `models.py` but **never actually used** in the coordinator code.

The consequence: these events appear in Python's structlog output (stdout/stderr) but NOT in the structured `events.jsonl` file that operators use for post-hoc analysis. The JSONL event trace — the primary diagnostic artifact — has zero SoM or dual-res events.

This is because `ScreenCoordinatorImpl` does not have access to the `EventLogger` instance (it's injected into the orchestrator, not the coordinator). The coordinator only has `structlog.get_logger()`.

**Evidence**:
- `coordinator.py:18` imports `structlog`, not `EventLogger`
- `coordinator.py:609`: `logger.info("som_annotate", ...)` — structlog, not EventLogger
- `coordinator.py:633`: `logger.info("som_parse", ...)` — structlog
- `coordinator.py:651`: `logger.warning("som_error", ...)` — structlog
- No import of `EventType` in coordinator.py
- The `EventType.SOM_ANNOTATE`, `SOM_PARSE`, `SOM_ERROR`, `DUAL_RES_GROUNDING` enums are defined but orphaned

**Recommendation**: Either:
(a) Inject `EventLogger` into `ScreenCoordinatorImpl` and use `log_event()` calls, or
(b) Accept that coordinator-level events flow through structlog and add a structlog-to-JSONL bridge, or
(c) Move the SoM/dual-res event logging to the orchestrator layer (in `_find_element()` and `_execute_step()`) where `EventLogger` is available.

Option (c) is simplest and matches the existing pattern where the orchestrator logs `ELEMENT_SEARCH`/`ELEMENT_FOUND` events for coordinator results.

#### PUSHBACK 4 (Slice 2): No `duration_ms` on dual-res grounding path

**Severity**: Low-Medium
**Location**: `coordinator.py:903-954` (`find_element_dual`)

The `find_element_dual()` method does not capture or log `duration_ms` for the dual-resolution VLM call. The timeout is enforced via `asyncio.wait_for()` at line 930, and a warning is logged on timeout — but neither the success path nor the timeout path includes `duration_ms`.

Compare with the SoM path which correctly captures `_ann_ms` at line 607. The dual-res path has no timing instrumentation at all.

**Evidence**: `coordinator.py:903-954` — no `time.monotonic()` call anywhere in `find_element_dual()`.

**Recommendation**: Add timing:
```python
_dual_start = time.monotonic()
# ... existing code ...
_dual_ms = int((time.monotonic() - _dual_start) * 1000)
logger.info("dual_res_grounding", duration_ms=_dual_ms, ...)
```

---

### Slice 3 (Engineer 3): Embedding + World-State + Lookahead

**Files reviewed**: `skills/embeddings.py`, `skills/registry.py`, `orchestrator/context_monitor.py`, `vision/coordinator.py` (`predict_action_outcome`, `_parse_prediction_response`), `vision/prompts/predict_outcome.md`, `logging/models.py`, `tests/unit/test_embedding_retrieval.py`, `tests/unit/test_world_state.py`, `tests/unit/test_lookahead.py`

#### What is done well

1. **EventType enums defined**: `EMBEDDING_BUILD`, `EMBEDDING_QUERY`, `EMBEDDING_RERANK_SKIP`, `EMBEDDING_ERROR`, `LOOKAHEAD_PREDICT`, `LOOKAHEAD_BLOCK`, `LOOKAHEAD_ERROR`, `STATE_DIFF` — all 8 defined in `models.py:72-84`.

2. **Embedding build duration captured**: `registry.py:131-139` wraps `build()` in `_build_start`/`_build_ms` and logs `"embedding_index_built"` with `skill_count`, `model`, and `duration_ms`. This directly addresses spec round 2 pushback 7.

3. **Embedding query logging is rich**: `registry.py:233-245` logs `"embedding_query"` with `event_type`, `top_skill`, `top_sim`, full `top_k` list with IDs and similarities, truncated `prompt`, and `duration_ms`. This is excellent observability — an operator can see exactly what the embedding layer returned and how long it took.

4. **Embedding rerank-skip logged**: `registry.py:264-270` logs `"embedding_rerank_skip"` with `skill_id`, `similarity`, and `gap`. This makes the conditional LLM skip transparent.

5. **Embedding build failure degrades gracefully**: `registry.py:140-146` catches `ImportError`, `OSError`, `RuntimeError` and logs a warning. Sets `_embedding_index = None` so the system falls back to keyword matching.

6. **Lookahead parse failure logged at debug level**: `coordinator.py:1073-1079` logs `"Lookahead prediction parse failed"` with `exc_info=True` at debug level. Uses optimistic/pessimistic defaults based on destructiveness.

7. **World-state milestone trimming logged**: `context_monitor.py:177-181` logs `"milestones_trimmed"` with `dropped_count` and `dropped_first` at debug level when milestones exceed 20. Useful for diagnosing long-running tasks.

8. **Context monitor is idempotent**: `format_state_diff()` is pure/read-only (DE review issue 10). State advance happens only in `update_cheap()` via `_advance_state_snapshot()`. This prevents observability calls from mutating state.

9. **Tests cover pipeline stages**: `test_embedding_retrieval.py` tests clear-winner (skip re-rank), ambiguous (fire re-rank), similar-skills (gap < 0.15), config gate, graceful degradation. `test_world_state.py` tests all StateDiff scenarios, milestone/obstacle caps, deduplication. `test_lookahead.py` tests parse with code fences, optimistic/pessimistic fallbacks, round-trip, timeout, capabilities.

#### PUSHBACK 5 (Slice 3): Embedding and lookahead logs use structlog, not EventType enums — same issue as Slice 2

**Severity**: Medium
**Location**: `registry.py:234`, `registry.py:264`, `coordinator.py:1074`

Same structural issue as Slice 2 pushback 3. The EventType enums `EMBEDDING_QUERY`, `EMBEDDING_RERANK_SKIP`, `EMBEDDING_BUILD`, `EMBEDDING_ERROR`, `LOOKAHEAD_PREDICT`, `LOOKAHEAD_BLOCK`, `LOOKAHEAD_ERROR`, `STATE_DIFF` are all defined but the actual log calls use structlog string messages, not `EventLogger.log_event()`.

The registry does include `event_type="embedding_query"` as a *string field* in the structlog data (line 236), which is a step in the right direction — but it's a structlog kwarg, not a structured EventLogger event. These won't appear in `events.jsonl`.

**Evidence**:
- `registry.py:234`: `logger.info("embedding_query", event_type="embedding_query", ...)` — structlog
- `registry.py:264`: `logger.info("embedding_rerank_skip", event_type="embedding_rerank_skip", ...)` — structlog
- `registry.py:135`: `logger.info("embedding_index_built", ...)` — structlog
- `coordinator.py:1074`: `logger.debug("Lookahead prediction parse failed", ...)` — structlog
- None of these components have access to `EventLogger`

**Recommendation**: Same as Slice 2 pushback 3 — either inject EventLogger into these components or emit the structured events from the orchestrator layer where EventLogger is available. The orchestrator already logs `SKILL_MATCH` events when a skill is matched — the embedding/rerank events could be emitted alongside those.

#### PUSHBACK 6 (Slice 3): No logging in `predict_action_outcome()` success path

**Severity**: Low-Medium
**Location**: `coordinator.py:1003-1028`

`predict_action_outcome()` calls the VLM and parses the response, but emits no log event on the success path. There is only a debug-level log on parse failure at line 1074. An operator cannot see from logs:
- That a lookahead prediction was requested
- What the VLM predicted (`likely_success`, `risk`, `mismatch_reason`)
- How long the prediction VLM call took
- Whether the prediction blocked dispatch

The `LOOKAHEAD_PREDICT` and `LOOKAHEAD_BLOCK` event types are defined but never emitted anywhere in the codebase.

**Evidence**:
- `coordinator.py:1003-1028` — no `log_event` or structlog call on success path
- Grep for `LOOKAHEAD_PREDICT` and `LOOKAHEAD_BLOCK` — only defined in `models.py`, never used
- No `time.monotonic()` wrapper around the VLM call in `predict_action_outcome()`

**Recommendation**: Add timing and logging:
```python
_la_start = time.monotonic()
response = await self._call_vision_model(prompt, screenshot_b64)
_la_ms = int((time.monotonic() - _la_start) * 1000)
result = self._parse_prediction_response(response, ...)
logger.info("lookahead_predict", duration_ms=_la_ms, likely_success=result["likely_success"], risk=result.get("risk", ""))
```

And emit `LOOKAHEAD_BLOCK` from the orchestrator when a prediction blocks dispatch.

---

### Cross-Slice Summary

| Criterion | Slice 1 | Slice 2 | Slice 3 |
|-----------|---------|---------|---------|
| EventType enums defined | 4/4 | 4/4 | 8/8 |
| EventType enums actually used via EventLogger | **4/4** | **0/4** | **0/8** |
| `duration_ms` on timed operations | 3/3 (infeasibility, phase1, phase2) | 1/2 (SoM yes, dual-res no) | 1/3 (embed build yes, query yes via structlog, lookahead no) |
| Error events on failure paths | Yes (DESTRUCTIVE_CONFIRM_ERROR) | Yes (som_error via structlog) | Yes (embed build failure via stdlib warning) |
| Diagnostic context sufficient for debugging | Yes — rich payloads | Partially — structlog only | Partially — structlog only |
| Tests verify observability | Partially (events checked, no duration_ms assertions) | No duration/event assertions | No event assertions |

### Critical Finding

**EventType enum orphaning in Slices 2 and 3**: 12 of 16 new EventType enums are defined but never emitted via `EventLogger.log_event()`. They exist only in `models.py`. The coordinator and registry components use structlog for logging, which goes to Python's logging infrastructure (stdout/stderr) but NOT to the JSONL event trace. This means the primary observability artifact (`events.jsonl`) has zero coverage of SoM, dual-res, embedding, lookahead, and state-diff events.

Slice 1 is the exception — the orchestrator owns `EventLogger` and correctly uses `log_event()` for all 4 infeasibility/confirmation event types.

This is a **systemic architectural gap**: the coordinator and registry were designed without access to `EventLogger`, and the engineers correctly logged to structlog instead. The fix is either to inject EventLogger into these components or to relay the events through the orchestrator.

### Verdict

**Slice 1**: PASS — All observability requirements met. 2 low-severity improvements possible (frustration context in infeasibility event, duration_ms test assertions).

**Slice 2**: CONDITIONAL PASS — Logging is present and structured via structlog, but events do not flow to JSONL event trace. Missing `duration_ms` on dual-res path. Requires bridging structlog events to EventLogger or emitting events from orchestrator.

**Slice 3**: CONDITIONAL PASS — Same structlog-vs-EventLogger issue. Embedding query/build logging is rich. Lookahead success path has no logging at all. `LOOKAHEAD_PREDICT` and `LOOKAHEAD_BLOCK` are orphaned enums.

**Overall**: The observability *intent* is correct across all 3 slices — the right data is being captured at the right decision points. The gap is a wiring issue: 12/16 event types need to be connected to `EventLogger` so they appear in `events.jsonl`. This is a medium-effort fix (emit from orchestrator or inject logger) and does not require architectural changes.
