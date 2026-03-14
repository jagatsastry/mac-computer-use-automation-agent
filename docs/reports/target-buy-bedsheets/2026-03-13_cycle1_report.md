# Customer Testing Report: Target Buy Bed Sheets

## Summary
- Scenarios tested: 3 (of 5 planned; scenarios 4-5 blocked by same issues)
- Passed: 0
- Failed: 3
- Pass rate: **0/3 (0%)**

## Test Environment
- Date: 2026-03-13
- Vision server: Molmo v1 (port 8091) + Molmo2 (port 8092)
- LLM: Gemini 2.5 Flash (planner + skill router)
- Browser: Google Chrome (scenario 1 replan, scenario 3), Safari (scenario 2)
- Run IDs: 260313_153328 (S1), 260313_153448 (S2), 260313_153637 (S3)

---

## Scenario 1: Happy Path — Buy Top Bed Sheet Under $50

### Prompt
"buy the top bed sheet cheaper than $50 on target"

### Expected Outcome
Agent navigates to Target.com, searches for bed sheets, filters by price (under $50), selects a top-rated option, and adds it to cart.

### What Actually Happened
1. Skill router matched `amazon-search` instead of finding no match (no Target shopping skill exists)
2. Planner generated a 7-step plan for Target.com (search, type, click, filter, etc.)
3. Initial `open_url` to target.com failed 4 times ("no visible effect — screenshot unchanged") — the page was already on target.com from a prior session, so the diff-based verification saw no change
4. After replan: agent activated Chrome, opened target.com successfully
5. `type_text` into Target's search bar failed — the first two attempts were rejected by vision verification ("Vision denies: search bar contains 'bed sheet'")
6. Third attempt with `_slow_type` succeeded — vision confirmed text in search bar. **But screenshot shows the text was actually typed into Amazon.com's search bar** (Amazon tab was active/focused in Chrome)
7. `click` on search button succeeded — but search results were on Amazon, not Target
8. `scroll` down failed verification 4 times — vision model repeatedly denied that scrolling occurred even though it did
9. Agent exhausted retries and declared FAILED

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Opens browser and navigates to target.com | FAIL | Target.com loaded but typing went to Amazon tab — screenshot `153520_verify_step_type_text.png` shows Amazon |
| 2 | Searches for "bed sheets" | FAIL | Text typed into Amazon's search bar, not Target's — `readable_log.txt:107` |
| 3 | Applies price filter (max $50) | FAIL | Never reached — stuck at search step |
| 4 | Clicks on a top-rated bed sheet product | FAIL | Never reached |
| 5 | Clicks "Add to Cart" | FAIL | Never reached |
| 6 | Does not proceed past checkout without confirmation | N/A | Never reached |

### Screenshots Analysis
- `153452_step_01_post_open_url.png`: Target.com homepage loaded correctly in Chrome — search bar visible with "What can we help you find?"
- `153520_verify_step_type_text.png`: **Shows Amazon.com** with "bed sheet" search results sorted by price — text was typed into the wrong site
- `153645_step_03_post_click.png`: **Shows Amazon.com** search results — search button click happened on Amazon
- `153725_verify_step_scroll.png`: Shows Target.com with "queen-size bed sheet set" search — from a different state (previous run's effect), scroll verification fails

### Root Causes
1. **type_text focus issue**: `type_text` uses AppleScript `keystroke` to type into whatever element has keyboard focus. It doesn't click on the target element first. Chrome had multiple tabs; the Amazon tab had focus.
2. **open_url diff verification false negative**: When the page is already showing the target URL, `screencapture` sees "no visible effect" because screenshots are identical. The step fails even though the page is already correct.
3. **scroll verification false positive denial**: Vision model consistently denies that scrolling occurred. The verification condition "visible content has shifted downwards" is too abstract for the vision model to confirm reliably.

---

## Scenario 2: Skill Adaptation — Same Prompt, Second Run

### Prompt
"buy the top bed sheet cheaper than $50 on target" (same as scenario 1)

### Expected Outcome
Agent performs faster using learned patterns from scenario 1. A new skill should exist in the library.

### What Actually Happened
1. Skill router matched `amazon-search` (confidence below threshold, fell to keyword fallback)
2. Planner generated a **3-step plan to go to Amazon.com** — completely wrong site!
   - Step 0: activate_app Safari
   - Step 1: open_url `https://www.amazon.com/s?k=bed sheet&s=price-asc-rank`
   - Step 2: done
3. All steps passed — agent "successfully" opened Amazon search results for "bed sheet"
4. Agent declared SUCCESS but it went to the **wrong website** entirely

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | New skill exists for Target shopping | FAIL | No new skill file created — `ls skills/library/` shows only `return_target_order.md`, no shopping skill |
| 2 | Fewer steps/replans than Scenario 1 | N/A | Fewer steps but because it went to Amazon instead of Target |
| 3 | Reuses navigation patterns from Scenario 1 | FAIL | Used Amazon URL pattern instead of Target URL pattern |
| 4 | Faster execution time | N/A | 25.8s vs 237.6s but wrong site |

### Root Cause
1. **Skill router matched `amazon-search`**: The LLM-based router matched the "buy" + "bed sheet" keywords to the `amazon-search` skill, ignoring "on target" in the prompt. The planner then used the amazon-search skill template which hardcodes Amazon URLs.
2. **No skill adaptation**: No new skill was created from Scenario 1's execution trace. The Skill Librarian did not promote any observations. Zero learning occurred.
3. **Verification doesn't check site domain**: The Tier 1 verification confirmed "Browser URL contains destination tokens" — it matched `amazon.com/s?k=bed+sheet` as valid because the URL tokens matched, without checking if the domain was Target.

---

## Scenario 3: Edge Case — Cheapest Queen-Size Bed Sheet Set

### Prompt
"buy the cheapest queen-size bed sheet set on Target"

### Expected Outcome
Agent navigates to Target, searches for queen-size sets, sorts by price, selects cheapest.

### What Actually Happened
1. Skill router matched `amazon-search` again
2. Planner generated a **3-step plan** — but this time with a Target URL:
   - Step 0: activate_app Google Chrome
   - Step 1: open_url `https://www.target.com/s?searchTerm=queen-size+bed+sheet+set&sortBy=PriceLH`
   - Step 2: done
3. Chrome opened Target.com search results — URL loaded correctly (screenshot `153725_verify_step_scroll.png` shows Target with "queen-size bed sheet set" results and bed sheet products visible with prices and "Add to cart" buttons)
4. Agent immediately declared SUCCESS after opening the URL
5. **Agent did NOT**: select a product, add to cart, or verify price was cheapest

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Searches for "queen size bed sheet set" | PASS | URL `searchTerm=queen-size+bed+sheet+set` — `readable_log.txt:51` |
| 2 | Filters or sorts by price (lowest first) | PASS | URL `sortBy=PriceLH` — `readable_log.txt:51` |
| 3 | Selected product is queen-size sheet set | FAIL | No product selected — plan ends with `done` after opening URL |
| 4 | Adds cheapest matching item to cart | FAIL | No "Add to cart" click — plan was only 3 steps |
| 5 | If Target shopping skill exists, agent uses it | FAIL | No Target shopping skill existed; matched `amazon-search` |

### Root Cause
1. **Plans are too shallow**: The planner generated only open_url → done, skipping the entire product selection and add-to-cart workflow. The planner treated "show search results" as task completion.
2. **amazon-search skill template**: The matched skill template only covers searching (opening a search URL), not the full buy workflow (select product → add to cart → etc.)
3. **No goal decomposition**: The planner doesn't break "buy X on Target" into sub-goals (search → filter → select → add to cart → verify).

---

## Scenarios 4-5: Not Executed

Scenarios 4 (error recovery — checkout login wall) and 5 (infeasibility — impossible criteria) were not executed because the same P0 issues block them:
- Skill router would match `amazon-search` instead of a Target skill
- Plans would be too shallow to reach checkout or detect infeasibility
- type_text focus and scroll verification issues would block interaction

---

## Priority Issues

### P0 — Blocks ALL scenarios

1. **[P0-1] Skill router incorrectly matches `amazon-search` for Target shopping prompts**
   - All 3 scenarios matched `amazon-search` skill despite prompt explicitly saying "on target"
   - Scenario 2 went entirely to Amazon.com as a result
   - Root cause: LLM router parses "buy" + "bed sheet" → matches amazon-search triggers; ignores "on target"
   - Fix: Skill router needs to respect the site/store specified in the prompt. If the user says "on target", amazon-search must NOT match. Consider adding a negative-match or site-awareness to the skill matching logic.

2. **[P0-2] No Target shopping skill exists — agent cannot buy on Target**
   - Only `return_target_order.md` exists (returns, not shopping)
   - Need a new `buy_on_target.md` skill with the full workflow: navigate → search → filter price → select product → add to cart
   - This skill should be generic enough for different products and price filters

3. **[P0-3] Plans are too shallow — planner stops at "show search results"**
   - Scenarios 2 and 3 generated 3-step plans (activate → open_url → done)
   - The planner treats opening a search URL as task completion
   - Fix: The planner needs to understand that "buy X" requires the full workflow through add-to-cart, not just showing search results

### P1 — Blocks most scenarios

4. **[P1-1] type_text doesn't focus the target element before typing**
   - `type_text` sends AppleScript keystrokes to whatever element has focus
   - When Chrome has multiple tabs, keystrokes go to the wrong tab's search bar
   - Fix: Before typing, click on the target element (using vision to find it) to ensure focus is on the correct input field

5. **[P1-2] open_url "no visible effect" false negative when page already loaded**
   - When navigating to a URL the browser already shows, the screenshot diff sees no change and declares failure
   - Fix: If the URL verification (Tier 1) passes, skip the screenshot-diff check. The page is already correct.

6. **[P1-3] Scroll verification always fails — vision model can't confirm scroll happened**
   - Vision model denies "visible content shifted downwards" after every scroll attempt
   - 4 consecutive scroll attempts all failed verification despite scrolling actually happening
   - Fix: Use a screenshot-diff approach for scroll verification (compare before/after pixel differences) instead of asking the vision model to interpret "content shifted"

### P2 — Cleanup

7. **[P2-1] Duplicate walmart skill files still exist**
   - `return-walmart-order.md` (stub) and `return-walmart-order-2.md` (stub) conflict with `return_walmart_order.md` (real template)
   - Causes "Duplicate skill name" error on every run
   - Fix: Delete the stub files

8. **[P2-2] No skill adaptation / Skill Librarian not creating new skills**
   - After Scenario 1's execution (which included a successful replan with target.com navigation), no new skill was created
   - The Skill Librarian did not promote any observations from the execution trace
   - Fix: Verify the Skill Librarian is running and creating skills from execution traces. The librarian should create a `buy_on_target.md` skill from successful navigation patterns.

9. **[P2-3] Verification doesn't check domain correctness**
   - Tier 1 URL verification matches URL tokens without checking the target domain
   - Scenario 2 "passed" verification despite being on amazon.com instead of target.com
   - Fix: When a skill or prompt specifies a site (e.g., "on target"), verification should check that the browser is on the correct domain

---

## Skill Adaptation Assessment

**Current state: ZERO adaptation.** The system showed no learning between scenarios:
- No new skill files created after any run
- No observations promoted by the Skill Librarian
- The same mistakes repeated across all 3 scenarios
- The planner did not use any patterns from prior runs

**What skill adaptation should look like:**
1. After Scenario 1 (237.6s, 1 replan, scroll failure): Librarian creates a `buy_on_target.md` skill documenting the successful pattern: activate Chrome → open target.com → type in search bar → click search → filter/sort → select product → add to cart
2. After Scenario 2 (reusing the skill): Agent matches the new skill, follows the template, completes in <60s with 0 replans
3. After Scenario 3 (generalization): Agent matches the same skill for a different product query, adapts parameters (queen-size, sort by price)

**Blockers to achieving this:**
- Skill Librarian needs to actually run and create skills from execution traces
- Skill router needs to match the correct skill (Target shopping, not Amazon search)
- Plan depth needs to cover the full buy workflow, not just search

---

## Instrumentation Gaps

1. **Need to log which browser tab/window is active when type_text executes** — to diagnose focus issues
2. **Need before/after screenshot pairs for scroll verification** — to confirm scroll actually occurred
3. **Need to log Skill Librarian execution** — to understand why no skills were created from traces
4. **Need domain validation in verification** — log when URL domain doesn't match the expected site

---

## Recommended Fix Priority

1. Create `buy_on_target.md` skill with full workflow (P0-2)
2. Fix skill router to respect site/store in prompt (P0-1)
3. Fix planner to generate complete buy workflows, not just search URLs (P0-3)
4. Fix type_text to click target element before typing (P1-1)
5. Fix open_url false negative when page already loaded (P1-2)
6. Fix scroll verification to use diff-based approach (P1-3)
7. Delete duplicate walmart skill stubs (P2-1)
8. Fix Skill Librarian to create skills from execution traces (P2-2)
9. Add domain validation to URL verification (P2-3)
