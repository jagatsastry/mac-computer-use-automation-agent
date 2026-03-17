# Speed Phase 1 -- Short-Seller Attack Report

**Date**: 2026-03-16
**Role**: Short-Seller (adversarial reviewer)
**Confidence this ships without issues**: LOW
**Recommendation**: BLOCK SHIP

---

## Summary

I read the PRD, architecture spec, and codebase analysis doc, then verified every material claim against the actual source code. I found 5 distinct gaps, any 2 of which combined are sufficient to block shipping. The spec looks thorough on paper but hides fundamental problems behind "additive changes" and "backward-compatible defaults" language. The real risk is not in what the spec says -- it is in what it does not say.

---

## Gap 1: AC-8 Is Unimplementable Without Modifying `agent.py` -- A File Not In Any Engineer's Scope

**Severity**: SHIP-BLOCKING

**The claim** (PRD AC-8, Spec Section 2.2): "Only exact AX matches (confidence >= 0.9) must skip pre-click validation; partial matches must go through crop validation."

**The reality**: The pre-click validation skip logic at `agent.py:2315-2318` reads:

```python
skip_validation = (
    location.source in ("accessibility", "grounding", "vision")
    or confidence >= 0.9
)
```

This skips validation for ALL sources -- accessibility, grounding, AND vision -- regardless of confidence. The `or confidence >= 0.9` clause is redundant because the first condition already matches every possible source string.

**Why this kills the feature**: Engineer 2's entire value proposition is that partial AX matches (confidence 0.6-0.85) should trigger pre-click validation. But even after Engineer 2 ships calibrated confidence values, the orchestrator will STILL skip validation because `location.source == "accessibility"` is always true for AX results. The calibrated confidence number is computed, logged, and then completely ignored.

**What the spec says about this**: The spec (Section 2.2, Change H) modifies `grounding_router.py` only. The PRD (AC-8) references `agent.py:~2314-2317` in its rationale but does not list `agent.py` as a file to modify. The codebase doc (Section 6.4) correctly identifies the skip logic but the spec never assigns anyone to change it.

**Evidence**: `agent.py:2315-2318`. The spec's file list for Engineer 2 is: `grounding_router.py` only. No engineer touches `agent.py`.

**What's missing**: A Change I in the spec that modifies `agent.py:2315-2318` to remove `"accessibility"` from the source skip list, so that only `confidence >= 0.9` controls the skip. Without this, AC-8 is dead on arrival.

---

## Gap 2: AppleScript `missing value` Will Poison the Batched JSON Parse, Silently Killing All Four Fields

**Severity**: HIGH (data loss, silent failure)

**The claim** (Spec Section 1.2, Change A): The batched JS expression `JSON.stringify({focused_value: document.activeElement.value || ... , ...})` returns a JSON string that is parsed with `json.loads()`. On "any failure", returns `{}`.

**The reality**: When a browser tab has no focused element (common: user clicked a non-input area, or the page just loaded), `document.activeElement` is `<body>`. The body element has no `.value` property, so `document.activeElement.value` is `undefined`. The `||` fallback chain works in this case. But there is a worse scenario.

When AppleScript's `do JavaScript` encounters a JS expression that returns `undefined` (not empty string, not null -- specifically `undefined`), Safari's AppleScript bridge returns the literal string `missing value`. This is not a JS string -- it is AppleScript's representation of a nil result. The `_get_browser_js` method at `applescript_actuator.py:438` checks `result.returncode == 0 and result.stdout.strip()`. The string `"missing value"` passes both checks.

For the current individual calls, this is harmless -- `focused_value` gets the string `"missing value"` and the type_text matcher fails to find the expected text in it, which is correct behavior.

For the batched `JSON.stringify()` call, the risk is different. If any interior property evaluates to `undefined` in a way that breaks the JSON structure (e.g., if `document.querySelector('h1')` returns `null` and the `|| {}` fallback doesn't fire due to a JS engine edge case, or if the entire expression fails mid-execution due to a security exception on one property), AppleScript returns `missing value` instead of the JSON string. `json.loads("missing value")` raises `JSONDecodeError`, the fallback returns `{}`, and ALL four fields become `None` -- including `focused_value` and `selected_text` which would have been individually retrievable before.

**The batching creates a failure coupling that does not exist today.** Currently, if `document.querySelector('h1')` throws (e.g., on a page with a restrictive CSP), only `page_heading` is lost. After batching, all four fields are lost because they share a single `JSON.stringify` call.

**What's missing**: (1) No test for the `missing value` string being returned from AppleScript. (2) No fallback to individual calls when the batch parse fails. (3) The spec claims "The complexity cost is minimal: one `json.loads()` call" -- but the failure mode is strictly worse than the current architecture because failure is correlated instead of independent.

---

## Gap 3: The Page Content Token Matcher Will False-Positive on Verify Conditions Containing Navigation Words

**Severity**: MEDIUM (correctness regression, silent wrong verdicts)

**The claim** (Spec Section 2.1, Change D): The page content token matcher uses `\b\w{3,}\b` to extract tokens from both the verify condition and page_heading/page_title, then checks for 2+ overlapping tokens or 1 token of 5+ chars.

**The attack**: Consider a real verify condition from the planner: `"Page shows shopping cart with items"`. Tokens extracted: `{page, shows, shopping, cart, with, items}`. Now consider a product page whose `<h1>` is `"Shopping Deals - Items on Sale"`. Heading tokens: `{shopping, deals, items, sale}`. Overlap: `{shopping, items}` -- 2 tokens, match triggers.

But the user is on the WRONG PAGE. They wanted the cart, and they are on a deals page. The matcher returns `(True, "Tier 1 page page_heading tokens match verify: {'shopping', 'items'}")`. Tier 2 vision is never invoked. The verification passes incorrectly.

This is not a contrived example. E-commerce sites (which the project explicitly targets -- see skill files for amazon, target, walmart, bestbuy) reuse vocabulary across pages. Words like "shopping", "items", "order", "account", "product", "details" appear in headings on many different pages.

**The spec's mitigation** (requiring 2+ tokens or 1 token of 5+ chars) is tuned to avoid false positives from SHORT common words like "the" and "cart". It does nothing to prevent false positives from LONG common words like "shopping", "product", "details", "account".

**What's missing**: (1) No negative test cases with e-commerce vocabulary overlap. (2) No analysis of the false positive rate on real page headings. (3) No consideration of requiring a RATIO threshold (e.g., >50% of verify tokens must match) rather than an absolute count. (4) No consideration that the matcher can never return `(False, ...)` -- it either matches or falls through -- which means false positives are the only failure mode. A false positive at Tier 1 prevents Tier 2 from ever running, so it is not self-correcting.

---

## Gap 4: Existing Tests Assert `confidence == 0.95` for All AX Results -- The Spec Underestimates the Regression Surface

**Severity**: MEDIUM-HIGH (blocks AC-13: all existing tests pass)

**The claim** (Spec Section 7, Backward Compatibility): "Default `match_score=1.0` parameter in `_ground_accessibility_match()` means any caller that doesn't pass a score gets `confidence = 0.6 + 0.35 * 1.0 = 0.95` -- identical to the current hardcoded value."

**The partial truth**: Yes, callers that don't pass a score will get 0.95. But the spec also says (Change H) to update `find_element()` so it DOES pass the computed score. The `_compute_match_score()` method calls `_extract_match_target()` then `_element_match_score()`. In the existing test mocks:

- `test_grounding_router.py` (fixture at line 46-48): Uses a bare `MagicMock()` with only `find_element_by_description` set. It does NOT set `_extract_match_target` or `_element_match_score` as explicit attributes. The `_compute_match_score()` method will follow the MagicMock detection path, find that `vars(self.accessibility)` does NOT contain these keys, and fall back to `1.0`. So these tests will pass -- but they test the FALLBACK path, not the calibration path.

- `test_grounding_router.py:337`: Asserts `result.confidence == 0.95`. This will pass only because the mock doesn't have score_fn set, triggering the 1.0 fallback. The test is accidentally correct.

- `test_grounding_router_adversarial.py:975`: Same pattern, same accidental correctness.

**The real problem**: The `find_element()` method at `grounding_router.py:109-194` has TWO paths that call `_ground_accessibility_match()`:
1. Line 115-118: Direct path for `accessibility_matches[0]` -- this is updated in the spec.
2. Line 159: `accessibility_result` is reused from step 1 -- NOT recomputed.

But there is a THIRD call site the spec identified: `_ground_accessibility()` at line 406-413. This uses `find_element_by_description()` which returns a SINGLE element, not the scored list from `_get_accessibility_matches()`. The spec correctly updates this (Change H, second block). However, if any OTHER caller of `_ground_accessibility_match()` is added in the future, the default `match_score=1.0` parameter silently produces 0.95 confidence without calibration. This is a maintenance trap.

**What's missing**: (1) No test that verifies the calibration path with real `_element_match_score` values flowing through `find_element()`. The spec's Engineer 2 test plan has `test_find_element_uses_calibrated_confidence` but this is in a NEW test file -- it does not verify that EXISTING tests still pass with different confidence values. (2) No grep-based audit of all callers of `_ground_accessibility_match()` to ensure they all pass computed scores.

---

## Gap 5: The Spec Claims "Zero Code Dependencies" Between Engineer 1 and Engineer 2, But the Token Matcher Depends on `_element_match_score` Behavior for Correctness

**Severity**: MEDIUM (integration risk, schedule risk)

**The claim** (Spec Section 10): "Engineer 1 and Engineer 2 have zero code dependencies on each other: Engineer 1 modifies `applescript_actuator.py` and `verifier.py`. Engineer 2 modifies `grounding_router.py`. No shared files."

**The hidden dependency**: The page content token matcher (Engineer 1, Change D) determines whether Tier 2 vision is invoked. The AX confidence calibration (Engineer 2) determines whether pre-click validation is invoked. Both features affect the same end-to-end flow: user clicks a link, page navigates, verification runs.

Consider this scenario: The user clicks "View Cart". After navigation, the page heading is "Your Shopping Cart". The token matcher matches `{"shopping", "cart"}` against the verify condition "Shopping cart page is visible" and returns `(True, ...)`. Tier 2 vision is skipped. Meanwhile, the AX grounding for the next element (e.g., "Checkout button") returns a partial match with confidence 0.78. If the `agent.py` skip logic is not also fixed (Gap 1), the partial match is accepted without validation.

If either feature ships alone, it works correctly: without the token matcher, Tier 2 catches wrong-page; without calibration, 0.95 confidence always skips validation. But TOGETHER, a false-positive token match (Gap 3) combined with an unchecked partial AX match (Gap 1) creates a two-fault failure chain that neither feature's tests cover.

**What's missing**: The integration test plan (Spec Section 2.3, Engineer 3) does not include a test that combines a false-positive token match with a partial AX confidence. The 12 integration tests all test features in isolation.

---

## Gap 6 (Bonus): JS Injection in Chrome Uses a Different AppleScript Syntax That May Not Return JSON.stringify Output Correctly

**Severity**: LOW-MEDIUM (browser-specific regression)

**The claim**: The batched JS expression uses `JSON.stringify({...})` and the output is parsed with `json.loads()`.

**The concern**: The Chrome AppleScript bridge at `applescript_actuator.py:423-426` uses:

```python
script = (
    f'tell application "Google Chrome" to execute '
    f"front window's active tab javascript "
    f'"{js}"'
)
```

Chrome's `execute ... javascript` AppleScript command may wrap the return value differently than Safari's `do JavaScript`. Specifically, Chrome's AppleScript bridge has historically returned JavaScript values with different quoting behavior. A `JSON.stringify()` call returns a JS string (with quotes). Safari's `do JavaScript` strips the outer quotes. Chrome's `execute javascript` may or may not strip them, depending on the Chrome version and whether the result is a primitive string.

If Chrome returns `'{"focused_value":"test","selected_text":"","page_title":"My Page","page_heading":"Welcome"}'` (with outer quotes), `json.loads()` will work. If it returns `"{"focused_value":"test",...}"` (with escaped inner quotes), `json.loads()` will fail.

**What's missing**: No test that validates the `JSON.stringify()` output format through the actual Chrome AppleScript bridge. All tests mock `subprocess.run`, which means the actual AppleScript-to-JSON serialization path is never tested. The spec explicitly acknowledges this is a mock-only test strategy (Section 5.1) but does not flag the Chrome quoting risk.

---

## Verdict

| # | Gap | Severity | Blocks Ship? |
|---|-----|----------|-------------|
| 1 | AC-8 unimplementable without `agent.py` change | SHIP-BLOCKING | YES |
| 2 | Batched JSON failure coupling | HIGH | CONDITIONAL (needs real-browser test) |
| 3 | Token matcher false positives on e-commerce vocabulary | MEDIUM | NO (but causes correctness regression) |
| 4 | Existing tests accidentally pass via fallback, hiding calibration gap | MEDIUM-HIGH | CONDITIONAL |
| 5 | Hidden cross-feature dependency in integration path | MEDIUM | NO (but creates untested failure chain) |
| 6 | Chrome JS quoting difference for JSON.stringify | LOW-MEDIUM | NO (but needs manual verification) |

**Recommendation: BLOCK SHIP**

Gap 1 alone is sufficient. The entire value proposition of Change 2 (AX confidence calibration) is that partial matches trigger pre-click validation. The current code at `agent.py:2315-2318` unconditionally skips validation for all accessibility-sourced results. No engineer is assigned to change this file. The feature will ship, the confidence numbers will be computed and logged, and then the skip logic will ignore them. The tests will pass because they only test the grounding_router in isolation, never the agent's skip logic with calibrated values.

To unblock:
1. Add `agent.py` to Engineer 2's file scope. Change the skip condition to `confidence >= 0.9` only (remove `"accessibility"` from the source list).
2. Add a fallback path in `_get_browser_js_batch()` that retries individual calls when `json.loads()` fails on the batch result.
3. Add negative test cases for the token matcher using real e-commerce heading vocabulary.
4. Add an integration test that threads calibrated AX confidence through the agent's pre-click validation path.
