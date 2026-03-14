# Customer Testing Report Cycle 2: walmart-return-fixes

## Summary
- Scenarios tested: 3
- Passed: 0
- Failed: 3

Cycle 2 shows clear improvements over cycle 1 in three areas: navigation, scroll recovery, and screenshot capture. However, two new blocking issues surfaced (pre-click validation false positives, replan crash) while the plan step dropping bug persists, preventing any scenario from completing.

---

## Scenario 1: Return a specific recent purchase (happy_path)

### Prompt
"Return the Crest 3D Whitestrips I bought on Walmart"

### Expected Outcome
Agent navigates to Walmart orders, finds the Crest 3D Whitestrips order, opens it, initiates the return flow, selects a return reason, and reaches the return confirmation or shipping label page.

### What Actually Happened
The agent matched `return-walmart-order` skill (confidence 0.98, params: item='Crest 3D Whitestrips') and generated a 7-step plan. Step 0 (open_url to walmart.com/orders) succeeded and was verified via tier 1 URL check. Step 1 (observe) described the screen. Step 2 attempted to click "order that matches 'Crest 3D Whitestrips'" -- the Molmo grounding model found an element at (423, 85), but pre-click validation (Gemini crop check) rejected the grounding result, declaring it didn't match. Replanning was triggered, but the replan attempt crashed with a `TypeError: unhashable type: 'dict'` at 12:58:29, aborting the entire run. Total duration: 128s.

**Screenshot analysis**: All 3 captured screenshots (step_00, step_01, find result) show the macOS desktop wallpaper (Lake Tahoe landscape), NOT the Walmart orders page in Safari. The grounding model's red crosshair at (423, 85) is placed on empty sky in the wallpaper image. This confirms the screenshot capture is photographing the desktop background rather than the browser window, which is the root cause of both the grounding failure AND the pre-click validation failure -- neither model can find Walmart UI elements on a landscape photo.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Walmart orders page loads in Safari (URL contains walmart.com/orders) | PASS | Tier 1 verify_pass at 12:57:00: "Browser URL 'https://www.walmart.com/orders' matches destination" (trace.md line 40-42). URL was correctly opened. |
| 2 | The correct order containing "Crest 3D Whitestrips" is opened | FAIL | Grounding model found element at (423, 85) but pre-click validation rejected it (stdout.txt line 42: "Pre-click validation failed: element at (423, 85) does not appear to be 'order that matches Crest 3D Whitestrips'"). Screenshots confirm the vision models were seeing the desktop wallpaper, not the browser (find_*.jpg shows crosshair on Lake Tahoe landscape). |
| 3 | The return reason selection screen appears and a reason is chosen | FAIL | Agent never reached this step. Crashed with "unhashable type: 'dict'" during replan (trace.md line 65, stdout.txt line 49). |

### Root Causes
1. **Screenshot captures desktop wallpaper, not browser window**: All screenshots show the macOS Lake Tahoe wallpaper. The open_url step verified successfully via tier 1 (URL check), confirming Safari did navigate to walmart.com/orders. But the screenshot mechanism is capturing the desktop layer behind the browser, not the frontmost window. This means the grounding model and pre-click validator are operating on completely wrong visual input.
2. **"unhashable type: 'dict'" crash in replan**: After pre-click validation failure triggered replanning, the replan code crashed with a TypeError. This is likely caused by the execution history containing dict objects being used in a set or as dict keys during the replan preparation. This crash prevented any recovery attempt.

### Recommended Fixes
1. Fix screenshot capture to ensure it captures the frontmost application window (Safari), not the desktop background. Check whether `screencapture` or the CGImage API is being called with the correct window ID or is falling back to the full desktop.
2. Fix the `unhashable type: 'dict'` crash in the replan codepath -- likely in execution history serialization where step params (dicts) are being used in a hashable context (set or dict key).

---

## Scenario 2: Vague description matching multiple possible orders (edge_case)

### Prompt
"Return the shoes I got from Walmart last month"

### Expected Outcome
Agent navigates to orders, scans for shoe-related items, and either picks the most recent shoe order or asks the user to clarify which pair if multiple shoe orders exist.

### What Actually Happened
The agent matched `return-walmart-order` (confidence 1.0, params: item='shoes') and generated a 7-step plan. Step 0 (open_url) succeeded via tier 1. Step 1 (observe) ran. Step 2 attempted to click "the order containing 'shoes'" -- Molmo found an element at (509, 767), but pre-click validation rejected it. Replanning triggered successfully this time (no crash), generating a 6-step plan (1 step dropped due to invalid `on_fail='abort_reason'`). The replan re-opened the URL, observed, added a wait_for_user login check, observed again, then attempted to click "the order containing 'shoes'" at step 4. Molmo found (509, 185) but pre-click validation rejected again. A retry with refined query "(look carefully, may be partially hidden)" was attempted -- Molmo returned (420, 85) -- pre-click validation rejected a third time. Retries exhausted. Total duration: 214s.

**Screenshot analysis**: All 5 step screenshots and all 3 find-result screenshots show the macOS Lake Tahoe wallpaper, confirming the same screenshot-capture-desktop-not-browser bug as scenario 1. The Molmo grounding crosshairs land on water, rocks, and sky -- never on any Walmart UI element. The grounding model is being asked to find "shoes" on a landscape photo and is returning arbitrary points.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Agent reaches the Walmart orders page and visually scans for shoe items | FAIL | open_url succeeded (tier 1 verified at 12:59:50 and 13:01:20, trace.md lines 40, 88). But all screenshots show desktop wallpaper, not the orders page (125951_step_00.png, 125958_step_01.png, 130121_step_00.png, 130128_step_01.png all show Lake Tahoe). The vision system never saw the actual orders page. |
| 2 | If multiple shoe orders exist, agent does not blindly return the wrong one | FAIL | Agent never identified any orders. All 3 grounding attempts returned false positive coordinates on the wallpaper image, which were correctly rejected by pre-click validation (trace.md lines 57, 115, 129). Agent never reached the order selection stage. |
| 3 | Agent does not start a return on a non-shoe item | PASS (vacuously) | Agent never clicked any order or started any return. Failure occurred before item selection. |

### Root Causes
1. **Same screenshot-captures-desktop bug as scenario 1**: All grounding attempts operated on the wallpaper image. The Molmo model returned hallucinated coordinates on landscape imagery.
2. **Plan step dropped due to invalid on_fail value**: During replan, the LLM returned 7 steps but 1 was dropped because it used `on_fail='abort_reason'`, which is not a valid on_fail value. Only `['abort', 'replan', 'retry_different', 'wait_for_user']` are accepted (stdout.txt line 49-50). This is the same class of bug as cycle 1's P0 step-dropping issue, now triggered by invalid on_fail values rather than invalid actions.
3. **Pre-click validation working correctly (but on wrong data)**: The pre-click validator correctly rejected all 3 grounding attempts because the cropped regions around (509,767), (509,185), and (420,85) showed wallpaper, not Walmart order cards. The validator is doing its job -- the input screenshots are the problem.

### Recommended Fixes
1. Same screenshot capture fix as scenario 1.
2. When the LLM returns an unrecognized `on_fail` value, fall back to `'replan'` instead of dropping the entire step. The step action and params are valid -- only the recovery strategy is wrong.

---

## Scenario 3: Item not eligible for return (error_recovery)

### Prompt
"Return the bag of dog food I ordered on Walmart"

### Expected Outcome
Agent navigates to orders, finds the dog food order, opens it, and discovers there is no "Start a return" button because the item is past its return window or is in a non-returnable category. Agent reports clearly to the user that the item is not eligible for return.

### What Actually Happened
The agent matched `return-walmart-order` (confidence 0.98, params: item='bag of dog food') and generated a plan. The LLM returned 6 steps, but 1 was dropped because it used `on_fail='scroll'` (not a valid value). The dropped step was the critical "click the order that contains 'bag of dog food'" step. This left the executed plan as: open_url -> observe -> click "Start a return button" -> wait_for_user -> done. The agent went straight to looking for "Start a return button" on the orders list page (wrong page -- should be on order detail page). Molmo correctly returned NOT_FOUND 4 times (initial + 3 scroll recovery attempts). After 3 scroll attempts, infeasibility detection fired correctly: "The 'Start a return button' is essential for the task goal and was not found on the current screen." Total duration: 163s.

**Screenshot analysis**: The first 3 screenshots (step_00, step_01, not_found attempt 1) show the Lake Tahoe wallpaper (same bug). The 4th and 5th screenshots (130523, 130554 -- after scroll recovery attempts 1 and 2) show the macOS **lock screen** with "Jagat Pudipeddi" and "Enter Password" -- the machine locked during execution. The 6th screenshot (130626) returns to the wallpaper. So during the scroll recovery phase, the agent was scrolling and searching for a return button while looking at the lock screen. Despite this, Molmo correctly returned NOT_FOUND for all attempts (it cannot find a "Start a return button" on a lock screen).

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Agent navigates to Walmart orders and locates the dog food order | FAIL | open_url succeeded (tier 1 verified at 13:04:07, trace.md line 40-42). But the "click order containing 'bag of dog food'" step was dropped during plan parsing due to invalid `on_fail='scroll'` (stdout.txt lines 29-30: "Plan step count mismatch: LLM returned 6 steps but only 5 parsed (1 dropped)"). Agent never attempted to find the dog food order. |
| 2 | Agent does not get stuck in a retry loop -- infeasibility fires within 3 attempts | PASS | Scroll recovery executed exactly 3 scroll attempts (trace.md lines 63-86: scroll recovery 1/3, 2/3, 3/3). Infeasibility check fired at 13:06:29 with force=True after exhausting all 3 scroll attempts (trace.md line 87-91). Agent did not loop endlessly. |
| 3 | Agent surfaces a clear message explaining the item cannot be returned | FAIL | Infeasibility message was: "The 'Start a return button' is essential for the task goal and was not found on the current screen, making the task unachievable in its current state" (trace.md line 90-91). This is a generic NOT_FOUND message, not a specific explanation about return eligibility. The agent never reached the order detail page to check eligibility because the "find order" step was dropped. Additionally, the machine was locked during scroll recovery (screenshots 130523, 130554 show lock screen), so the agent was searching for UI elements on the lock screen. |

### Root Causes
1. **Plan step dropped due to invalid on_fail='scroll'**: The LLM generated a correct step to find the dog food order with `on_fail='scroll'`, but 'scroll' is not a valid on_fail value. The planner dropped the entire step instead of falling back to a valid on_fail strategy. This is the same plan-step-dropping P0 bug from cycle 1, still present but now caused by invalid on_fail values.
2. **Machine locked during execution**: Screenshots 130523 and 130554 show the macOS lock screen. The machine auto-locked ~1 minute into the scroll recovery phase. The agent continued executing (scrolling, searching) while locked. The scroll actions sent to the locked screen had no effect on Safari (which was behind the lock screen).
3. **Screenshot captures wrong content**: Even before the lock, screenshots showed the desktop wallpaper, not Safari.
4. **Scroll recovery works mechanically but on wrong screen**: The scroll recovery feature (new since cycle 1) correctly executed 3 attempts and then triggered infeasibility. The mechanics are sound. But it was scrolling on the lock screen/desktop, not in Safari.

### Recommended Fixes
1. Fall back to `on_fail='replan'` instead of dropping steps with unrecognized on_fail values.
2. Disable screen auto-lock during agent execution, or have the agent detect the lock screen and pause/alert.
3. Fix screenshot capture to target the browser window.

---

## Priority Issues

1. **[P0] Screenshot capture returns desktop wallpaper, not browser window** -- Blocks ALL 3 scenarios. All screenshots across all runs show the macOS Lake Tahoe desktop wallpaper or the lock screen instead of the Safari browser window. The open_url step passes tier 1 verification (URL check via AppleScript), confirming Safari is navigated correctly, but the vision pipeline is screenshot-ing the wrong layer. This means the grounding model, pre-click validator, and screen description model all operate on completely wrong visual input. Every click attempt either produces hallucinated coordinates (on landscape imagery) or correctly returns NOT_FOUND (because there are no web UI elements in a landscape photo). Root cause is likely in the screenshot capture code path: either `screencapture` is not targeting the correct window, or CGWindowListCreateImage is capturing the desktop composite without the browser in front.

2. **[P0] Plan step dropping on invalid on_fail values (partially fixed)** -- Blocks 2 of 3 scenarios (S2, S3). Cycle 1's step-dropping bug was caused by invalid action names; that appears fixed (all actions parse correctly now). But the same drop behavior now triggers on invalid `on_fail` values: the LLM generates `on_fail='scroll'` (S3) and `on_fail='abort_reason'` (S2), which are not in the valid set `['abort', 'replan', 'retry_different', 'wait_for_user']`. The planner drops the entire step instead of falling back to a default like `'replan'`. In S3, this dropped the critical "find the order" step, causing the agent to skip straight to looking for "Start a return button" on the wrong page.

3. **[P1] "unhashable type: 'dict'" crash during replan** -- Blocks 1 of 3 scenarios (S1). After pre-click validation fails and replanning is triggered, the replan code path crashes with `TypeError: unhashable type: 'dict'`. This prevented any recovery in scenario 1. Likely caused by execution history containing dict params being used in a set/frozenset context or as a dict key during the replan phase.

4. **[P1] Pre-click validation false positive (masked by P0)** -- Would block all 3 scenarios if P0 is fixed. The pre-click validation correctly rejects grounding results that don't match. However, this is currently untestable because the screenshots being validated are of the desktop wallpaper, not the browser. Once the screenshot capture is fixed, the pre-click validation may still be too aggressive for semantic element descriptions like "order that matches 'Crest 3D Whitestrips'" -- these are not pixel-exact labels and the validator may reject correct grounding results. Needs re-evaluation after P0 is fixed.

5. **[P2] Machine auto-locked during execution** -- Affected scenario 3. The macOS lock screen appeared during scroll recovery (screenshots 130523, 130554 at 1:04pm and 1:05pm). The agent continued executing scroll actions on the lock screen. Agent should either disable auto-lock during execution or detect the lock screen and pause.

---

## Improvements Since Cycle 1

1. **Navigation no longer skipped**: All 3 scenarios now include `open_url('https://www.walmart.com/orders')` in their plans and it succeeds. In cycle 1, scenarios 2 and 3 omitted navigation entirely due to stale screen state. This P0 from cycle 1 is **fixed**.

2. **Scroll recovery works**: Scenario 3 demonstrated 3 scroll-down-and-retry attempts before declaring infeasibility. In cycle 1, all scenarios declared infeasibility after exactly 1 failed attempt with 0 scroll recovery. This P1 from cycle 1 is **fixed** (mechanically, though it was scrolling the wrong screen due to the screenshot bug).

3. **Screenshots captured**: All 3 scenarios saved multiple screenshots (3 in S1, 8 in S2, 6 in S3). In cycle 1, zero screenshots were saved across all runs. This P2 from cycle 1 is **fixed** (though the screenshots show the wrong content due to the new P0 bug).

4. **wait_for_user no longer blocks for 120s**: Scenario 2's replan included a wait_for_user step that auto-resumed after 5 seconds on screen change detection (stdout.txt line 64: "Screen changed (9.5%) after 5s"). In cycle 1, wait_for_user blocked for the full 120s timeout. This P1 from cycle 1 is **fixed**.

5. **Duplicate skill warning gone**: No "Duplicate skill name" warnings appeared in any of the 3 runs. The P2 duplicate skill file issue from cycle 1 is **fixed**.

6. **Skill routing improved**: All 3 scenarios correctly matched `return-walmart-order` with high confidence (0.98-1.0) and correct parameter extraction. The skill router is working well.

7. **Replan actually triggers**: Scenarios 1 and 2 both triggered replanning on click failures (unlike cycle 1 where infeasibility fired immediately). The orchestrator now attempts recovery before giving up. S1 crashed during replan (dict bug), S2 replanned successfully but still failed due to the screenshot bug.

---

## Cycle 1 vs Cycle 2 Summary

| Issue | Cycle 1 Status | Cycle 2 Status |
|-------|---------------|----------------|
| P0: Plan step dropping (invalid actions) | OPEN -- steps silently dropped | FIXED for actions, OPEN for on_fail values |
| P0: Stale screen navigation skipped | OPEN -- no open_url in S2, S3 | FIXED -- all scenarios navigate |
| P1: wait_for_user 120s timeout | OPEN -- 120s wasted | FIXED -- 5s auto-resume |
| P1: No scroll recovery | OPEN -- 0 scrolls, immediate abort | FIXED -- 3 scroll attempts in S3 |
| P2: No screenshots | OPEN -- 0 screenshots | FIXED (but captures wrong content) |
| P2: Duplicate skills | OPEN -- stub overwrites real skill | FIXED -- no duplicate warning |
| NEW P0: Screenshots show desktop, not browser | N/A | OPEN -- blocks all 3 scenarios |
| NEW P1: "unhashable type: dict" replan crash | N/A | OPEN -- blocks S1 |
| NEW P1: Pre-click validation false positives | N/A | OPEN (masked by screenshot bug) |
| NEW P2: Machine auto-lock during execution | N/A | OPEN -- affected S3 |

**Net assessment**: 4 of 6 cycle 1 issues are fixed. 0 of 3 scenarios pass (same as cycle 1). The fundamental blocker shifted from "agent doesn't navigate or recover" to "agent navigates and recovers but is visually blind because screenshots capture the desktop wallpaper." Fixing the screenshot capture P0 is the single highest-leverage change for cycle 3.
