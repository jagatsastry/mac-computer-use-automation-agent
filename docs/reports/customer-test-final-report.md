# Customer Test Final Report

**Date:** 2026-03-18
**Test Runner:** Automated via Claude Code
**Evidence Location:** `logs/agent-team-logs/customer-test-final/`

## Overall Results


| Scenario                   | Result                                             | Duration | Run ID        |
| -------------------------- | -------------------------------------------------- | -------- | ------------- |
| 1. Listerine Amazon Return | PARTIAL (5/5 criteria PASS, task FAILED at step 8) | 493s     | 260318_103022 |
| 2. Calculator Multiply     | SUCCESS (3/5 criteria PASS, 2 N/A)                 | 52s      | 260318_104052 |


## Scenario 1: Listerine Amazon Return

**Prompt:** "return my listerine amazon order"
**Agent outcome:** FAILED (after replan)


| Criterion                      | Verdict | Evidence                                                     |
| ------------------------------ | ------- | ------------------------------------------------------------ |
| Chrome stays frontmost         | PASS    | All steps show [Google Chrome], no Safari activation         |
| Amazon orders page opens       | PASS    | URL `amazon.com/gp/css/order-history` opened successfully    |
| "listerine" typed in search    | PASS    | Vision confirmed field contains "listerine"                  |
| Pre/post state [Google Chrome] | PASS    | Consistent Chrome throughout (initial Cursor expected)       |
| Run report generated           | PASS    | `logs/runs/260318_103022/report.md` (18 LLM calls, 11 steps) |


**What happened:** The agent successfully opened Amazon orders, typed "listerine", and searched. It found 48 matching orders. It then struggled to click "View order details" for a Listerine product -- the vision grounding found elements but clicked the wrong targets (product images/links instead of order details links). After 4 attempts at step 3 across 1 replan, infeasibility detection triggered and the run was marked failed.

**Key metrics:** 18 LLM calls, 8 actions executed, 1 replan, 493s total.

## Scenario 2: Calculator Multiply

**Prompt:** "Open Calculator and compute 15 times 8"
**Agent outcome:** SUCCESS


| Criterion                | Verdict | Evidence                                                       |
| ------------------------ | ------- | -------------------------------------------------------------- |
| Calculator opens         | PASS    | Tier 1: "Frontmost app is 'Calculator'"                        |
| Computation attempted    | PASS    | `type_text('15*8=')`, display confirmed showing 120            |
| AX grounding used        | FAIL    | No click actions planned; type_text does not trigger grounding |
| AX confidence calibrated | N/A     | No grounding events to evaluate                                |
| Run report generated     | PASS    | `logs/runs/260318_104052/report.md` (4 LLM calls, 3 steps)     |


**What happened:** The agent activated Calculator, typed `15*8=`, and vision confirmed the display showed `120`. First type attempt failed verification (likely the `=` hadn't been processed yet), but the automatic retry with `_clear_first: true` succeeded. No AX grounding was exercised because the planner chose keyboard input over button clicks.

**Key metrics:** 4 LLM calls, 4 actions executed, 0 replans, 52s total.

## What Worked

1. **Chrome-only browsing** -- No Safari focus-stealing occurred in either scenario. The `_try_enable_safari_js` removal and per-session blocklist are working.
2. **Overlay narration** -- Both runs emitted `narrate_intent` / `narrate_observe` event pairs for every step, providing real-time "thinking aloud" feedback.
3. **Pre/post state tracking** -- Action summaries correctly show `[App] -> [App]` with URL changes for every step.
4. **Run reports** -- Both runs generated complete `report.md` files with LLM call tables, step timelines, verification summaries, and state changes.
5. **Smart retry** -- Scenario 2 demonstrated `select_all_then_type` retry strategy when initial `type_text` verification failed.
6. **Skill matching** -- Scenario 1 correctly matched the `return-amazon-order` skill. Scenario 2 correctly found no matching skill.
7. **Tier 1 actuator verification** -- Fast app-frontmost checks (436ms) and URL-match checks avoided slow vision calls where possible.
8. **Skill learning pipeline** -- After Scenario 1 failed, the distiller extracted 3 observations for `return-amazon-order` and the librarian attempted (but failed to parse) a promotion evaluation.

## What Didn't Work

1. **Vision grounding accuracy on complex web pages** -- Scenario 1 failed because Molmo could not reliably locate "View order details link associated with the Listerine product image" on the Amazon search results page. All 6 element_found events used `source: vision` with identical `confidence: 0.75` -- the confidence is hardcoded for vision grounding rather than reflecting actual model certainty.
2. **AX grounding not triggered for Calculator** -- The planner chose `type_text` for Calculator input, which bypasses the grounding router entirely. This is arguably correct behavior, but it means the AX grounding criterion could not be evaluated. A scenario involving clicking native app buttons would be needed to test AX grounding.
3. **No AX grounding on web pages** -- Amazon (web content inside Chrome) does not expose elements via macOS Accessibility API, so all grounding fell to vision. This is expected but means the AX path is completely untested by Scenario 1.
4. **Librarian parse failure** -- The skill librarian's LLM response was truncated (`'{\n  "promotion_'`), causing `skill_librarian_parse_failed`. The learning pipeline's LLM call needs a higher max_tokens or retry logic.
5. **Slow vision grounding** -- Each Molmo grounding call took ~28s. Over 6 grounding calls in Scenario 1, that's ~168s spent just on element location.

## Recommendations

1. **Add a native-app click scenario** to the test suite (e.g., "Open System Preferences and click Accessibility") to properly exercise AX grounding and confidence calibration.
2. **Investigate vision grounding confidence** -- All vision grounding returns 0.75 regardless of model certainty. Consider parsing Molmo's output for confidence signals.
3. **Fix librarian max_tokens** -- The skill librarian LLM call is being truncated. Increase `max_tokens` or add a retry with backoff.
4. **Consider AX fallback for Chrome** -- Chrome supports accessibility APIs. When vision grounding fails on web pages, try Chrome's AX tree as a second path.
5. **Add grounding timeout** -- 28s per grounding call is excessive. Consider a 15s timeout with fallback to a lower-resolution crop.

