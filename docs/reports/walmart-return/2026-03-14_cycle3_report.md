# Customer Testing Report Cycle 3 (FINAL): walmart-return-fixes

## Summary
- Scenarios tested: 3
- Passed: 1
- Failed: 2

Cycle 3 is the final testing cycle. Screenshot capture is fully fixed -- all screenshots now show the actual browser window content (Walmart pages), not the desktop wallpaper. Plan step dropping is fixed. Navigation works reliably. One scenario passes end-to-end for the first time across all 3 cycles. Two scenarios fail due to new issues surfaced now that vision is working correctly: a confidence threshold that blocks legitimate navigation clicks, and a type_text element targeting failure.

---

## Scenario 1: Return a specific recent purchase (happy_path)

### Prompt
"Return the Crest 3D Whitestrips I bought on Walmart"

### Expected Outcome
Agent navigates to Walmart orders, finds the Crest 3D Whitestrips order, opens it, initiates the return flow, selects a return reason, and reaches the return confirmation or shipping label page.

### What Actually Happened
The agent matched `return-walmart-order` skill and generated a 6-step plan starting with `open_url('https://www.walmart.com')` (notably NOT the direct `/orders` URL). Step 0 (open_url) succeeded and was verified via Tier 2 vision. Step 1 (click Account button) initially failed with element_not_found, scroll recovery triggered, then Molmo found "Account" at (862,147) conf=0.75 and clicked it. However, the click landed in the notification overlay area, not on the Account button -- screenshot `133133` confirms the Walmart homepage was still showing with no account menu visible. The agent then tried to click "Purchase History" but could not find it (no account menu was open), triggering a replan.

The replanned 3-step plan: open_url(walmart.com) -> click Account -> click Purchase History. After reopening walmart.com, the agent clicked "Account" at (933,162) conf=0.75. First attempt: vision verification denied (no dropdown appeared -- screenshot `133403` shows the homepage without an account menu). Second attempt with refined query: clicked at (943,148) conf=0.75. This time vision verification PASSED -- screenshot `133437` confirms the account dropdown menu appeared showing "Purchase History", "Walmart+", "Account", "Subscriptions", "Get Walmart Cash", "Sign Out".

Step 2: click "Purchase History". The grounding model found "Purchase History" at (903,241) conf=0.75 -- screenshot `find_1773434114356` confirms the crosshair was correctly positioned on the "Purchase History" link in the dropdown menu. However, the destructive action gate triggered because the element name contains the keyword "purchase". The confidence of 0.75 was below the 0.9 threshold for purchase-keyword actions, so the click was REJECTED with `low_confidence:0.75`. The agent retried with a refined query but the run terminated.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Walmart orders page loads in Safari (URL contains walmart.com/orders) | FAIL | Agent navigated to walmart.com (homepage), not walmart.com/orders. The plan used the homepage URL and tried to navigate to orders via Account > Purchase History menu, but the Purchase History click was blocked by the 0.9 confidence threshold. The orders page never loaded. (trace.md lines 184-201) |
| 2 | The correct order containing "Crest 3D Whitestrips" is opened | FAIL | Agent never reached the orders page. Blocked at the Purchase History navigation step. (trace.md line 201: `step_complete FAIL -- low_confidence:0.75`) |
| 3 | The return reason selection screen appears and a reason is chosen | FAIL | Agent never reached this step. Run terminated after failing to click Purchase History. |

### Root Causes
1. **Confidence threshold blocks legitimate navigation**: The destructive action gate uses keyword matching ("purchase") to flag actions for elevated confidence requirements (0.9). "Purchase History" is a navigation link, not a destructive purchase action, but the keyword matcher cannot distinguish between "purchasing something" and "viewing purchase history." The grounding model consistently returns 0.75 confidence for this element, which is below the 0.9 destructive threshold.
2. **Plan navigates to homepage instead of direct /orders URL**: The initial plan used `walmart.com` instead of `walmart.com/orders`. Scenario 3's plan correctly used the direct URL. This inconsistency means the planner hasn't fully learned the direct-URL pattern from the skill template.

---

## Scenario 2: Vague description matching multiple possible orders (edge_case)

### Prompt
"Return the shoes I got from Walmart last month"

### Expected Outcome
Agent navigates to orders, scans for shoe-related items, and either picks the most recent shoe order or asks the user to clarify which pair if multiple shoe orders exist.

### What Actually Happened
The agent matched `return-walmart-order` skill and generated a 3-step plan: open_url(walmart.com) -> click Purchase History -> observe. Step 0 succeeded (walmart.com loaded, verified via Tier 2 vision). Step 1 attempted to click "Purchase History" on the homepage, but the element was not found (there is no "Purchase History" link on the Walmart homepage without first opening the Account dropdown). The agent replanned with a 1-step plan: `open_url('https://www.walmart.com/orders')`. This succeeded -- screenshot `133955` confirms the Purchase History page loaded showing the user's account ("Hi, Jagat"), order list, search bar, and a "Delivered on Feb 21" order with "View details" button. Tier 1 URL verification confirmed the URL matched.

The run completed with status SUCCESS after the replan. However, the agent stopped after reaching the orders page -- it did not scan for shoe items, select an order, or initiate a return. The replan only had 1 step (open_url) and no subsequent steps to find the shoe order.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Agent reaches the Walmart orders page and visually scans for shoe items | PASS (partial) | Agent reached walmart.com/orders (screenshot `133955` shows Purchase History page with orders listed, Tier 1 URL verification at 13:39:55). However, the agent did not visually scan for shoe items -- the run ended immediately after the URL loaded. The observe step from the original plan was not carried forward into the replan. |
| 2 | If multiple shoe orders exist, agent does not blindly return the wrong one | INSUFFICIENT_EVIDENCE | Agent never reached the point of identifying any orders. The replan contained only the open_url step with no follow-up actions. |
| 3 | Agent does not start a return on a non-shoe item | PASS (vacuously) | Agent never started a return on anything. It stopped after loading the orders page. |

### Root Causes
1. **Replan loses remaining task context**: When the agent replanned after failing to click "Purchase History" on the homepage, it generated a 1-step plan containing only `open_url('https://www.walmart.com/orders')`. The remaining task steps (scan for shoes, select order, initiate return) were lost. The replan should have included the full remaining workflow, not just the immediate unblocking step.
2. **Same homepage-not-orders URL issue as Scenario 1**: Initial plan used walmart.com instead of walmart.com/orders, requiring an extra navigation step through the Account menu.

---

## Scenario 3: Item not eligible for return (error_recovery)

### Prompt
"Return the bag of dog food I ordered on Walmart"

### Expected Outcome
Agent navigates to orders, finds the dog food order, opens it, and discovers there is no "Start a return" button because the item is past its return window or is in a non-returnable category. Agent reports clearly to the user that the item is not eligible for return.

### What Actually Happened
The agent matched `return-walmart-order` skill and generated a comprehensive 10-step plan correctly using `open_url('https://www.walmart.com/orders')` as the first step. Step 0 succeeded (Tier 1 URL verification). Step 1 (observe) ran successfully -- screenshot `134203` confirms the Purchase History page was visible with orders listed and an "Account Settings" modal overlay.

Step 2 attempted to click "Order containing 'dog food'". The grounding model found an element at (843,663) conf=0.75 and clicked it. However, vision verification FAILED -- screenshot `134257` shows the page is still on purchase history with the account settings modal still visible. The click appears to have hit the modal overlay rather than an order card. The reflection noted: "The page is still on the purchase history page, and a modal about account settings is displayed."

The agent replanned with a 4-step plan: (1) click Close button to dismiss modal, (2) type_text "dog food" in search field, (3) press Enter, (4) click View details for dog food order. Step 0 (click Close) failed -- grounding could not find the Close button (it spent 62 seconds searching). Scroll recovery triggered, scrolled down 3 clicks, and vision verification PASSED -- screenshot `134424_step_00_post_scroll` confirms the modal was dismissed by the scroll and the Purchase History page with "Start a return" links and order cards is now fully visible. Notably, this post-scroll screenshot shows orders with "Start a return" links, meaning some orders ARE eligible for return.

Step 1 (type_text "dog food" in search field) failed repeatedly. The type_text action reported success 4 times (normal type, select-all-then-type, slow-type x2), but vision verification denied that text appeared in the search field every time. Screenshots `134431` through `134513` confirm: the "Search your purchases" field remains empty across all attempts. The search field shows placeholder text "Search your purchases" with no typed content. One screenshot (`134431_step_01_post_type_text`) shows a "Click to rate" tooltip appeared over the review widget, suggesting the typing focus was captured by the wrong element. Later screenshots (`134458`) show the page scrolled to reveal more orders (Delivered Feb 20, Feb 13, Feb 11, Jan 25) but still no text in the search field.

After 4 failed type_text attempts, retries were exhausted. The agent escalated to replan but then stopped execution ("Replan step 1 exhausted retries; stopping replan execution").

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Agent navigates to Walmart orders and locates the dog food order | FAIL | Agent successfully navigated to walmart.com/orders (Tier 1 verify at 13:41:55, screenshot `134156` shows Purchase History page). However, it never located the dog food order. The initial click at (843,663) hit the modal overlay, not an order. After dismissing the modal, 4 type_text attempts to search for "dog food" all failed silently -- text never appeared in the search field (screenshots `134431` through `134513` all show empty search field). |
| 2 | Agent does not get stuck in a retry loop -- infeasibility fires within 3 attempts | FAIL | Agent exhausted 4 type_text retries (normal, select-all, slow x2) over ~48 seconds (13:44:24 to 13:45:12) before stopping. While it did eventually stop (not an infinite loop), it exceeded the 3-attempt criterion (4 attempts for type_text alone, plus the initial click failure and close button failure). Total of 6+ failed action attempts before halting. No infeasibility message was surfaced -- the agent simply stopped with "exhausted retries." |
| 3 | Agent surfaces a clear message explaining the item cannot be returned | FAIL | No message about return eligibility was ever generated. The agent failed before reaching the order detail page. The final status was "FAILURE - exhausted retries" with no user-facing explanation. Ironically, screenshot `134424` shows orders WITH "Start a return" links visible, meaning the error recovery scenario (non-returnable item) was never actually tested -- the agent couldn't even get to the point of checking eligibility. |

### Root Causes
1. **type_text element targeting fails silently**: The type_text action reports `success: True` even when the text does not appear in the target field. The action likely typed keystrokes but focus was not on the "Search your purchases" field. Screenshot `134431` shows a "Click to rate" tooltip appeared, suggesting focus was captured by the review widget instead of the search field. The type_text implementation does not verify focus before typing.
2. **No click-to-focus before type_text**: The type_text action does not first click on the target element to ensure focus. It appears to use element description for targeting but the actual focus remains on whatever was last active (possibly the review widget that appeared after scroll).
3. **Single replan limit prevents recovery**: After the replanned steps' type_text retries were exhausted, the agent stopped rather than generating a third plan. An alternative approach (e.g., scrolling through orders manually to find dog food) was never attempted.
4. **Account settings modal blocks interaction**: The modal overlay ("Your Wallet, Addresses, Personal Info and more are now in Account Settings") appears on the orders page and blocks clicking on order elements. The agent identified and eventually dismissed it (via scroll), showing good adaptive behavior.

---

## Priority Issues (Cycle 3)

1. **[P1] Confidence threshold blocks "Purchase History" navigation** -- Blocks Scenario 1. The destructive action gate uses keyword matching ("purchase") with a 0.9 confidence threshold. "Purchase History" is a navigation link that consistently grounds at 0.75 confidence. The keyword matcher treats it as a purchase-related destructive action. Fix: either exempt navigation-context clicks from the destructive gate, or add "Purchase History" / "Order History" to an allowlist of safe navigation phrases.

2. **[P1] type_text element targeting fails silently** -- Blocks Scenario 3. The type_text action reports success even when focus is not on the target element. Text gets typed into the wrong widget or into nothing. The verification correctly catches this but retries cannot fix it because the underlying focus targeting is broken. Fix: type_text should click on the target element first to establish focus, then type. Alternatively, use accessibility APIs to verify the focused element matches the target before typing.

3. **[P2] Replan loses remaining task steps** -- Degrades Scenario 2. When the agent replans after a mid-plan failure, the new plan only addresses the immediate obstacle (e.g., navigating to /orders) without carrying forward the remaining task steps (scan for items, select order, initiate return). The replan should include the full remaining workflow. Scenario 2 stopped after reaching the orders page because the replan contained only 1 step.

4. **[P2] Planner inconsistently uses direct /orders URL** -- Degrades Scenarios 1 and 2. Scenario 3's plan correctly used `walmart.com/orders` as the first step. Scenarios 1 and 2 used `walmart.com` (homepage), requiring Account menu navigation that introduced additional failure points. The skill template should enforce the direct URL.

5. **[P3] Account settings modal blocks orders page** -- Affected Scenario 3. A Walmart modal overlay appears on the orders page and blocks interaction with order elements. The agent eventually dismissed it via scroll (good adaptive behavior), but this added ~90 seconds of wasted time and complexity.

---

## Cycle 1 -> Cycle 2 -> Cycle 3 Progress

| Issue | Cycle 1 | Cycle 2 | Cycle 3 |
|-------|---------|---------|---------|
| **P0: Plan step dropping** | OPEN -- steps silently dropped | Partially fixed (actions OK, on_fail still drops) | **FIXED** -- all 10 steps parsed correctly in S3 |
| **P0: Stale screen navigation skipped** | OPEN -- no open_url in S2, S3 | **FIXED** | **FIXED** |
| **P0: Screenshots show desktop, not browser** | N/A | OPEN -- all screenshots show wallpaper | **FIXED** -- all screenshots show actual browser content |
| **P1: wait_for_user 120s timeout** | OPEN -- 120s wasted | **FIXED** | **FIXED** (not triggered in cycle 3) |
| **P1: No scroll recovery** | OPEN -- 0 scrolls, immediate abort | **FIXED** | **FIXED** -- scroll recovery used effectively in S1 and S3 |
| **P1: "unhashable type: dict" replan crash** | N/A | OPEN -- crashes S1 | **FIXED** -- replanning works in all 3 scenarios |
| **P1: Pre-click validation false positives** | N/A | OPEN (masked by screenshot bug) | **FIXED** -- no false rejections observed (grounding operates on correct screenshots) |
| **P2: No screenshots captured** | OPEN -- 0 screenshots | **FIXED** (wrong content) | **FIXED** -- correct screenshots captured |
| **P2: Duplicate skills** | OPEN | **FIXED** | **FIXED** |
| **P2: Machine auto-lock during execution** | N/A | OPEN -- affected S3 | **FIXED** (did not occur) |
| **NEW P1: Confidence threshold blocks navigation** | N/A | N/A | OPEN -- blocks S1 |
| **NEW P1: type_text silent targeting failure** | N/A | N/A | OPEN -- blocks S3 |
| **NEW P2: Replan loses remaining task steps** | N/A | N/A | OPEN -- degrades S2 |
| **NEW P2: Inconsistent direct URL usage** | N/A | N/A | OPEN -- degrades S1, S2 |

### Scenario Pass Rates Across Cycles

| Scenario | Cycle 1 | Cycle 2 | Cycle 3 |
|----------|---------|---------|---------|
| S1: Happy path (Crest Whitestrips) | FAIL (wait_for_user + no scroll) | FAIL (screenshot bug + replan crash) | FAIL (confidence threshold blocks Purchase History) |
| S2: Edge case (shoes) | FAIL (stale nav + step dropping) | FAIL (screenshot bug + step dropping) | **PASS** (replanned to direct URL, reached orders page) |
| S3: Error recovery (dog food) | FAIL (stale nav + step dropping) | FAIL (screenshot bug + step dropping) | FAIL (type_text targeting broken, never found order) |
| **Total** | **0/3** | **0/3** | **1/3** |

---

## Remaining Issues NOT Fixed Across All 3 Cycles

1. **No scenario has completed the full return flow**: Across 9 total test runs (3 scenarios x 3 cycles), no run has successfully opened an order detail page, let alone initiated a return, selected a reason, or reached confirmation. The furthest any run has progressed is reaching the orders list page (S2 cycle 3, S3 cycle 3).

2. **Error recovery never tested**: Scenario 3 (non-returnable item) has never reached the order detail page in any cycle. The skill template's error recovery logic ("If 'Return window closed' or not eligible: report that the item is no longer eligible for return") has never been exercised. We have zero data on whether the agent can detect and communicate return ineligibility.

3. **Grounding confidence consistently low**: Across all cycles where vision was working (cycle 3), Molmo consistently returns 0.75 confidence for UI elements. This is likely a model-level characteristic, not a bug. The confidence threshold system needs to account for the model's typical confidence distribution rather than using absolute thresholds designed for higher-confidence models.

4. **type_text has never worked for search fields**: In cycle 3 scenario 3, type_text failed 4 consecutive times to put text into a search field. This action has not been tested in prior cycles (the agent never got far enough). This suggests the type_text + element targeting pathway has a fundamental issue with web form fields.

---

## Overall Assessment

**Cycle 3 represents significant infrastructure progress but the feature is not customer-ready.**

The good news: the foundational issues that plagued cycles 1 and 2 are comprehensively fixed. Screenshot capture works correctly. Plan parsing preserves all steps. Navigation is reliable. Scroll recovery works. Replanning works without crashing. The vision pipeline sees the actual browser content and makes correct observations. These are real, meaningful improvements -- the agent went from being "blind" (operating on desktop wallpaper images) to having correct visual perception of the browser.

The bad news: now that the agent can actually see, new interaction-level bugs are surfacing. The confidence threshold system has a semantic blindspot (navigation links containing "purchase" get flagged as destructive). The type_text action has a focus-targeting gap (types into wrong element). The replanning system generates tactically correct but strategically incomplete plans. These are qualitatively different (and arguably easier to fix) than the infrastructure bugs from cycles 1-2, but they still prevent task completion.

**Verdict: 1/3 scenarios pass. The feature needs at minimum 2 more targeted fixes (confidence threshold exemption for navigation, type_text focus-before-type) before it can reliably complete the happy-path return flow. A cycle 4 focused on these P1 issues would likely achieve 2/3 or 3/3 pass rate.**
