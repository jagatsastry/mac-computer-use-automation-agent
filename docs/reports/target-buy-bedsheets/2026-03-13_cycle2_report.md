# Customer Testing Report: Target Buy Bed Sheets — Cycle 2

## Summary

- **Scenarios tested:** 3 (of 5 planned; scenarios 4-5 blocked by same issues)
- **Passed:** 0
- **Failed:** 3
- **Pass rate:** **0/3 (0%)**
- **Cycle 1 comparison:** 0/3 pass -> 0/3 pass (no improvement in pass rate)
- **Fixes applied:** 9 (P0-1 through P2-3)
- **Fixes that worked in live run:** 4 of 9 (partially or fully)
- **Fixes that failed or were untestable:** 5 of 9

## Test Environment

- **Date:** 2026-03-13
- **Vision server:** Molmo v1 (port 8091)
- **Vision verifier:** Gemini 2.5 Flash (Google API)
- **LLM planner:** Gemini 2.5 Flash
- **Browser:** Google Chrome
- **Run IDs:** 260313_175216 (S1), 260313_175819 (S2), 260313_180348 (S3)

---

## Scenario 1: Happy Path — Buy Top Bed Sheet Under $50

### Prompt
"buy the top bed sheet cheaper than $50 on target"

### Expected Outcome
Agent navigates to Target.com, searches for bed sheets, filters by price (under $50), selects a top-rated option, and adds it to cart.

### What Actually Happened

1. **Skill routing FIXED**: `buy-on-target` skill matched with confidence 1.0 (direct match). No longer matching `amazon-search`. *Evidence: `readable_log.txt:6` — "Skill matched: buy-on-target, params: {product: bed sheet, max_price: 50}"*
2. **Plan depth FIXED**: 8-step plan generated (open_url, type_text, press_key, sort, price filter, click product, add to cart, done). *Evidence: `readable_log.txt:9-17`*
3. **open_url to target.com PASSED** via Tier 1 URL verification. *Evidence: `readable_log.txt:52-53`*
4. **type_text "bed sheet" FAILED** — vision verifier repeatedly denied that "bed sheet" appeared in the search bar. 4 attempts (original + select_all_then_type + 2x slow_type), all failed verification.
   - First attempt: vision model grounded "Search input field" at (473, **767**) — far below the actual search bar at ~(473, 152). Text was typed into the wrong part of the page.
   - *Evidence: `stdout.txt:36` — `Element found via grounding model: x=473, y=767`*
   - *Evidence: screenshots `175343_step_01_post_type_text.png` and `175343_verify_step_type_text.png` — Target.com homepage visible, search bar says "What can we help you find?" (empty), page has scrolled down to "Save on trip essentials" section*
   - Subsequent retries found the correct location (473, 147/152) but vision verification still denied text was present.
   - *Evidence: `stdout.txt:50` — `Element found via grounding model: x=473, y=147` (correct on retry)*
   - *Evidence: screenshot `175556_verify_step_type_text.png` — Target.com scrolled further down, "Fresh picks for Easter" section, search bar still empty*
5. After replan: click on search field with specific placeholder text PASSED — vision confirmed cursor blinking at (473, 162). *Evidence: `readable_log.txt:97-102`, debug image `find_...What_can_we_help_yo.jpg` shows crosshair on search bar*
6. type_text after replan started but timed out at 290s before completing.

### Success Criteria Verdicts

| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Opens browser, navigates to target.com | **PASS** | Screenshot `175243_step_00_post_open_url.png` shows Target.com homepage in Chrome, URL bar shows "target.com", user logged in as "Hi, Jagat" |
| 2 | Searches for "bed sheets" | **FAIL** | Text never appeared in search bar. Screenshots `175343_verify_step_type_text.png` through `175556_verify_step_type_text.png` all show empty search bar. First attempt misgrounded to (473, 767) |
| 3 | Applies price filter (max $50) | **FAIL** | Never reached — stuck at search step |
| 4 | Clicks on a top-rated bed sheet | **FAIL** | Never reached |
| 5 | Clicks "Add to Cart" | **FAIL** | Never reached |
| 6 | Does not proceed past checkout | **N/A** | Never reached |

### Root Causes

1. **Vision grounding misfire on first type_text attempt**: Molmo grounded "Search input field" to (473, 767) instead of (473, ~152). The type_text focus fix (P1-1) is working — it does find_element before typing — but the element was found at the wrong location. The text went to a non-input area of the page.
2. **Vision verifier (Gemini) consistently denies typed text**: Even when subsequent retries grounded correctly (473, 147), the vision verifier never confirmed "bed sheet" was in the search bar. The screenshots prove the text was NOT actually typed (search bar remains empty in all screenshots). This means the type_text action itself failed to insert characters — the AppleScript keystroke was not received by the browser.
3. **Timeout**: At 290s the run was killed, preventing completion of the replanned steps.

### Comparison with Cycle 1
- **Improved**: Correct site (Target.com, not Amazon); correct skill matched; correct plan depth (8 steps vs 3)
- **Still broken**: type_text fails to insert text into the search bar; agent never reaches search results

---

## Scenario 2: Skill Adaptation — Same Prompt, Second Run

### Prompt
"buy the top bed sheet cheaper than $50 on target" (same as Scenario 1)

### Expected Outcome
Agent performs the same task faster with fewer retries, using learned patterns from Scenario 1.

### What Actually Happened

1. **Skill routing FIXED**: `buy-on-target` matched again (confidence 0.98). *Evidence: `readable_log.txt:6`*
2. **Same 8-step plan** generated — identical to Scenario 1. *Evidence: `readable_log.txt:9-17`*
3. **open_url PASSED** via Tier 1. *Evidence: `readable_log.txt:50-53`, screenshot `175846_step_00_post_open_url.png` — Target.com homepage*
4. **type_text FAILED** — identical failure pattern to Scenario 1:
   - 4 type_text attempts, all failed vision verification
   - Vision model found element at (473, 767) on first attempt — same misgrounding as S1
   - *Evidence: S2 `evidence.md:16` — "found element at (473, 767) — notably different Y coordinate"*
   - All verification screenshots show empty search bar on Target.com homepage
   - *Evidence: screenshots `175929_verify_step_type_text.png` through `180157_step_01_post_type_text.png`*
5. After replan: planner assumed text was already in search bar, issued press_key(enter) — FAILED. Vision denied search results page was visible (because search was never submitted).
   - *Evidence: `readable_log.txt:93-95` — Skill Patch note: "The text 'bed sheet' was already present in the search bar, so we skipped typing"*
   - 4 press_key(enter) attempts, all failed. Screenshots `180229_step_00_post_press_key.png` through `180253_verify_step_press_key.png` all show Target.com homepage scrolled down to "Fresh picks for Easter" — no search results.
   - **A macOS notification ("Rice, Today 6:00 PM") appeared**, partially obscuring the top-right corner of the screen.
6. **Skill distillation ran but parse_failed**: 0 observations extracted. *Evidence: `stdout.txt:134` — "skill_distillation_parse_failed", `stdout.txt:135` — "observations: 0"*
7. No new skill file created.

### Success Criteria Verdicts

| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | New skill exists for Target shopping | **PARTIAL PASS** | `buy_on_target.md` exists from the fix cycle (P0-2), not from auto-adaptation. No NEW skill was auto-generated. |
| 2 | Fewer steps/replans than Scenario 1 | **FAIL** | Same step count (8), same replan count (1). S2 had MORE failures (8 vs 4). Duration similar: 274.7s vs 290.1s |
| 3 | Reuses navigation patterns from S1 | **FAIL** | Identical plan — no adaptation from S1's execution trace. Same misgrounding repeated. |
| 4 | Faster execution time | **FAIL** | 274.7s vs 290.1s — negligibly different, both hit retry exhaustion |

### Root Cause

1. **Same type_text failure**: Identical misgrounding (473, 767) and verification denial loop.
2. **Replan incorrectly assumes success**: The planner's replan assumed "bed sheet" was already typed and skipped to press_key(enter), but the text was never actually entered.
3. **Skill distillation parser broken**: The distiller attempted to extract observations but the LLM response was truncated/malformed, yielding 0 observations. No learning occurred.

### Comparison with Cycle 1
- **Improved**: Correct site (Target.com vs Amazon in C1 Scenario 2). Skill routing correct.
- **Still broken**: No skill adaptation occurring. Distillation parse failure prevents learning. Exact same failure repeated.

---

## Scenario 3: Edge Case — Cheapest Queen-Size Bed Sheet Set

### Prompt
"buy the cheapest queen-size bed sheet set on Target"

### Expected Outcome
Agent navigates to Target, searches for queen-size sets, sorts by price, selects cheapest.

### What Actually Happened

1. **Skill routing FIXED**: `buy-on-target` matched with params: product='queen-size bed sheet set'. Generalization worked. *Evidence: `readable_log.txt:6-7`*
2. **Plan generated**: 7 steps (no press_key/enter step — planner assumed type_text auto-submits). *Evidence: `readable_log.txt:9-16`*
3. **open_url PASSED** via Tier 1. *Evidence: `readable_log.txt:49-52`, screenshot `180413_step_00_post_open_url.png`*
4. **type_text FAILED** — same vision verification denial pattern. Only 1 attempt before replan (on_fail was "replan" not "retry_different").
   - *Evidence: `readable_log.txt:53-58`, screenshot `180503_verify_step_type_text.png` — Target.com homepage scrolled down, search bar empty*
5. **Replan**: Planner correctly inferred search was already done (it wasn't), skipped to sorting. 5-step plan starting with click "Sort by dropdown".
   - *Evidence: `readable_log.txt:62-67`, Skill Patch note at line 70*
6. **"Sort by dropdown" NOT FOUND**: Vision could not locate the element (69s search). The page was still on the Target homepage, not search results.
   - *Evidence: `readable_log.txt:73`, screenshot `180643_not_found_Sort_by_dropdown.png`*
7. **Scroll fallback PASSED**: scroll down 3 clicks confirmed via pixel diff. *Evidence: `readable_log.txt:76-79`, screenshot `180651_step_00_post_scroll.png`*
8. **"Price: Low to High option" MISGROUNDED**: Found at (11, 10) — the top-left corner of the screen (Apple menu area). Clicked on the Apple logo. This opened the Apple menu.
   - *Evidence: `trace.md:123,130` — `element_found at (11, 10) conf=0.75`, `Clicked (16, 13)`*
   - *Evidence: debug image `find_...Price_Low_to_High_option.jpg` — crosshair at top-left corner*
   - *Evidence: screenshot `180735_verify_step_click.png` — Target.com homepage with page scrolled, no sort dropdown visible*
9. **Retry with refined query**: Found at (11, 185) — Apple menu opened, click went to "Sleep" or "Restart..." menu item area.
   - *Evidence: `trace.md:159` — `element_found at (11, 185)`*
   - *Evidence: debug image `find_...partially_hid.jpg` — Apple menu is open, crosshair on "Shut Down..." area*
   - *Evidence: screenshot `180820_verify_step_click.png` — **macOS "Restart" confirmation dialog visible**: "Are you sure you want to restart your computer now?"*
10. **Agent nearly triggered a system restart**. The misgrounded click on (11, 185) hit "Restart..." in the Apple menu, producing the system restart confirmation dialog.

### Success Criteria Verdicts

| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Searches for "queen size bed sheet set" | **FAIL** | type_text failed, search never submitted. Screenshot `180503_verify_step_type_text.png` — search bar empty |
| 2 | Filters or sorts by price (lowest first) | **FAIL** | Sort dropdown not found (page was still homepage). Misgrounded clicks went to Apple menu, triggering restart dialog |
| 3 | Selected product is queen-size sheet set | **FAIL** | Never reached product selection |
| 4 | Adds cheapest matching item to cart | **FAIL** | Never reached |
| 5 | If Target shopping skill exists, agent uses it | **PASS** | `buy-on-target` matched and was used. `readable_log.txt:6` |

### Root Causes

1. **Same type_text verification failure**: Text never entered into search bar.
2. **Replan hallucinates screen state**: Planner's replan assumed search results were visible based on a misleading screen description, skipping directly to sorting. But the page was still the homepage.
3. **Catastrophic misgrounding**: Vision model grounded "Price: Low to High option" to (11, 10) — the Apple menu icon. This is a 0.75-confidence hallucination. The element does not exist on the visible page.
4. **Safety hazard**: The misgrounded click opened the Apple menu and then hit "Restart...", producing the system restart dialog. Only the macOS confirmation dialog prevented a live system restart during testing.

### Comparison with Cycle 1
- **Improved**: Correct site (Target.com). Skill matched correctly (buy-on-target vs amazon-search). Scroll verification passed via pixel diff (P1-3 fix worked).
- **New issue**: Agent nearly restarted the computer via misgrounded clicks on the Apple menu.
- **Still broken**: type_text, vision verification, element grounding accuracy.

---

## Scenarios 4-5: Not Executed

Blocked by the same type_text verification failure that prevents all three tested scenarios from progressing past the search step.

---

## Fix Assessment Table

| Fix ID | Fix Description | Status | Evidence |
|--------|----------------|--------|----------|
| **P0-1** | Skill router respects site/store in prompt | **WORKED** | All 3 scenarios matched `buy-on-target` (conf 0.98-1.0) instead of `amazon-search`. S1 `readable_log.txt:6`, S2 `readable_log.txt:6`, S3 `readable_log.txt:6` |
| **P0-2** | `buy_on_target.md` skill created | **WORKED** | Skill file exists at `src/automation_agent/skills/library/buy_on_target.md` with 6-step workflow, parameters, and error recovery |
| **P0-3** | Plan depth — full buy workflow | **WORKED** | S1/S2: 8-step plans covering search through add-to-cart. S3: 7-step plan. No more 3-step "open URL and done" plans. `readable_log.txt:9-17` |
| **P1-1** | type_text focuses element before typing | **PARTIALLY WORKED** | type_text does call find_element first (evidence: `stdout.txt:36` shows element_found before typing). But first attempt misgrounded to (473, 767). Text was never actually inserted. The focus fix is executing but the underlying grounding error causes it to click the wrong location. |
| **P1-2** | open_url no-diff false negative | **WORKED** | All 3 scenarios: open_url to target.com passed Tier 1 URL verification on first attempt. No "no visible effect" failures. S1 `readable_log.txt:52-53` |
| **P1-3** | Scroll verification via pixel diff | **WORKED** | S3: scroll down 3 clicks passed via `_scroll_pixel_changed: True` and "Scroll confirmed via screenshot pixel diff". `readable_log.txt:77-79` |
| **P2-1** | Delete duplicate walmart skill stubs | **UNTESTED** | No walmart-related errors appeared, suggesting duplicates were removed. Cannot confirm without checking file system. |
| **P2-2** | Skill Librarian enabled | **FAILED** | S2 `stdout.txt:134-135`: "skill_distillation_parse_failed", "observations: 0". Distiller ran but failed to parse LLM response. No new skills created. No adaptation between S1 and S2. |
| **P2-3** | Domain verification | **UNTESTED** | No domain mismatch occurred (all runs stayed on target.com). The fix was not exercised because the agent never navigated to the wrong site. Cannot confirm the fix works in the relevant failure mode. |

### Summary: 4 fixes worked, 1 partially worked, 2 failed/untestable, 2 untested

---

## Priority Issues (Cycle 2)

### P0 — Blocks ALL scenarios

1. **[P0-NEW-1] type_text fails to insert text into browser search bars**
   - **Symptom**: type_text action reports success but text never appears in the search field. All 3 scenarios, all retry strategies (normal, select_all_then_type, slow_type) fail identically.
   - **Screenshot evidence**: Every post-type_text screenshot shows Target.com search bar still displaying "What can we help you find?" (placeholder text) — zero characters entered.
   - **Root cause hypothesis**: The type_text focus fix (P1-1) calls find_element, which sends the click to the grounded coordinates. On the first attempt, Molmo misgrounds to (473, 767) — a non-interactive area of the page. The click lands on promo content, not the search input. Subsequent attempts ground correctly (473, ~152) but the page may have scrolled, or the click does not actually focus the input. AppleScript `keystroke` then sends characters to whatever has focus (possibly the URL bar or nowhere).
   - **Evidence**: S1 `stdout.txt:36` — first find_element returned y=767 (wrong), S1 `stdout.txt:50` — second find_element returned y=147 (correct). But screenshots show text never entered regardless.
   - **Severity**: Blocks 100% of scenarios. No scenario can progress past the search step.
   - **Fix**: type_text needs a post-type verification at the actuator level (check clipboard paste or DOM state), not just at the orchestrator vision-verification level. Also consider using JavaScript injection to set input field values directly, bypassing AppleScript keystroke entirely.

2. **[P0-NEW-2] Vision verifier (Gemini) consistently returns false for text-in-input verification**
   - **Symptom**: Vision verifier denies "bed sheet" appears in search bar even when asked with multiple rephrased conditions: "The focused text field contains 'bed sheet'", "The text 'bed sheet' appears in the search bar", "The search input field contains 'bed sheet'" — all return False.
   - **Root cause**: The text genuinely is not in the search bar (screenshots prove it), so the verifier is actually correct. The real problem is P0-NEW-1 (text not typed). However, even if text were present, the 3-call verification pattern (rephrase 3 times, all must agree) makes it harder to pass.
   - **Evidence**: S1 `stdout.txt:39-45` — three Gemini calls, all return False for slightly different phrasings.

3. **[P0-NEW-3] Vision grounding hallucination — catastrophic misgrounding to system UI**
   - **Symptom**: Molmo grounds "Price: Low to High option" to (11, 10) — the Apple menu icon — with 0.75 confidence, when no such element exists on the page. This caused the agent to open the Apple menu and trigger the macOS restart dialog.
   - **Screenshot evidence**: S3 `180735_verify_step_click.png` shows Apple menu open after misgrounded click. S3 `180820_verify_step_click.png` shows **system restart confirmation dialog**.
   - **Root cause**: When an element doesn't exist on screen, Molmo returns a low-confidence coordinate that happens to land on system UI. The 0.75 confidence is above the 0.5 gate threshold, so the click proceeds.
   - **Severity**: Safety hazard. The agent nearly restarted the user's computer.
   - **Fix**: (a) Raise confidence threshold for clicks outside the browser content area. (b) Add a safety zone that rejects clicks in the macOS menu bar region (y < 25) unless explicitly targeting system UI. (c) Validate that clicked coordinates are within the browser viewport bounds.

### P1 — Significant issues

4. **[P1-NEW-1] Replan hallucinates screen state**
   - The planner's replan in S2 assumed "bed sheet" was already typed ("we skipped typing and directly pressed Enter to submit the search") when it wasn't. In S3, the replan assumed search results were visible and jumped to sorting. Both hallucinated the screen state.
   - **Fix**: Include the actual screenshot or screen description in the replan prompt, not just the failure history. The planner needs to see what's really on screen.

5. **[P1-NEW-2] Skill distillation parser broken — zero observations extracted**
   - S2 `stdout.txt:134`: LLM response was truncated/malformed, parser failed, 0 observations recorded. No learning between runs.
   - **Fix**: Fix the distiller's response parsing. Add retry/fallback for malformed LLM output. Log the full truncated response for debugging.

### P2 — Cleanup

6. **[P2-NEW-1] macOS notifications obstruct screen**
   - S2 and S3 screenshots show a persistent "Rice, Today 6:00 PM" notification in the top-right corner, partially covering browser UI. This could confuse vision grounding.
   - **Fix**: Enable Do Not Disturb mode before agent runs to suppress notifications.

---

## What Improved from Cycle 1

| Area | Cycle 1 | Cycle 2 | Verdict |
|------|---------|---------|---------|
| **Skill routing** | `amazon-search` matched for all 3 scenarios | `buy-on-target` matched for all 3 scenarios | **FIXED** |
| **Target skill** | Did not exist | `buy_on_target.md` exists with 6-step workflow | **FIXED** |
| **Plan depth** | 3-step plans (open URL + done) | 7-8 step plans (full workflow) | **FIXED** |
| **Open URL verification** | Failed 4 times ("no visible effect") | Passed first attempt via Tier 1 | **FIXED** |
| **Scroll verification** | Always failed (vision model denied scroll) | Passed via pixel diff (`_scroll_pixel_changed`) | **FIXED** |
| **Wrong site** | S2 went to Amazon.com entirely | All scenarios stayed on Target.com | **FIXED** |
| **type_text** | Text went to Amazon tab (focus issue) | Text never entered at all (grounding + actuator issue) | **DIFFERENT BUG** |
| **Skill adaptation** | Zero — no skill created, no learning | Zero — distiller ran but parse_failed, no learning | **NOT FIXED** |
| **Domain verification** | S2 "passed" on amazon.com | Not exercised (stayed on target.com) | **UNTESTED** |

## What's Still Broken

1. **type_text is the singular blocker**: All 3 scenarios fail at the exact same step — typing text into the search bar. The original Cycle 1 focus bug (typing into wrong tab) is replaced by a new bug (grounding to wrong coordinates, text never entering the field at all).
2. **Skill adaptation/learning**: Still zero. The distiller's parse failure prevents any observations from being recorded.
3. **Vision grounding accuracy**: Molmo produces confident but wrong coordinates for elements that don't exist on screen, leading to dangerous misclicks.

## Recommended Fix Priority (Cycle 3)

1. **[CRITICAL] Fix type_text to actually insert characters into the browser search bar** — This is the single fix that unblocks all scenarios. Consider: (a) clicking the element, then using `osascript` `set value` on the AX element, (b) using clipboard paste (Cmd+V) instead of keystroke, (c) using JavaScript `document.querySelector('input').value = 'text'` via browser automation.
2. **[CRITICAL] Add safety bounds for click coordinates** — Reject clicks in the macOS menu bar zone (y < 30 in screen coordinates) to prevent system UI interaction. An agent should never click the Apple menu.
3. **[HIGH] Fix skill distiller response parser** — Must handle truncated/malformed LLM responses gracefully.
4. **[HIGH] Ground truth screen description for replans** — Include actual screenshot analysis in the replan prompt so the planner doesn't hallucinate screen state.
5. **[MEDIUM] Suppress macOS notifications during runs** — Use `defaults write` or Focus mode to prevent notification banners from obscuring the screen.

---

## Instrumentation Gaps

1. **Need to log what AppleScript actually does during type_text** — whether `keystroke` fires, which element has focus, whether characters were received. Currently the actuator reports `{success: True}` even when no text was typed.
2. **Need to log the full distiller LLM response** — the truncated response that caused parse_failed is only partially visible in logs. Log the complete raw response.
3. **Need before/after screenshots for type_text** — capture screenshot immediately before and after `keystroke` to confirm whether characters appeared.
4. **Need viewport bounds validation** — log when a grounded coordinate falls outside the browser content area or in system UI zones.
