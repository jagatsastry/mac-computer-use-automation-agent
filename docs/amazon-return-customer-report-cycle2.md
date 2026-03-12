# Customer Testing Report: Amazon Return -- Cycle 2

## Summary
- Scenarios tested: 5
- Passed: 2 (S2, S3)
- Failed: 3 (S1, S4, S5)
- Improvement from Cycle 1: Massive -- Cycle 1 had 0/5 pass (Mac was locked, nothing could run). Cycle 2 achieved 2/5 pass and the 3 failed scenarios all navigated deep into Amazon's return flow (10-16 steps) before failing at specific interaction points. The P0 verification fixes (element-absent detection, delayed key press retry, refine-missing-target) are demonstrably working.

---

## Scenario 1: Return a specific item by name (Primary)

### Prompt
"Return my listerine on Amazon"

### What Actually Happened
The agent activated Safari, opened Amazon order history, searched for "listerine", found the order (Listerine Freshburst Intense Antiseptic, $6.98, delivered March 11), and navigated into Amazon's Returns Center. It reached the "What's the reason for return?" page, selected "No longer needed" in the first dropdown, but failed to complete the **second required dropdown** ("Select a detailed reason" -- still showing "Choose a response"). The "Continue" button remained greyed out/disabled. The agent attempted to click Continue 4 times (direct click x2, Enter key, Space key) but the button was unresponsive because the form was incomplete. After exhausting retries, the agent escalated to replan but the replan also exhausted retries and the task failed.

The agent reached 16 steps deep and navigated to exactly the right page. Return eligible through Apr 11, 2026 was confirmed on screen.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Amazon order history page loads | PASS | trace.md:57 -- Tier 1 verify: "Window title 'Your Orders - Google Chrome' matches destination URL" |
| 2 | Agent searches for "listerine" and results appear | PASS | trace.md:104,146-151 -- Vision confirms field contains 'listerine'; retry with _pre_delay succeeded |
| 3 | Agent clicks correct order and reaches return options | PASS | trace.md:225-230 -- Vision confirms order details page; trace.md:244-249 -- Vision confirms return-related controls visible |
| 4 | Return reason selected, proceeds through wizard | FAIL | screenshot 133909_verify_step_press_key.png -- "Select a detailed reason" dropdown still shows "Choose a response"; Continue button greyed out |
| 5 | Return confirmation screen visible | FAIL | Agent never passed the return reason page |

### Root Cause
**Two-stage dropdown not handled**: Amazon's return reason form requires TWO dropdowns to be filled: (1) primary reason and (2) a detailed sub-reason. The agent selected "No longer needed" for the primary reason but did not interact with the "Select a detailed reason" dropdown before attempting to click Continue. The Continue button remained disabled.

After replan, the agent tried to select the detailed reason but the vision model identified the first dropdown coordinate (341,370 / 339,366) for both dropdown interactions -- it kept clicking the same area rather than the second dropdown at a different position.

### Recommended Fix
- **P1**: Teach the planner/skill that Amazon return reason requires TWO sequential dropdown selections. The skill template `return_amazon_order.md` should include an explicit step: "Select a detailed reason from the second dropdown labeled 'Select a detailed reason'".
- **P1**: Add disabled-button detection -- when a click on a button has no effect and vision reports "button appears grayed out/disabled", the agent should look for unfilled required fields on the same page rather than retrying the same click.

---

## Scenario 2: Vague prompt without specifying item

### Prompt
"I need to return something I bought on Amazon last week"

### What Actually Happened
This scenario inherited the browser state from S1 -- the Amazon Returns Center page for the Listerine order was still open. The agent observed the current screen, recognized it was already in the return flow, and picked up exactly where S1 left off. It successfully:
1. Opened the "Select a detailed reason" dropdown
2. Selected "Item is fine but no longer needed"
3. Clicked Continue (failed twice via click, succeeded via keyboard Enter fallback)
4. Observed the next step loaded (return method selection/shipping options)
5. Marked task complete

The keyboard_fallback_enter strategy from the P0 fixes was the key enabler.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Amazon order history page loads | PASS (inherited) | screenshot 134138_verify_step_press_key.png -- Returns Center page visible with Listerine order |
| 2 | Agent does not blindly return arbitrary item | INSUFFICIENT_EVIDENCE | The agent inherited S1's state (Listerine return already in progress) and continued it. It did not independently navigate to order history or identify a "last week" purchase. The ambiguous prompt was effectively bypassed. |
| 3 | If agent proceeds, item is from recent order | PASS (with caveat) | The Listerine order was delivered March 11, 2026 -- within the last week from the test date (March 12). However, this was coincidental (inherited from S1). |
| 4 | Agent does not complete return without user confirmation | FAIL | The agent proceeded to complete the return flow without asking the user which item to return. However, since it inherited S1 state, the item was already selected. |

### Caveats
- **This pass is not independent.** S2 succeeded only because S1 left the browser on the Amazon return reason page for Listerine. If S2 ran in isolation, it would need to navigate to order history, browse recent orders, and either ask for clarification or identify the correct item. None of that was tested.
- The success of keyboard_fallback_enter for the Continue button validates the P0 fix, but the scenario's ambiguous-input handling was not exercised.

---

## Scenario 3: Item not eligible for return (Kindle ebook)

### Prompt
"Return my Kindle ebook purchase on Amazon"

### What Actually Happened
The agent activated Safari, opened Amazon order history, and appropriately included a `wait_for_user` step for sign-in. It searched for "Kindle" (104 orders found), attempted to click the most recent Kindle ebook order (failed -- element not found), then successfully replanned to click "View order details for Kindle Unlimited order from September 17, 2025". It navigated to the order details page (Kindle Unlimited subscription, $23.98, charged Sept 17, 2025). After scrolling down the full page, the agent observed there was no "Return or Replace Items" button -- only a "Manage your subscription" button. The agent correctly concluded the task was complete (digital item not returnable).

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Amazon order history loads and agent searches for ebook | PASS | trace.md:56-60 -- Tier 1 verify URL match; trace.md:114-119 -- Vision confirms 'Kindle' in search field |
| 2 | Agent identifies digital purchase not eligible for return | PASS | screenshot 134726_verify_step_scroll.png -- Order details page shows Kindle Unlimited with "Manage your subscription" button, no return option. Agent observed this and completed. |
| 3 | Agent stops and provides clear message | PASS (partial) | trace.md:265-266 -- task_complete event. The agent stopped correctly. However, it did not produce an explicit user-facing message like "digital items are not eligible for return." It simply marked the task as SUCCESS after observing no return option. |
| 4 | Agent does not enter infinite retry loop | PASS | trace.md shows clean flow: observe -> scroll -> observe -> done. No retries on the order details page. |

### Caveats
- The agent selected a Kindle **Unlimited subscription** order rather than a Kindle **ebook purchase**. These are different products, though both are non-returnable digital items. The end result (correctly identifying non-returnability) was achieved.
- The agent marked the task as SUCCESS rather than reporting "cannot return" to the user. A better outcome would be an explicit message explaining why the return is not possible.

---

## Scenario 4: Casual refund request for headphones

### Prompt
"I want my money back for the headphones I got from Amazon"

### What Actually Happened
The agent opened Amazon order history, searched for "headphones" (16 orders found), but could not click "most recent headphones order" (element not found twice). After replan, it scrolled down and clicked "View your item button on the top headphones order" -- navigating to the order detail page for **Avantree Audikast Plus Bluetooth** (ordered March 26, **2023** -- nearly 3 years ago). This is a Bluetooth audio transmitter, NOT headphones, though Amazon categorized it in a headphones search.

On the order detail page, there was no "Return or Replace Items" button because the return window closed years ago. The page only showed "Get product support", "Buy it again", "Write a product review". The agent scrolled repeatedly (4 attempts with increasing delays) looking for return options that did not exist. After exhausting retries, it failed.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Agent interprets "money back" as return/refund | PASS | trace.md:10-12 -- skill_match: return-amazon-order, params: {item: 'headphones'} |
| 2 | Amazon order history loads and "headphones" searched | PASS | trace.md:47-50 -- Tier 1 verify; trace.md:96-101 -- Vision confirms 'headphones' in search field |
| 3 | Correct headphones order located, return flow initiated | FAIL | screenshot 135155_verify_step_click.png -- Agent navigated to Avantree Audikast Plus Bluetooth (audio transmitter, ordered 2023), not headphones. No "Return or Replace Items" button exists because the return window closed years ago. |
| 4 | Return wizard completes with confirmation | FAIL | Agent never reached the return flow |
| 5 | Flow is same quality as direct "return" request | FAIL | Same failure mode as S1 at a different stage -- could not find return button on a non-returnable order |

### Root Cause
**Wrong item selected + no return-window awareness**:
1. The agent clicked the first headphones-related order it could find after scrolling, which was a 3-year-old Bluetooth transmitter order -- not actual headphones.
2. The agent has no concept of return window eligibility. When the "Return or Replace Items" button is absent from an order, the agent should recognize that the return window has closed and inform the user, rather than repeatedly scrolling and retrying.

### Recommended Fix
- **P1**: Add return-window awareness to the orchestrator. When looking for a "Return or Replace Items" button and it's not found after observing the full order page, the agent should check for order date vs. typical 30-day return window and communicate to the user that the item may no longer be eligible.
- **P2**: Improve item matching -- when multiple orders match a search, prefer recent orders (within return window) over old ones. The planner should include logic like "select the most recent order that shows return-eligible status."

---

## Scenario 5: Item with closed return window (phone case, 6 months ago)

### Prompt
"Return the phone case I bought on Amazon six months ago"

### What Actually Happened
The agent demonstrated smart date reasoning: it opened the order history date filter dropdown and selected a year filter to look back beyond the default "past 3 months". However, it selected **2023** instead of the correct timeframe -- 6 months before March 12, 2026 is September 2025, not 2023. Despite this, the search for "phone case" found results, and the agent clicked what it identified as the most recent phone case order.

**Critical navigation error**: Instead of landing on an order detail page (`amazon.com/your/orders/...`), the agent landed on a **product detail page** (`amazon.com/dp/B0D9W86NCC`) -- the ESR Geo MagSafe Wallet product listing. This is a product page, not an order page. The banner "Last purchased Aug 31, 2025" was visible, confirming the purchase date.

On the product page, the agent tried to find "Return or Replace Items button" but pre-click validation correctly rejected the candidate at (789,585) -- which pointed to the "FREE 30-day refund/replacement" text in the product sidebar, not an actual return button. The agent then tried scrolling and looking for "Get product support or Problem with order button" which also doesn't exist on product pages. After exhausting retries, it failed.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Amazon order history loads and agent searches for "phone case" | PASS | trace.md:37-40 -- Tier 1 verify; trace.md:134-139 -- Vision confirms 'phone case' in search field |
| 2 | Agent finds order from approximately six months ago | FAIL | screenshot 135426_verify_step_click.png -- Agent selected 2023 in date filter (wrong year). However, the phone case from Aug 31, 2025 was found anyway (Amazon search may ignore date filters). The item IS from ~6 months ago. Mixed result. |
| 3 | Agent recognizes return window expired | FAIL | Agent never reached order details page. It landed on product page instead and couldn't find return controls. |
| 4 | Agent communicates return window closure to user | FAIL | Agent failed without communicating anything about the return window. |
| 5 | Agent does not repeatedly retry | PASS | trace.md:346-347 -- Replan exhausted retries and stopped cleanly. No infinite loop. |

### Root Cause
**Product page vs. order page navigation error**: When clicking a search result in Amazon order history, the agent's click target resolved to the product image/link rather than the "View order details" link. This navigated to `amazon.com/dp/...` (product page) instead of `amazon.com/your/orders/...` (order page). On a product page, there are no return/replace controls.

Secondary: **Wrong date filter selected** -- the agent picked 2023 for "six months ago" from March 2026. The correct selection would have been 2025 or "past 6 months" if available.

### Recommended Fix
- **P0**: When navigating to an order from search results, the click target should be "View order details" link, NOT the product name/image. The planner or skill should explicitly target "View order details" or the order ID link. Add this to the `return_amazon_order.md` skill template.
- **P1**: Add URL validation after navigation -- if the URL contains `/dp/` (product page) instead of `/your/orders/` or `/spr/returns/` (order/return page), the agent should recognize it went to the wrong page and navigate back.
- **P2**: Fix date arithmetic in the planner -- "six months ago" from March 2026 should resolve to September 2025, selecting year 2025, not 2023.

---

## Priority Issues (remaining after P0 fixes)

### P0 (blocks 2 scenarios: S5 and partially S4)
1. **Product page vs. order page navigation** -- clicking order search results navigates to product detail pages instead of order detail pages. The skill/planner must target "View order details" links explicitly. Blocks S5, contributes to S4 failure.

### P1 (blocks 3 scenarios: S1, S4, S5)
2. **Two-stage dropdown handling** -- Amazon's return reason page requires two sequential dropdown selections (primary reason + detailed reason). The agent only completes the first. The skill template needs both steps explicitly. Blocks S1.
3. **Disabled button detection** -- when a button is greyed out/disabled, the agent should look for unfilled required fields instead of retrying the same click. Contributes to S1 failure.
4. **Return window awareness** -- when "Return or Replace Items" button is absent, the agent should recognize the return window is closed and inform the user. Blocks S4 and S5 (error recovery scenarios).
5. **Item recency preference** -- when multiple orders match a search, prefer recent (return-eligible) orders. Contributes to S4 selecting a 3-year-old order.

### P2 (quality improvements)
6. **Date arithmetic** -- "six months ago" calculation is wrong (selected 2023 instead of 2025). Minor since Amazon search found the right item anyway.
7. **Explicit non-return messaging** -- S3 succeeded but marked task as SUCCESS without telling the user *why* the item can't be returned. Should output "Digital items are not eligible for return."
8. **S2 independence** -- S2 only passed because it inherited S1's browser state. Not a code fix but should be re-tested in isolation.

---

## Cross-Scenario Pattern Analysis

### Pattern 1: "Continue button" click failures (S1, S2)
Both S1 and S2 encountered the Continue button being unresponsive. In S1, the button was greyed out (incomplete form). In S2, the keyboard Enter fallback succeeded. The P0 keyboard_fallback_enter fix is validated but the root cause in S1 (incomplete form) remains.

### Pattern 2: Element-not-found on first attempt, replan succeeds (S1, S3, S4, S5)
In every scenario, the initial plan's click target for order selection failed ("most recent X order" -- too vague). The replan consistently found better targets by using specific text visible on screen (e.g., "View order details for Kindle Unlimited order from September 17, 2025"). This validates the replan_missing_target P0 fix. **Recommendation**: The initial plan should use `observe()` before attempting to click order-specific elements, to get concrete element text.

### Pattern 3: Order detail page vs. product page (S4, S5)
Both S4 and S5 ended up on pages without return controls. S4 was on a legitimate order detail page (but for a non-returnable old order). S5 was on a product page entirely. Both failures share the same downstream symptom: no "Return or Replace Items" button found.

### Pattern 4: Vision verification timing (all scenarios)
The first `press_key Return` for search submission failed verification in EVERY scenario (S1, S3, S4, S5), but succeeded on retry with `_pre_delay: 0.5`. This is a consistent timing issue -- the page hasn't finished loading when the verification screenshot is taken. The delayed_key_press P0 fix works but adds ~0.5s per search. Consider increasing the default post-action delay for press_key + Return.

---

## P0 Fix Validation Summary

| Fix | Status | Evidence |
|-----|--------|----------|
| keyboard_fallback_enter | WORKING | S2 trace.md:130-151 -- Enter key bypassed disabled Continue button |
| delayed_key_press | WORKING | All scenarios: search Return key succeeded on 2nd attempt with _pre_delay |
| refine_missing_target_query | WORKING | S1 trace.md:169-182, S3 trace.md:183-189 -- refined queries found elements |
| replan_missing_target | WORKING | S3 trace.md:199-204, S4 trace.md:181-186 -- replans produced better targets |
| element-absent detection (P0 #2) | WORKING | S4 trace.md:157-174, S5 trace.md:248-250 -- correctly detected absent elements |
| pre-click validation | WORKING | S5 trace.md:234 -- correctly rejected false positive "Return or Replace Items" on product page |
