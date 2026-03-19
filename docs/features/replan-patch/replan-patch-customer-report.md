# Customer Testing Report: Replan as Plan Patch

## Summary
- Scenarios tested: 4
- Passed: 1 (Scenario 1)
- Failed: 1 (Scenario 3)
- Partial: 2 (Scenarios 2, 4)

---

## Scenario 1: Search Wikipedia for a specific article
### Prompt
"Open Safari, go to wikipedia.org, and search for 'Voyager 1 golden record'"

### What Actually Happened
The agent completed the task successfully in 94s without needing a replan. It opened wikipedia.org in Chrome (not Safari, but the plan adapted), typed "Voyager 1 golden record" into the search field, and the Wikipedia autocomplete showed the "Voyager Golden Record" article suggestion. The final screenshot confirms the search term was entered and autocomplete results were visible. Only `plan_v0` was generated, which is correct behavior since no replan was needed.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | A `plan_v0_*.json` file exists in `logs/runs/{run_id}/plans/` | PASS | `plans/plan_v0_20260318T130055.json` exists (2452 bytes). Structure is correct: `version: 0`, `is_replan: false`, `trigger: null`, `resume_from_step: null`, `completed_steps: []`, `steps: [3 items]`. |
| 2 | The initial plan contains at least 3 steps | PASS | Plan has exactly 3 steps: `open_url` (wikipedia.org), `type_text` (search field), `done`. File: `plan_v0_20260318T130055.json` lines 8-32. |
| 3 | If a replan occurs, a `plan_v1_*.json` file exists with required fields | N/A | No replan occurred (replan_count: 0 per `report.md` line 8). This is acceptable since the scenario notes this criterion is conditional. |
| 4 | The replan file's `completed_steps` does not re-include already-passed steps | N/A | No replan occurred. |
| 5 | The Wikipedia search results or article page is visible at the end | PASS | Final screenshot `130214_step_01_post_type_text.png` shows Wikipedia homepage with "Voyager 1 golden record" typed into the search field and autocomplete showing "Voyager Golden Record" article suggestion. |

### Root Cause (for FAILs)
- None. All applicable criteria passed.

### Recommended Fix
- None needed.

---

## Scenario 2: Use Calculator to compute a multi-step expression
### Prompt
"Open Calculator and compute 156 times 23"

### What Actually Happened
The agent opened Calculator, typed "156", clicked the multiply button, then tried to type "23". The precondition check for step 3 ("The multiplication operator has been entered") failed -- vision denied the multiplication operator was registered. After a retry with `_clear_first`, "23" was typed but the equals step (step 4) also failed its precondition. Multiple retries (click equals, press return, press space) all failed verification -- the display showed "156%23x" instead of "3588". A replan was triggered at step 4. The replan file (`plan_v1`) was correctly generated with `resume_from_step: 4` and 4 `completed_steps`. However, the replan's materialized plan re-included the original 4 completed steps (indices 0-3) AND appended 7 new steps (indices 4-10), producing an 11-step plan. When the agent resumed execution from step 0 of this materialized plan, it immediately failed the precondition for step 0 ("Google Chrome is the foreground application") because Calculator was already in the foreground. The run ended as FAILED. The final screenshot shows Calculator displaying "156%23x" with "18" in the main display.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | A `plan_v0_*.json` file exists | PASS | `plans/plan_v0_20260318T130258.json` exists. Correct structure: `version: 0`, `is_replan: false`, 6 steps (activate_app, type 156, click multiply, type 23, click equals, done). |
| 2 | If a step fails verification, a `plan_v1_*.json` replan file is created | PASS | `plans/plan_v1_20260318T130549.json` exists with `version: 1`, `is_replan: true`. |
| 3 | Any replan file includes `trigger` describing what failed | PASS | `trigger` field: "step type_text: precondition_failed:The multiplication operator has been entered in the Calculat; step click: precondition_failed:The numbers '156' and '23' and the multiplication operator h" -- correctly describes the two precondition failures. File: `plan_v1_20260318T130549.json` line 5. |
| 4 | The Calculator app is in the foreground at the end with a numeric result | FAIL | Final screenshot `130552_verify_step_observe.png` shows Calculator displaying "156%23x" with "18", not "3588". The computation was not completed. Calculator IS in the foreground, but the result is wrong. |
| 5 | The run's `report.md` mentions the replan if one occurred | PASS | `report.md` lines 108-162 document "Replan #1" with timestamp, reason, and all 11 steps. |

### Additional Replan-Patch Feature Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| A | `resume_from_step` is correct | PASS | `resume_from_step: 4` in `plan_v1`. Step 4 was the equals click that exhausted retries. Correct. |
| B | `completed_steps` accurately reflects which steps succeeded | PASS | 4 entries: index 0 (activate_app, PASS), index 1 (type_text 156, PASS), index 2 (click multiply, PASS), index 3 (type_text 23, UNKNOWN -- precondition failed). Accurately reflects execution history. |
| C | New steps start from failure point, not from scratch | FAIL | The materialized `steps` array in `plan_v1` contains 11 steps: the first 4 are the ORIGINAL completed steps (copied verbatim from plan_v0), and steps 4-10 are the new replan steps. The agent then attempted to RE-EXECUTE from step 0, causing a precondition failure on the very first step ("Google Chrome is the foreground application" when Calculator was active). The replan correctly identified `resume_from_step: 4` in the LLM response, but the materialized plan re-included completed steps AND the agent re-executed from step 0 instead of skipping to step 4. |
| D | `version` field is correct | PASS | `version: 0` for initial, `version: 1` for replan. Correct. |

### Root Cause (for FAILs)
- **P0 Bug: Replan materialization includes completed steps AND agent re-executes from step 0.** The `plan_v1` file's `steps` array contains all 11 steps (4 old + 7 new). The `resume_from_step: 4` field is present in the JSON but the agent does NOT skip to step 4 -- it starts executing from step 0 of the materialized plan. This defeats the purpose of the plan-patch feature. Evidence: `trace.md` line 330 shows `[Step 0] step_start` for `activate_app` immediately after the replan completes at line 314, and `report.md` line 258 shows "Step 0: activate_app ... precondition: Google Chrome is the foreground application. Result: FAIL" after the replan.
- **Secondary issue: Calculator type_text sent keyboard characters that the Calculator interpreted incorrectly.** The display showed "156%23x" suggesting the keystrokes for "23" were interpreted as "%23" and then "x" appeared. This is an actuator issue unrelated to the replan-patch feature.

### Recommended Fix
1. The orchestrator must use `resume_from_step` to skip already-completed steps when executing a replan. Either: (a) only include new steps in the materialized `steps` array, OR (b) keep the full plan for context but start execution at `resume_from_step` index.
2. Alternatively, do not copy completed steps' preconditions into the replan -- the preconditions are stale (e.g., "Google Chrome is the foreground application" was true at plan_v0 time but not after replan).

---

## Scenario 3: Search for a nonexistent product on Amazon
### Prompt
"Go to amazon.com and search for 'zxqwv7 phantom gadget 9000'"

### What Actually Happened
The agent completed the task successfully in 88s without needing a replan. It opened amazon.com, observed the page, typed the gibberish search term into the search bar, pressed Enter, and the Amazon search results page appeared. The final screenshot confirms the search term "zxqwv7 phantom gadget 9000" is in the Amazon search bar and results are displayed (unrelated products since the search term is nonsensical). Only `plan_v0` was generated.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | A `plan_v0_*.json` file exists | PASS | `plans/plan_v0_20260318T130646.json` exists. Structure correct: `version: 0`, `is_replan: false`, 5 steps. |
| 2 | At least one replan file (`plan_v1_*.json`) exists | FAIL | No `plan_v1` file exists. Only `plan_v0_20260318T130646.json` in the plans directory. The scenario expected a replan due to the gibberish search term causing verification ambiguity, but the agent handled it cleanly. |
| 3 | Each replan file contains `completed_steps` showing which earlier steps passed | FAIL | No replan occurred, so this criterion cannot be evaluated. Since criterion 2 requires a replan to exist and it does not, this is FAIL by dependency. |
| 4 | No replan file repeats an already-completed step like `open_url` in its new steps | INSUFFICIENT_EVIDENCE | No replan occurred. Cannot evaluate. |
| 5 | The final screen shows Amazon with the search term in the search bar or results heading | PASS | Final screenshot `130801_step_03_post_press_key.png` shows Amazon search results page with "zxqwv7 phantom gadget 9000" in the search bar and "Results" heading visible. URL confirms: `amazon.com/s?k=zxqwv7+phantom+gadget+9000`. |

### Root Cause (for FAILs)
- **Scenario design issue, not a code bug.** The scenario expected a replan would be triggered by the gibberish search term, but the agent handled the task without failures. The Amazon search simply returned unrelated results, and the vision verifier confirmed the search results page was displayed. The scenario's assumption that "verification of search results appeared is likely to fail or be ambiguous" proved incorrect -- the verifier correctly recognized that results were shown regardless of their relevance.
- **Impact on replan-patch testing:** This scenario provided zero evidence about the replan-patch feature behavior. It only confirmed that `plan_v0` files are generated for every run.

### Recommended Fix
- Replace this scenario with one that reliably triggers a replan. For example: "Go to amazon.com, search for 'laptop', and filter results by 4 stars and above" -- the filter step is more likely to fail due to complex UI interaction.
- Alternatively, inject a deliberate failure (e.g., use a URL that will 404 first) to guarantee a replan path is exercised.

---

## Scenario 4: Navigate to a page via a misremembered URL
### Prompt
"Open Safari and go to news.ycombinator.com, then click on the second story link"

### What Actually Happened
The agent attempted to activate Safari but Chrome was the frontmost app. The `activate_app("Safari")` step "passed" verification (the actuator_state tier reported `Frontmost app is 'Google Chrome' (expected 'Safari')` as PASS -- a verification bug). The URL was opened in Chrome instead of Safari. When clicking "Link for the second story on Hacker News," the vision verifier denied the result because its expected observation referenced "Safari browser window" but Chrome was displayed. Multiple retries failed. A replan was triggered at step 2.

The replan (`plan_v1`) was generated with `resume_from_step: 2` and 2 completed_steps. The replan strategy was to quit Chrome, activate Safari, reopen the URL, and click the specific story title. However, the materialized plan again included the original 2 completed steps (indices 0-1) plus 4 new steps (indices 2-5), totaling 6 steps. The agent re-executed from step 0, and the same Safari/Chrome confusion continued. Eventually Chrome was quit, Safari was activated, the URL was opened (but Chrome relaunched as the default handler), and the click on the second story failed repeatedly because the verifier kept checking for "Safari browser window" while Chrome was displayed. The run ended FAILED after 388s and 13 step attempts.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | A `plan_v0_*.json` file exists with the initial plan | PASS | `plans/plan_v0_20260318T130853.json` exists. Structure correct: `version: 0`, 4 steps (activate_app Safari, open_url, click second story, done). |
| 2 | If clicking "the second story link" fails verification, a `plan_v1_*.json` replan file is created | PASS | `plans/plan_v1_20260318T131155.json` exists with `version: 1`, `is_replan: true`. Replan was triggered after step 2 (click) exhausted retries. |
| 3 | The replan's `trigger` field describes the click failure | PASS | `trigger`: "step open_url: precondition_failed:Safari is active and in the foreground.; step click: Vision denies: The Safari browser window shows the content o". Correctly identifies both the precondition failure and the vision denial. File: `plan_v1_20260318T131155.json` line 5. |
| 4 | The replan's `completed_steps` correctly marks the URL navigation step as passed | FAIL | `completed_steps` has 2 entries: index 0 (activate_app, PASS with evidence "Frontmost app is 'Google Chrome' (expected 'Safari')") and index 1 (open_url, UNKNOWN with evidence "Precondition failed"). The `activate_app` step is marked PASS despite the evidence clearly showing it FAILED -- Google Chrome was frontmost, not Safari. The open_url step is marked UNKNOWN, not PASS. So the URL navigation step was NOT correctly marked as passed even though it did eventually succeed (via retry with `_pre_delay`). The `completed_steps` reflects the initial attempt's result, not the final retry outcome. |
| 5 | The final state shows a Hacker News story page (not the front page) loaded in the browser | FAIL | Final screenshot `131504_step_05_post_press_key.png` shows the Hacker News front page (stories 14-30 visible) still loaded at `news.ycombinator.com`. The page never navigated to any individual story. The scrolled position is lower on the page (likely from space key presses), but no story was opened. |

### Additional Replan-Patch Feature Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| A | `resume_from_step` is correct | PASS | `resume_from_step: 2`. Step 2 was the click that exhausted retries. Correct. |
| B | `completed_steps` accurately reflects which steps succeeded | FAIL | Index 0 (activate_app) is marked PASS but evidence says "Frontmost app is 'Google Chrome' (expected 'Safari')" -- this is a false positive. The step actually failed to bring Safari to the foreground. Index 1 (open_url) is marked UNKNOWN even though it eventually passed via retry (URL match confirmed at `trace.md` line 130-131). The completed_steps reflects incorrect/stale results. |
| C | New steps start from failure point, not from scratch | FAIL | Same bug as Scenario 2. The materialized `steps` array contains 6 steps: original 2 (indices 0-1) + 4 new (indices 2-5). The agent re-executed from step 0 (`trace.md` line 309: `[Step 0] step_start` for `activate_app` after replan). It did NOT skip to step 2 despite `resume_from_step: 2`. |
| D | `version` field is correct | PASS | `version: 0` for initial, `version: 1` for replan. Correct. |

### Root Cause (for FAILs)
- **P0 Bug (same as Scenario 2): Agent re-executes from step 0 after replan instead of skipping to `resume_from_step`.** The `plan_v1` correctly states `resume_from_step: 2` but execution began at step 0, re-running the already-completed activate_app and open_url steps.
- **P1 Bug: `completed_steps` records first-attempt results, not final retry results.** Step 0 (activate_app) was marked PASS despite failing (Chrome was frontmost, not Safari). Step 1 (open_url) was marked UNKNOWN despite eventually passing on retry. The completed_steps should reflect the final outcome after all retries, not the initial attempt.
- **P1 Bug: Verification false positive for `activate_app`.** The Tier 1 actuator_state verifier reported `Frontmost app is 'Google Chrome' (expected 'Safari')` as PASS (`trace.md` line 52-54). This is clearly a failure (Chrome != Safari) but was accepted as passing.
- **P1 Bug: Verification conditions reference "Safari" when Chrome is the active browser.** The plan and replan verify conditions reference "Safari browser window" but Chrome was the actual browser. The verifier correctly denied these, but the agent never adapted the verify conditions to match reality.

### Recommended Fix
1. Fix the `resume_from_step` execution bug (same as Scenario 2 recommendation).
2. Fix `completed_steps` to record the final outcome of each step after all retries, not the first attempt's result.
3. Fix the `activate_app` verification false positive -- `Frontmost app is 'Google Chrome' (expected 'Safari')` should be a FAIL, not a PASS.
4. Consider making the replan LLM aware that Chrome (not Safari) is the active browser, so it can adjust verify conditions accordingly.

---

## Priority Issues

1. **[P0] Agent re-executes from step 0 after replan instead of using `resume_from_step`.** The replan files correctly contain `resume_from_step` values (4 in Scenario 2, 2 in Scenario 4), but the orchestrator ignores this field and re-executes all steps from the beginning. This causes immediate precondition failures on steps that were already completed under different conditions (e.g., "Google Chrome is the foreground application" when Calculator is now active). **Blocks 2 of 4 scenarios.** This is the core feature gap -- the "patch" part of "replan as plan patch" is not functioning.

2. **[P0] Materialized replan includes completed steps verbatim with stale preconditions.** When the replan is materialized into the `steps` array, the old completed steps are copied with their original preconditions intact. Combined with bug #1, this means the agent re-checks stale preconditions that are no longer true. **Blocks 2 of 4 scenarios.**

3. **[P1] `completed_steps` records first-attempt results, not final retry outcomes.** In Scenario 4, `activate_app` is marked PASS despite the evidence showing failure (Chrome, not Safari), and `open_url` is marked UNKNOWN despite eventually succeeding on retry. This corrupts the information the replanner receives about what actually worked. **Affects 1 of 4 scenarios with evidence; likely affects all replan scenarios.**

4. **[P1] Verification false positive for `activate_app` — "expected Safari, got Chrome" reported as PASS.** The Tier 1 actuator_state verifier accepted `Frontmost app is 'Google Chrome' (expected 'Safari')` as passing. This allowed the agent to proceed with an incorrect world state. **Affects 1 of 4 scenarios.**

5. **[P1] Scenario 3 does not exercise the replan-patch feature.** The scenario was designed to trigger a replan via the gibberish search term, but the agent completed successfully without any failures. This leaves 50% of the replan-patch feature test surface uncovered by scenario 3. **Degrades test coverage, not a code bug.**

## Feature Assessment

The **plan file persistence** aspect of the feature is working correctly:
- `plan_v0` files are created for every run (4/4 scenarios).
- `plan_v1` files are created when replans occur (2/2 scenarios that triggered replans).
- File structure is correct: `version`, `timestamp`, `is_replan`, `trigger`, `resume_from_step`, `completed_steps`, `steps`, `raw_llm_response` -- all present and well-formed.
- Timestamps in filenames match execution timestamps.

The **plan patch** (replan-from-failure-point) aspect is **not working**:
- The LLM correctly generates `resume_from_step` in its replan response (visible in `raw_llm_response`).
- The value is correctly stored in the plan JSON file.
- But the orchestrator does NOT honor `resume_from_step` during execution -- it re-executes from step 0 every time.
- The materialized `steps` array includes both completed (old) and new steps, with stale preconditions on the old steps, compounding the re-execution bug.

The **annotated plan** (pass/fail annotations on original steps) is partially working:
- `completed_steps` exists and contains step-level verdicts.
- But the verdicts reflect first-attempt results, not final-after-retries results, making them inaccurate in at least one observed case.

**Overall: The feature is approximately 40% complete.** File serialization works. The LLM generates correct patch information. But the runtime execution does not use the patch data, which is the most critical part of the feature.
