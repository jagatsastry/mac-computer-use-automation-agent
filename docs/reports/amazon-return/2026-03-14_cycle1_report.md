# Customer Testing Report: Amazon Return

## Summary
- Scenarios tested: 5
- Passed: 0
- Failed: 5
- Needs re-run: 0

All 5 scenarios failed. Scenarios 1, 2, and 4 share the same root cause: inability to successfully interact with the Amazon order search bar. Scenario 3 progressed the furthest but lacked error-recovery intelligence for digital/subscription orders. Scenario 5 failed on date-filter dropdown interaction.

---

## Scenario 1: Return a specific item by name (Primary)

### Prompt
"Return my listerine on Amazon"

### Expected Outcome
The agent opens Amazon order history, searches for "listerine," finds the matching order, navigates the return flow, selects a reason, and completes the return until a confirmation screen is visible.

### What Actually Happened
The agent matched the `return-amazon-order` skill and extracted `item=listerine`. It successfully opened `amazon.com/gp/your-account/order-history` (verified via window title "Your Orders - Google Chrome"). It then attempted to click the "Search all orders" search bar at coordinates (597, 246) which mapped to screen (882, 315). The vision model (Molmo) consistently found this element at the same coordinates with conf=0.75.

However, **the click was actually landing correctly** -- screenshot `110122_verify_step_click.png` shows the search bar IS focused with autocomplete suggestions ("Tylenol", "tylenol") visible. Screenshot `110149_verify_step_click.png` shows "tylenol" text in the search bar with a clear active state. Despite this, the **vision verifier denied** the condition "The search field becomes active with a text cursor" -- it could not detect the focus state.

After the initial plan's 3 retries (refine_element_query, jump_to_page_top_and_retry_click, keyboard_fallback_space), the agent replanned with a different element description ("Search all orders text input field"). The replan also failed identically -- the click landed correctly but verification kept denying focus. The later screenshots (`110156_verify_step_press_key.png`, `110259_verify_step_press_key.png`) show the search bar in a full-screen view, clearly empty and focused with cursor visible. Total duration: 155 seconds.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Amazon order history page loads | PASS | trace.md line 37: Tier 1 verify_pass "Window title 'Your Orders - Google Chrome' matches destination URL" |
| 2 | Agent searches for "listerine" and results appear | FAIL | readable_log.txt line 89: "Retries exhausted for step 2, escalating to replan" -- never typed "listerine" |
| 3 | Agent clicks into correct order and reaches return options | FAIL | Agent never progressed past search bar click |
| 4 | Return reason selected and return wizard proceeded | FAIL | Agent never reached return flow |
| 5 | Return confirmation screen visible | FAIL | Agent never reached return flow |

### Screenshots Analysis
- `110122_verify_step_click.png`: Amazon Orders page with search bar active, showing "Tylenol"/"tylenol" autocomplete suggestions. The search bar IS focused -- this is a false-negative verification.
- `110149_verify_step_click.png`: Search bar contains "tylenol" text with clear active state and dismiss button. Another false-negative -- the field is clearly active.
- `110156_verify_step_press_key.png`: Full-page view, search bar empty with cursor visible. Focus is present.
- `110259_verify_step_press_key.png`: Nearly identical to above -- search bar focused but verifier still denies.
- `debug/find_*_Search_all_orders_search_bar.jpg`: Crosshair at (597,246) correctly targeting the search bar area.
- `debug/find_*_Search_all_orders_text_input_field.jpg`: Same crosshair position (597,247) -- element description change did not change targeting.

### Root Cause
**False-negative vision verification of search bar focus state.** The click WAS landing on the correct element. The search bar WAS becoming focused (screenshots prove this with autocomplete appearing and cursor visible). But the Tier 2 vision verifier consistently denied "The search field becomes active with a text cursor." The verifier likely cannot distinguish a subtle CSS focus state (blue outline, cursor) on the Amazon search input from its unfocused state, especially at the downscaled screenshot resolution used for verification.

A secondary issue: the autocomplete from previous searches (showing "Tylenol"/"tylenol") may have confused the verifier into thinking the field was not freshly activated.

### Recommended Fix
1. **Relax verification for click-on-text-field steps**: When a step's action is `click` on an element containing "search", "text field", or "input", and the verify condition checks for "focused" or "cursor visible", consider using a **type-and-check** strategy instead: skip verifying the click focus and immediately proceed to `type_text` -- then verify that typed text appears in the field.
2. **Add accessibility-based verification for input focus**: Use Hammerspoon or AppleScript to query whether the frontmost text field is focused (e.g., `AXFocusedUIElement`). This would provide a reliable Tier 1 check.
3. **Consider skipping verification for low-risk intermediate steps**: Clicking a search bar is an intermediate step where failure is immediately caught by the next step (type_text will fail if the field isn't focused).

---

## Scenario 2: Vague prompt without specifying the item

### Prompt
"I need to return something I bought on Amazon last week"

### Expected Outcome
The agent opens Amazon order history and either browses recent orders and asks for clarification, or makes a reasonable attempt to identify recent orders from the past week. It should not blindly return an arbitrary item.

### What Actually Happened
The agent matched the `return-amazon-order` skill with `item="something I bought on Amazon last week"`. It correctly activated Safari, opened the Amazon order history URL (verified via Tier 1). It scrolled up to reveal search controls (verified via Tier 2 vision). Then it attempted to click "Search all orders search bar" at (598, 246) -> screen (883, 315).

This scenario failed identically to Scenario 1: the click landed correctly but verification denied the focus state. Screenshot `110503_verify_step_click.png` shows the search bar with "Tylenol"/"tylenol" autocomplete (same as Scenario 1 -- residual from earlier search). The trace at line 169 confirms: "The search field appears to have some text (tylenol) but no clear indication of an active cursor or field focus."

After replan, the agent tried "Search Orders text field" -- same result. Total duration: 159 seconds.

**Important behavioral observation**: The agent planned to type "last week" into the search bar -- this would NOT be useful on Amazon since the order search searches by product name, not date. The agent did not attempt to use the date filter dropdown or browse recent orders. Even if the search bar had worked, this approach was flawed.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Amazon order history page loads | PASS | trace.md line 55-57: "Tier 1: Window title 'Your Orders - Google Chrome' matches destination URL" |
| 2 | Agent does not blindly return arbitrary item | PASS (vacuously) | Agent never got far enough to return anything; but the plan had "click most recent order from search results" which could have been arbitrary |
| 3 | If agent proceeds, item is from last week | INSUFFICIENT_EVIDENCE | Agent never reached order selection |
| 4 | Agent does not complete return without user confirmation | INSUFFICIENT_EVIDENCE | Agent never reached return flow |

### Screenshots Analysis
- `110450_verify_step_scroll.png`: Full-page Amazon Orders with search bar unfocused, "Search all orders" placeholder visible. Orders visible: Caster wheels (Mar 11), Whole Foods (Mar 9).
- `110503_verify_step_click.png`: Same page at lower resolution showing search bar with "Tylenol"/"tylenol" autocomplete. Search bar appears active but verifier denied.
- `110534_verify_step_press_key.png`: Full-page view, search bar empty with cursor visible. Verifier still denied.
- `110635_verify_step_press_key.png`: Nearly identical to above.

### Root Cause
**Primary**: Same false-negative verification bug as Scenario 1 -- search bar focus not detected.
**Secondary**: Planning deficiency -- the agent planned to search Amazon orders for "last week" (a date phrase), but Amazon's order search only supports product-name queries. The correct approach would be to use the date filter dropdown or simply browse the default "past 3 months" view.

### Recommended Fix
1. Same search-bar verification fixes as Scenario 1.
2. **Improve planner handling of ambiguous/vague prompts**: When the item is vague or unspecified, the planner should generate an `observe` step to read recent orders and present them to the user rather than attempting a keyword search. The skill template should include guidance for ambiguous items.
3. **Skill template should differentiate search-by-name vs browse-by-date**: Add a conditional branch in the `return-amazon-order` skill for when the item name is too vague to search.

---

## Scenario 3: Item is not eligible for return (Kindle ebook)

### Prompt
"Return my Kindle ebook purchase on Amazon"

### Expected Outcome
The agent opens order history, searches for a Kindle ebook, discovers digital purchases are not eligible for return, and communicates this clearly to the user.

### What Actually Happened
This scenario progressed significantly further than the others. The agent:

1. **Clicked "Digital Orders" tab** -- correctly identified that Kindle ebooks would be under digital orders. Verified by Tier 2 vision (trace line 47). Screenshot `110752_verify_step_click.png` shows Digital Orders tab active with subscriptions (Amazon Kids+ $5.99, Amazon Photos $0.99).

2. **Scrolled down** to reveal more digital orders. Verified. Screenshot `110806_verify_step_scroll.png` shows additional subscriptions (Prime Video $2.99).

3. **Attempted to click search bar** -- initially failed (same false-negative verification), but **succeeded on retry with cmd+up pre-key** (trace line 151: "Vision confirms the clicked region satisfies: The search field is highlighted and ready for text input"). Screenshot `110838_verify_step_click.png` shows search bar with "tylenol" autocomplete.

4. **Typed "Kindle ebook"** -- verified successfully. Screenshot `110841_verify_step_type_text.png` shows "Kindle ebook" in the search field.

5. **Pressed Return** -- verification failed (vision denied search results appeared). The page may have been loading. Agent replanned to click "Search Orders" button instead.

6. **Clicked "Search Orders" button** -- succeeded. Screenshot `110919_verify_step_click.png` shows search results: "24 orders matching 'Kindle ebook'" but ALL results were subscription renewals (Amazon Kids+, Amazon Photos) -- NOT actual Kindle ebook purchases.

7. **Clicked "View order details"** on the first result (Amazon Kids+ subscription). Screenshot `110943_verify_step_click.png` shows Order Details for Amazon Kids+ $5.99, with only a "Manage your subscription" button -- NO return option.

8. **Scrolled down looking for return controls** -- 4 retries, all failed. Screenshots `110958_verify_step_scroll.png`, `111003_verify_step_scroll.png`, `111015_verify_step_scroll.png` show the agent scrolling past the order details into "Recommended based on your shopping trends" product ads. No return controls exist because this is a subscription, not a returnable item.

Total duration: 192.6 seconds. The agent NEVER recognized that digital orders/subscriptions cannot be returned and NEVER communicated this to the user.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Amazon order history loads and agent searches for ebook | PASS | trace.md lines 151-175: search field activated, "Kindle ebook" typed and verified, Search Orders button clicked, results page loaded |
| 2 | Agent identifies item is digital/not eligible for return | FAIL | Agent clicked into Amazon Kids+ subscription (not a Kindle ebook) and never recognized the absence of return controls as indicating ineligibility |
| 3 | Agent stops and provides clear message about ineligibility | FAIL | Agent got stuck scrolling through product recommendations looking for a non-existent return button. No user-facing message was generated |
| 4 | Agent does not enter infinite retry loop | FAIL | Agent retried scrolling 4 times with increasing delays (0.5s, 1.0s, 1.5s) looking for return controls that don't exist -- this is a bounded retry loop, but the behavior is still undesirable |

### Screenshots Analysis
- `110752_verify_step_click.png`: Digital Orders tab active. Shows subscriptions only (Amazon Kids+, Amazon Photos). No Kindle ebook purchases visible.
- `110806_verify_step_scroll.png`: More subscriptions (Prime Video). Still no Kindle ebook.
- `110819_verify_step_click.png`: Search bar with "tylenol" autocomplete (residual from prior scenarios).
- `110838_verify_step_click.png`: Search field focused with "tylenol" autocomplete visible.
- `110841_verify_step_type_text.png`: "Kindle ebook" typed in search field.
- `110845_verify_step_press_key.png`: Same view after Return pressed, page still showing subscription list (search not yet triggered).
- `110919_verify_step_click.png`: Search results: "24 orders matching 'Kindle ebook'" -- all Amazon Kids+ and Amazon Photos subscriptions. These are NOT Kindle ebooks.
- `110932_verify_step_scroll.png`: More search results -- all subscriptions.
- `110943_verify_step_click.png`: Order Details for Amazon Kids+ subscription. "Manage your subscription" button visible, no return option.
- `110958_verify_step_scroll.png`: Scrolled down on order details -- shows payment info, order summary, "Manage your subscription" button. No return controls.
- `111003_verify_step_scroll.png`: Scrolled further -- "Recommended based on your shopping trends" product ads. Completely past the order information.
- `111009_verify_step_scroll.png`: More product recommendations.
- `111015_verify_step_scroll.png`: Even more product recommendations and "Recommended based on your purchase history."

### Root Cause
**Three distinct failures**:
1. **No error-recovery logic for missing return controls**: The orchestrator has no concept of "the expected UI element does not exist because this type of item cannot be returned." When the plan says "look for return button" and it's not found, the only strategies are retry and replan -- there is no "recognize impossibility and inform user" pathway.
2. **Search result misinterpretation**: Amazon returned "24 orders matching 'Kindle ebook'" but all results were subscription renewals, not actual Kindle ebook purchases. The agent did not verify that the clicked order was actually a Kindle ebook -- it just clicked the first result.
3. **No early termination on absent expected UI**: When a subscription order details page shows only "Manage your subscription" and no return options, the agent should recognize this as a signal that the order type is non-returnable, not continue scrolling.

### Recommended Fix
1. **Add "negative verification" / "absent element detection"**: When the plan expects to find a "Return" button and it's absent after scrolling through the entire page, the agent should emit a user-facing message explaining the item may not be eligible for return.
2. **Add order-type awareness to the skill**: The `return-amazon-order` skill should include guidance like: "If the order details page shows 'Manage your subscription' instead of 'Return or replace items', this is a subscription/digital item and cannot be returned through the standard flow. Inform the user."
3. **Validate search results before clicking**: Before clicking "View order details" on a search result, verify the result matches the expected item type (ebook vs subscription).

---

## Scenario 4: Different phrasing -- casual refund request

### Prompt
"I want my money back for the headphones I got from Amazon"

### Expected Outcome
The agent interprets "money back" as a return/refund request, navigates to Amazon order history, searches for "headphones," and completes the return flow.

### What Actually Happened
The agent correctly matched the `return-amazon-order` skill with `item=headphones`, demonstrating that the skill's trigger keywords handle refund-oriented language well. It activated Safari, opened the Amazon order history URL (both verified via Tier 1).

Then it hit the same search bar interaction wall. First click at (597, 246) -> screen (882, 315). Screenshot `111212_verify_step_click.png` shows the search bar with autocomplete suggestions now including "Tylenol", "Kindle ebook", "tylenol" (accumulated from prior scenarios). The search bar WAS focused. But the verifier denied it.

After replanning, the agent tried "Search all orders text field" -- same coordinates, same failure pattern. Screenshots `111325_verify_step_click.png` and `111334_verify_step_press_key.png` confirm the search bar was focused (showing text cursor in the full-page view), but verification kept failing.

Total duration: 144.6 seconds. Agent correctly interpreted the prompt but could not progress past the search bar.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Agent interprets prompt as return/refund request | PASS | trace.md line 10: skill_match "return-amazon-order" with params {'item': 'headphones'} |
| 2 | Amazon order history loads and "headphones" is searched | FAIL | Order history loaded (trace.md line 57), but agent never typed "headphones" -- blocked by search bar verification |
| 3 | Correct headphones order located and return flow initiated | FAIL | Agent never progressed past search bar |
| 4 | Return wizard completes with confirmation visible | FAIL | Agent never reached return flow |
| 5 | Flow is same quality as direct "return" request | FAIL | Flow failed at the same point as Scenario 1 |

### Screenshots Analysis
- `111212_verify_step_click.png`: Amazon Orders page, search bar active with autocomplete ("Tylenol", "Kindle ebook", "tylenol"). Bar IS focused -- false-negative verification.
- `111325_verify_step_click.png`: Same page, search bar showing "tylenol" text and autocomplete with dismiss button. Clearly active.
- `111334_verify_step_press_key.png`: Full-page view, search bar focused with cursor. Verifier still denied.

### Root Cause
Identical to Scenario 1: false-negative vision verification of search bar focus state. The presence of autocomplete suggestions from prior searches may be an additional confound.

### Recommended Fix
Same as Scenario 1. Additionally: **clear browser autocomplete/history between test runs** to prevent residual suggestions from confusing the verifier.

---

## Scenario 5: Item exists but return window has closed

### Prompt
"Return the phone case I bought on Amazon six months ago"

### Expected Outcome
The agent navigates to order history, adjusts the date filter to include 6-month-old orders, searches for "phone case," finds the order, discovers the return window has passed, and informs the user.

### What Actually Happened
The agent correctly recognized it needed to expand the date range (default is "past 3 months") to find a 6-month-old order. It planned to click the order filter dropdown first.

1. **Clicked the date filter** ("97 orders placed in past 3 months") at (267, 322) -> screen (394, 412). Verified by Tier 2 vision. Screenshot `111450_verify_step_click.png` shows the dropdown open with options: "last 30 days", "past 3 months" (currently selected), "2026", "2025", "2024", "2023", "2022", "2021", "2020", "2019", "2018", "2017", "2016", "2015", "2014", "2013". **Note: there is no "past 6 months" option** -- Amazon only offers "past 3 months" or specific years.

2. **Tried to click "6 months or past 6 months option"** -- element not found (because it doesn't exist). Agent replanned.

3. **Replanned to click "past year or 2024 option"** after observing. Found element at (280, 404) and clicked. Debug image `find_1773339341946_past_year_or_2024_option_in_the_dropdown.jpg` shows the crosshair targeting the "2024" option in the dropdown. However, the click appears to have selected "2024" and the page navigated to `orders?timeFilter=year-2024`.

4. **Verification failed**: Screenshot `111554_verify_step_click.png` shows a **blank page loading** -- the Amazon orders page for 2024 is loading but content hasn't appeared yet. The verifier reported "The dropdown is not currently open/visible on the page" which is correct (it closed after selection) but the verification condition was "Dropdown closes and page shows orders from the selected longer time period" -- the page hadn't loaded yet.

5. Agent tried to refine and re-click "past year or 2024 option in the dropdown" but the dropdown was now closed, so element not found. Replan exhausted; agent stopped.

Total duration: 124 seconds.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Amazon order history loads and agent searches for "phone case" | FAIL | Order history loaded, date filter opened, but agent got stuck on date selection and never searched for "phone case" |
| 2 | Agent finds order from approximately six months ago | FAIL | Agent never completed date filter selection to see older orders |
| 3 | Agent recognizes return window has expired | FAIL | Agent never reached the order or its return controls |
| 4 | Agent communicates return window expiration to user | FAIL | No user-facing message generated |
| 5 | Agent does not repeatedly retry or click unrelated controls | PASS | Agent's retries were bounded and it stopped after replan exhaustion |

### Screenshots Analysis
- `111450_verify_step_click.png`: Date filter dropdown open, showing all available time period options. No "past 6 months" option exists. The "2025" option would have been the correct choice for a 6-month-old order (6 months before March 2026 = September 2025).
- `111554_verify_step_click.png`: Blank loading page at `orders?timeFilter=year-2024`. The page hadn't rendered yet when verification ran.
- `debug/find_*_Order_filter_showing_97_orders_placed_in_past_3_months.jpg`: Crosshair correctly targeting the "past 3 months" dropdown.
- `debug/find_*_past_year_or_2024_option_in_the_dropdown.jpg`: Crosshair targeting the "2024" option in the dropdown. This was the wrong year -- "2025" would have been correct, since September 2025 is ~6 months before March 2026.

### Root Cause
**Three failures**:
1. **Amazon's date filter doesn't have a "past 6 months" option**: The planner assumed this option existed. Amazon only offers "last 30 days", "past 3 months", or specific calendar years.
2. **Wrong year selected on replan**: The agent selected "2024" when it should have selected "2025" (6 months before March 2026 is September 2025, which falls in year 2025).
3. **Verification timing**: After clicking the year option, the verification ran before the page finished loading, causing a false-negative. No wait-for-page-load logic exists.

### Recommended Fix
1. **Teach the skill about Amazon's actual date filter options**: The `return-amazon-order` skill should document that Amazon only offers "last 30 days", "past 3 months", or specific years -- never "past 6 months" or custom ranges. For orders older than 3 months, select the appropriate year.
2. **Add date reasoning to the planner**: Given "six months ago" from March 2026, the planner should calculate that September 2025 falls in year 2025 and select "2025" from the dropdown.
3. **Add page-load wait after dropdown selection**: After selecting a date filter option that triggers a page reload, wait for the page content to appear (e.g., wait for an order card element or the "X orders placed in..." text) before verifying.

---

## Priority Issues

1. **[P0] False-negative vision verification of text field focus** -- blocks 4/5 scenarios (1, 2, 4 and first attempt of 3). The Tier 2 vision verifier cannot reliably detect whether an HTML text input field is focused. This is the single most impactful bug: the agent clicks correctly and the field IS focused, but verification rejects it, triggering wasteful retry/replan cycles that eventually exhaust all attempts. **Fix: bypass focus verification for text fields and instead verify by attempting to type and checking if text appeared.**

2. **[P0] No "element absent" / negative-result awareness** -- blocks 2/5 scenarios (3, 5). The orchestrator has no mechanism to recognize that a required UI element (return button, specific dropdown option) does not exist and never will. It can only retry and replan, never conclude "this action is impossible given the current page state." **Fix: add an `on_absent` handler that lets the planner express what to do when an expected element does not exist (e.g., inform user, try alternative path).**

3. **[P1] Residual browser state (autocomplete) confuses verification** -- degrades 4/5 scenarios. The search bar autocomplete persists across scenarios (accumulating "Tylenol", "Kindle ebook", "tylenol"), which may confuse the vision verifier and is a confound in verification screenshots. **Fix: clear browser data between test runs, or use incognito mode.**

4. **[P1] Planner does not reason about Amazon's actual UI** -- degrades 2/5 scenarios (2, 5). The planner assumed Amazon has a "past 6 months" date filter (Scenario 5) and that searching "last week" would work for date filtering (Scenario 2). **Fix: improve the `return-amazon-order` skill template with accurate documentation of Amazon's order history UI controls.**

5. **[P1] No page-load wait after navigation** -- degrades 1/5 scenarios (5). After clicking a dropdown option that triggers a page reload, verification runs before content appears. **Fix: add a wait-for-content strategy after actions that trigger page navigation.**

6. **[P2] Agent lacks ability to communicate inability to user** -- degrades 2/5 scenarios (3, 5). When the agent encounters a situation where the return cannot be completed (digital item, expired window), there is no mechanism to stop the automation and display a message to the user. **Fix: add a `tell_user` action type that allows the agent to surface information to the user and optionally halt execution.**

## Instrumentation Gaps

1. **No screenshot captured at the moment of search bar click**: The verification screenshots are taken AFTER the click, but we need a screenshot of the exact cursor position at click time for debugging coordinate accuracy. The debug `find_*.jpg` images show the planned click position but not the actual post-click state.

2. **No DOM/accessibility state logged**: The agent logs vision results but not the accessibility tree or DOM state. Adding the focused element's accessibility role and label to the verify event would make it trivial to distinguish "click landed but verification failed" from "click missed the target."

3. **No page-load timing logged**: There is no event for when a page finishes loading after navigation. This makes it impossible to distinguish "verification ran before page loaded" from "verification ran on wrong content."

4. **No user-message event type**: When the agent should communicate to the user (e.g., "this item cannot be returned"), there is no event type to capture this intent. The `skill_expand` event captures learned observations, but these are not surfaced to the user.

5. **Autocomplete/browser state not captured**: The search bar autocomplete suggestions indicate residual browser state from prior runs. Test harness should log or clear browser state between scenarios.
