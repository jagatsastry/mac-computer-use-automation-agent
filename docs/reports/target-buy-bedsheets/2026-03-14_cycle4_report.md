# Customer Testing Report: Target Buy Bed Sheets — Cycle 4

## Summary
- Scenarios tested: 2 (re-ran S2 and S3 which failed in Cycle 3; S1 passed in Cycle 3)
- Passed: 0
- Failed: 2
- Pass rate: **1/3 overall (33%)** — same as Cycle 3, but root cause shifted

## Test Environment
- Date: 2026-03-14
- Vision server: Molmo v1 (port 8091)
- LLM: Gemini 2.5 Flash (planner)
- Browser: Chrome
- Commits tested: All through 95e7f81 + uncommitted grounding pipeline fixes (simplified find_element.md prompt, <points x1/y1> parser, out-of-range auto-escalation, crosshair annotation infrastructure)
- Run IDs: 260313_230335 (S2), 260313_231111 (S3)

---

## Grounding Fix Impact: MAJOR IMPROVEMENT

| Metric | Cycle 3 | Cycle 4 | Status |
|--------|---------|---------|--------|
| Sort button grounding | (11,10), (144,767) — corners/Dock | **(147,261), (144,261)** — exactly on Sort button | **FIXED** |
| Sort dropdown opens | Sometimes (lucky clicks) | Consistently on retry | **IMPROVED** |
| "Price: Low to High" grounding | N/A (never reached reliably) | (833,315) — between low/high options | **NEW ISSUE** |
| Debug images with crosshair | No annotation | Full crosshair + confidence labels | **FIXED** |

The simplified `find_element.md` prompt (removing `<point x="500" y="250">` examples) eliminated the coordinate space confusion. Molmo now outputs coordinates in its native 0-100 range consistently.

---

## Scenario 2: Skill Adaptation — Same Prompt, Second Run

### Result: ❌ FAIL

### Prompt
"buy the top bed sheet cheaper than $50 on target"

### What Actually Happened
1. Skill matched: `buy-on-target` with params `{product: 'bed sheet'}` ✅
2. Plan: 6 steps — open_url → sort → price low-to-high → click product → add to cart → done ✅
3. `open_url` to `target.com/s?searchTerm=bed%20sheet` — Tier 1 verified ✅
4. `click Sort by dropdown` — found at **(147,261)** — exactly on Sort button ✅
   - First attempt: vision denied dropdown opened (false negative)
   - Retry: same coordinates (144,261), **vision confirmed dropdown opened** ✅
5. `click Price: Low to High option` — found at **(833,315)** — BETWEEN low-to-high and high-to-low ❌
   - The Sort modal shows radio buttons ~47px apart vertically
   - "Price: low to high" text is at ~y=288, "Price: high to low" at ~y=335
   - Crosshair at y=315 splits the difference, click lands on "high to low"
   - Vision correctly denies "products sorted low to high" (because high-to-low was selected)
   - All retries (refined query, enter, space) hit same coordinates
6. Replan: tries "Price: low to high option" directly — same (830,325) coordinates — same issue
7. **Exhausted retries** — FAIL

### Metrics
- Duration: 268.2s (vs 496.3s in Cycle 3 — 46% faster)
- Steps passed: 2
- Steps failed: 9
- Replans: 1
- Exit code: 1 (FAIL)

### Root Cause
**Molmo grounding precision**: At 1024x768 resolution, the "Price: low to high" and "Price: high to low" radio buttons are ~47px apart vertically. Molmo consistently grounds to y=315-325 which is between the two options. The click lands on "high to low" instead of "low to high".

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | New skill exists for Target shopping | FAIL | No new skill created from S1 — librarian not promoting |
| 2 | Fewer steps/replans than Scenario 1 | N/A | Failed before completion |
| 3 | Reuses patterns from Scenario 1 | PASS | Same buy-on-target skill, same URL pattern |
| 4 | Faster execution time | N/A | Failed |

---

## Scenario 3: Edge Case — Cheapest Queen-Size Bed Sheet Set

### Result: ❌ FAIL

### Prompt
"buy the cheapest queen-size bed sheet set on Target"

### What Actually Happened
1. Skill matched: `buy-on-target` with `{product: 'queen-size bed sheet set'}` ✅
2. Plan: 6 steps — same structure as S2 ✅
3. `open_url` → `target.com/s?searchTerm=queen-size%20bed%20sheet%20set` — Tier 1 verified ✅
4. `click Sort by dropdown` — found at **(147,261)** — exactly on Sort button ✅
   - Same pattern: first attempt denied, retry confirmed
5. `click Price: Low to High option` — found at **(833,315)** — same between-options issue ❌
   - Identical coordinates to S2 — Molmo's answer is deterministic for this Sort modal layout
   - All retries hit same spot
6. Replan: tries "Price: Low to High radio button" — (830,325) — still between options
7. **Exhausted retries** — FAIL

### Metrics
- Duration: 266.9s (vs 382.7s in Cycle 3 — 30% faster)
- Steps passed: 2
- Steps failed: 9
- Replans: 1
- Exit code: 1 (FAIL)

### Root Cause
Identical to S2 — Molmo grounding precision insufficient to distinguish vertically adjacent radio buttons in Target's Sort modal.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Searches for "queen size bed sheet set" | PASS | URL searchTerm=queen-size%20bed%20sheet%20set |
| 2 | Sorts by price (lowest first) | FAIL | Click lands on "high to low" instead |
| 3 | Selected product is queen-size sheet set | FAIL | Never reached product page |
| 4 | Adds cheapest matching item to cart | FAIL | Never reached add-to-cart |
| 5 | Agent matches buy-on-target skill | PASS | buy-on-target matched |

---

## Cycle-over-Cycle Progress

| Metric | Cycle 1 | Cycle 2 | Cycle 3 | Cycle 4 |
|--------|---------|---------|---------|---------|
| Pass rate | 0/3 (0%) | 0/3 (0%) | 1/3 (33%) | **1/3 (33%)** |
| Skill routing | ❌ amazon-search | ✅ buy-on-target | ✅ buy-on-target | ✅ buy-on-target |
| Plan depth | ❌ 3-step | ✅ 7-8 step | ✅ 6-8 step | ✅ 6 step |
| Sort button grounding | N/A | ❌ corners | ⚠️ sometimes | **✅ correct (147,261)** |
| Price option grounding | N/A | N/A | N/A (rarely reached) | **❌ between options (833,315)** |
| Execution time (S2) | N/A | N/A | 496s | **268s (-46%)** |
| Execution time (S3) | N/A | N/A | 383s | **267s (-30%)** |
| Debug images | ❌ none | ❌ none | ❌ no crosshair | **✅ crosshair + confidence** |

---

## Priority Issues

### P0 — Remaining Blocker

1. **[P0-1] Molmo grounding precision: can't distinguish adjacent radio buttons (47px gap)**
   - Molmo consistently returns y=315 for "Price: Low to High" in Target's Sort modal
   - The actual radio button text is at y≈288, but "Price: high to low" is at y≈335
   - y=315 is the midpoint — lands on the wrong option
   - Deterministic: same coordinates across S2 and S3, across retries
   - **Root cause**: Molmo sees both options and returns a coordinate between them
   - **Fix options** (ranked by impact):
     a. **Plan-level fix**: Change skill to sort via URL param (`?sortBy=PriceLow`) instead of clicking the Sort modal — bypasses grounding entirely (like the search URL fix)
     b. **Prompt engineering**: Ask Molmo to find the radio button CIRCLE for "low to high" specifically, not the text label
     c. **Click offset**: When grounding finds a sort option, shift click upward by ~30px to hit the correct option
     d. **Accessibility/DOM query**: Use AppleScript to query the DOM for the radio button element

### P1 — Degrading Quality

2. **[P1-1] Sort button requires two clicks to open modal**
   - First click at (147,261) lands on Sort button but vision denies modal opened
   - Second click (retry) at (144,261) — nearly identical — succeeds
   - Likely a timing issue: modal takes >100ms to animate open, vision screenshot captures pre-animation state
   - **Fix**: Add a small delay (300ms) before taking verification screenshot after click

3. **[P1-2] No skill adaptation — Skill Librarian still not promoting execution traces**
   - S2 expected to find a new/adapted skill from S1's trace — none exists
   - Same issue as Cycles 2 and 3
   - Librarian pipeline is not running post-execution

### P2 — Cosmetic / Efficiency

4. **[P2-1] Duplicate walmart skill warning still appearing**
   - `"Duplicate skill name 'return-walmart-order'"` logged on every run
   - Not blocking but noisy

---

## Screenshots Analysis

### Sort Button Grounding (FIXED)
- `find_*_Sort_by_dropdown.jpg` (S2): Crosshair at (147,261) — exactly on the "Sort" button in the filter bar ✅
- `find_*_Sort_by_dropdown.jpg` (S3): Same coordinates (147,261) ✅

### Price: Low to High Grounding (NEW ISSUE)
- `find_*_Price_Low_to_High_option.jpg` (S2): Crosshair at (833,315) — between "low to high" and "high to low" radio buttons ❌
- `find_*_Price_Low_to_High_option.jpg` (S3): Same (833,315) — deterministic misplacement ❌
- The crosshair annotation clearly shows the problem: it's right on the boundary between the two options

---

## Recommendations

1. **Immediate fix (P0-1)**: Modify `buy_on_target.md` skill to use Target's URL-based sorting: `target.com/s?searchTerm=X&sortBy=PriceLow` — this completely bypasses the Sort modal grounding, just like the search URL bypass eliminated the type_text grounding issue
2. **Short-term (P1-1)**: Add 300ms post-click delay before verification screenshot capture
3. **Medium-term**: Investigate why Molmo returns the midpoint between two similar text elements — may need prompt adjustment to target radio button circles rather than text labels
