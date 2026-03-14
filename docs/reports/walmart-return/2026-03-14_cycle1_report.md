# Customer Testing Report: Walmart Return (Cycle 1)

## Summary
- Scenarios tested: 3
- Passed: 0
- Failed: 3

---

## Scenario 1: Return a specific recent purchase (happy_path)

### Prompt
"Return the Crest 3D Whitestrips I bought on Walmart"

### Expected Outcome
Agent navigates to Walmart orders, finds the Crest 3D Whitestrips order, opens it, initiates the return flow, selects a return reason, and reaches the return confirmation or shipping label page.

### What Actually Happened
The agent correctly matched the `return-walmart-order` skill (confidence 0.98, params: item='Crest 3D Whitestrips') and generated an 11-step plan. Steps 0-1 executed successfully: Safari was activated and `https://www.walmart.com/orders` was opened. The plan included a `wait_for_user` step at step 3 asking the user to log in. This step **timed out after 120 seconds** (no screen change detected) -- the user was already logged in, so no login page appeared, but the wait_for_user mechanism relies on screen-change detection which did not fire. After the timeout, an observe step ran, then the agent attempted to click "Order containing 'Crest 3D Whitestrips'" at step 5. Molmo grounding returned NOT_FOUND, Gemini fallback also failed to locate the element (47s of searching). The agent declared the task infeasible after a single failed click attempt. Total duration: 222.2s.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Walmart orders page loads in Safari (URL contains walmart.com/orders) | PASS | `verify_pass` at 11:39:37 -- "Browser URL 'https://www.walmart.com/orders' matches destination" (events.jsonl line 15-16) |
| 2 | The correct order containing "Crest 3D Whitestrips" is opened | FAIL | `element_not_found` at 11:42:47 -- Molmo returned NOT_FOUND; Gemini fallback also failed (trace.md line 84-86). Order was never opened. |
| 3 | The return reason selection screen appears and a reason is chosen | FAIL | Agent never reached this step. Aborted at step 5 (readable_log.txt line 68-69). |

### Root Causes
1. **wait_for_user timeout wasted 120s**: The plan unconditionally inserted a "please log in" wait step. The user was already logged in, so no screen change occurred, causing a 120s timeout. The wait_for_user mechanism auto-resumes only on screen change, but a page that is already loaded produces no change.
2. **Grounding failure on order list**: After the timeout, both Molmo and Gemini failed to locate "Order containing 'Crest 3D Whitestrips'" on the orders page. Possible causes: (a) the item was not visible in the viewport (Walmart paginated orders, required scrolling), (b) the visual grounding prompt used a semantic description ("Order containing ...") that didn't match Walmart's card layout, or (c) the page had not fully rendered. No screenshots were saved, so we cannot confirm which.
3. **No scroll recovery attempted**: After the NOT_FOUND, the agent declared infeasibility immediately (force=True) with 0 replans. The plan had `on_fail: retry_different` with `max_retries: 3` for this step, but the orchestrator did not retry or scroll -- it went straight to infeasibility.

### Recommended Fixes
1. Make `wait_for_user` conditional: only insert it if the observe step detects a login page. Alternatively, add a "skip if already logged in" guard that checks the page content before waiting.
2. Add scroll-on-not-found recovery for order list steps -- the skill template already says "scroll down to find it" but the plan's `on_fail: retry_different` was not honored.
3. Save screenshots on every observe and every NOT_FOUND event for post-mortem debugging.

---

## Scenario 2: Vague description matching multiple possible orders (edge_case)

### Prompt
"Return the shoes I got from Walmart last month"

### Expected Outcome
Agent navigates to orders, scans for shoe-related items, and either picks the most recent shoe order or asks the user to clarify which pair if multiple shoe orders exist.

### What Actually Happened
The agent matched `return-walmart-order` (confidence 0.98, params: item='shoes') and generated a 6-step plan. **Critically, the plan omitted the `open_url` and `activate_app` steps entirely.** The screen description from the initial observe showed Walmart's orders page was still visible in Safari from Scenario 1's run. The planner saw this existing state and assumed the agent was already on the correct page, generating a plan that started with observe then jumped directly to clicking "Start a return button" -- skipping order navigation and order selection entirely. Molmo returned NOT_FOUND for "Start a return button" (which was never going to be on the orders list page). Gemini fallback also failed. The agent declared infeasibility after a single failed click at step 1. Total duration: 63.7s, 0 steps passed.

Note: The LLM response in events.jsonl shows the plan JSON actually contained a step to click "Order card or link for 'shoes' from last month" before clicking "Start a return button", but the steps_summary in the plan_complete event shows this step was dropped. The executed plan jumped from observe directly to "Start a return button", suggesting a plan parsing or step-flattening bug.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Agent reaches the Walmart orders page and visually scans for shoe items | FAIL | Agent never navigated to walmart.com/orders. Plan omitted open_url step. The observe at step 0 described whatever was on screen (stale state from scenario 1), not a fresh orders page load (events.jsonl lines 5-6, readable_log.txt line 48). |
| 2 | If multiple shoe orders exist, agent does not blindly return the wrong one | INSUFFICIENT_EVIDENCE | Agent never reached the point of identifying any orders. It failed at step 1 trying to click "Start a return button" which doesn't exist on the orders list page (trace.md lines 36-41). |
| 3 | Agent does not start a return on a non-shoe item | PASS (vacuously) | Agent never started a return on anything. However, this is a vacuous pass -- the agent simply failed before it could make any selection. |

### Root Causes
1. **Stale screen state from previous scenario**: The test runner executed scenarios sequentially without resetting browser state. Safari still had the Walmart orders page open from Scenario 1. The planner saw this and generated a plan that assumed the orders page was already ready.
2. **Planner does not enforce mandatory skill steps**: The `return-walmart-order` skill explicitly says step 1 is `open_url: https://www.walmart.com/orders`, but the planner ignored this because the screen description showed the page was already open. The skill template should be treated as a minimum-steps contract, not an optional suggestion.
3. **Plan step dropped during parsing**: The LLM returned a 7-step plan including "Order card or link for 'shoes' from last month", but the executed plan had only 6 steps with the order-finding step missing from the steps_summary. This suggests a parsing bug that dropped the step between observe and "Start a return button".
4. **No scroll or replan on failure**: Same as scenario 1 -- single NOT_FOUND triggers immediate infeasibility abort with 0 replans.

### Recommended Fixes
1. Reset browser state between scenarios in the test harness (close all tabs or navigate to blank page).
2. Force the planner to always include skill-mandated steps (open_url, activate_app) regardless of current screen state, or have the orchestrator prepend these steps automatically.
3. Investigate the plan-parsing bug: the LLM's JSON contained 7 steps but only 6 were executed. The "click order" step was silently dropped.
4. Add scroll-and-retry before declaring infeasibility on the first NOT_FOUND.

---

## Scenario 3: Item not eligible for return (error_recovery)

### Prompt
"Return the bag of dog food I ordered on Walmart"

### Expected Outcome
Agent navigates to orders, finds the dog food order, opens it, and discovers there is no "Start a return" button because the item is past its return window or is in a non-returnable category. Agent reports clearly to the user that the item is not eligible for return.

### What Actually Happened
Identical failure pattern to Scenario 2. The agent matched `return-walmart-order` (confidence 0.98, params: item='bag of dog food') and generated a 4-step plan. The LLM response JSON actually contained 5 steps including "click Order containing 'bag of dog food'" with scroll recovery, but the steps_summary shows only 4 steps -- the order-finding step was again dropped during plan parsing. The executed plan went: observe -> click "Start a return button" -> wait_for_user -> done. The "Start a return button" was not found (NOT_FOUND from both Molmo and Gemini). Agent declared infeasibility after 1 failed click. Total duration: 58.3s, 0 steps passed.

The error recovery test was never actually tested -- the agent failed to even navigate to the dog food order, let alone discover it was non-returnable.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Agent navigates to Walmart orders and locates the dog food order | FAIL | Agent never navigated to walmart.com/orders (no open_url step in plan). Never attempted to find the dog food order. The "Order containing 'bag of dog food'" step was present in the LLM's JSON but dropped from the executed plan (events.jsonl line 4: steps_summary shows only 4 steps vs 5 in llm_response). |
| 2 | Agent does not get stuck in a retry loop -- infeasibility fires within 3 attempts | PASS (with caveat) | Agent declared infeasibility after exactly 1 failed click attempt (trace.md lines 44-49). It did not loop. However, this is a "wrong reason" pass: it fired infeasibility because it was looking for "Start a return button" on the orders list page (wrong page), not because it correctly identified the item as non-returnable. |
| 3 | Agent surfaces a clear message explaining the item cannot be returned | FAIL | The infeasibility message was: "The 'Start a return button' is essential for the task goal and was not found on the current screen, making the task unachievable in its current state." This is a generic NOT_FOUND message, not a specific explanation about return eligibility. The agent never reached the order detail page to check for return eligibility (stdout.txt lines 48-49). |

### Root Causes
1. **Same stale-state + plan-parsing bugs as Scenario 2**: Plan omitted navigation, dropped the order-finding step.
2. **Error recovery path never exercised**: The skill template's error recovery section says "If 'Return window closed' or not eligible: report that the item is no longer eligible for return" -- but the agent never reached the order detail page where this condition would be observable.
3. **Generic infeasibility message**: The infeasibility abort message does not distinguish between "element not on this page because I'm on the wrong page" vs. "element genuinely does not exist for this item."

### Recommended Fixes
1. Same navigation and plan-parsing fixes as Scenario 2.
2. Teach the infeasibility checker to differentiate between "wrong page" and "feature absent" by checking whether the current URL matches the expected URL for the current step.
3. When the skill's error recovery section mentions specific failure modes (e.g., "return window closed"), the planner should generate explicit check-and-report branches in the plan.

---

## Priority Issues

1. **[P0] Plan step dropping during parsing** -- The LLM generates correct multi-step plans including order-finding steps, but the orchestrator's plan parser silently drops steps. In Scenario 2, a 7-step LLM plan became 6 executed steps; in Scenario 3, a 5-step plan became 4 executed steps. Both times, the critical "find and click the order" step was the one dropped. This blocks all 3 scenarios because without finding the order, no return flow can proceed.

2. **[P0] Planner skips navigation when screen state looks "close enough"** -- When Safari already shows walmart.com/orders from a previous run, the planner omits open_url and activate_app steps. Each scenario invocation should be treated as a fresh execution, not an incremental continuation. Blocks scenarios 2 and 3 directly; would also block scenario 1 if run after another scenario.

3. **[P1] wait_for_user blocks for 120s when no login is needed** -- The plan unconditionally inserts a wait_for_user("please log in") step. When the user is already logged in, the screen-change detection never fires, wasting 120s. This degraded scenario 1 and wasted 55% of its total runtime.

4. **[P1] No scroll-on-not-found recovery** -- All 3 scenarios declared infeasibility after exactly 1 failed click with 0 replans. The plan's `on_fail: retry_different` and `on_fail: scroll` directives were ignored by the orchestrator. The skill template explicitly says to scroll down when the item is not visible, but this never happened.

5. **[P2] No screenshots captured in any run** -- No screenshots were saved for any of the 3 runs, making post-mortem debugging impossible. We cannot confirm what was actually on screen when grounding failed.

6. **[P2] Duplicate skill definitions** -- Warning in all 3 runs: "Duplicate skill name 'return-walmart-order': 'return_walmart_order.md' overwrites previous definition." Two files (`return-walmart-order.md` and `return_walmart_order.md`) define the same skill name. The underscore version (which has minimal/stub content) overwrites the hyphenated version (which has the real skill template). This may be why the planner's output quality degraded in scenarios 2-3 -- it may have received the stub skill template instead of the detailed one.

## Instrumentation Gaps

1. **Screenshot capture on every observe and NOT_FOUND**: Currently no screenshots are persisted in the run log directory. Add screenshot saving to the event log so post-mortem analysis can confirm what the vision model actually saw.
2. **Screen description text not logged**: The observe steps produce screen descriptions (lengths 173-2027 chars noted in stdout) but the actual text is not recorded in events.jsonl or trace.md. Log the full screen description to understand what the planner and grounding models were working with.
3. **Plan diff logging**: When the executed plan differs from the LLM's raw JSON (steps dropped, reordered, etc.), log a diff showing what changed during parsing. This would have immediately surfaced the step-dropping bug.
4. **Skill template version logging**: Log which skill file was actually loaded (full path) in the event stream, so the duplicate-skill-overwrites issue is traceable per-run.
5. **wait_for_user outcome logging**: The wait_for_user step should log whether it exited via screen-change detection or timeout, and include the before/after screen descriptions to diagnose why detection failed.
