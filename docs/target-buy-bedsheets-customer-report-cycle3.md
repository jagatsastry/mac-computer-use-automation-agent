# Customer Testing Report: Target Buy Bed Sheets — Cycle 3

## Summary
- Scenarios tested: 3
- Passed: 1
- Failed: 2
- Pass rate: **1/3 (33%)** — up from 0/3 in Cycles 1 and 2

## Test Environment
- Date: 2026-03-13
- Vision server: Molmo v1 (port 8091)
- LLM: Gemini 2.5 Flash (planner)
- Browser: Safari
- Commits tested: 7d462fb (target-buy fixes), ad751b5 (type_text blocker), 1c1535a (PB4/5/7), 9692b10 (type-text-hardening)
- Run IDs: 260313_204252 (S1), 260313_205535 (S2), 260313_210421 (S3)

---

## Fixes Verified Working (from Cycles 1-2 issues)

| Issue | Status | Evidence |
|-------|--------|----------|
| P0-1 Skill routing | ✅ FIXED | buy-on-target matched 3/3 scenarios (was amazon-search) |
| P0-2 Target skill | ✅ FIXED | buy_on_target.md used with searchTerm URL pattern |
| P0-3 Shallow plans | ✅ FIXED | 6-8 step plans with sort, product click, add-to-cart (was 3-step) |
| P1-1 type_text focus | ✅ BYPASSED | Direct search URL eliminates type_text entirely |
| P1-2 open_url no-diff | ✅ FIXED | Tier 1 URL token verification passes immediately |
| P1-3 Scroll verification | ✅ FIXED | Scroll accepted via Tier 1 actuator/JS signals |
| P2-1 Walmart duplicates | ✅ FIXED | No duplicate skill errors in any run |
| P2-3 Domain verification | ✅ FIXED | Correct target.com domain matched |
| URL encoding | ✅ FIXED | Spaces encoded as %20 in search URLs |

---

## Scenario 1: Happy Path — Buy Top Bed Sheet Under $50

### Result: ✅ PASS (with caveats)

### Prompt
"buy the top bed sheet cheaper than $50 on target"

### What Actually Happened
1. Skill matched: `buy-on-target` with params `{product: 'bed sheet', max_price: 50}` ✅
2. Plan: 7 steps — open_url → sort by → price low-to-high → click product → observe → add to cart → done ✅
3. `open_url` to `target.com/s?searchTerm=bed%20sheet` — Tier 1 verified ✅
4. `click Sort by dropdown` — found at (11,10), vision verified sort options visible ✅
5. `click Price: Low to High` — found at (11,767), verified after escalation ✅
6. `click First product listing` — found at (562,85) but vision denied product detail page loaded ❌
   - Retry with refined query (421,646) — vision denied ❌
   - Keyboard fallback enter — denied ❌
   - Keyboard fallback space — **vision confirmed product page loaded** ✅
7. `click Add to cart` — NOT FOUND, retries exhausted → replan
8. **Replan**: same 7-step plan with specific product name "Room Essentials Solid Microfiber Sheet Set"
9. Replan: open_url ✅, found Sort as "Filter" at (113,261) ✅, Price Low-to-High ✅
10. Replan product click: grounded at (11,10) — wrong corner. Retries, finally space key → confirmed ✅
11. "Add to cart" not found, scroll down + retry → **Tier 1 verified cart confirmation** ✅
12. `done` ✅

### Metrics
- Duration: 717.1s
- Steps passed: 9
- Steps failed: 13
- Replans: 1
- Exit code: 0 (SUCCESS)

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Opens browser and navigates to target.com | PASS | Tier 1: URL `target.com/s?searchTerm=bed%20sheet` — readable_log.txt:51 |
| 2 | Searches for "bed sheets" | PASS | Direct search URL with searchTerm param |
| 3 | Applies price filter (max $50) or sorts by price | PASS | Clicked "Price: Low to High" — readable_log.txt:59-63 |
| 4 | Clicks on a bed sheet product | PASS | "Room Essentials Solid Microfiber Sheet Set" — product page confirmed by vision |
| 5 | Clicks "Add to Cart" and cart confirms | PARTIAL | Tier 1 verified cart confirmation, but vision model didn't confirm. Scroll-down-and-retry strategy likely triggered add-to-cart indirectly |
| 6 | Does NOT proceed past checkout | PASS | Agent called done after cart step |

### Caveats
- 13 failed steps out of 22 total — many retries and keyboard fallbacks needed
- Grounding accuracy poor: (11,10) and (11,767) are far-left corners, not product images
- Space key workaround was luck — space activated the focused element which happened to be a product link
- Cart verification was indirect (Tier 1 only, vision didn't confirm)

---

## Scenario 2: Skill Adaptation — Same Prompt, Second Run

### Result: ❌ FAIL

### Prompt
"buy the top bed sheet cheaper than $50 on target" (same as scenario 1)

### What Actually Happened
1. Skill matched: `buy-on-target` ✅
2. Plan: 8 steps (added activate_app) — correct depth ✅
3. `activate_app Safari` — Tier 1 verified ✅
4. `open_url` — Tier 1 URL verified ✅
5. `click Sort by dropdown` — NOT FOUND initially, scroll-down-and-retry helped find at (144,767)
6. `click Price: Low to High option` — NOT FOUND, scroll helped find at (196,162)
7. `observe` — OK
8. `click First product listing after sorting` — found at (217,317), but vision denied product page ❌
9. **Replan**: Sort button → Price: Low to High → click product
10. Replan Sort button at (147,261) — vision denied sort menu appeared repeatedly ❌
11. Keyboard fallbacks (enter, space) — all denied by vision
12. **Exhausted retries** — FAIL

### Metrics
- Duration: 496.3s
- Steps passed: 4
- Steps failed: 7
- Replans: 1
- Exit code: 1 (FAIL)

### Root Causes
1. **Vision verification over-rejection**: Vision model (Gemini 2.5 Flash) consistently denied "Sort options are visible" and "Product detail page loaded" even when the actions may have worked
2. **Grounding misfire**: Molmo placed clicks at (144,767), (196,162) — likely not on the target elements
3. **No skill adaptation**: No new skill file created from Scenario 1's execution. The skill librarian distillation pipeline did not promote observations.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | New skill exists for Target shopping | FAIL | No new skill file — only buy_on_target.md exists (pre-built) |
| 2 | Fewer steps/replans than Scenario 1 | FAIL | Failed faster but not because of efficiency |
| 3 | Reuses patterns from Scenario 1 | N/A | Task failed before completion |
| 4 | Faster execution time | N/A | Task failed |

---

## Scenario 3: Edge Case — Cheapest Queen-Size Bed Sheet Set

### Result: ❌ FAIL

### Prompt
"buy the cheapest queen-size bed sheet set on Target"

### What Actually Happened
1. Skill matched: `buy-on-target` with `{product: 'queen-size bed sheet set'}` ✅
2. Plan: 6 steps — direct search URL ✅
3. `open_url` → `target.com/s?searchTerm=queen-size%20bed%20sheet%20set` — Tier 1 verified ✅
4. `click Sort by dropdown` — found at (144,767), **vision confirmed sort options visible** ✅
5. `click Price: Low to High` — found at (11,315), verified ✅
6. `click First product listing` — found at (144,767), vision denied product page ❌
   - Refined query at (14,315) — denied ❌
   - Enter key — denied ❌
   - Space key — denied ❌
7. **Replan**: press escape (dismiss modal) → click First product listing
8. Escape + vision confirms clear page ✅
9. `click First product listing` — found at (11,767), vision denied product page ❌
   - Refined query at (555,10) — denied ❌
   - Enter/space — denied ❌
10. **Exhausted retries** — FAIL

### Metrics
- Duration: 382.7s
- Steps passed: 4
- Steps failed: 8
- Replans: 1
- Exit code: 1 (FAIL)

### Root Causes
1. **Grounding coordinates consistently wrong**: (144,767), (11,767), (555,10) — none of these are over product listing images. The Molmo model can't locate "First product listing" on Target's search results.
2. **Vision verification correct**: It correctly denies "product detail page loaded" because the click never navigated to a product page — the click landed in the wrong spot.

### Success Criteria Verdicts
| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | Searches for "queen size bed sheet set" | PASS | URL searchTerm=queen-size%20bed%20sheet%20set — readable_log.txt:50 |
| 2 | Sorts by price (lowest first) | PASS | Price: Low to High clicked and verified — readable_log.txt:58-63 |
| 3 | Selected product is queen-size sheet set | FAIL | Never navigated to product page |
| 4 | Adds cheapest matching item to cart | FAIL | Never reached add-to-cart step |
| 5 | Agent matches buy-on-target skill | PASS | buy-on-target matched with product param |

---

## Cycle-over-Cycle Progress

| Metric | Cycle 1 | Cycle 2 | Cycle 3 |
|--------|---------|---------|---------|
| Pass rate | 0/3 (0%) | 0/3 (0%) | **1/3 (33%)** |
| Skill routing | ❌ amazon-search | ✅ buy-on-target | ✅ buy-on-target |
| Plan depth | ❌ 3-step shallow | ✅ 7-8 step | ✅ 6-8 step |
| URL encoding | ❌ raw spaces | ✅ %20 | ✅ %20 |
| open_url verification | ❌ "no visible effect" | ✅ Tier 1 URL | ✅ Tier 1 URL |
| type_text needed | ❌ focus issue | ❌ grounding misfire | ✅ bypassed via URL |
| Sort step | N/A | ❌ grounding | ⚠️ sometimes works |
| Product click | N/A | N/A | ❌ grounding accuracy |
| Add to cart | N/A | N/A | ⚠️ S1 only (indirect) |

---

## Priority Issues

### P0 — Remaining Blocker

1. **[P0-1] Molmo grounding accuracy for product listings**
   - Molmo consistently places "First product listing" and specific product names at extreme coordinates: (11,10), (144,767), (11,767), (555,10)
   - These coordinates are in the far-left edge or corners of the screen, not on product images
   - This is not a code bug — it's a vision model accuracy limitation
   - All 3 scenarios hit this issue; S1 only passed because space key activated the focused element by coincidence
   - **Fix options**: (a) Use a higher-accuracy grounding model, (b) Implement accessibility-based element finding as primary strategy for web pages, (c) Use CSS selectors or DOM queries via AppleScript to find elements by text/role

### P1 — Degrading Quality

2. **[P1-1] Vision verification over-rejection**
   - Gemini 2.5 Flash (verification model) frequently denies conditions that may be true
   - "Sort options are visible" denied in S2 after clicking the correct Sort button
   - "Product detail page loaded" denied in all scenarios even when navigation may have occurred
   - This causes unnecessary retries and replans, doubling or tripling execution time
   - **Fix options**: (a) Lower the verification threshold (accept first vision result if plausible), (b) Add Tier 0.5 — DOM/accessibility check before vision, (c) Reduce reliance on vision verification for common UI patterns

3. **[P1-2] No skill adaptation / Skill Librarian not creating skills from execution traces**
   - S2 expected faster execution from learned patterns — none were created
   - The skill librarian distillation pipeline is not promoting observations
   - Same issue as Cycle 2

### P2 — Efficiency

4. **[P2-1] Execution time too long**
   - S1 (only success): 717 seconds (12 minutes) for a task a human does in 30 seconds
   - Most time spent on Molmo grounding calls (~25-30s each) and vision verification (~5-8s each)
   - With 13 failed steps generating retries, overhead is massive

---

## Instrumentation Gaps
- Need screenshots at each grounding call showing the bounding box the model selected
- Need DOM/accessibility tree dump at product listing step to understand what elements are clickable
- Need Molmo confidence scores (all showing 0.75 — suspiciously uniform, likely hardcoded floor)

---

## Recommendations

1. **Short-term**: Add CSS selector / DOM query fallback for Target.com product listings (use AppleScript to query `document.querySelectorAll('.ProductCard')` or similar)
2. **Medium-term**: Implement accessibility-first element finding for web pages — use the DOM tree rather than vision grounding for clickable elements
3. **Long-term**: Upgrade grounding model (Molmo v1 3-bit is 75% accurate on ScreenSpot but struggles with complex e-commerce layouts)
