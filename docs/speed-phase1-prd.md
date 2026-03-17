# Speed Phase 1 — Product Requirements Document

**Date**: 2026-03-16
**Author**: Product Manager (Claude Opus 4.6)
**Status**: Draft
**Research inputs**: `docs/speed-phase1-sota.md`, `docs/speed-phase1-codebase.md`

---

## Problem Statement

The automation agent is functionally correct but slow for novel browser tasks. Vision model calls dominate runtime: element finding (29s/element via Molmo), verification (6-22s per Tier 2 check), and screen description (20-30s). For tasks without learned skill shortcuts, vision accounts for 60%+ of runtime.

Three specific bottlenecks are addressable in Phase 1 using infrastructure the codebase already has:

1. **type_text verification falls through to Tier 2 vision when `focused_value` is unpopulated.** The actuator's `get_state()` already calls `_get_browser_js()` for `focused_value` and `selected_text`, but any JS failure (timeout, non-browser) leaves both fields `None`, causing every type_text step to escalate to a 6-22s vision check.

2. **AX grounding confidence is hardcoded at 0.95** (`grounding_router.py:389`), regardless of whether the AX match was exact, substring, or weak token overlap. This means partial matches skip pre-click validation and occasionally target the wrong element, triggering costly replan cycles.

3. **Post-navigation click verification has no fast path.** After clicking a link that navigates to a new page, the verify condition (e.g., "page shows Product Details") always falls through to Tier 2 vision because Tier 1 has no page-content matchers beyond URL/domain.

---

## Scope

### In Scope

| Change | Workstream | Files Affected |
|--------|-----------|----------------|
| JS injection for type_text verification | Engineer 1 | `applescript_actuator.py`, `config.py` |
| AX confidence calibration from match_score | Engineer 2 | `grounding_router.py`, `accessibility.py` |
| JS-enriched page state for click verification | Engineer 3 | `applescript_actuator.py`, `verifier.py` |

### Out of Scope

- **Chrome AX enablement** — requires user to manually enable "Accessibility" in chrome://accessibility, which is user friction incompatible with zero-config goals.
- **JS click verification via `document.elementFromPoint()`** — SOTA doc notes this approach, but it is unreliable for overlapping elements, iframes, and shadow DOM. Excluded per SOTA risk assessment.
- **AX-only screen description** — replacing vision-based `describe_screen()` with AX tree traversal risks quality regression on complex web pages (SOTA doc Section 4 notes web AX traversal can take seconds on large pages).
- **Phase 2 changes** — AX disambiguation gate, web content tree walking, AX-augmented describe are deferred.

---

## Acceptance Criteria

All criteria use stable IDs (AC-1 through AC-15). Each is independently testable.

### Change 1: JS Injection for type_text Verification

**AC-1**: `get_active_element_value()` must return the value of `document.activeElement` via AppleScript JS injection in Safari and Chrome, or `None` for non-browser apps.

> *Rationale*: The method already exists (`applescript_actuator.py:444-456`) but calls `get_state()` internally, adding a redundant subprocess call. The contract is: return the string value of the focused form element, or `None` when JS injection is unavailable.
>
> *Codebase ref*: `_get_browser_js()` at `applescript_actuator.py:410-442` is the underlying transport. SOTA doc Section 1 confirms `document.activeElement.value` is the standard pattern used by Playwright, Puppeteer, and Selenium.

**AC-2**: `get_selected_text()` must return `window.getSelection().toString()` via the same pattern.

> *Rationale*: Same contract as AC-1 for selected text. Already exists at `applescript_actuator.py:458-470`.
>
> *Codebase ref*: SOTA doc Section 3 "Reliable JS Queries" table confirms `window.getSelection().toString()` is the standard approach.

**AC-3**: `get_state()` must populate `focused_value` and `selected_text` fields when the frontmost app is a browser and `js_verification_enabled` config is `True`.

> *Rationale*: `get_state()` at lines 552-564 already does this with two separate `_get_browser_js()` calls. The requirement codifies the existing behavior and ensures it remains gated behind the config flag. The codebase doc (Section 1.3) notes the gating pattern uses `getattr` with default `True`.
>
> *Codebase ref*: `get_state()` lines 552-564. Config flag at `config.py:467-470`.

**AC-4**: Existing Tier 1 type_text verification must resolve conclusively (pass/fail) when `focused_value` is populated, without escalating to Tier 2 vision.

> *Rationale*: The type_text matcher at `verifier.py:504-516` already returns `(True, evidence)` when the expected text is found in `focused_value`, and `(False, evidence)` when at least one field is populated but none match. The requirement ensures this conclusive resolution is preserved — when JS injection succeeds, the 6-22s Tier 2 vision check is avoided entirely.
>
> *Codebase ref*: Verifier type_text matcher at lines 504-516. Note the `focused_text` ghost field (codebase doc Section 12.3) — it is checked but never populated.

**AC-5**: JS injection must complete in <200ms per call (SOTA doc says ~50ms typical).

> *Rationale*: SOTA doc Section 1 reports ~0.27s (270ms) for a single `do JavaScript` call with Web Inspector disabled, and ~50ms for simple expressions. The 200ms threshold provides margin while ensuring the fast path is meaningfully faster than Tier 2 (6-22s). The 2s timeout in `_get_browser_js()` (`applescript_actuator.py:436`) is the upper bound before failure.
>
> *Codebase ref*: `_get_browser_js()` timeout at line 436. SOTA doc Section 1 "Key Failure Modes" table notes the 10-15s latency failure mode when Safari Web Inspector JSContext debugging is enabled.

**AC-6**: `js_verification_enabled` config flag must default to `True`.

> *Rationale*: The flag already exists at `config.py:467-470` with default `True`. This AC codifies the default and prevents it from being changed to `False` without deliberate product decision.
>
> *Codebase ref*: `config.py:467-470`.

### Change 2: AX Confidence Calibration

**AC-7**: AX grounding confidence must be calibrated from `match_score`: exact match (score=1.0) maps to confidence=0.95, partial match (score~0.7) maps to confidence~0.85, weak match (score~0.3) maps to confidence~0.71.

> *Rationale*: Currently `_ground_accessibility_match()` at `grounding_router.py:389` hardcodes `confidence=0.95` for all AX matches regardless of quality. The `_element_match_score()` function (`accessibility.py:380-414`) already computes a quality signal: 1.0 for exact matches, 0.9 for substring, 0.45+0.4*overlap for token overlap. This score is used only for sorting candidates (line 346), never for confidence. Calibrating confidence from score means weak matches (token overlap only) get lower confidence, triggering pre-click validation that catches wrong-element errors before they cause replan cycles.
>
> *SOTA ref*: Similo algorithm (SOTA doc Section 2) demonstrates multi-attribute weighted scoring with calibrated thresholds. The mapping function `0.6 + 0.35 * score` produces values compatible with the orchestrator's existing threshold gates (0.5 default, 0.9 critical).
>
> *Codebase ref*: `_element_match_score()` at `accessibility.py:380-414`. Confidence gating at `agent.py:2297-2312`. Threshold function at `agent.py:3011-3033`.

**AC-8**: Only exact AX matches (confidence >= 0.9) must skip pre-click validation; partial matches must go through crop validation.

> *Rationale*: The orchestrator's `_validate_candidate()` at `agent.py:3255-3306` is currently skipped for all AX matches (source="accessibility") and for confidence >= 0.9. With calibrated confidence, partial matches (score 0.7 -> confidence ~0.85) should no longer bypass validation. This prevents the wrong-element-click failure mode identified in the SOTA doc's "False high confidence on generic elements" row.
>
> *Codebase ref*: Pre-click validation skip at `agent.py:~2314-2317`. The skip condition checks both `source == "accessibility"` and `confidence >= 0.9`.

**AC-9**: The confidence formula must be `0.6 + 0.35 * min(match_score, 1.0)`.

> *Rationale*: This specific formula ensures:
> - Exact match (1.0) -> 0.95 (matches current hardcoded value, passes critical threshold)
> - Substring match (0.9) -> 0.915 (passes critical threshold — correct, since substring "Submit" in "Submit Order" is a reliable match)
> - Token overlap 0.7 -> 0.845 (passes default threshold, NOT critical — triggers pre-click validation for submit/pay/delete)
> - Token overlap 0.3 -> 0.705 (passes default threshold but well below critical)
> - Zero match (0.0) -> 0.6 (passes default 0.5 threshold — intentional, since AX found an element by role even if text didn't match)
>
> The `min(match_score, 1.0)` cap prevents the focused (+0.05) and enabled (+0.02) bonuses from pushing confidence above 0.95.
>
> *SOTA ref*: Similo research recommends tiered thresholds: >= 0.90 high confidence, 0.50-0.89 medium. The formula aligns with these tiers.

### Change 3: JS-Enriched Page State for Click Verification

**AC-10**: `get_state()` must populate `page_title` (from `document.title`) and `page_heading` (from `document.querySelector('h1')?.textContent`) for browser apps.

> *Rationale*: After a click that navigates to a new page, the verify condition typically references page content (e.g., "page shows Product Details"). Currently, Tier 1 can only check URL/domain — any content-based condition falls through to Tier 2 vision (6-22s). Adding `page_title` and `page_heading` to the state dict enables Tier 1 content matching.
>
> *Implementation note*: The codebase doc (Section 12.1) warns that adding 2 more `_get_browser_js()` calls increases `get_state()` to 6 subprocess calls. The SOTA doc (Section 1 "Recommended Approach") and codebase doc (Section 13.1) both recommend batching all JS fields into a single `JSON.stringify()` call to keep the total at 1 subprocess call instead of 4+.
>
> *SOTA ref*: SOTA doc Section 3 "Composite JS State Snapshot" pattern. The single-call JSON extraction is used by Playwright and Puppeteer internally.
>
> *Codebase ref*: `get_state()` at `applescript_actuator.py:497-567`. JS expression patterns at codebase doc Section 9.1.

**AC-11**: Tier 1 verifier must check `page_heading` and `page_title` tokens against the verify condition for click/open_url actions.

> *Rationale*: The verifier's `_verify_tier1()` currently has matchers for `activate_app`, `open_url` (URL/domain), `type_text` (focused_value), and `scroll` (position). There is no matcher that checks page content against arbitrary verify conditions. A new matcher should tokenize the verify string and `page_heading`/`page_title`, then check for meaningful token overlap (not just substring, to avoid false positives like "cart" matching "Carter").
>
> *Pattern*: Follow the type_text matcher at `verifier.py:504-516` — return `(True, evidence)` on match, `None` when fields are `None` (not `(False, ...)`, which would incorrectly reject when JS is unavailable).
>
> *Codebase ref*: Tier 1 matcher insertion point at `verifier.py:586-588` (after scroll, before final `return None`). Codebase doc Section 9.3 specifies the pattern.

**AC-12**: If `page_heading`/`page_title` are `None` (non-browser or JS failure), verification must fall through to Tier 2 (no false negatives).

> *Rationale*: The SOTA doc (Section 1 "Key Failure Modes") identifies multiple scenarios where JS injection fails: non-browser apps, TCC permission denial, "Allow JavaScript from Apple Events" not enabled, Web Inspector debug mode, cross-origin iframes. In all these cases, the new matcher must return `None` (inconclusive), not `(False, ...)`. This preserves the existing Tier 2 vision fallback.
>
> *Codebase ref*: Verifier tier contract (codebase doc Section 12.7): all tier methods return `Optional[Tuple[bool, str]]`. `None` means inconclusive.

### Cross-Cutting

**AC-13**: All existing tests must continue to pass (current count: ~1456).

> *Rationale*: Speed Phase 1 is additive — new capabilities layered onto existing tiers. No existing behavior should regress. The codebase doc (Section 8.7) notes key test files: `test_verifier.py` (22 tests), `test_grounding_router.py` (25 tests), `test_js_verification.py` (11 tests), `test_vision_arch_improvements.py` (79 tests).
>
> *Landmine*: `.env` leaks `AGENT_MODEL_PROVIDER=anthropic` into pydantic-settings during tests. All `_make_config()` helpers must pin `model_provider="local"` (codebase doc Section 10.6).

**AC-14**: Each change must have >= 8 unit tests covering happy path, error paths, and edge cases.

> *Rationale*: Minimum coverage to validate:
> - **Change 1** (8+ tests): Safari happy path, Chrome happy path, non-browser returns None, timeout returns None, config flag disabled, empty string handling, `get_state()` populates both fields, Tier 1 resolves conclusively.
> - **Change 2** (8+ tests): exact match confidence, substring match confidence, token overlap confidence, zero score confidence, formula boundary values, pre-click validation triggered for partial match, pre-click validation skipped for exact match, mock detection compatibility.
> - **Change 3** (8+ tests): page_title populated, page_heading populated, both None for non-browser, JS failure falls through, Tier 1 matches heading tokens, Tier 1 matches title tokens, no false positive on partial token, None fields return None (not False).
>
> *Test patterns*: Follow `test_js_verification.py` (patch subprocess), `test_grounding_router.py` (mock AX elements), `test_verifier.py` (mock actuator state). Codebase doc Sections 9.4-9.6.

**AC-15**: All new JS injection calls must fall through gracefully (return `None`) on timeout, error, or non-browser context.

> *Rationale*: `_get_browser_js()` already returns `None` for non-browser apps, `TimeoutExpired`, and `OSError` (`applescript_actuator.py:410-442`). Any new JS injection (including batched JSON extraction for AC-10) must preserve this contract. Callers must treat `None` as "data unavailable" and fall through to the next verification tier.
>
> *SOTA ref*: SOTA doc Section 1 "Key Failure Modes" table lists 7 failure modes, all of which must result in graceful `None` return rather than exceptions.

---

## Success Metrics

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| type_text verification latency | 6-22s (Tier 2 vision) | <200ms (Tier 1 JS) | Time from verify call to result for type_text steps with `focused_value` populated |
| Post-navigation click verification latency | 6-22s (Tier 2 vision) | <200ms (Tier 1 JS) | Time from verify call to result when `page_heading`/`page_title` match verify condition |
| Wrong-element replan cycles from AX | ~1 per 5-step task (anecdotal) | 0 for calibrated matches | Count of replan events where cause is "wrong element clicked" and source was AX |
| 5-step form fill verification time | ~120s (24s/step * 5 steps, assuming all Tier 2) | ~5s (1s/step * 5 steps, assuming Tier 1 resolves 4/5) | End-to-end verification time for a 5-step form fill on a browser target |
| Tier 2 avoidance rate (browser tasks) | ~20% (only URL/domain match at Tier 1) | ~60-80% (URL + title + heading + focused_value) | Percentage of browser verification conditions resolved at Tier 0 or Tier 1 |

---

## Research References

| Reference | Location | Relevance |
|-----------|----------|-----------|
| Single-call JSON extraction pattern | SOTA doc Section 1 "Recommended Approach" | Foundation for AC-10 batched JS injection |
| Similo scoring algorithm | SOTA doc Section 2, ACM TOSEM citation | Basis for AC-7/AC-9 confidence calibration formula |
| Playwright auto-retrying assertions | SOTA doc Section 3, Approach 1 | Pattern for JS-based verification with retry |
| Composite JS state snapshot | SOTA doc Section 3, Approach 2 | Pattern for batched page state extraction |
| Safari 10-15s latency failure mode | SOTA doc Section 1 "Key Failure Modes" | Risk context for AC-5 latency target |
| AX vs DOM tradeoffs | SOTA doc Section 4 | Justification for out-of-scope decisions |
| Verifier type_text matcher | Codebase doc Section 2.4, lines 504-516 | Existing pattern for AC-4 and AC-11 |
| `_ground_accessibility_match()` hardcoded 0.95 | Codebase doc Section 3.2, line 389 | Target for AC-7/AC-8/AC-9 |
| `get_state()` subprocess cost | Codebase doc Section 12.1 | Constraint for AC-10 implementation |
| `.env` model_provider leak | Codebase doc Section 12.6, 10.6 | Landmine for AC-13/AC-14 test implementation |
| `focused_text` ghost field | Codebase doc Section 12.3 | Known inconsistency in type_text matcher |
| Pre-click validation skip logic | Codebase doc Section 6.4 | Target for AC-8 behavior change |

---

## Implementation Sequencing

1. **Engineer 1 (JS injection layer) ships first.** Changes to `get_state()` affect the state dict consumed by both the verifier (Engineer 3) and indirectly by the grounding validation path (Engineer 2). Engineer 1 stabilizes the state dict contract.

2. **Engineer 2 (AX confidence) and Engineer 3 (page state verification) proceed in parallel** after Engineer 1's `get_state()` changes land. They have no mutual dependencies.

3. **Integration test pass** after all three changes merge. Run full `pytest` suite to validate AC-13.

---

## Risks

| Risk | Severity | Mitigation | AC Impact |
|------|----------|------------|-----------|
| "Allow JavaScript from Apple Events" not enabled in Safari | Medium | `_get_browser_js()` already returns `None` on failure; AC-15 ensures graceful fallback | AC-1, AC-2, AC-3, AC-10 |
| Safari Web Inspector debug mode causes 10-15s JS latency | Low | SOTA doc documents the fix (uncheck "Automatically Show Web Inspector for JSContexts"); AC-5 latency target will fail but AC-15 timeout-based fallback preserves correctness | AC-5 |
| Batched JSON.stringify adds quoting complexity | Low | Codebase doc Section 12.2 notes single-quote-inside-double-quote works in AppleScript; test with both Safari and Chrome | AC-10 |
| AX confidence calibration rejects previously-accepted matches | Medium | Formula AC-9 maps score 0.0 to confidence 0.6, which still passes the 0.5 default threshold; only critical-action threshold (0.9) rejects weak matches, which is the desired behavior | AC-7, AC-8 |
| Token matching in page_heading/page_title produces false positives | Low | Use token overlap with minimum threshold (e.g., 2+ tokens matching), not substring match; return `None` (inconclusive) rather than `(False, ...)` on ambiguous matches | AC-11 |
| `.env` leaks `model_provider=anthropic` into test config | High (blocks AC-13) | All `_make_config()` helpers must pin `model_provider="local"` per codebase doc Section 10.6 | AC-13, AC-14 |
