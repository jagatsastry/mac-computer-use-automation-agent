# Customer Testing Report: Order Returns (Generalizability)

## Summary
- Scenarios tested: 5
- Passed: 0
- Failed: 4
- Needs re-run: 1 (Scenario 2 — false positive)

| # | Scenario | Result | Duration | Root Cause |
|---|----------|--------|----------|------------|
| 1 | Amazon return with specific item | FAIL | 79.6s | type_text fails to target search input; vision false-negative on verification |
| 2 | Walmart return with vague item | FALSE PASS | 15.4s | Agent only opened URL, never searched for item or initiated return |
| 3 | Target return for a gift | FAIL | 66.6s | Login gate blocks access; agent cannot authenticate |
| 4 | Ambiguous retailer | FAIL | 209.8s | Skill/planner confusion: matched Amazon skill, planned Target actions; login gate |
| 5 | Item not found in order history | FAIL | 261.8s | Same type_text bug as Scenario 1; never reached search or error-reporting phase |

---

## Scenario 1: Amazon return with specific item name

### Prompt
"I need to return the wireless headphones I bought on Amazon last week"

### Expected Outcome
The agent navigates to the Amazon orders page, locates the wireless headphones order, and reaches the return initiation screen.

### What Actually Happened
1. Agent activated Safari (PASS).
2. Opened `https://www.amazon.com/your-orders`, which redirected to Amazon order-history in **Chrome** (the default browser), not Safari. Tier 1 verified via URL match (PASS).
3. Agent ran `observe()` — took a screenshot of the Amazon orders page in Chrome.
4. Agent attempted `type_text('wireless headphones', element='Search all orders text input')`. The actuator reported success, but vision Tier 2 denied the text was present in the search field. This failed 3 times across two plans (original type, select-all-then-type, and replan), exhausting all retries.
5. The agent never reached the point of searching for or selecting the wireless headphones order, let alone initiating a return.

The replan correctly identified that Chrome (not Safari) was the active browser, but the type_text action still failed on the replan — the underlying issue is that the text is being typed without first focusing/clicking the search input field.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Safari opens to Amazon orders page | FAIL | `readable_log.txt` line 72-73: open_url opened Chrome (default browser), not Safari. Screenshot `091738_verify_step_type_text.png` confirms Chrome is the active browser (Chrome menu bar visible). |
| 2 | Correct order containing "wireless headphones" is identified | FAIL | Agent never successfully searched for the item. All type_text attempts were denied by vision verification (`trace.md` lines 80-85, 117-122, 140-145). |
| 3 | Return flow is initiated | FAIL | Agent exhausted retries at the search step. Never reached order selection or return initiation. |

### Screenshots Analysis
- **091738_verify_step_type_text.png**: Shows Amazon order-history page in Chrome. Visible orders: Finish dishwasher rinse ($12.48), Nespresso coffee pods ($39.90). The "Search all orders" input field is visible but empty — confirming vision was correct that typing did not land in the field. No wireless headphones visible on this page section.
- **091805_verify_step_type_text.png**: Same Amazon orders page scrolled down. Shows metal plates ($8.81), mounting tape ($19.72). Search field not visible in this viewport. Still no wireless headphones.
- **091815_verify_step_type_text.png**: Further down the orders page. Shows mouthwash ($6.54), dishwasher cleaner ($8.91), a planter ($80.92). No wireless headphones visible in any of the 3 screenshots' order listings.

### Root Cause
**type_text does not click/focus the search input before typing.** The actuator's `type_text` action types keystrokes into whatever element currently has focus. On the Amazon orders page, no input field has focus by default, so the keystrokes are lost or go to the wrong element. Vision correctly detects the search field is empty and denies verification.

### Recommended Fix
1. The planner should generate a `click` action on the search input field BEFORE `type_text`. The replan attempted this but the `click('Search all orders text input')` also failed because the element description did not match what Molmo could ground on the page.
2. Add a `click_and_type` composite action that first clicks the target element, then types — or modify the `type_text` action to accept an `element` param that triggers a click-to-focus before typing.
3. The skill template should include an explicit "click the search field" step before typing.

---

## Scenario 2: Walmart return with vague item description

### Prompt
"Return that Walmart order, the blue shirt"

### Expected Outcome
The agent opens the Walmart orders page, finds an order containing a blue shirt, and begins the return process.

### What Actually Happened
1. Skill router correctly matched `return-walmart-order` with params `{item: 'the blue shirt'}` (confidence 0.98, `stdout.txt` line 19).
2. Planner generated a **1-step plan**: just `open_url('https://www.walmart.com/orders')`.
3. The URL opened successfully. Tier 1 verified the URL matched.
4. The agent declared the task **completed successfully** after just 1 step (15.4 seconds).

The agent did NOT search for the blue shirt, did NOT click on any order, and did NOT start a return. It only opened the Walmart orders page URL and stopped.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Agent navigates directly to walmart.com/orders | PASS | `readable_log.txt` line 28-31: URL opened and verified via Tier 1. |
| 2 | Agent visually identifies order matching "blue shirt" | FAIL | `readable_log.txt` shows only 1 step in the plan — no observe, no click, no search. The plan never attempted to find the item (`trace.md` lines 16-27). |
| 3 | "Start a return" button is clicked | FAIL | The agent declared success after opening the URL. No return flow was initiated (`trace.md` line 46: `task_complete`). |

### Screenshots Analysis
No screenshots were captured (0 files in screenshots/ and debug/). This is consistent with the agent completing in a single open_url step that only required Tier 1 URL verification — vision was never invoked.

### Root Cause
**Planner generated a trivially incomplete plan.** The LLM (Gemini 2.5 Flash) produced only 1 step: open the URL. It failed to generate the subsequent steps for: observing the page, finding the blue shirt order, clicking on it, and initiating the return. The skill template presumably contains these steps, but the planner either truncated the plan or misunderstood the scope of the task.

This is a **false positive** — the agent reported SUCCESS but did not accomplish the goal.

### Recommended Fix
1. Add a **plan completeness validator** that checks whether the plan covers the full skill template workflow (e.g., if the skill says "find item, click return," a plan with only "open URL" should be rejected).
2. The `done()` action should require a verification condition that checks whether the actual goal was achieved, not just whether the last step succeeded.
3. Consider requiring a minimum number of steps when a skill template is matched — a 1-step plan for a multi-step return flow should trigger a replan.

---

## Scenario 3: Target return for a gift

### Prompt
"I want to return a baby blanket I got from Target, it was the wrong size"

### Expected Outcome
The agent opens the Target orders page, locates the baby blanket order, and navigates through the return flow including selecting a size-related reason.

### What Actually Happened
1. Skill router correctly matched `return-target-order` with params `{item: 'baby blanket'}` (confidence 0.98, `stdout.txt` line 19).
2. Planner generated a 6-step plan: open URL, observe, click order card for baby blanket, click "Return an item" button, select return reason, done.
3. `open_url('https://www.target.com/orders')` succeeded. URL verified via Tier 1 — but the actual URL was the **Target login page** (`https://www.target.com/login?client_id=ecom-web-1.0.0&...`). The Tier 1 verifier matched on the "target.com" token and passed it despite the redirect to login.
4. `observe()` took a screenshot of the Target login page.
5. `click` for the baby blanket order card failed: Molmo grounding returned no result (the login page has no order cards), Gemini fallback also found nothing. Element not found.
6. Agent declared task **infeasible** after the click failure — correctly identifying it cannot proceed.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Safari opens to target.com/orders | FAIL | `trace.md` line 40: Actual URL is `target.com/login?...`. The agent was redirected to the login page because the user was not authenticated. Tier 1 incorrectly passed this as a match. |
| 2 | Baby blanket order is found | FAIL | `readable_log.txt` line 54: "NOT FOUND: the order card or 'View order details' link associated with a 'baby blanket' or similar item". The login page has no orders to search. |
| 3 | Return flow reaches reason selection with size-related reason | FAIL | Agent never got past the login page. Task was declared infeasible (`trace.md` line 66). |

### Screenshots Analysis
No screenshots were captured (0 files). The only vision interaction was the observe step and the failed click grounding. No verification screenshots were saved because the click failed at the actuator level (element_not_found), not at the vision verification level.

### Root Cause
**Login gate blocks all progress.** Target.com redirects `/orders` to the login page when the user is not authenticated. The agent has no capability to authenticate (fill in username/password). The Tier 1 verifier was fooled because the redirected URL still contains "target.com" — it matched on domain tokens rather than requiring the actual orders page path.

Secondary issue: The "wrong size" reason from the prompt was correctly extracted by the planner into the plan (step 4 mentions selecting a return reason), but was never reached due to the login blocker.

### Recommended Fix
1. **Login detection**: After opening a URL that redirects, check if the resulting page is a login/sign-in page. If so, either use `wait_for_user` to ask the user to log in, or report that authentication is required.
2. **Tier 1 URL verification tightening**: The verifier should check for path matches, not just domain tokens. `target.com/login` should NOT pass verification for the condition "Target orders page is visible."
3. Skills should include a "handle login gate" step as a contingency.

---

## Scenario 4: Ambiguous retailer — user just says "return"

### Prompt
"Can you return the coffee maker I ordered? I think it was like $40"

### Expected Outcome
The agent either asks which retailer or defaults to Amazon and searches for a coffee maker around $40.

### What Actually Happened
1. Skill router matched `return-amazon-order` (confidence 0.9) as the top candidate. This is a reasonable default since no retailer was specified. However, the router also returned Target (0.75) and Walmart (0.75) as alternatives (`stdout.txt` line 19).
2. **Critical bug**: Despite matching the Amazon skill, the planner generated a plan that navigates to **Target.com**, not Amazon. Step 0 was `wait_for_user('Please log in to your Target account...')` and Step 1 was `open_url('https://www.target.com/orders')`.
3. The `wait_for_user` step waited 120 seconds for a screen change (login), then timed out.
4. After timeout, the agent opened `target.com/orders`, which redirected to Target's login page (same as Scenario 3).
5. Agent observed the Target login page, then tried to click "Return an item button" which was not found on the login page.
6. Task declared infeasible.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Agent does not crash or silently pick random retailer | FAIL | `readable_log.txt` lines 6-7: Matched Amazon skill, but `trace.md` lines 17-27 show the plan targets Target.com. The agent did not ask for clarification; it silently picked the wrong retailer despite the skill match saying Amazon. This is a planner/skill coherence failure. |
| 2 | Agent navigates to retailer's orders page and searches for "coffee maker" | FAIL | Agent navigated to Target login page, not Amazon orders. Never searched for "coffee maker" (`readable_log.txt` lines 48-57). |
| 3 | Price hint "$40" is used to disambiguate | FAIL | The price was never used. The plan mentions "around $40" in one click element description (`readable_log.txt` line 47: "Order for 'coffee maker' around $40"), but the agent never reached the orders page to use it. |

### Screenshots Analysis
No screenshots captured (0 files in both screenshots/ and debug/). The agent spent 120s on wait_for_user, then failed at element_not_found which does not trigger screenshot capture.

### Root Cause
**Skill-planner incoherence.** The skill router selected `return-amazon-order`, but the planner generated steps for Target.com. The planner likely saw the Target login page still visible in the browser (from Scenario 3 which ran immediately before) and adapted its plan to the current screen state rather than following the matched skill's instructions. This represents a fundamental disconnect between the skill routing decision and the planner's output.

Secondary: Same login gate issue as Scenario 3.

### Recommended Fix
1. **Skill-plan coherence check**: After the planner generates a plan, validate that the plan's URLs and actions align with the matched skill. If `return-amazon-order` is matched, the plan should not contain `target.com` URLs.
2. **Clarification flow for ambiguous input**: When the skill router returns multiple candidates with close confidence scores (0.9 vs 0.75 vs 0.75), and the prompt contains no explicit retailer name, the agent should ask the user which retailer to use.
3. The planner should be instructed to follow the skill template rather than adapting to whatever page happens to be open.

---

## Scenario 5: Item not found in order history

### Prompt
"Return the running shoes from my Amazon orders"

### Expected Outcome
The agent navigates to Amazon orders, searches for running shoes, and when none are found, reports failure clearly without clicking unrelated orders.

### What Actually Happened
1. Skill matched `return-amazon-order` with params `{item: 'running shoes'}`.
2. Agent opened `amazon.com/your-orders` successfully (verified via Tier 1 URL match in Chrome).
3. Agent ran `observe()`, then attempted `type_text('running shoes')` without specifying an element to click first.
4. The type_text reported success from the actuator, but vision Tier 2 denied the text was in the search field. This repeated 4 times with different strategies (normal type, select-all-then-type, two slow-type attempts).
5. Replan: Agent tried to `click('Search all orders text input')` first, but Molmo could not ground this element. Vision found no match after 60+ seconds of searching. Element not found.
6. Agent tried the refined query "Search all orders text input (visible on the same relevant card/section only)" — still not found after another 60 seconds.
7. Task exhausted retries and failed. Total duration: 261.8 seconds.

The agent never successfully searched for running shoes, so it never had the opportunity to detect that the item was absent and report failure gracefully.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Agent opens Amazon orders page and visually scans for "running shoes" | FAIL | Agent opened the page successfully (`trace.md` line 39-44), but never successfully typed into the search field or scanned orders visually. All type_text attempts failed verification (`readable_log.txt` lines 52-78). |
| 2 | Agent does NOT click on an unrelated order | PASS | The agent never clicked on any order — it got stuck at the search step. While not the intended way to pass, it did not click unrelated orders. Screenshots `092638-092904` confirm orders page showing caster wheels, Whole Foods orders — no running shoes — and the agent did not click any of them. |
| 3 | Agent reports failure clearly | FAIL | The agent's final state was "FAILED (exhausted retries)" — a generic infrastructure failure, not a clear "running shoes not found" message. The user would see the agent burned 4+ minutes retrying a type operation, not a meaningful item-not-found report (`readable_log.txt` lines 107-111). |

### Screenshots Analysis
All 5 screenshots show the Amazon "Your Orders" page in Chrome at `amazon.com/gp/css/order-history`:
- **092638_verify_step_type_text.png**: Full Amazon orders page visible. "Search all orders" input field is empty. First visible order: ASHGOOB Caster Wheels ($11.10, March 11). Below: Whole Foods Market pickup ($187.69, March 9). "97 orders placed in past 3 months" visible. No running shoes.
- **092645_verify_step_type_text.png**: Identical view, 10 seconds later. Search field still empty. Confirms type_text keystrokes are not landing in the field.
- **092706_verify_step_type_text.png**: Same view, 20 seconds later. After slow_type attempt. Search field still empty.
- **092728_verify_step_type_text.png**: Same view again. Another slow_type attempt failed. Clock shows 09:27.
- **092904_verify_step_type_text.png**: Same view after replan. The search field is still empty despite the replan's click attempt also failing. This confirms the grounding model (Molmo) cannot locate the search input on this page.

Key observation from screenshots: The "Search all orders" field is clearly visible in the UI (placeholder text "Search all orders" with a magnifying glass icon and a "Search Orders" button next to it). The grounding failure suggests Molmo cannot match the text description "Search all orders text input" to this specific UI element.

### Root Cause
**Same type_text focus bug as Scenario 1**, compounded by **grounding failure on the Amazon search input field**. Molmo (the vision grounding model) cannot locate the "Search all orders" text input element on Amazon's orders page, even though it is clearly visible in screenshots. This is likely because:
1. The search input uses a specific UI pattern (gray placeholder text inside a bordered field) that Molmo's training data may not cover well.
2. The element description "Search all orders text input" may not match what Molmo looks for on the page.

### Recommended Fix
1. Same as Scenario 1: implement `click_and_type` or ensure type_text focuses the target first.
2. For the Amazon orders search field specifically: try alternative grounding queries like "search box," "text field with placeholder 'Search all orders'," or use accessibility-based grounding which can identify input fields by their HTML role.
3. Add a skill-level fallback: if search fails, scroll through orders visually and match items by name, which avoids the search field entirely.

---

## Priority Issues

1. **[P0] type_text does not focus target input field** — Blocks Scenarios 1 and 5 (both Amazon). The actuator's `type_text` types into whatever has focus rather than the specified element. When the planner includes an `element` param in type_text, the actuator ignores it for focus purposes. This is the single most impactful bug.

2. **[P0] Planner generates trivially incomplete plans** — Caused false positive in Scenario 2. A multi-step return workflow was reduced to a single open_url step, and the agent declared success. There is no plan completeness check.

3. **[P0] Login gate handling is absent** — Blocks Scenarios 3 and 4 (Target), would block Walmart if not already logged in. Retailers redirect `/orders` to login pages; the agent has no strategy for this. Tier 1 URL verification incorrectly passes login redirects.

4. **[P1] Skill-planner incoherence** — In Scenario 4, the matched skill (Amazon) did not match the generated plan (Target). The planner is influenced by the current screen state more than the skill template.

5. **[P1] Grounding model cannot locate Amazon search input** — Molmo fails to ground "Search all orders text input" on Amazon's orders page despite it being clearly visible. This blocks the search-based order-finding approach for Amazon scenarios.

6. **[P1] Tier 1 URL verification is too permissive** — Matching on domain tokens alone means `target.com/login` passes as "Target orders page." Should require path-level matching or at minimum reject known login/auth paths.

7. **[P2] No clarification flow for ambiguous retailer** — When no retailer is mentioned, the agent should ask rather than guess. The router picks the highest-confidence skill (Amazon at 0.9), but 0.9 is not definitive and the planner then contradicts it anyway.

8. **[P2] Error reporting is infrastructure-flavored, not user-friendly** — "Exhausted retries" is an internal failure mode. Users need messages like "Could not find running shoes in your Amazon orders."

## Generalizability Assessment

**Overall: 0/5 scenarios achieved their customer goal. The agent cannot reliably complete order returns on any retailer.**

### What works across retailers
- **Skill routing is accurate.** The LLM router (Gemini 2.5 Flash) correctly matched Amazon, Walmart, and Target skills with high confidence (0.9-0.98) in all 5 scenarios, including the ambiguous Scenario 4 where it reasonably defaulted to Amazon. Parameter extraction (item names) was also correct in all cases.
- **Navigation to retailer URLs works.** `open_url` successfully opened all three retailers' order pages (Amazon, Walmart, Target). Tier 1 URL verification correctly confirmed navigation in the non-login cases.

### What fails across retailers
- **Interaction with page elements fails universally.** Whether it is typing into search fields (Amazon) or clicking order cards/buttons (Target), the agent cannot reliably interact with retailer-specific UI elements. The grounding model (Molmo) and the text-based actuator both struggle with real-world e-commerce page layouts.
- **Login gates are completely unhandled.** Any retailer that requires authentication (Target confirmed; Walmart and Amazon may also require it in some states) creates an impassable barrier.
- **Plans are not validated against skill templates.** The planner can produce plans that contradict the matched skill or are trivially incomplete, with no guardrail.

### Retailer-specific patterns
| Retailer | Navigation | Authentication | Element Interaction | Search |
|----------|-----------|---------------|-------------------|--------|
| Amazon | Works (Chrome, not Safari) | Already logged in during test | type_text fails; grounding cannot find search input | Never reached |
| Walmart | Works | Was not tested (agent stopped too early) | Never attempted | Never attempted |
| Target | Redirects to login | Blocks all progress | Never reached (login gate) | Never reached |

### Key insight
The agent's architecture has a working "outer loop" (skill routing, URL navigation, high-level planning) but the "inner loop" (element grounding, text input, click targeting, login handling) is not production-ready. The gap between "navigate to the right page" and "interact with elements on that page" is where every scenario breaks down. Until the type_text focus issue, grounding accuracy on real e-commerce pages, and login gate handling are fixed, the return automation workflow will not be viable on any retailer.
