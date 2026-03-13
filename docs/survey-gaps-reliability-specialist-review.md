# Survey Gaps — Reliability Specialist Review

**Reviewer**: Reliability Specialist
**Date**: 2026-03-13
**Status**: In Progress — Round 1
**Documents Reviewed**: survey-gaps-spec.md, survey-gaps-prd.md, survey-gaps-sota.md, survey-gaps-codebase.md

---

## Round 1 Findings

### PUSHBACK 1: No timeout on `_check_infeasibility()` LLM call — unbounded blocking risk

**Severity**: P0 — can hang the entire orchestrator loop indefinitely

**Location**: spec.md §Gap 5, `_check_infeasibility()` method (lines 153-213)

**Issue**: The `_check_infeasibility()` method calls `self.planner.check_infeasibility()` which internally calls `self._call_llm(prompt)`. There is no timeout specified for this LLM call. If the LLM backend is slow, unresponsive, or the connection hangs (common with local Ollama/llama.cpp servers under memory pressure — see MEMORY.md note about running only one MLX model at a time), this call blocks the entire `execute()` loop indefinitely.

This is especially dangerous because `_check_infeasibility()` is triggered precisely when the system is already in a degraded state (frustration threshold hit). If the underlying cause of frustration is the VLM server being overloaded, the infeasibility check will also hang, creating a deadlock-like situation where the "safety valve" itself is stuck.

**Missing from spec**:
1. No timeout on `planner.check_infeasibility()` call
2. No fallback behavior when the LLM call times out
3. No circuit breaker to prevent repeated infeasibility checks from stacking up if each one hangs

**Recommendation**:
- Add `config.infeasibility_check_timeout_s: float` (default 10.0) — the LLM call must be wrapped in `asyncio.wait_for()` with this timeout.
- On timeout: treat as hard-abort with `infeasibility_reason = "Infeasibility check timed out after {N}s"`. This is the safe default — if we can't even determine feasibility, continuing is wasteful.
- Add this to the Configuration Summary and the FrustrationScore control flow.

---

### PUSHBACK 2: `ConsoleConfirmationHandler.confirm()` has no timeout — blocks automation indefinitely if user walks away

**Severity**: P0 — agent hangs forever waiting for stdin in attended mode

**Location**: spec.md §Gap 6, `ConsoleConfirmationHandler` (lines 562-580)

**Issue**: The `ConsoleConfirmationHandler.confirm()` method calls `asyncio.to_thread(input, ...)` with no timeout. If the user steps away from the terminal (common during long automation tasks, and the project explicitly warns users to "GTFO" during e2e tests), the agent blocks indefinitely on stdin.

This is worse than it appears: the entire `execute()` loop is blocked, meaning the `max_iterations` budget is not being consumed, the frustration score is not advancing, and no other safety mechanisms can fire. The agent is in a zombie state — consuming no resources but also making no progress and providing no feedback.

**Missing from spec**:
1. No timeout on the confirmation prompt
2. No behavior defined for when confirmation times out
3. No periodic reminder or escalation if user doesn't respond
4. No interaction with `max_iterations` budget — does waiting for confirmation count as an iteration?

**Recommendation**:
- Add `config.confirmation_timeout_s: float` (default 120.0) — wrap the `input()` call in `asyncio.wait_for()`.
- On timeout: default to **deny** (safe default — don't execute destructive action without explicit consent). Log with decision `"timeout_denied"`.
- Add a periodic stdout reminder every 30s: `"Still waiting for confirmation... (timeout in Xs)"`.
- Document that confirmation wait time does NOT count against `max_iterations`.

---

### PUSHBACK 3: Lookahead parse failure defaults to optimistic pass-through — silent safety bypass

**Severity**: P1 — defeats the purpose of lookahead as a safety gate

**Location**: spec.md §Gap 4, `_parse_prediction_response()` (lines 1460-1499)

**Issue**: The spec explicitly states that when VLM output is unparseable, the system returns `{"likely_success": True, ...}` (optimistic default). The rationale given is "blocking execution on a parse error in an optional feature is worse than missing a prediction."

This rationale is flawed for the specific case where lookahead is the ONLY safety gate. Per AC-31 and the spec's own Feature Interaction Matrix, when `confirm_destructive="never"` (unattended/headless mode) AND `lookahead_enabled=True`, lookahead is explicitly described as "the only safety gate." If the VLM consistently returns unparseable output (model mismatch, prompt drift, context overflow), the optimistic default silently disables the only safety mechanism, and destructive actions execute without any check.

**Missing from spec**:
1. No distinction between "optional advisory" and "sole safety gate" modes for the parse fallback
2. No monitoring/alerting for parse failure rate
3. No circuit breaker — if N consecutive parses fail, the system should degrade loudly, not silently

**Recommendation**:
- Track consecutive parse failures in a counter. After 3 consecutive failures, switch the default to **pessimistic** (`likely_success: False`) and log a warning: `"Lookahead parse failing consistently — blocking destructive actions as safety precaution"`.
- When `confirm_destructive="never"` (lookahead is sole safety gate), the default should always be pessimistic, not optimistic. The agent can retry/replan, which is better than executing a destructive action blindly.
- Add `LOOKAHEAD_PARSE_FAILURE` event type for observability.

---

### PUSHBACK 4: `_image_diff_ratio()` threshold of 0.05 is not validated against real screenshot noise

**Severity**: P1 — frustration detection may fire spuriously or never fire

**Location**: spec.md §Gap 5, Control Flow (lines 115-118)

**Issue**: The frustration score's same-state detection relies on `_image_diff_ratio(prev, new) < 0.05` to determine "no visible change." The 0.05 threshold is stated as a constant with no discussion of:

1. **JPEG compression artifacts**: Screenshots are captured as JPEG (per `capture.py`). Re-encoding the same screen at different times produces slightly different bytes due to compression non-determinism. This can cause diff > 0.05 even when nothing changed visually, preventing frustration from accumulating.
2. **Cursor blink / clock updates**: The macOS menu bar clock updates every minute. Cursor blink in text fields changes pixels. These create real pixel diffs that could prevent same-state detection from ever triggering on a static page.
3. **Loading spinners / animations**: A loading spinner on an otherwise static page will produce diff > 0.05, masking the fact that the page content hasn't changed.

If the threshold is too low, frustration never accumulates and infeasibility detection is effectively disabled. If too high, it fires on minor visual changes and causes premature abort.

**Missing from spec**:
1. No empirical basis for the 0.05 threshold
2. No discussion of screenshot comparison method (pixel-level? structural similarity? histogram?)
3. `_image_diff_ratio` is referenced but not defined in the spec — its implementation is inherited from existing code with no reliability analysis

**Recommendation**:
- Document what `_image_diff_ratio()` actually computes (the existing codebase should be checked).
- Consider using a structural similarity metric (SSIM) or limiting comparison to the main content area (excluding menu bar, dock, clock).
- Make the threshold configurable: `config.infeasibility_diff_threshold: float` (default 0.05) with a note that it should be tuned empirically.
- Add a unit test that verifies the threshold handles JPEG re-encoding noise (re-encode same screenshot, verify diff < threshold).

---

### PUSHBACK 5: No graceful degradation path when `fastembed` model download fails at startup

**Severity**: P1 — embedding index build crashes the skill registry initialization

**Location**: spec.md §Gap 3, `EmbeddingIndex.__init__()` (lines 1034-1042) and `_rebuild_router()` (lines 1120-1146)

**Issue**: The `EmbeddingIndex.__init__()` calls `TextEmbedding(model_name=model_name)` which downloads the model on first use (~50MB for `BAAI/bge-small-en-v1.5`). If this download fails (no internet, corporate firewall, disk full, corrupted cache), the constructor raises an exception.

The `_rebuild_router()` method wraps this in a `try/except ImportError`, but `TextEmbedding()` can also raise `RuntimeError`, `OSError`, `HTTPError`, or `ConnectionError` during download — none of which are caught. This means a network failure during model download crashes `_rebuild_router()`, which crashes `__init__()`, which crashes the entire agent startup.

**Missing from spec**:
1. The `except ImportError` is too narrow — only catches missing package, not download/initialization failures
2. No retry logic for transient network failures
3. No cached model validation (corrupted downloads)
4. No user-visible error message explaining the failure

**Recommendation**:
- Widen the except clause to `except (ImportError, Exception) as e:` with a logged warning: `"Embedding index unavailable: {e}. Falling back to LLM + keyword matching."`
- Better: use `except Exception as e:` in `_rebuild_router()` around the entire embedding block, so ANY failure in embedding init/build is caught and degraded gracefully.
- Add a startup health check log line: `"Skill retrieval: embedding={enabled/disabled/failed}, LLM router={enabled}, keyword={enabled}"` so operators can see the effective retrieval stack.

---

### PUSHBACK 6: No recovery path when `ConfirmationHandler` implementation raises an exception

**Severity**: P1 — unhandled exception in confirmation path crashes the orchestrator

**Location**: spec.md §Gap 6, `_prompt_user_confirmation()` (lines 529-536)

**Issue**: `_prompt_user_confirmation()` delegates to `self._confirmation_handler.confirm(step)` with no error handling. The `ConsoleConfirmationHandler` catches `EOFError` and `KeyboardInterrupt`, but:

1. Future implementations (`OverlayConfirmationHandler` mentioned in spec) may raise different exceptions (WebSocket errors, PyObjC failures, timeout errors).
2. If `asyncio.to_thread()` itself fails (thread pool exhaustion), the exception propagates uncaught.
3. A buggy mock handler in tests could raise unexpected exceptions.

Since this code runs in the critical path of the orchestrator loop, an unhandled exception here terminates the entire `execute()` call with a stack trace rather than a clean `ExecutionResult`.

**Recommendation**:
- Wrap `_prompt_user_confirmation()` in a try/except that catches `Exception`, logs the error, and defaults to **deny** (safe default):
  ```python
  async def _prompt_user_confirmation(self, step: ActionStep) -> bool:
      try:
          return await self._confirmation_handler.confirm(step)
      except Exception as e:
          logger.error(f"Confirmation handler failed: {e}", exc_info=True)
          self._log_confirmation(step, "error_denied")
          return False
  ```
- Add `"error_denied"` to the valid decision values in AC-10.

---

### PUSHBACK 7: World-state `completed_milestones` cap at 20 with oldest-dropped creates silent context loss

**Severity**: P2 — planner loses early milestone context on long tasks

**Location**: spec.md §Gap 2, `record_step_outcome()` (lines 1291-1307)

**Issue**: When `completed_milestones` exceeds 20 entries, the oldest are dropped: `self.context.completed_milestones[-20:]`. For long tasks (20+ steps), the planner loses visibility into early milestones. This is problematic because:

1. Early milestones often represent critical setup steps (login, navigation to correct page) that the planner needs to know happened.
2. If a replan occurs at step 25, the planner has no record of steps 1-5, potentially causing it to re-attempt completed setup.
3. The drop is silent — no log, no summary of what was dropped.

**Recommendation**:
- When milestones are dropped, emit a `STATE_DIFF` log event noting how many milestones were trimmed.
- Consider a two-tier approach: keep a compact summary of dropped milestones (e.g., "5 earlier milestones completed including: login, navigate to orders") alongside the detailed recent 20.
- Alternatively, make the cap configurable: `config.max_milestones: int` (default 20).

---

## Summary of Round 1

| # | Finding | Severity | Category |
|---|---------|----------|----------|
| 1 | No timeout on infeasibility LLM call | P0 | Timeout/Boundary |
| 2 | No timeout on confirmation prompt | P0 | Timeout/Boundary |
| 3 | Lookahead parse failure silently bypasses safety | P1 | Graceful Degradation |
| 4 | Image diff threshold unvalidated | P1 | Failure Mode |
| 5 | Embedding model download failure crashes startup | P1 | Graceful Degradation |
| 6 | Confirmation handler exception crashes orchestrator | P1 | Recovery Path |
| 7 | Milestone cap silently drops context | P2 | Recovery Path |

**Blocking issues**: #1 and #2 are P0 — both represent paths where the agent hangs indefinitely with no timeout or recovery. These must be addressed before the spec can be approved.

---

## Round 1 Resolution

All 7 findings addressed in spec revision. Verified fixes:

| # | Finding | Fix | Verified |
|---|---------|-----|----------|
| 1 | Infeasibility timeout | `asyncio.wait_for()` with `config.infeasibility_timeout_s` (default 30s), hard-abort on timeout. Timeout event logged with `duration_ms`. | Yes |
| 2 | Confirmation timeout | `asyncio.wait_for()` with `_DEFAULT_TIMEOUT_S = 120.0` on `ConsoleConfirmationHandler`, constructor-injectable for tests. Auto-deny on timeout with printed warning. | Yes |
| 3 | Lookahead parse safety | `is_hard_destructive` parameter added to `_parse_prediction_response()`. Pessimistic default for hard-destructive keywords AND all destructive steps when `confirm_destructive=NEVER`. | Yes |
| 4 | Image diff threshold | `_image_diff_ratio()` fully documented with algorithm, empirical rationale, per-pixel noise threshold of 20, and configurable via `config.infeasibility_same_state_threshold`. | Yes |
| 5 | Fastembed download | `_rebuild_router()` catches `(ImportError, OSError, RuntimeError)`, sets `_embedding_index = None`, logs warning. `EmbeddingIndex.__init__()` also catches `OSError/RuntimeError` with re-raise for caller. | Yes |
| 6 | Confirmation handler exception | `_prompt_user_confirmation()` wraps in try/except with `DESTRUCTIVE_CONFIRM_ERROR` event and fail-safe deny. | Yes |
| 7 | Milestone cap logging | Confirmed dropped count logging before trimming. | Yes — implicit in `record_step_outcome()` |

---

## Round 2 Findings

### PUSHBACK 8: No timeout on `predict_action_outcome()` VLM call — lookahead can hang like infeasibility

**Severity**: P1 — lookahead VLM call has same hanging risk as the infeasibility call that was fixed in Round 1

**Location**: spec.md §Gap 4, orchestrator integration (lines 1955-1964)

**Issue**: Round 1 correctly identified and fixed the timeout on `_check_infeasibility()` (now wrapped in `asyncio.wait_for()`). However, the lookahead `predict_action_outcome()` VLM call at line 1958 has the exact same risk but no timeout. The spec shows:

```python
prediction = await self.coordinator.predict_action_outcome(
    action=step.action, params=step.params,
    expected_observation=..., screenshot_b64=screenshot,
    is_hard_destructive=_is_hard,
)
```

This is a VLM call that sends a screenshot + prompt. On local backends (Molmo-MLX, Qwen), it can take 10+ seconds normally and hang indefinitely under memory pressure. Unlike infeasibility (which fires only when the system is already struggling), lookahead fires on every destructive step — a more frequent code path with the same hanging risk.

The observability section correctly wraps the call with `time.monotonic()` for `duration_ms`, but there is no `asyncio.wait_for()` timeout to bound the wait.

**Recommendation**:
- Add `config.lookahead_timeout_s: float` (default 15.0) — wrap in `asyncio.wait_for()`.
- On timeout: use the fallback default (optimistic or pessimistic depending on `is_hard_destructive`). Log with `LOOKAHEAD_ERROR` event type (which already exists).
- This is consistent with the infeasibility timeout pattern established in Round 1.

---

### PUSHBACK 9: `annotate_screenshot()` has no error handling — PIL failure crashes find_element path

**Severity**: P1 — image processing errors propagate uncaught through the grounding path

**Location**: spec.md §Gap 1, `annotate_screenshot()` (lines 987-1042) and coordinator integration (lines 1080-1113)

**Issue**: The `annotate_screenshot()` function performs multiple PIL operations (base64 decode, Image.open, draw operations, JPEG re-encode) with no try/except. Failure modes include:

1. **Corrupt screenshot bytes** — `base64.b64decode()` succeeds but `Image.open()` raises `PIL.UnidentifiedImageError` or `OSError`
2. **Zero-size bounding boxes** — elements with `width=0` or `height=0` create degenerate rectangles that may raise PIL errors
3. **Font rendering** — `draw.text()` can fail if the default PIL font is unavailable (some minimal environments)
4. **Memory pressure** — creating a copy of the screenshot as RGB + drawing on it doubles memory usage for large screenshots

The coordinator's `find_element()` calls `annotate_screenshot()` directly at line 1092 with no error handling. If it raises, the entire `find_element()` call fails, which fails `_find_element()`, which fails the step.

The observability section (lines 2638-2645) shows a try/except for SoM in `coordinator.py`, but this is described as a pattern, not embedded in the actual `find_element()` code shown at lines 1080-1113. The spec should make it explicit that the SoM path in `find_element()` is wrapped.

**Recommendation**:
- The SoM path in `find_element()` must wrap both `annotate_screenshot()` and the subsequent VLM call in try/except, falling through to the standard grounding path on failure. The observability section shows this pattern but it needs to be explicit in the `find_element()` code block at lines 1080-1113:
  ```python
  try:
      annotated_b64 = annotate_screenshot(screenshot_b64, candidates, screen_size)
      # ... VLM call with SoM prompt ...
  except Exception as e:
      logger.log_event(EventType.SOM_ERROR, f"SoM failed: {e}", ...)
      # Fall through to standard grounding path
  ```
- Add a test: `test_som_annotation_failure_falls_through` — mock `annotate_screenshot` to raise `OSError`, verify standard `find_element` path is used.

---

### PUSHBACK 10: `_is_destructive_step()` return type change breaks existing tests and callers

**Severity**: P1 — interface change from `bool` to `tuple[bool, Optional[str]]` is a breaking change with no migration path

**Location**: spec.md §Gap 6, `_is_destructive_step()` (lines 592-664) and Gap 4 orchestrator integration (line 1932)

**Issue**: The spec changes `_is_destructive_step()` from returning `bool` (original spec) to returning `tuple[bool, Optional[str]]` (after security review). This is used in two different slices:

1. **Slice 1 (Gap 6)**: Confirmation control flow at line 522: `is_destructive, matched_keyword = _is_destructive_step(step)`
2. **Slice 3 (Gap 4)**: Lookahead at line 1932: `_la_destructive, _la_keyword = self._is_destructive_step(step)`

The cross-slice dependency section (line 41) states: "If Slice 3 lands before Slice 1, lookahead uses a stub `_is_destructive_step()` that always returns `False`." But the stub would return `bool`, not `tuple[bool, Optional[str]]`. When Slice 3 code destructures the return value as a tuple, it will crash with `ValueError: not enough values to unpack` if the stub returns a plain `False`.

**Recommendation**:
- Specify that the stub `_is_destructive_step()` must return `(False, None)` not just `False`. Update the cross-slice dependency section to include the return type.
- Better: since both slices depend on this method's signature, define the method signature (not implementation) as a shared contract in the Domain Slice Decomposition section. Both slices can implement the internals, but the return type is fixed.
- Add a test in Slice 3: `test_destructive_step_stub_returns_tuple` that verifies the stub returns a tuple.

---

### PUSHBACK 11: No timeout on `_call_vision_model_with_images()` in dual-resolution grounding

**Severity**: P1 — same timeout gap pattern as findings #1 and #8

**Location**: spec.md §Gap 7, `find_element_dual()` (lines 1229-1233) and orchestrator integration (lines 1267-1272)

**Issue**: `find_element_dual()` calls `self._call_vision_model_with_images(prompt, [context_b64, screenshot_b64])` which sends TWO images to the VLM. This is inherently slower than single-image calls because:

1. Token count roughly doubles (two images = ~3200 tokens on Anthropic, or 2x inference time on local VLMs)
2. Local backends (Molmo-MLX) may OOM on two simultaneous high-res images, causing the server to hang

The orchestrator integration wraps the call with `time.monotonic()` for observability but has no `asyncio.wait_for()` timeout. This is the same pattern that was identified and fixed for infeasibility (finding #1) and should be fixed for lookahead (finding #8).

**Recommendation**:
- Reuse the existing VLM call timeout mechanism (if one exists in the codebase) or add `config.dual_res_timeout_s: float` (default 20.0).
- On timeout: fall back to single-image `find_element()` path. Log with `DUAL_RES_GROUNDING` event including `timeout: True`.
- Consider: the dual-res path already has a fallback (standard single-image path at line 1293). On timeout, the natural recovery is to try this fallback rather than failing the step entirely.

---

### PUSHBACK 12: `format_state_diff()` called from `format_for_planner()` creates hidden coupling

**Severity**: P2 — not a bug today, but a maintenance hazard

**Location**: spec.md §Gap 2, `format_for_planner()` (line 1769) and `_record_context()` (line 1803)

**Issue**: `format_state_diff()` is called from TWO locations:
1. `format_for_planner()` at line 1769 — called when building planner context
2. `_record_context()` at line 1803 — called after each step to log STATE_DIFF events

The spec correctly notes that `format_state_diff()` is "pure/idempotent" and that snapshot advance happens in `update_cheap()`. However, calling it from `format_for_planner()` means that every time the planner context is built (including during replan), a diff is computed. This has two subtle issues:

1. **Double logging**: If `_record_context()` runs after a step and then `format_for_planner()` runs during replan (both within the same `update_cheap()` cycle), the same diff appears in both the STATE_DIFF log event AND the planner context. This is not a bug (the data is correct) but operators may be confused by seeing the same diff in two places.

2. **Empty diff in planner context**: After `_record_context()` logs a diff, the next call to `format_for_planner()` during replan will show the same diff (idempotent). But if `update_cheap()` runs between them (it does — line 236), the snapshot advances and `format_state_diff()` may return `None` (no changes since last update). The planner then sees NO diff in its context, even though things changed since the PREVIOUS plan.

**Recommendation**:
- This is acceptable for now given the idempotent design, but add a comment in `format_for_planner()` noting that the diff shown is "since last `update_cheap()` call" not "since last plan". Engineers should understand that the diff window is tied to the update cycle, not the planning cycle.
- Consider caching the last non-None diff and including it in `format_for_planner()` when the current diff is None, with a `[stale]` marker.

---

## Summary of Round 2

| # | Finding | Severity | Category |
|---|---------|----------|----------|
| 8 | No timeout on lookahead VLM call | P1 | Timeout/Boundary |
| 9 | SoM annotate_screenshot() has no error handling in find_element() | P1 | Graceful Degradation |
| 10 | `_is_destructive_step()` return type breaks cross-slice stub | P1 | Interface Contract |
| 11 | No timeout on dual-res VLM call | P1 | Timeout/Boundary |
| 12 | format_state_diff() double-call from format_for_planner() | P2 | Maintenance Hazard |

**Blocking issues**: No P0s in Round 2. Findings #8, #9, #10, and #11 are P1 — all follow patterns identified in Round 1 and should be straightforward to fix with the same approaches already applied.

---

## Round 2 Resolution Verification

Re-read full spec after tech-lead stated all 5 findings addressed. Verification results:

| # | Finding | Status | Evidence |
|---|---------|--------|----------|
| 8 | Lookahead VLM timeout | **NOT FIXED** | Lines 2171-2177: `await self.coordinator.predict_action_outcome(...)` has no `asyncio.wait_for()`. Observability section (lines 2934-2939) has try/except for `Exception` which catches errors but NOT hangs. No `lookahead_timeout_s` in config summary (lines 2400-2480). |
| 9 | SoM error handling in find_element() | **PARTIALLY FIXED** | Observability section (lines 2914-2921) shows try/except pattern as a standalone code block. But the actual `find_element()` implementation (lines 1283-1311) does NOT include the try/except inline. These are in different spec sections — implementers may miss it. |
| 10 | Cross-slice stub return type | **NOT FIXED** | Line 42 still says "returns `False`" — not `(False, None)`. The method implementation shows `return False, None` (line 769) but the cross-slice dependency section does not specify the stub's return type for Slice 3. |
| 11 | Dual-res VLM timeout | **NOT FIXED** | Lines 1489-1494: `await self.coordinator.find_element_dual(...)` has no `asyncio.wait_for()`. No `dual_res_timeout_s` in config summary. |
| 12 | Diff window documentation | **PARTIALLY ADDRESSED** | `format_state_diff()` docstring (line 1926-1932) documents idempotency. No explicit comment in `format_for_planner()` about the diff window being tied to `update_cheap()` cycle. Acceptable — low risk. |

---

## Round 3 Findings

### Assessment: Findings #8, #10, #11 remain open. Finding #9 needs inline integration. Finding #12 is acceptable.

The outstanding items are not new findings — they are unresolved Round 2 issues. I'm re-stating them with reduced scope and specific fix text to make resolution trivial.

### PUSHBACK 13 (re-filed #8+#11): VLM calls in lookahead and dual-res lack timeout protection

**Severity**: P1 — same class of issue that was P0 for infeasibility in Round 1

**Location**:
- Lookahead: spec lines 2171-2177 (`predict_action_outcome()`)
- Dual-res: spec lines 1489-1494 (`find_element_dual()`)

**Issue**: The infeasibility LLM call correctly uses `asyncio.wait_for()` (line 274). These two VLM calls do not. Both send screenshots to the VLM backend, which is the same server that can hang under memory pressure. The try/except for `Exception` in the observability section (lines 2934-2939) catches raised errors but NOT indefinite hangs — a coroutine that never completes never raises.

**Minimum fix** — two options (either is acceptable):

**Option A: Per-feature timeouts** (more granular):
```python
# Config additions:
lookahead_timeout_s: float = Field(default=15.0, ...)
dual_res_timeout_s: float = Field(default=20.0, ...)

# Lookahead (line 2171):
try:
    prediction = await asyncio.wait_for(
        self.coordinator.predict_action_outcome(...),
        timeout=self.config.lookahead_timeout_s,
    )
except asyncio.TimeoutError:
    self.logger.log_event(EventType.LOOKAHEAD_ERROR, "Lookahead timed out", ...)
    prediction = _PESSIMISTIC_DEFAULT if _is_hard else _OPTIMISTIC_DEFAULT

# Dual-res (line 1489):
try:
    result = await asyncio.wait_for(
        self.coordinator.find_element_dual(...),
        timeout=self.config.dual_res_timeout_s,
    )
except asyncio.TimeoutError:
    self.logger.log_event(EventType.DUAL_RES_GROUNDING, "Dual-res timed out", ...)
    result = None  # Falls through to single-image path
```

**Option B: Generic VLM timeout** (simpler, covers all cases):
Add a `config.vlm_call_timeout_s: float` (default 30.0) that wraps ALL VLM calls at the coordinator level in `_call_vision_model()` and `_call_vision_model_with_images()`. This covers lookahead, dual-res, SoM, and any future VLM calls without per-feature config. The infeasibility timeout (30s) stays separate because it's an LLM/text call, not a VLM/image call.

I prefer Option B — it's more robust and prevents this class of issue from recurring with future VLM calls.

---

### PUSHBACK 14 (re-filed #10): Cross-slice stub return type must be explicit

**Severity**: P1 — will cause a runtime crash if Slice 3 lands before Slice 1

**Location**: spec line 42 (Domain Slice Decomposition)

**Issue**: Line 42 says the stub "always returns `False`". Line 2145 destructures as `_la_destructive, _la_keyword = self._is_destructive_step(step)`. A `False` stub will crash.

**Minimum fix** — replace line 42 text:

**Current**: "If Slice 3 lands before Slice 1, lookahead uses a stub `_is_destructive_step()` that always returns `False`."

**Proposed**: "If Slice 3 lands before Slice 1, lookahead uses a stub `_is_destructive_step()` that always returns `(False, None)` — matching the `tuple[bool, Optional[str]]` return type defined in Gap 6."

---

### PUSHBACK 15 (re-filed #9): SoM try/except must be shown inline in `find_element()` code block

**Severity**: P2 (downgraded from P1) — the pattern exists but in a different spec section

**Location**: spec lines 1283-1311 vs lines 2914-2921

**Issue**: The `find_element()` code block (lines 1283-1311) shows `annotate_screenshot()` called without error handling. The try/except is in the Observability section (lines 2914-2921) as a separate pattern block. Engineers implementing from the Gap 1 section will miss it.

**Minimum fix**: Add the try/except inline in the `find_element()` code block at lines 1288-1291, wrapping the entire SoM path. Reference the observability section for the error event pattern. Alternatively, add a comment: `# Error handling: see Observability §SoM Error Events`.

---

## Summary of Round 3

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| 13 | VLM calls lack timeout (re-filed #8+#11) | P1 | NEW — consolidated with Option A/B fix paths |
| 14 | Cross-slice stub return type (re-filed #10) | P1 | NEW — one-line text fix |
| 15 | SoM try/except not inline (re-filed #9) | P2 | Downgraded — pattern exists, just in wrong section |

**Assessment**: Findings #13 and #14 are P1 and must be addressed. #15 is P2 and acceptable as-is with a comment. Once #13 and #14 are resolved, the spec meets reliability requirements for approval.

**Note on approval path**: All original 7 Round 1 findings were fixed. Round 2 identified 5 refinements, of which 2 were fully addressed (#12 acceptable, observability error patterns added). The 3 remaining items are re-filed above with minimal fixes specified. After these are addressed, this reviewer is ready to approve.

---

## Round 3 Resolution Verification

All 3 re-filed findings verified as fixed:

| # | Finding | Status | Evidence |
|---|---------|--------|----------|
| 13a | Lookahead VLM timeout | **FIXED** | Line 2246: `asyncio.wait_for()` with `config.lookahead_timeout_s` (default 15.0). On timeout: pessimistic default for `_is_hard`, optimistic otherwise. Logs `LOOKAHEAD_ERROR` with `timeout: True` and fallback type. Config field at line 2578. |
| 13b | Dual-res VLM timeout | **FIXED** | Line 1528: `asyncio.wait_for()` with `config.dual_res_timeout_s` (default 20.0). On timeout: `result = None`, falls through to single-image path. Logs `DUAL_RES_GROUNDING` with `timeout: True`. Config field at line 2562. |
| 14 | Cross-slice stub return type | **FIXED** | Line 42 now reads: "returns `(False, None)` — matching the `tuple[bool, Optional[str]]` return type defined in Gap 6." |
| 15 | SoM try/except inline | **FIXED** | Lines 1318-1345: entire SoM path (annotate + VLM call + parse) wrapped in `try/except Exception`. Logs `SOM_ERROR` with element count. Falls through to standard grounding path. Comment references Observability section. |

---

## APPROVED

**[SPECIALIST] SPEC REVIEW: APPROVED after 3 rounds [RELIABILITY]**

### Reliability Posture Summary

The spec now has comprehensive reliability coverage across all 7 gaps:

**Timeout boundaries** (4 sites):
- `_check_infeasibility()` -- 30s, hard-abort on timeout
- `ConsoleConfirmationHandler.confirm()` -- 120s, auto-deny on timeout
- `predict_action_outcome()` -- 15s, optimistic/pessimistic fallback on timeout
- `find_element_dual()` -- 20s, fall-through to single-image on timeout

**Graceful degradation** (5 paths):
- SoM annotation failure -> standard coordinate grounding
- Embedding model download failure -> LLM + keyword matching
- Lookahead parse failure -> optimistic/pessimistic based on destructive classification
- Confirmation handler exception -> fail-safe deny
- Dual-res timeout -> single-image fallback

**Recovery paths**:
- Frustration score with configurable thresholds and advisory check cap
- Infeasibility detection with planner verification loop (bounded)
- Cross-slice stub contract specified with correct return type

**Observability**:
- Every new code path emits structured events with `duration_ms`
- Error-specific event types (`*_ERROR`) for all failure paths
- `TASK_SUMMARY` aggregate event for per-execution feature usage

### Residual Risks (acceptable)

1. `_image_diff_ratio()` 0.05 threshold is empirically justified but not calibrated per-display-type. Configurable via `infeasibility_same_state_threshold`. Low risk.
2. `format_state_diff()` diff window tied to `update_cheap()` cycle, not planning cycle. Documented as idempotent. Low risk.
3. Milestone cap at 20 with oldest-dropped. Logged before trim. Could lose early setup context on very long tasks (25+ steps). Low risk for typical use cases.

### Total Findings Across 3 Rounds

| Round | Findings | P0 | P1 | P2 | Fixed |
|-------|----------|----|----|----|----|
| 1 | 7 | 2 | 4 | 1 | 7/7 |
| 2 | 5 | 0 | 4 | 1 | 2/5 (3 re-filed) |
| 3 | 3 (re-filed) | 0 | 2 | 1 | 3/3 |
| **Total** | **15** | **2** | **10** | **3** | **15/15** |

---

## Round 3 Resolution

All 3 findings addressed in spec:

| # | Finding | Fix | Status |
|---|---------|-----|--------|
| 13 | VLM calls lack timeout | Lookahead: `asyncio.wait_for()` with `config.lookahead_timeout_s=15.0`. Dual-res: `asyncio.wait_for()` with `config.dual_res_timeout_s=20.0`. Both with appropriate fallback defaults and error logging. | **FIXED** |
| 14 | Cross-slice stub return type | Line 42 updated to `(False, None)` matching `tuple[bool, Optional[str]]` | **FIXED** |
| 15 | SoM try/except inline | Entire SoM path in `find_element()` wrapped in `try/except Exception` with `SOM_ERROR` event and fall-through to standard grounding | **FIXED** |

---

## [SPECIALIST] SPEC REVIEW: APPROVED after 3 rounds

**Status**: APPROVED

All 15 findings across 3 rounds resolved:
- Round 1: 7 findings (2 P0, 4 P1, 1 P2) — all fixed and verified
- Round 2: 5 findings (4 P1, 1 P2) — 2 fully addressed, 3 re-filed
- Round 3: 3 re-filed findings (2 P1, 1 P2) — all fixed

Key reliability improvements achieved:
- Timeouts on ALL external calls (infeasibility LLM, confirmation prompt, lookahead VLM, dual-res VLM)
- Graceful degradation for fastembed download failures
- Pessimistic parse defaults for safety-critical paths
- Injectable ConfirmationHandler with error recovery
- Cross-slice stub type safety

---

## Implementation Review (Post-Build)

**Date**: 2026-03-13
**Status**: Implementation verified against spec — 107/107 tests passing
**Files Inspected**: All source files from all 3 slices + all 7 test files

---

### Test Execution

```
107 passed in 1.30s
```

All 7 test suites pass: `test_infeasibility.py` (9), `test_confirmation.py` (26), `test_som.py` (13), `test_dual_resolution.py` (7), `test_embedding_retrieval.py` (11), `test_world_state.py` (15), `test_lookahead.py` (13), plus integration tests.

---

### Slice 1 (Engineer 1): Infeasibility + Confirmation — Reliability Verdict

**Files reviewed**: `orchestrator/agent.py` (FrustrationScore, `_check_infeasibility`, `_is_destructive_step`, `_prompt_user_confirmation`, `_log_confirmation`), `orchestrator/confirmation.py`, `planner/planner.py` (`check_infeasibility`), `config.py`

#### Finding R-IMPL-1: Infeasibility timeout — CORRECTLY IMPLEMENTED

**Evidence** (`agent.py:964-994`): `_check_infeasibility()` uses `asyncio.wait_for()` with `self.config.infeasibility_timeout_s` (default 30.0). On `TimeoutError`: returns `ExecutionResult(success=False, infeasibility_reason="...timed out...")`. Logs event with `duration_ms`. Test `test_infeasibility_timeout_returns_infeasible` confirms with `timeout=0.01`.

**Verdict**: Spec finding #1 fully addressed. No reliability gap.

#### Finding R-IMPL-2: Confirmation timeout — CORRECTLY IMPLEMENTED

**Evidence** (`confirmation.py:31-63`): `ConsoleConfirmationHandler` has `_DEFAULT_TIMEOUT_S = 120.0`, constructor-injectable. `asyncio.wait_for()` wraps the `asyncio.to_thread(input, ...)` call. On timeout: prints warning, returns `False` (deny). Catches `EOFError` and `KeyboardInterrupt`. Test `test_auto_deny_handler` verifies.

**Verdict**: Spec finding #2 fully addressed. No reliability gap.

#### Finding R-IMPL-3: Confirmation handler exception recovery — CORRECTLY IMPLEMENTED

**Evidence** (`agent.py:1106-1118`): `_prompt_user_confirmation()` wraps `self._confirmation_handler.confirm(step)` in `try/except Exception`, logs `DESTRUCTIVE_CONFIRM_ERROR`, returns `False` on failure. Test `test_prompt_user_confirmation_error_denies` verifies with `BrokenHandler` that raises `RuntimeError`.

**Verdict**: Spec finding #6 fully addressed. No reliability gap.

#### Finding R-IMPL-4: Planner `check_infeasibility` parse failure — CORRECTLY HANDLED

**Evidence** (`planner/planner.py:530-555`): `_parse_infeasibility_response()` catches `json.JSONDecodeError`, logs warning, defaults to `{"infeasible": True, "reason": "Failed to parse LLM response"}` — the safe default (abort rather than continue).

**Verdict**: Good fail-safe design. Parse failure = abort, not silent continuation.

#### PUSHBACK R-IMPL-5: `_check_infeasibility` absent-element extraction regex is fragile

**Severity**: P2

**Evidence** (`agent.py:952-956`):
```python
absent = [
    sr.error[len("Element absent:"):].strip()[:_MAX_ABSENT_LEN]
    for sr in step_results
    if sr.error and sr.error.startswith("Element absent:")
][:_MAX_ITEMS]
```

The extraction depends on the exact prefix `"Element absent:"` in `sr.error`. However, `_execute_step()` (line 844) generates errors with prefix `"Element not found:"`. These are different strings. The absent-element list passed to `check_infeasibility()` will ALWAYS be empty because no `StepResult.error` ever starts with `"Element absent:"`.

The frustration detection at line 449 uses `"not found"` (lowercase substring match), which is different again. This means the infeasibility check's `absent_elements` parameter is effectively dead code — the LLM receives `absent_elements=[]` even when elements were actually absent.

**Impact**: The infeasibility LLM call still works (it receives failure_history), but the `absent_elements` context is permanently empty. The LLM makes its decision without knowing which specific elements are missing.

**Recommendation**: Either change the absent extraction to match the actual error string (`"Element not found:"`), or better — add a structured `absent_element` field to `StepResult` rather than parsing error strings.

---

#### PUSHBACK R-IMPL-6: No retry budget on infeasibility LLM calls

**Severity**: P2

**Evidence** (`agent.py:940-1028`): `_check_infeasibility()` makes a single LLM call via `asyncio.wait_for()`. If the LLM returns garbage JSON (not a timeout — a fast but garbled response), `_parse_infeasibility_response` defaults to `infeasible=True`. The agent aborts.

The planner's `_call_llm()` has retry logic for 429/503 errors (via `_call_anthropic_llm` / `_call_gemini_llm`), but if the LLM returns valid HTTP with garbage content, there's no retry. A transient model issue (context overflow, truncated response) causes immediate abort.

This is acceptable because the alternative (retrying) risks the same hanging problem that finding #1 addressed. The `advisory_checks_used` counter effectively provides a slow-retry mechanism across infeasibility checks. Noting for documentation, not as a change request.

**Verdict**: Acceptable — fail-safe direction. No action needed.

---

### Slice 2 (Engineer 2): SoM + Dual-Resolution — Reliability Verdict

**Files reviewed**: `vision/annotator.py`, `vision/coordinator.py` (SoM path, `find_element_dual`, `_parse_som_response`), `orchestrator/agent.py` (`_find_element` dual-res integration)

#### Finding R-IMPL-7: SoM try/except fallback — CORRECTLY IMPLEMENTED

**Evidence** (`coordinator.py:594-656`): The entire SoM path is wrapped in `try/except Exception`. On failure: logs `som_error` with element count and error string. Falls through to the standard grounding path (`base_prompt + _call_vision_model`). Test `test_som_error_falls_back_to_standard` verifies by forcing `get_screen_size` to raise.

**Verdict**: Spec finding #9/#15 fully addressed. No reliability gap.

#### Finding R-IMPL-8: Dual-res timeout — CORRECTLY IMPLEMENTED

**Evidence** (`coordinator.py:929-942`): `find_element_dual()` wraps `_call_vision_model_with_images()` in `asyncio.wait_for(timeout=self.config.dual_res_timeout_s)`. On `TimeoutError`: logs warning with `timeout_s` and element description, returns `None`. Caller in `agent.py:1868` checks `if dual_result is not None` and falls through to single-image path. Test `test_find_element_dual_sends_two_images` verifies the happy path. Config default `dual_res_timeout_s=30.0`.

**Verdict**: Spec finding #11/#13b fully addressed. No reliability gap.

#### Finding R-IMPL-9: SoM confidence cap — CORRECTLY IMPLEMENTED

**Evidence** (`coordinator.py:977-979`): `_SOM_CONFIDENCE_CAP = 0.85`. `confidence = min(raw_conf, _SOM_CONFIDENCE_CAP)`. This ensures SoM results always trigger phase-2 confirmation for destructive actions (since 0.85 < `_CRITICAL_CONFIDENCE_THRESHOLD` of 0.9). Test `test_parse_som_confidence_capped_at_085` verifies. Cross-integration test `test_som_destructive_triggers_confirmation` verifies the confirmation cascade.

**Verdict**: Correct reliability design — SoM never short-circuits destructive confirmation.

#### Finding R-IMPL-10: annotate_screenshot pure function — degenerate box handling

**Evidence** (`annotator.py:56-57`): `if right <= left or bottom <= top: continue` — degenerate bounding boxes are skipped silently. No crash on zero-size elements.

**Verdict**: Correctly handles edge case. No reliability gap.

#### PUSHBACK R-IMPL-11: Dual-res in agent.py has no timeout wrapping at the caller level

**Severity**: P2

**Evidence** (`agent.py:1864-1873`): The comment says "Timeout is handled inside find_element_dual() itself — no outer wait_for needed." This is correct — `find_element_dual()` at `coordinator.py:930` wraps the VLM call in `asyncio.wait_for()`. HOWEVER, the outer call site does not protect against exceptions from `find_element_dual()` OTHER than timeout. If the method raises an unexpected exception (e.g., network error during the non-timed-out portion), it would propagate uncaught from `_find_element()`.

Looking more carefully: `_find_element()` has no try/except around the `find_element_dual()` call. The broader `execute()` method has a top-level `except Exception` at line 603, so the entire task would fail rather than crash. But the fallback to single-image path would NOT be attempted.

**Recommendation**: Wrap the `find_element_dual()` call in `_find_element()` with `try/except Exception` that falls through to the single-image path, consistent with the SoM error handling pattern. Log as `DUAL_RES_GROUNDING` with error detail.

---

### Slice 3 (Engineer 3): Embedding + World-State + Lookahead — Reliability Verdict

**Files reviewed**: `skills/embeddings.py`, `skills/registry.py` (embedding integration, `_rebuild_router`), `vision/coordinator.py` (`predict_action_outcome`, `_parse_prediction_response`), `orchestrator/context_monitor.py`

#### Finding R-IMPL-12: Embedding graceful degradation — CORRECTLY IMPLEMENTED

**Evidence** (`embeddings.py:33-41`): `EmbeddingIndex.__init__()` catches `(OSError, RuntimeError)` from `TextEmbedding()`, logs warning, and re-raises. The caller in `registry.py:140` catches `(ImportError, OSError, RuntimeError)`, sets `_embedding_index = None`, logs warning. Test `test_rebuild_router_graceful_degradation_on_error` verifies with `OSError("Model download failed")`.

**Verdict**: Spec finding #5 fully addressed. Three-layer degradation: embedding -> LLM router -> keyword fallback.

#### Finding R-IMPL-13: Embedding query on empty index — CORRECTLY HANDLED

**Evidence** (`embeddings.py:80-82`): `if self._embeddings is None or len(self._skill_ids) == 0: return []`. No crash on empty index. Test `test_query_on_empty_index` verifies.

**Verdict**: No reliability gap.

#### Finding R-IMPL-14: Lookahead parse failure — CORRECTLY IMPLEMENTED with pessimistic/optimistic split

**Evidence** (`coordinator.py:1030-1079`): `_parse_prediction_response()` has `is_hard_destructive` parameter. Parse failure uses `_PESSIMISTIC_DEFAULT` (blocks action) for hard-destructive, `_OPTIMISTIC_DEFAULT` (allows action) for non-destructive. Tests `test_parse_prediction_optimistic_fallback` and `test_parse_prediction_pessimistic_fallback` verify both paths.

**Verdict**: Spec finding #3 fully addressed. Safe default for destructive paths.

#### Finding R-IMPL-15: World-state resource limits — CORRECTLY IMPLEMENTED

**Evidence** (`context_monitor.py`):
- Milestones capped at 20 (`line 175-183`), keeps latest. Logs dropped count.
- Obstacles capped at 20 (`line 195-202`), keeps latest.
- Obstacle text truncated at 200 chars (`line 194`).
- Interactive elements display capped at 20 in `format_for_planner()` (`line 264`).
- `last_vision_description` truncated at 500 chars (`line 299`).
- Obstacles in planner output capped at 5 most recent (`line 284`).
- Milestones in planner output capped at 10 most recent (`line 277`).

Tests: `test_milestones_capped_at_20`, `test_obstacles_capped_at_20`, `test_record_step_outcome_obstacle_truncation` all verify.

**Verdict**: Spec finding #7 fully addressed. Comprehensive truncation at all output boundaries.

#### PUSHBACK R-IMPL-16: `predict_action_outcome()` NOT wired into `_execute_step()` — lookahead is unintegrated

**Severity**: P1 — the feature exists in coordinator but is NOT called from the orchestrator

**Evidence**: Searched `orchestrator/agent.py` for `predict_action_outcome`, `lookahead`, and found NO matches. The coordinator method `predict_action_outcome()` (`coordinator.py:1003-1028`) is fully implemented with timeout-aware design and pessimistic/optimistic fallbacks. The config fields `lookahead_enabled`, `lookahead_skip_when_confirmed`, and `lookahead_timeout_s` exist in `config.py`. But `_execute_step()` in `agent.py` has NO code that calls the coordinator's lookahead. The feature is spec'd, implemented in the coordinator, tested in isolation, but never invoked from the orchestrator.

The test `test_predict_action_outcome_timeout_pessimistic` tests the coordinator in isolation but not the integration. No test verifies that `_execute_step()` calls `predict_action_outcome()` before dispatching destructive actions.

This means: even with `lookahead_enabled=True`, no lookahead check runs before any action. The `config.lookahead_enabled` flag is dead. The entire lookahead safety mechanism is a no-op at the system level.

**Impact**: When `confirm_destructive=never` (headless/CI mode), the spec's Feature Interaction Matrix declares lookahead as "the only safety gate." Since it's not wired in, there IS no safety gate in headless mode with destructive actions.

**Recommendation**: Wire `predict_action_outcome()` into `_execute_step()` between the phase-2 confirmation gate and action dispatch. The spec already describes the integration point. This is a missing integration, not a missing implementation.

---

#### PUSHBACK R-IMPL-17: `predict_action_outcome()` has no `asyncio.wait_for` at the coordinator level

**Severity**: P2 (mitigated by R-IMPL-16 — the code isn't called)

**Evidence** (`coordinator.py:1003-1028`): `predict_action_outcome()` calls `self._call_vision_model(prompt, screenshot_b64)` which is a VLM call with no timeout wrapper. The spec required `asyncio.wait_for(timeout=config.lookahead_timeout_s)` at the call site (either coordinator or orchestrator). Neither location has it.

The `_call_vision_model()` has an implicit HTTP timeout via `httpx.AsyncClient(timeout=self.config.vision_server_timeout)` (default 300s), but that's a 5-minute timeout, far exceeding the spec's 15s requirement.

When R-IMPL-16 is fixed (lookahead wired in), the caller should add `asyncio.wait_for(timeout=self.config.lookahead_timeout_s)` around the call, consistent with the dual-res pattern in `find_element_dual()`.

**Recommendation**: Add the timeout wrapper when wiring in lookahead per R-IMPL-16.

---

### Cross-Slice Findings

#### Finding R-IMPL-18: `DestructiveClassification` dataclass — CORRECTLY REPLACES tuple

**Evidence** (`agent.py:84-100`): The spec's Round 3 finding #14 (cross-slice stub return type) is moot — the implementation uses a `DestructiveClassification` frozen dataclass with `__bool__` instead of a raw tuple. This is cleaner and eliminates the destructuring crash risk entirely. No stub is needed because Slice 1 and Slice 3 landed together.

**Verdict**: Better than spec. No reliability gap.

#### Finding R-IMPL-19: Config validation — CORRECTLY BOUNDED

**Evidence** (`config.py`): All new config fields have `gt=0` or `gt=0.0` validators:
- `infeasibility_timeout_s`: `gt=0.0`
- `dual_res_timeout_s`: `gt=0.0`
- `lookahead_timeout_s`: `gt=0.0`
- `infeasibility_same_state_limit`: `gt=0`
- `infeasibility_max_advisory_checks`: `gt=0`
- `skill_embedding_rerank_threshold`: `gt=0.0, le=1.0`

**Verdict**: No zero-timeout or negative-threshold bugs possible.

---

### Implementation Review Summary

| # | Finding | Severity | Slice | Status |
|---|---------|----------|-------|--------|
| R-IMPL-1 | Infeasibility timeout | -- | 1 | CORRECT |
| R-IMPL-2 | Confirmation timeout | -- | 1 | CORRECT |
| R-IMPL-3 | Confirmation exception recovery | -- | 1 | CORRECT |
| R-IMPL-4 | Infeasibility parse failure | -- | 1 | CORRECT |
| R-IMPL-5 | Absent-element extraction prefix mismatch | P2 | 1 | **PUSHBACK** |
| R-IMPL-6 | No retry on infeasibility LLM call | P2 | 1 | Acceptable |
| R-IMPL-7 | SoM try/except fallback | -- | 2 | CORRECT |
| R-IMPL-8 | Dual-res timeout | -- | 2 | CORRECT |
| R-IMPL-9 | SoM confidence cap | -- | 2 | CORRECT |
| R-IMPL-10 | Degenerate bounding box handling | -- | 2 | CORRECT |
| R-IMPL-11 | Dual-res caller no exception handling | P2 | 2 | **PUSHBACK** |
| R-IMPL-12 | Embedding graceful degradation | -- | 3 | CORRECT |
| R-IMPL-13 | Empty embedding index query | -- | 3 | CORRECT |
| R-IMPL-14 | Lookahead parse pessimistic/optimistic | -- | 3 | CORRECT |
| R-IMPL-15 | World-state resource limits | -- | 3 | CORRECT |
| R-IMPL-16 | Lookahead NOT wired into orchestrator | **P1** | 3 | **PUSHBACK** |
| R-IMPL-17 | Lookahead timeout not at coordinator level | P2 | 3 | **PUSHBACK** |
| R-IMPL-18 | DestructiveClassification dataclass | -- | cross | CORRECT |
| R-IMPL-19 | Config validation bounds | -- | cross | CORRECT |

### Pushback Counts

| Slice | Engineer | Pushbacks | P1 | P2 |
|-------|----------|-----------|----|----|
| 1 | Engineer 1 | 2 | 0 | 2 (R-IMPL-5, R-IMPL-6) |
| 2 | Engineer 2 | 2 | 0 | 2 (R-IMPL-11, accepted as P2) |
| 3 | Engineer 3 | 2 | 1 | 1 (R-IMPL-16 P1, R-IMPL-17 P2) |
| **Total** | | **6** | **1** | **5** |

### Overall Reliability Verdict

**CONDITIONAL PASS** — The implementation correctly addresses 13 of the 15 spec reliability requirements verified across 3 rounds. All error paths are tested. Timeouts are configured at infeasibility and dual-res call sites. Graceful degradation works for SoM, embeddings, and confirmation handler failures. Resource limits enforce caps on all list structures.

**One P1 blocker**: R-IMPL-16 — Lookahead (`predict_action_outcome`) is implemented in the coordinator but NOT wired into the orchestrator's `_execute_step()`. This is a complete feature gap, not a partial implementation. Until this is wired in, `config.lookahead_enabled=True` has no effect, and headless/CI mode has no pre-action safety gate for destructive steps.

**Five P2 issues**: All are acceptable for initial ship, but should be tracked:
- R-IMPL-5: Absent-element list is always empty (prefix mismatch)
- R-IMPL-6: Documented as acceptable
- R-IMPL-11: Dual-res exceptions not caught at caller
- R-IMPL-17: Blocked by R-IMPL-16 resolution
