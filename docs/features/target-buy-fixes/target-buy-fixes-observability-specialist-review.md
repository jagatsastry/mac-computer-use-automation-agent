# Observability Specialist Review: Target Buy Fixes (9 Gaps)

**Reviewer**: Observability Specialist
**Date**: 2026-03-13
**Spec Revision**: R7
**Verdict**: **NOT APPROVED** (2 blocking issues, 6 non-blocking observations)

---

## Review Methodology

Three progressively deeper review rounds, each examining the spec (R7), PRD (R3), SOTA research, and codebase analysis through the lens of structured logging, metrics, diagnostic hooks, and operator debuggability.

---

## Round 1: Missing Logging and Metrics in the Spec

### Finding R1-1 [BLOCKING]: No structured event logging for site entity extraction decisions (P0-1)

The spec adds `logger.info("Suppressing wrong-site candidate", ...)` and `logger.info("No skill for extracted site, falling to planner", ...)` using structlog. These are good but insufficient. The **extraction decision itself** — which entities were found, which patterns matched, and what the input prompt was — is not logged as a structured event.

The existing event logging system (`EventLogger` with `EventType` enums) is the primary operator observability mechanism. JSONL event logs in `AGENT_LOG_DIR` are what operators grep through. The structlog messages go to stderr and are ephemeral. If a customer reports wrong routing, the operator needs to reconstruct:

1. What site entity was extracted (or why none was)
2. Which candidates were filtered and why
3. What the final routing decision was

Currently the spec produces:
- `SKILL_MATCH` or `SKILL_NO_MATCH` event (existing) — but this doesn't say WHY the match happened or failed
- structlog messages for suppression — these are not in JSONL event logs

**What's missing**: A `SITE_ENTITY_EXTRACTED` event (or an extension to `SKILL_MATCH` event data) that includes: `{prompt, extracted_sites, known_sites_count, candidates_before_filter, candidates_after_filter, filtered_out}`. Without this, when routing goes wrong, the operator has to reproduce the prompt locally to debug — they can't see the extraction decision from logs alone.

**Specific gap**: The multi-site conflict path (`len(site_entities) > 1 → return None`) and the no-skill-for-site path both silently return `None`. The existing `SKILL_NO_MATCH` event fires, but the *reason* (multi-site conflict vs. no skill vs. no extraction) is indistinguishable in the event log.

**Recommendation**: Add event data to the existing `SKILL_MATCH` / `SKILL_NO_MATCH` events:
```python
self._emit(EventType.SKILL_NO_MATCH, {
    "reason": "multi_site_conflict",  # or "site_filter_empty" or "no_candidates"
    "extracted_sites": site_entities,
    "prompt_snippet": prompt[:100],
})
```

### Finding R1-2 [NON-BLOCKING]: Scroll verification tier used is not logged (P1-3)

The spec defines a 3-tier scroll verification chain (JS scrollY → pixel-diff → actuator fallback). The verifier returns a verdict string like `"Scroll down confirmed: scrollY 0 → 500 (delta 500)"` or `"Scroll accepted: actuator reported success"`. This is useful for debugging individual steps.

However, for aggregate analysis — "what percentage of scroll verifications use each tier?" — there is no structured metric. Operators can't answer: "Is JS scrollY working reliably? Should we invest in improving pixel-diff?"

**Recommendation**: Include `scroll_verification_tier: "S1_js" | "S2_pixel" | "S3_actuator"` in the `VERIFY_PASS` event data for scroll steps. This allows filtering event logs to compute tier distribution.

### Finding R1-3 [NON-BLOCKING]: Skill librarian promotion decisions not event-logged (P2-2)

The spec changes config defaults (enable librarian, lower thresholds) but adds no new observability. The librarian pipeline (`evaluate_run → _evaluate_run_inner → _decide_promotion_type → commit`) already has structlog messages in the existing code, but these are not event-logged.

When the librarian promotes or declines to promote, there is no `SKILL_PROMOTED` or `SKILL_PROMOTION_DECLINED` event type. An operator cannot determine from JSONL logs whether the librarian is working, how many observations are queuing, or what the promotion rate is.

**Recommendation**: This is an existing gap (not introduced by this spec), so it's non-blocking for this review. However, since the spec enables the librarian by default and lowers thresholds, the observability gap becomes active. Consider adding `SKILL_LIBRARIAN_EVALUATE` and `SKILL_PROMOTED` event types in a follow-up.

### Finding R1-4 [NON-BLOCKING]: type_text click-to-focus outcome not logged at event level (P1-1)

The spec adds `slog.warning("type_text element not found, typing to current focus")`. Good. But the **success path** — element found, clicked at (x, y), waited 300ms, then typed — has no log at all. An operator debugging a type_text failure has no way to know if click-to-focus ran, whether it found the element, or where it clicked.

**Recommendation**: Add a structlog info message on the success path:
```python
slog.info(
    "type_text click-to-focus succeeded",
    element=element_desc,
    x=sx, y=sy,
    confidence=getattr(location, "confidence", None),
)
```

---

## Round 2: Sharpen Findings — Concrete Recommendations

### Finding R2-1 [BLOCKING]: Domain verification failure message lacks operator-actionable context (P2-3)

The spec defines the domain mismatch message as:
```
"Browser domain '{actual_domain}' does not match expected domain '{expected_domain}'"
```

This tells the operator WHAT happened but not WHY or HOW TO FIX. When domain verification fails, the operator needs to know:
1. Was the expected domain injected from a skill's `site` field or from prompt entity extraction?
2. What was the original prompt?
3. What URL did the step try to open?

The `_inject_domain_verification` method silently mutates `step.verify` text. If an operator sees `"AND browser domain is target.com"` in a verification failure, they have no way to trace back to whether this came from a matched skill or from prompt-level entity extraction. This distinction matters for debugging — a skill-sourced domain means the skill matched correctly but the plan navigated wrong; a prompt-sourced domain means entity extraction worked but routing may have failed.

**Recommendation**: Add a `_domain_source` attribute to the step or include the source in the verify text injection:
```python
step.verify = f"{step.verify} AND browser domain is {expected_domain}"
# Also log for traceability:
slog.debug(
    "domain_verification_injected",
    step_action=step.action,
    expected_domain=expected_domain,
    source="skill_metadata" or "prompt_extraction",
    url=step.params.get("url", ""),
)
```

This is blocking because without this log, debugging wrong-domain failures requires correlating across multiple disconnected log lines (entity extraction → routing → plan generation → verification), which is impractical in production.

### Finding R2-2 [NON-BLOCKING]: open_url `_no_visible_change` metadata is stored but never surfaced (P1-2)

The spec adds `actuator_result["_no_visible_change"] = True` and `slog.info("open_url had no visible effect, deferring to verification")`. The structlog message is good. But the `_no_visible_change` flag in actuator_result is described as "available to downstream logging and diagnostics" — yet no downstream code reads it.

This metadata could be valuable: if Tier 1 URL verification subsequently passes, the operator learns that the page was already loaded (not a failure). If Tier 1 fails, the metadata shows the action truly had no effect.

**Recommendation**: Include `_no_visible_change` in the `VERIFY_PASS` or `VERIFY_FAIL` event data when present. This completes the diagnostic story.

### Finding R2-3 [NON-BLOCKING]: `get_scroll_position()` timeout/failure is silent (P1-3)

The spec's `get_scroll_position()` catches `TimeoutExpired`, `ValueError`, and `OSError`, returning `None`. But it doesn't log which exception occurred. When Tier S1 is consistently failing (all returning `None`), the operator can't distinguish between:
- JS permission denied (fixable by enabling "Allow JavaScript from Apple Events")
- osascript timeout (browser may be hung)
- Non-browser context (expected behavior)

The verifier just sees `None` and falls through. The root cause is invisible.

**Recommendation**: Add structlog debug messages in the except handlers:
```python
except subprocess.TimeoutExpired:
    slog.debug("get_scroll_position timed out")
    return None
except (ValueError, OSError) as exc:
    slog.debug("get_scroll_position failed", error=str(exc))
    return None
```

---

## Round 3: Deep Issues — Edge Cases and Debugging Blind Spots

### Finding R3-1 [NON-BLOCKING]: No observability into skill fallback plan compilation failures (P0-2/P0-3)

The spec notes that `_compile_skill_instruction()` returns `None` for unrecognized verb patterns, causing `_build_skill_fallback_plan()` to return `None` (no fallback). The spec carefully designed buy_on_target.md steps to use recognized patterns. But if a future skill author writes a step that doesn't compile, the fallback silently disappears.

The codebase analysis shows the compiler has 11 regex patterns. An unrecognized step causes the entire fallback to abort at line 1830. Currently there is no log indicating which step failed or that the fallback was aborted.

**Recommendation**: Add a structlog warning when `_compile_skill_instruction()` returns `None`:
```python
if compiled is None:
    slog.warning(
        "skill_step_not_compilable",
        step_text=instruction[:80],
        skill_id=skill_id,
    )
    return None  # existing behavior
```
And in `_build_skill_fallback_plan()`:
```python
if fallback_plan is None:
    slog.info(
        "fallback_plan_aborted",
        skill_id=skill_id,
        reason="unrecognized_step_pattern",
    )
```

This is non-blocking because the spec's skill template uses only recognized patterns, but it's a landmine for future skill authors.

### Finding R3-2 [NON-BLOCKING]: Confidence dead zone fix is not observable (P2-2)

The spec lowers `min_confidence` from 0.7 to 0.5 to fix the dead zone where replan observations (capped at 0.6) could never qualify. This is well-reasoned. However, there is no way for an operator to verify the fix is working — no log showing "observation with confidence 0.6 passed min_confidence=0.5 gate" or "observation with confidence 0.4 filtered by min_confidence=0.5 gate".

The librarian's qualification loop (`librarian.py:139-146`) silently skips groups that don't meet thresholds via `continue`. An operator can't tell if observations are being generated but filtered, or not generated at all.

**Recommendation**: Add a structlog debug message in the qualification loop:
```python
if score < self.config.skill_librarian_min_confidence:
    slog.debug(
        "observation_group_below_threshold",
        category=key, score=score,
        threshold=self.config.skill_librarian_min_confidence,
        count=len(group),
    )
    continue
```

### Finding R3-3: Aggregate metrics for spec success criteria are not designed

The PRD defines success metrics (Section 4) including:
- Skill routing accuracy: 3/3 correct
- Plan depth: >= 5 steps through add-to-cart
- Scroll verification: >= 3/4 pass
- Skill adaptation: >= 1 new skill promoted after 2 runs

These metrics require querying JSONL event logs across runs. The event types exist (`SKILL_MATCH`, `PLAN_COMPLETE`, `VERIFY_PASS`) but the data fields needed for filtering are not fully specified:
- `PLAN_COMPLETE` doesn't include `interaction_step_count`
- `VERIFY_PASS` doesn't include `action_type` for filtering scroll-only verdicts
- No event for librarian promotion

This is acceptable for initial launch (manual e2e testing), but if the team wants automated regression checks against these metrics, the event data will need enrichment.

**Not blocking** — the PRD metrics are measured via manual e2e testing, not automated log analysis.

---

## Summary of Findings

| # | Finding | Severity | Fix Effort | Recommendation |
|---|---------|----------|------------|----------------|
| R1-1 | No structured event for site entity extraction decisions | **BLOCKING** | Low | Add extraction result to `SKILL_MATCH`/`SKILL_NO_MATCH` event data |
| R2-1 | Domain verification failure lacks source context | **BLOCKING** | Low | Log domain injection source (skill vs. prompt) when injecting |
| R1-2 | Scroll verification tier not in event data | Non-blocking | Trivial | Add `scroll_verification_tier` to `VERIFY_PASS` data |
| R1-3 | Skill librarian decisions not event-logged | Non-blocking | Medium | Future: add `SKILL_PROMOTED` event type |
| R1-4 | type_text click-to-focus success path not logged | Non-blocking | Trivial | Add info log on success |
| R2-2 | `_no_visible_change` metadata never surfaced | Non-blocking | Trivial | Include in `VERIFY_PASS`/`VERIFY_FAIL` data |
| R2-3 | `get_scroll_position()` failure reason is silent | Non-blocking | Trivial | Add debug logs in except handlers |
| R3-1 | Skill fallback compilation failure is silent | Non-blocking | Low | Log unrecognized step pattern |
| R3-2 | Confidence dead zone fix is not observable | Non-blocking | Trivial | Log filtered observations in librarian |

---

## Verdict: NOT APPROVED

**Two blocking issues must be addressed before implementation:**

1. **R1-1**: Site entity extraction decisions must be captured in structured event logs (not just structlog). Without this, operators cannot debug routing failures from production logs alone. This is the core new routing logic — if it goes wrong, observability is the first responder.

2. **R2-1**: Domain verification injection must log its source (skill metadata vs. prompt extraction). Without this, domain mismatch failures cannot be triaged — the operator doesn't know which upstream decision produced the expected domain.

**Both fixes are low-effort** (adding fields to existing log calls and one new debug log). They do not require architectural changes.

The 6 non-blocking observations are recommended improvements that would materially help operators but are not prerequisite for shipping.

---

## Recommended Spec Changes

### For R1-1 (blocking):

In section 2.1.2 (Site Filter in Registry), after the `extract_site_entity` call in `match()`, add event emission:

```python
# After extract_site_entity call:
if self._event_logger:
    self._event_logger.log(EventType.SKILL_MATCH if site_entities else EventType.SKILL_NO_MATCH, {
        "extracted_sites": site_entities,
        "candidates_before_filter": len(valid),
        "candidates_after_filter": len(filtered) if site_entities else len(valid),
        "filter_reason": "site_mismatch" if (site_entities and not filtered) else None,
    })
```

Or more simply, enrich the existing `SKILL_MATCH`/`SKILL_NO_MATCH` events in the `match()` method with `extracted_sites` and `filter_applied` fields.

### For R2-1 (blocking):

In section 3.3.2 (Orchestrator Changes), in `_inject_domain_verification()`, add:

```python
def _inject_domain_verification(
    self, plan: ActionPlan, expected_domain: str, source: str = "unknown"
) -> None:
    marker = "browser domain is"
    for step in plan.steps:
        if step.action == "open_url" and step.verify:
            if marker not in step.verify:
                step.verify = (
                    f"{step.verify} AND browser domain is {expected_domain}"
                )
                slog.debug(
                    "domain_verification_injected",
                    step_url=step.params.get("url", "")[:80],
                    expected_domain=expected_domain,
                    source=source,
                )
```

And at the call site in `execute()`:

```python
if site_entities and len(site_entities) == 1:
    expected_domain = f"{site_entities[0]}.com"
    source = "skill_metadata" if skill_match else "prompt_extraction"
    self._inject_domain_verification(plan, expected_domain, source=source)
```

---

## Phase 2: Implementation Review

**Reviewer**: Observability Specialist
**Date**: 2026-03-14
**Review mode**: 2 rounds per engineer (evidence gate, then sharpen/drop)

---

### Engineer 1: P0-1 (site routing), P0-2 (Target skill), P2-1 (duplicate cleanup)

**Files reviewed**: `router.py`, `registry.py`, `matcher.py`, `models.py`, `loader.py`, `buy_on_target.md`, `amazon_search.md`, `route_skill.md`, `test_site_routing.py`

#### R1-1 [SPEC BLOCKING]: Site entity extraction event logging
**STATUS: PARTIALLY ADDRESSED — 1 BLOCKING issue remains**

Engineer 1 added structured event logging at `registry.py:254-259`:
```python
if site_entities and self._event_logger:
    self._event_logger.log_event(
        EventType.SKILL_MATCH,
        "Site entity extracted: {}".format(site_entities),
        data={"extracted_sites": site_entities, "filter_applied": True},
    )
```

**IMPL-E1-1 [BLOCKING]**: The `SKILL_MATCH` event fires *before* the multi-site conflict guard at line 262. On a prompt like "buy sheets on target and amazon", a `SKILL_MATCH` event is logged (with `extracted_sites: ["amazon", "target"]`), then the method returns `None` — no skill matched. This produces a false-positive `SKILL_MATCH` event in JSONL logs, which corrupts operator alerting and confuses debugging.

Fix: Move event emission to after the multi-site guard. Emit `SKILL_NO_MATCH` with `reason: "multi_site_conflict"` on the conflict path. Also emit `SKILL_NO_MATCH` with `reason: "no_extraction"` when `site_entities is None` for the no-extraction path.

#### R3-1 [SPEC NON-BLOCKING]: Skill fallback compilation failure logging
**STATUS: ADDRESSED**

`slog.warning("skill_fallback_unrecognized_step", step_text=instruction[:80])` at `agent.py:1580-1583`. Logs the failing step text. The method now skips (continues) rather than aborting the entire fallback. Good.

---

### Engineer 2: P0-3 (plan depth), P1-1 (type_text focus), P2-3 (domain verification)

**Files reviewed**: `agent.py` (lines 310-370, 1493-1540, 2170-2227, 3575-3577), `verifier.py` (lines 396-414, 246-251), `plan_from_prompt.md`, `test_type_text_focus.py`, `test_planner.py`, `test_verifier.py`

#### R2-1 [SPEC BLOCKING]: Domain verification failure source context
**STATUS: NOT ADDRESSED — DOWNGRADED to NON-BLOCKING**

The `_inject_domain_verification()` method at `agent.py:1520-1539` logs `source_entity` (the entity name like "target"), not the *source* of the domain (skill metadata vs. prompt extraction). However, upon implementation analysis, domain injection is ONLY called from `extract_site_entity(goal)` — there is currently no code path where a skill's `site` metadata feeds into domain injection. The source is always "prompt_extraction". A `source` parameter would currently be constant.

Downgraded to NON-BLOCKING because the theoretical issue (future skill-sourced domain) is not present in the implementation. Recommended for forward compatibility.

#### R1-4 [SPEC NON-BLOCKING]: type_text click-to-focus success path logging
**STATUS: NOT ADDRESSED**

At `agent.py:2187-2204`, the click-to-focus success path (element found, clicked, waited) has no log message. Only the failure/fallback paths log. An operator debugging "typed into wrong field" cannot tell from logs whether click-to-focus ran, where it clicked, or what confidence it had.

**IMPL-E2-1 [NON-BLOCKING]**: Add `slog.info("type_text click-to-focus succeeded", element=element_desc, x=sx, y=sy, confidence=...)` after the click at line 2201.

---

### Engineer 3: P1-2 (open_url false negative), P1-3 (scroll verification), P2-2 (skill librarian)

**Files reviewed**: `agent.py` (lines 1080-1110, 2240-2290), `verifier.py` (lines 475-516), `applescript_actuator.py` (lines 311-349, 295-309), `config.py` (lines 196-221), `test_open_url_verification.py`, `test_scroll_verification.py`, `test_skill_librarian.py`

#### R1-2 [SPEC NON-BLOCKING]: Scroll verification tier in event data
**STATUS: NOT ADDRESSED**

Scroll verification at `verifier.py:476-516` returns descriptive evidence strings (e.g., "Scroll confirmed via scrollY delta"), but no structured `scroll_verification_tier` field in `VERIFY_PASS` event data. Aggregate analysis requires string parsing.

**IMPL-E3-1 [NON-BLOCKING]**: Add `scroll_verification_tier: "S1_js"|"S2_pixel"|"S3_actuator"` to the VERIFY_PASS event data for scroll steps.

#### R2-2 [SPEC NON-BLOCKING]: `_no_visible_change` metadata surfaced
**STATUS: NOT ADDRESSED**

`_no_visible_change` is set at `agent.py:1099` but never read in `verifier.py`. The flag exists in the actuator_result dict but is not included in VERIFY_PASS/VERIFY_FAIL event data.

**IMPL-E3-2 [NON-BLOCKING]**: Include `_no_visible_change` in verification event data when present.

#### R2-3 [SPEC NON-BLOCKING]: `get_scroll_position()` failure reason logging
**STATUS: NOT ADDRESSED**

At `applescript_actuator.py:347`, all exceptions (`TimeoutExpired`, `ValueError`, `TypeError`) are caught with a bare `pass`. No structlog import in the file. No debug messages to distinguish failure modes.

**IMPL-E3-3 [NON-BLOCKING]**: Import structlog, split except clauses, add debug messages for timeout vs. parse error.

#### R3-2 [SPEC NON-BLOCKING]: Confidence dead zone observability
**STATUS: NOT ADDRESSED**

At `librarian.py:144-146`, groups below `min_confidence` are silently skipped. The `"skill_librarian_no_qualifying_groups"` log at line 150 fires when nothing qualifies but doesn't say why. Three distinct filter reasons (too few observations, too few runs, score too low) are indistinguishable.

**IMPL-E3-4 [NON-BLOCKING]**: Add debug logs in each `continue` branch of the qualification loop.

---

## Implementation Review Summary

| # | Finding | Engineer | Severity | Status |
|---|---------|----------|----------|--------|
| IMPL-E1-1 | False positive SKILL_MATCH on multi-site conflict | Eng 1 | **BLOCKING** | Event fires before guard |
| IMPL-E2-1 | Missing click-to-focus success path log | Eng 2 | Non-blocking | Not implemented |
| IMPL-E3-1 | Scroll tier not in structured event data | Eng 3 | Non-blocking | Not implemented |
| IMPL-E3-2 | `_no_visible_change` not surfaced in events | Eng 3 | Non-blocking | Not implemented |
| IMPL-E3-3 | `get_scroll_position` silent exception handling | Eng 3 | Non-blocking | Not implemented |
| IMPL-E3-4 | Librarian qualification filter not logged | Eng 3 | Non-blocking | Not implemented |

### Spec Review Findings — Implementation Status

| Spec Finding | Severity | Implementation Status |
|-------------|----------|----------------------|
| R1-1 (site extraction event) | BLOCKING | Partially addressed — event fires too early (IMPL-E1-1) |
| R2-1 (domain source context) | BLOCKING | Not addressed — downgraded to non-blocking (single source in practice) |
| R1-2 (scroll tier in events) | Non-blocking | Not addressed (IMPL-E3-1) |
| R1-4 (click-to-focus success log) | Non-blocking | Not addressed (IMPL-E2-1) |
| R2-2 (_no_visible_change surfaced) | Non-blocking | Not addressed (IMPL-E3-2) |
| R2-3 (scroll position failure reason) | Non-blocking | Not addressed (IMPL-E3-3) |
| R3-1 (fallback compilation log) | Non-blocking | **ADDRESSED** |
| R3-2 (dead zone observability) | Non-blocking | Not addressed (IMPL-E3-4) |

## Implementation Review Verdict

**CONDITIONALLY APPROVED** — 1 blocking issue (IMPL-E1-1) must be fixed. 5 non-blocking recommendations for improved operator debuggability.

The IMPL-E1-1 fix is ~10 lines: move event emission in `registry.py:match()` to after the multi-site guard and add a `SKILL_NO_MATCH` event on the conflict path.

The original spec review had 2 blocking issues. R1-1 was partially addressed (but introduced a new bug — premature event emission). R2-1 was downgraded after implementation analysis showed a single source path. Net: 1 blocking remains from a new implementation-level issue.
