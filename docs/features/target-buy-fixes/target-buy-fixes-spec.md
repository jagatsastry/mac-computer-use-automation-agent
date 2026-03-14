# Architecture Spec: Target Buy Fixes (9 Gaps)

**Date**: 2026-03-13
**Status**: DRAFT (R7 — Short-Seller fixes: concise element descriptions, compilation tests, dependency ordering, Chrome syntax validation, Hammerspoon/S2 tests)
**Author**: Tech Lead (AI)
**PRD**: `docs/features/target-buy-fixes/target-buy-fixes-prd.md`
**SOTA**: `docs/features/target-buy-fixes/target-buy-fixes-sota.md`
**Codebase**: `docs/features/target-buy-fixes/target-buy-fixes-codebase.md`

---

## 1. Overview

Nine fixes decomposed into 3 engineer slices. All changes stay within existing component boundaries (skills, orchestrator, planner, verifier). No new components, no new dependencies, no new protocols.

### Slice Assignments

| Slice | Engineer | Fixes | Files touched |
|-------|----------|-------|---------------|
| **1** | Engineer 1 | P0-1 (skill router), P0-2 (Target buy skill), P2-1 (delete duplicates) | 6 files |
| **2** | Engineer 2 | P0-3 (plan depth), P1-1 (type_text focus), P2-3 (domain verification) | 4 files |
| **3** | Engineer 3 | P1-2 (open_url false neg), P1-3 (scroll verification), P2-2 (skill librarian) | 3 files |

### Critical Path

P0-1 + P0-2 must land before P0-3 can be tested (router must route to the new skill, and the skill must exist for truncation detection to have a fallback). P2-3 (domain verification) depends on P0-1 (site entity extraction) for the expected domain signal.

**Execution order**: Engineer 1 (Slice 1) must complete `extract_site_entity()` and `buy_on_target.md` before Engineer 2 starts P0-3 and P2-3. Engineer 2 can start P1-1 (type_text focus) immediately — it has no cross-slice dependencies. Engineer 3 (Slice 3) is fully independent.

---

## 2. Slice 1: Skill Router Fix + Target Buy Skill + Delete Duplicates

### 2.1 P0-1: Site-Aware Skill Routing

**Approach**: Entity extraction pre-pass (SOTA recommendation). A pure function extracts the site entity from the prompt before routing. The registry uses it as a hard filter on candidates.

#### 2.1.1 New Function: `extract_site_entity`

**File**: `src/automation_agent/skills/router.py`

```python
import re
from typing import Dict, List, Optional, Set

# Seed list of known e-commerce sites. Supplemented at runtime by sites
# from loaded skill metadata (see _build_known_sites).
_SEED_SITES: frozenset[str] = frozenset({
    "amazon", "target", "walmart", "bestbuy", "ebay",
    "costco", "etsy", "newegg",
})


def _build_known_sites(skills: Dict[str, "Skill"]) -> frozenset[str]:
    """Derive known sites from skill metadata + seed list.

    AC-4: The canonical list of recognized sites is derived from existing
    skill metadata (skills that declare a `site` field) plus the seed list.
    """
    sites: set[str] = set(_SEED_SITES)
    for skill in skills.values():
        skill_site = (skill.metadata.get("site") or "").lower().strip()
        if skill_site:
            sites.add(skill_site)
    return frozenset(sites)


def _build_site_patterns(known_sites: frozenset[str]) -> list[re.Pattern]:
    """Build preposition-anchored and .com regex patterns from known sites."""
    sites_alt = "|".join(re.escape(s) for s in sorted(known_sites))
    return [
        re.compile(
            r"\b(?:on|from|at)\s+(" + sites_alt + r")(?:'s)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(" + sites_alt + r")\.com\b",
            re.IGNORECASE,
        ),
    ]


def extract_site_entity(
    prompt: str,
    known_sites: Optional[frozenset[str]] = None,
) -> Optional[List[str]]:
    """Extract explicit site/store entities from the user prompt.

    Returns a list of lowercase site names (e.g., ["target"]) if explicit
    site references are found, or None if no site is specified.

    AC-4: If multiple different sites are found (e.g., "buy from amazon
    and target"), returns all of them. The caller must handle conflicts
    (return no match when len > 1).

    Only matches preposition-anchored patterns ("on target", "from amazon")
    or domain patterns ("target.com") to avoid false positives like
    "buy target gift card" where "target" is the product.
    """
    sites = known_sites or _SEED_SITES
    patterns = _build_site_patterns(sites)
    found: set[str] = set()
    for pattern in patterns:
        for match in pattern.finditer(prompt):
            found.add(match.group(1).lower())
    return sorted(found) if found else None
```

**Design decisions**:
- `_build_known_sites()` derives the known-site list from loaded skill metadata (`site` field) + seed list. Called in `_rebuild_router()` so it stays in sync as skills are loaded. New skills with `site: bestbuy` auto-register without code changes (DE gap #1).
- `extract_site_entity` returns a **list** of all matched sites, not just the first. If `len > 1`, the registry returns no match (conflicting sites). If `len == 1`, normal filtering applies (DE gap #2, AC-4 multi-site).
- `_build_site_patterns` is called with the dynamic known-sites set (not a module-level constant). **Pattern caching**: The registry stores `self._known_sites` and rebuilds it in `_rebuild_router()`. The `_build_site_patterns()` call in `extract_site_entity` compiles regex on each call (~microseconds for ~10 sites). Caching is deferred — if profiling shows it matters, the registry can cache `_site_patterns` alongside `_known_sites` in `_rebuild_router()`.
- Requires preposition anchor ("on", "from", "at") or `.com` suffix. Prevents false positives like "buy target gift card".
- Returns `None` when no site found — the caller decides what to do (fall through to normal routing). **Canonical return type is `Optional[List[str]]`**, not `list[str]`. `None` = no site detected (skip filtering), `["target"]` = one site (filter), `["amazon", "target"]` = conflict (return no match). Empty list `[]` is never returned — would be ambiguous with `None`.

#### 2.1.2 Site Filter in Registry

**File**: `src/automation_agent/skills/registry.py`

Add a `site` field to the skill frontmatter (optional). The registry reads it from `Skill.metadata.get("site")`.

In `SkillRegistryImpl.__init__`, store the known sites:

```python
self._known_sites: frozenset[str] = _SEED_SITES
```

In `_rebuild_router()`, rebuild the known sites from loaded skills:

```python
def _rebuild_router(self) -> None:
    self._known_sites = _build_known_sites(self._skills)
    # ... rest of existing rebuild ...
```

In `SkillRegistryImpl.match()`, extract sites and apply filter:

```python
async def match(self, prompt: str) -> Optional[SkillMatchResult]:
    # NEW: Extract site entities before routing
    site_entities = extract_site_entity(prompt, self._known_sites)

    # AC-4: Multiple conflicting sites → return no match
    if site_entities and len(site_entities) > 1:
        logger.info(
            "Multiple sites in prompt, returning no match",
            sites=site_entities,
        )
        return None

    site_entity = site_entities[0] if site_entities else None

    # ... existing Stage 1 (embedding) and Stage 2b (LLM) routing ...

    # After getting candidates from any stage:
    if site_entity and valid:
        valid = self._filter_by_site(valid, site_entity)
        if not valid:
            logger.info(
                "All candidates filtered by site entity",
                site_entity=site_entity,
            )
            return None

    # ... rest of existing logic ...
```

```python
def _filter_by_site(
    self,
    candidates: list[SkillRouteCandidate],
    site_entity: str,
) -> list[SkillRouteCandidate]:
    """Remove candidates whose site metadata mismatches the extracted entity.

    A skill with no site metadata is kept (generic skills are not filtered).
    A skill whose site matches the entity is kept.
    A skill whose site DIFFERS from the entity is removed entirely.
    """
    filtered = []
    for c in candidates:
        skill = self._skills.get(c.skill_id)
        if skill is None:
            continue
        skill_site = (skill.metadata.get("site") or "").lower()
        if not skill_site:
            # Generic skill (no site metadata) — keep it
            filtered.append(c)
        elif skill_site == site_entity:
            # Site matches — keep it
            filtered.append(c)
        else:
            # Site mismatch — suppress entirely
            logger.info(
                "Suppressing wrong-site candidate",
                skill_id=c.skill_id,
                skill_site=skill_site,
                expected_site=site_entity,
            )
    return filtered
```

**Design decisions**:
- Wrong-site skills are **fully suppressed**, not downgraded to analogical. Returning amazon-search as analogical for a "buy on target" prompt adds noise and no value (AC-1).
- Generic skills (no `site` field) pass through. This preserves backward compatibility.
- The filter runs AFTER LLM routing, not before. This means the LLM still sees all skills in its prompt and can make informed decisions. The filter is a post-hoc safety net.
- The keyword fallback (Stage 3) also gets filtered by the same logic — add the filter after line 391.

#### 2.1.3b AC-4b: No-Skill Graceful Degradation

When `extract_site_entity` returns a site but `_filter_by_site` removes all candidates (no skill exists for that site), the registry must return `None` — not fall through to a different-site skill. This is already the natural behavior: if `valid` is empty after filtering, `match()` returns `None`, and the planner generates a plan from scratch.

Add a log message for observability:

```python
    if site_entity and not valid:
        logger.info(
            "No skill for extracted site, falling to planner",
            site_entity=site_entity,
        )
        return None
```

This ensures P0-1 (routing fix) is not blocked by P0-2 (skill creation). Even without `buy_on_target.md`, the router correctly refuses to match `amazon-search` for a "buy on target" prompt.

#### 2.1.3 Skill Metadata: `site` Field

**File**: `src/automation_agent/skills/library/amazon_search.md` — add `site: amazon` to frontmatter

**File**: `src/automation_agent/skills/library/buy_on_target.md` — add `site: target` (new file, see P0-2)

**File**: `src/automation_agent/skills/library/return_target_order.md` — add `site: target`

The `site` field is stored in `Skill.metadata` (the catch-all dict from YAML frontmatter). No changes to `loader.py` or `models.py` needed — unknown YAML keys already go into `metadata`.

#### 2.1.4 Router Prompt Enhancement

**File**: `src/automation_agent/skills/prompts/route_skill.md`

Add to the Rules section at the bottom:

```
- CRITICAL: If the user specifies a website or store name (e.g., "on Target",
  "from Amazon", "target.com"), you MUST only match skills for that specific site
  as "direct". A skill for a DIFFERENT site must be labeled "analogical" with
  confidence below 0.3. Prefer returning no match over matching the wrong site.
```

This is a belt-and-suspenders approach: the prompt tells the LLM to avoid wrong-site matches, and the post-filter catches cases where the LLM ignores the instruction.

#### 2.1.5 Files Modified (P0-1)

| File | Change |
|------|--------|
| `src/automation_agent/skills/router.py` | Add `extract_site_entity()`, `_build_known_sites()`, `_build_site_patterns()`, `_SEED_SITES` |
| `src/automation_agent/skills/registry.py` | Add `_filter_by_site()`, call `extract_site_entity()` in `match()` |
| `src/automation_agent/skills/prompts/route_skill.md` | Add site-awareness rule |
| `src/automation_agent/skills/library/amazon_search.md` | Add `site: amazon` |
| `src/automation_agent/skills/library/return_target_order.md` | Add `site: target` |
| `tests/unit/test_router_v2.py` | New tests for site extraction and filtering |

---

### 2.2 P0-2: Target Shopping Skill

**File**: `src/automation_agent/skills/library/buy_on_target.md` (NEW)

```yaml
---
name: buy-on-target
skill-id: buy-on-target
description: Search for a product on Target.com and add it to cart
summary: Navigate to Target, search for a product, apply filters, select a product, and add to cart.
tags: [ecommerce, target, shopping, buy, cart]
trigger-keywords: [target, buy, purchase, shop, add to cart, target.com]
site: target
required-keywords: [target, target.com]
parameters:
  product:
    type: string
    required: true
    description: Product to search for
    examples: ["bed sheets", "queen-size bed sheet set", "throw pillow"]
  max_price:
    type: string
    required: false
    description: Maximum price filter (e.g., "$50", "50")
    examples: ["$50", "25"]
requires:
  apps: [Safari, Google Chrome]
  os: darwin
success-condition: Product has been added to cart and cart confirmation is visible
max-retries: 3
---

## Steps
1. Use open_url to navigate to https://www.target.com
   - verify: Target.com homepage or search page is visible
   - on_fail: If login page appears, wait_for_user to log in
2. Type "{{product}}" and press Enter
   - verify: Target search results page is visible with product listings for {{product}}
   - on_fail: If search bar not found, click on the search icon first
3. Click on the "sort by" dropdown
   - verify: Sort options are visible
   - on_fail: If sort/filter not found, scroll up to find sorting controls
4. Click on a product listing
   - verify: Product detail page is loaded with "Add to cart" button
   - on_fail: If no matching product visible, scroll down to find more options
5. Click on the "Add to cart" button
   - verify: Cart confirmation appears or cart icon badge updates
   - on_fail: If "Add to cart" not visible, scroll down to find it
6. Use done to confirm product added to cart. Checkout requires user confirmation.
   - verify:

## Error Recovery
- If login page appears: wait for user to sign in, then continue
- If CAPTCHA appears: wait for user to solve it, then continue
- If search returns no results: try a broader search term
- If product is out of stock: look for "Notify me" or select a different product
- If price is above max_price: scroll to find cheaper options or sort by price
- If "Add to cart" button is disabled: check if size/color selection is required first

## Notes
- Target uses "Add to cart" button (not "Buy now")
- Target search URL format: target.com/s?searchTerm=<query>
- Target sort by price: may need to click "Price: low to high" in sort dropdown
- Size/color selection may be required before "Add to cart" is enabled
- This skill stops at add-to-cart; checkout is out of scope
```

**CRITICAL: Skill Step Verb Patterns for `_compile_skill_instruction()`**

The compiler at `agent.py:1654-1830` only recognizes these verb patterns:

| Pattern | Compiles to |
|---------|------------|
| `Use open_url to navigate to {url}` | `open_url` step |
| `Navigate to {url_or_element}` | `open_url` (if URL) or `click` |
| `Open {app}` / `Use activate_app to open {app}` | `activate_app` step |
| `Click on/the {element}` | `click` step |
| `Find {context} and click "{button}"` | `click` step |
| `Find {element} and click it` | `click` step |
| `Type "{text}" and press Enter` | `type_text` + `press_key` steps |
| `Press {key_combo}` | `press_key` step |
| `Use done` / `done` / `Complete task` | `done` step |
| `wait for user` / `wait for the user` | `wait_for_user` step |

**Unrecognized verbs return `None`, causing `_build_skill_fallback_plan()` to return `None` (no fallback).** This means:
- "Sort by price" → `None` (BAD)
- "Select a product" → `None` (BAD)
- "Add to cart" → `None` (BAD)
- "scroll down" → `None` (no compiler pattern for scroll)

All skill steps MUST use recognized patterns. The template above uses:

| Skill Step | Instruction text (after `_parse_skill_steps` strips `N. `) | Compiler match | Compiled action |
|------------|-----------------------------------------------------------|----------------|-----------------|
| 1 | `Use open_url to navigate to https://www.target.com` | `url_match` (line 1747) | `open_url` |
| 2 | `Type "{{product}}" and press Enter` | `type_match` (line 1789) | `type_text` + `press_key` |
| 3 | `Click on the "sort by" dropdown` | `click_match` (line 1781) | `click` — element: `the "sort by" dropdown` (4 words) |
| 4 | `Click on a product listing` | `click_match` | `click` — element: `a product listing` (3 words) |
| 5 | `Click on the "Add to cart" button` | `click_match` | `click` — element: `the "Add to cart" button` (5 words) |
| 6 | `Use done to confirm product added to cart...` | `lower.startswith("use done")` (line 1665) | `done` |

**Interaction step count**: Steps 2 (type_text), 3 (click), 4 (click), 5 (click) = **4 interaction steps**. This exceeds AC-10's threshold of 3, satisfying truncation detection.

**Why step 1 uses base URL (not search URL)**: The `url_match` regex at line 1748 is `^Use open_url to navigate to\s+(https?://\S+)$`. The `\S+` stops at whitespace. If the URL contains `{{product}}` expanded to "bed sheets" (with a space), the regex truncates at the space. Using the base URL `https://www.target.com` avoids this. Step 2 handles the search via `Type "{{product}}" and press Enter`, which the compiler recognizes as `type_text` + `press_key`.

**Design decisions**:
- 5 steps (not phases): navigate, sort/filter, select product, add to cart, done. Matches skill file format conventions.
- `trigger-keywords` includes "target" alongside generic shopping terms but NOT competitor names. The `site: target` metadata handles filtering.
- `max_price` is optional — not all buy prompts specify a price constraint.
- The `done` step explicitly says "Checkout requires user confirmation" to prevent scope creep.
- Each step has `on_fail` with scroll recovery, matching existing skill patterns.
- Step 1 uses the direct search URL to skip homepage navigation (like amazon_search.md does).
- `requires.apps` lists both Safari and Google Chrome per AC-5 (customer testing used both browsers).

#### 2.2.1 AC-7: Keyword Fallback Guard

The keyword fallback in `registry.py:383-429` must require at least one Target-specific keyword ("target", "target.com") to match `buy-on-target`. Generic shopping keywords alone ("buy", "shop") must NOT be sufficient.

**Implementation**: Add a `required_keywords` field to the skill frontmatter:

```yaml
required-keywords: [target, target.com]
```

In the keyword fallback (`match_skill()` in `matcher.py`), after counting hits, check if any required keyword was hit:

```python
if skill.metadata.get("required-keywords"):
    required = set(k.lower() for k in skill.metadata["required-keywords"])
    prompt_lower = prompt.lower()
    if not any(rk in prompt_lower for rk in required):
        continue  # Skip this skill — required keyword not in prompt
```

This prevents "buy cheap bed sheets" (no "target" keyword) from matching `buy-on-target` via keyword fallback. The LLM router can still match if it infers Target intent from context, but keyword fallback requires explicit Target mention.

---

### 2.3 P2-1: Delete Duplicate Walmart Skills

**Action**: Delete two files:
- `src/automation_agent/skills/library/return-walmart-order.md`
- `src/automation_agent/skills/library/return-walmart-order-2.md`

Both are stubs with `trusted: false`. The canonical `return_walmart_order.md` remains.

No code changes. Just file deletion. These files are currently staged in git (status shows `A`).

---

### 2.4 Slice 1 Test Plan

**File**: `tests/unit/test_site_routing.py` (NEW)

Tests for `extract_site_entity`:

| Test | Input | Expected |
|------|-------|----------|
| `test_extract_on_target` | "buy bed sheets on target" | `["target"]` |
| `test_extract_from_amazon` | "order from amazon" | `["amazon"]` |
| `test_extract_at_walmart` | "shop at walmart" | `["walmart"]` |
| `test_extract_target_com` | "search target.com" | `["target"]` |
| `test_extract_possessive` | "on Target's website" | `["target"]` |
| `test_no_site_generic` | "buy cheap bed sheets" | `None` |
| `test_no_false_positive_product` | "buy target gift card" | `None` |
| `test_no_false_positive_verb` | "target the cheapest option" | `None` |
| `test_case_insensitive` | "buy sheets ON TARGET" | `["target"]` |
| `test_multi_site_conflict` | "buy from amazon and target" | `["amazon", "target"]` |
| `test_on_sale_at_target` | "buy bedsheets on sale at target" | `["target"]` (not "sale") |
| `test_dynamic_known_sites` | known_sites includes "shopify", prompt="on shopify" | `["shopify"]` |
| `test_build_known_sites_from_skills` | skill with `site: zappos` | "zappos" in known_sites |

Tests for `_filter_by_site`:

| Test | Scenario | Expected |
|------|----------|----------|
| `test_filter_removes_wrong_site` | site_entity="target", skill has site="amazon" | Candidate removed |
| `test_filter_keeps_matching_site` | site_entity="target", skill has site="target" | Candidate kept |
| `test_filter_keeps_generic_skill` | site_entity="target", skill has no site | Candidate kept |
| `test_filter_no_entity` | site_entity=None | All candidates kept |
| `test_filter_no_skill_for_site` | site_entity="bestbuy", no bestbuy skill | Returns empty (no match) |

Tests for AC-7 keyword fallback guard:

| Test | Input | Expected |
|------|-------|----------|
| `test_keyword_requires_target` | "buy bed sheets on target" | buy-on-target matches (has "target") |
| `test_keyword_no_target_no_match` | "buy cheap bed sheets" | buy-on-target does NOT match (no "target" keyword) |
| `test_keyword_target_com` | "search target.com for sheets" | buy-on-target matches (has "target.com") |

Tests for skill file validation:

| Test | Scenario | Expected |
|------|----------|----------|
| `test_buy_on_target_loads` | Load buy_on_target.md | No errors |
| `test_buy_on_target_validates` | Run validate_all() | Zero errors for buy-on-target |
| `test_buy_on_target_has_site_metadata` | Check metadata | `site` == "target" |
| `test_buy_on_target_keywords_no_amazon` | Check trigger-keywords | "amazon" NOT in keywords |
| `test_duplicate_walmart_deleted` | Check skills/library/ | return-walmart-order.md and -2.md do not exist |

Tests for skill step compilation:

| Test | Scenario | Expected |
|------|----------|----------|
| `test_buy_on_target_steps_compile` | Feed each step through `_compile_skill_instruction()` | No step returns `None` |
| `test_buy_on_target_element_descriptions_concise` | Check element param from compiled click steps | Each element description ≤ 8 words |

All tests use `_make_config(model_provider="local", grounding_model="", grounding_server_url="", _env_file=None)`.

---

## 3. Slice 2: Plan Depth + type_text Focus + Domain Verification

### 3.1 P0-3: Plan Depth — Full Buy Workflow

**Approach**: Combination of (1) planner prompt enhancement with goal-completion criteria and (2) improved truncation detection. The skill template (P0-2) provides the fallback plan depth.

#### 3.1.1 Planner Prompt Enhancement

**File**: `src/automation_agent/planner/prompts/plan_from_prompt.md`

Add after rule 9 in the CRITICAL RULES section:

```
10. **E-commerce goal completion**: For "buy", "purchase", "shop", or "add to cart" goals:
    - Opening a URL is NOT completion. Showing search results is NOT completion.
    - The plan MUST include steps through add-to-cart at minimum.
    - A complete buy plan includes: navigate → search → select product → add to cart → done.
    - Do NOT end the plan after opening a search URL.
11. **Plan depth**: When a skill template is provided as a prior, your plan MUST cover
    all phases in the skill template. Do not generate a plan shorter than the skill's
    step count unless the current screen state shows the task is partially complete.
```

#### 3.1.2 Improved Truncation Detection

**File**: `src/automation_agent/orchestrator/agent.py`

Enhance `_is_truncated_plan` to catch plans that have some interaction but are still too shallow relative to the skill.

Per AC-10, the threshold is 3 interaction steps:

```python
@staticmethod
def _is_truncated_plan(plan: ActionPlan, fallback: Optional[ActionPlan]) -> bool:
    """Return True when the LLM plan is suspiciously shorter than the skill.

    A plan is truncated if:
    1. The fallback has interaction steps but the plan has none (existing), OR
    2. The fallback has >= 3 interaction steps but the plan has < 3.
    """
    if fallback is None:
        return False
    interaction_actions = {"click", "type_text", "scroll"}
    plan_interactions = sum(
        1 for s in plan.steps if s.action in interaction_actions
    )
    fallback_interactions = sum(
        1 for s in fallback.steps if s.action in interaction_actions
    )
    # Original check: fallback has interactions, plan has none
    if fallback_interactions > 0 and plan_interactions == 0:
        return True
    # AC-10: fallback has >= 3 interactions, plan has < 3
    if fallback_interactions >= 3 and plan_interactions < 3:
        return True
    return False
```

For `buy_on_target` (3 interaction steps: sort/filter click, product click, add-to-cart click), this triggers when the LLM plan has 0, 1, or 2 interaction steps. A plan with all 3 interaction types passes.

#### 3.1.3 Files Modified (P0-3)

| File | Change |
|------|--------|
| `src/automation_agent/planner/prompts/plan_from_prompt.md` | Add rules 10-11 (e-commerce completion, plan depth) |
| `src/automation_agent/orchestrator/agent.py` | Enhance `_is_truncated_plan()` with ratio-based check |
| `tests/unit/test_planner.py` | New tests for truncation detection |

---

### 3.2 P1-1: type_text Element Focus (NameError Fix)

**Root cause**: `_dispatch_action()` at agent.py:2110-2111 references `screenshot_b64` which is not defined in that method's scope. The variable only exists in `_find_element()`.

**Fix**: Capture a screenshot at the start of the type_text branch.

**File**: `src/automation_agent/orchestrator/agent.py`

```python
elif action == "type_text":
    # Click-to-focus: if an element is specified, find and click it first
    element_desc = params.pop("element", None)
    if element_desc and not params.pop("_skip_focus", False):
        try:
            # FIX: Capture screenshot for find_element (was NameError)
            focus_screenshot = await self.coordinator.capture_screenshot()
            location = await self.coordinator.find_element(
                element_desc, screenshot_b64=focus_screenshot
            )
            if location and hasattr(location, "x") and location.x is not None:
                sx = (
                    location.screen_x
                    if location.screen_x is not None
                    else location.x
                )
                sy = (
                    location.screen_y
                    if location.screen_y is not None
                    else location.y
                )
                self.actuator.click(sx, sy)
                await asyncio.sleep(max(self.config.action_delay, 0.3))
            else:
                slog.warning(
                    "type_text element not found, typing to current focus",
                    element=element_desc,
                )
        except Exception:
            slog.warning(
                "type_text click-to-focus failed, typing to current focus",
                element=element_desc,
            )
    # ... rest of type_text handling (unchanged) ...
```

**Design decisions**:
- When `find_element` returns `None` or fails: log a **warning** (not debug) and fall through to typing at current focus. This matches option (c) from PRD review — don't fail the step, let verification catch wrong-focus issues.
- The log level change from `debug` to `warning` ensures focus failures are visible in logs for debugging.
- No confidence gating on the find_element result. The existing confidence gating in `_find_element()` (0.5 threshold, 0.9 for destructive) already handles this. The `find_element` call in `_dispatch_action` is a simpler call directly on the coordinator, not the gated `_find_element` wrapper. For click-to-focus, we accept any confidence — clicking the wrong element is recoverable (verification catches it), but not clicking at all is worse.

#### 3.2.1 Files Modified (P1-1)

| File | Change |
|------|--------|
| `src/automation_agent/orchestrator/agent.py` | Fix `screenshot_b64` → `focus_screenshot` in type_text branch, change `slog.debug` → `slog.warning` |
| `tests/unit/test_type_text_focus.py` | NEW: tests for click-to-focus behavior |

---

### 3.3 P2-3: Domain Verification

**Approach (AC-17)**: The orchestrator injects the expected domain into each `open_url` step's `verify` text before passing it to the verifier. This requires NO changes to the verifier's interface — it operates entirely through existing verify-text processing.

#### 3.3.1 Data Flow

```
User prompt → AutomationAgent.execute()
  → extract_site_entity(prompt) → "target"
  → expected_domain = "target.com"
  → For each open_url step in plan:
      → step.verify += " AND browser domain is target.com"
  → verifier._verify_tier1() sees the domain constraint in verify text
  → verifier._verify_tier1() checks browser_url domain vs expected domain
```

#### 3.3.2 Orchestrator Changes

**File**: `src/automation_agent/orchestrator/agent.py`

In `execute()`, after planning and before executing steps, inject domain constraints:

```python
from automation_agent.skills.router import extract_site_entity

# After plan is generated:
site_entities = extract_site_entity(goal)  # Returns Optional[List[str]]
if site_entities and len(site_entities) == 1:
    # IMPORTANT: Index [0] to extract the str from the List[str].
    # DO NOT use `site_entities` directly — that would produce
    # "['target'].com" garbage instead of "target.com".
    expected_domain = f"{site_entities[0]}.com"  # e.g., "target.com"
    self._inject_domain_verification(plan, expected_domain)
# Multi-site (len > 1): already handled by router returning no match.
# No domain injection — planner operates without skill constraint.
```

```python
def _inject_domain_verification(
    self, plan: ActionPlan, expected_domain: str
) -> None:
    """Append domain verification to open_url steps' verify text.

    AC-17: Ensures the browser navigates to the correct domain.
    Idempotent: skips injection if "browser domain is" already present
    (prevents verify text bloat across replans).
    """
    marker = "browser domain is"
    for step in plan.steps:
        if step.action == "open_url" and step.verify:
            if marker not in step.verify:
                step.verify = (
                    f"{step.verify} AND browser domain is {expected_domain}"
                )
```

**Mutability**: `ActionStep` is a `@dataclass` (not frozen) — `verify` is a plain `str` attribute, fully mutable. In-place mutation is safe.

**Idempotency**: The `marker` check prevents duplicate injection when the plan is replanned or the method is called multiple times. Without this, verify text would grow unbounded: `"... AND browser domain is target.com AND browser domain is target.com"`.

#### 3.3.3 Verifier Changes

**File**: `src/automation_agent/orchestrator/verifier.py`

In `_verify_tier1`, add domain extraction from the verify text and check it against the browser URL. Add this AFTER the existing URL/token matching block (before the title fallback):

```python
        # AC-17: Domain verification from verify text
        domain_match = re.search(
            r"browser domain is (\S+)", step.verify, re.IGNORECASE
        )
        if domain_match and browser_url:
            expected_domain = domain_match.group(1).lower().replace("www.", "")
            actual_domain = self._extract_base_domain(browser_url)
            # Dot-prefix check: "nottarget.com" != "target.com" and does
            # not endswith ".target.com" → rejected. "shop.target.com"
            # endswith ".target.com" → accepted.
            if not (
                actual_domain == expected_domain
                or actual_domain.endswith(f".{expected_domain}")
            ):
                return (
                    False,
                    f"Browser domain '{actual_domain}' does not match "
                    f"expected domain '{expected_domain}'",
                )
```

```python
@staticmethod
def _extract_base_domain(url: str) -> str:
    """Extract the base domain from a URL, stripping www. and protocol.

    AC-18: Normalizes for subdomain matching (shop.target.com → target.com).
    """
    parsed = urlparse(url)
    host = (parsed.netloc or parsed.path).lower().replace("www.", "")
    return host
```

**Design decisions**:
- Domain is injected into verify text, not as a verifier attribute. This follows AC-17's approach: no verifier interface changes. The verify text is the natural place for step-level assertions.
- AC-18 compliance: `_extract_base_domain` strips `www.`. Domain check uses dot-prefix guard: `actual == expected or actual.endswith(f".{expected}")`. This rejects `nottarget.com` (not equal, doesn't end with `.target.com`) while accepting `shop.target.com` (ends with `.target.com`).

- The domain check runs AFTER the URL/token match succeeds. Even if URL tokens match, a domain mismatch fails the step.
- For prompts with no site entity, no injection happens → no domain check → no regression.
- The regex `r"browser domain is (\S+)"` is a simple, specific pattern unlikely to collide with natural verify text.
- The `import re` is already present in `verifier.py` (from `urlparse` usage).

#### 3.3.4 Files Modified (P2-3)

| File | Change |
|------|--------|
| `src/automation_agent/orchestrator/agent.py` | Add `_inject_domain_verification()`, call in `execute()` |
| `src/automation_agent/orchestrator/verifier.py` | Add domain regex check in `_verify_tier1`, `_extract_base_domain()` |
| `tests/unit/test_verifier.py` | New tests for domain verification |

---

### 3.4 Slice 2 Test Plan

**File**: `tests/unit/test_planner.py` (MODIFIED — add tests)

| Test | Scenario | Expected |
|------|----------|----------|
| `test_truncated_plan_no_interactions` | Plan has 0 interactions, fallback has 3 | `True` |
| `test_truncated_plan_shallow` | Plan has 1 interaction, fallback has 6 | `True` (1 < 6//2 = 3) |
| `test_not_truncated_plan_sufficient` | Plan has 3 interactions, fallback has 3 | `False` |
| `test_not_truncated_no_fallback` | fallback is None | `False` |

**File**: `tests/unit/test_type_text_focus.py` (NEW)

| Test | Scenario | Expected |
|------|----------|----------|
| `test_type_text_clicks_element_first` | type_text with element="search bar" | `click()` called before `type_text()` |
| `test_type_text_captures_screenshot` | type_text with element | `capture_screenshot()` called |
| `test_type_text_no_element_no_click` | type_text without element | `click()` NOT called |
| `test_type_text_find_element_fails_fallthrough` | find_element returns None | `type_text()` still called |
| `test_type_text_find_element_exception_fallthrough` | find_element raises | `type_text()` still called |
| `test_type_text_skip_focus_flag` | type_text with _skip_focus=True | `click()` NOT called |

**File**: `tests/unit/test_verifier.py` (MODIFIED — add tests)

| Test | Scenario | Expected |
|------|----------|----------|
| `test_domain_match_target` | verify contains "browser domain is target.com", URL="www.target.com/s?q=..." | Pass |
| `test_domain_mismatch_amazon` | verify contains "browser domain is target.com", URL="www.amazon.com/s?k=..." | Fail |
| `test_domain_subdomain_match` | verify contains "browser domain is target.com", URL="shop.target.com/..." | Pass |
| `test_domain_no_constraint` | verify does NOT contain "browser domain is", URL="amazon.com" | Inconclusive (None) — no domain check |
| `test_domain_nottarget_rejected` | verify contains "browser domain is target.com", URL="nottarget.com" | Fail |
| `test_extract_base_domain` | "https://www.target.com/s?q=sheets" | "target.com" |
| `test_extract_base_domain_subdomain` | "https://shop.target.com/p/123" | "shop.target.com" |
| `test_inject_domain_verification` | open_url step with verify="Target page visible" | verify becomes "Target page visible AND browser domain is target.com" |
| `test_inject_domain_no_site` | no site entity extracted | verify text unchanged |
| `test_inject_domain_idempotent` | `_inject_domain_verification` called twice on same plan | verify text contains exactly one "browser domain is" clause |
| `test_inject_domain_type_safety` | `extract_site_entity` returns `["target"]` | `expected_domain` is `"target.com"` (str), NOT `"['target'].com"` |

All tests use `_make_config(model_provider="local", grounding_model="", grounding_server_url="", _env_file=None)`.

---

## 4. Slice 3: open_url False Negative + Scroll Verification + Skill Librarian

### 4.1 P1-2: open_url Screenshot-Diff False Negative

**Root cause**: At agent.py:1077-1081, when `screenshot_diff.screen_changed()` returns `False` for an `open_url` action, `actuator_result["success"]` is overwritten to `False`. This prevents verification from running (line 1086 returns early).

**Fix**: For `open_url` actions, store the diff result as metadata but do NOT override `actuator_result["success"]`. Let verification decide.

**File**: `src/automation_agent/orchestrator/agent.py`

Replace lines 1059-1081:

```python
        if (
            self.screenshot_diff
            and step.action in ("click", "open_url")
            and actuator_result.get("success", False)
        ):
            await asyncio.sleep(0.3)
            if step.action == "click":
                click_x = actuator_result.get(
                    "image_x", actuator_result.get("x", step.params.get("x", 0))
                )
                click_y = actuator_result.get(
                    "image_y", actuator_result.get("y", step.params.get("y", 0))
                )
                visible_effect = (
                    self.screenshot_diff.region_changed(click_x, click_y)
                    or self.screenshot_diff.screen_changed()
                )
            else:
                visible_effect = self.screenshot_diff.screen_changed()

            self._last_step_pixel_changed = bool(visible_effect)

            if not visible_effect:
                if step.action == "open_url":
                    # P1-2 FIX: Don't override success for open_url.
                    # Store as metadata; let Tier 1 URL verification decide.
                    actuator_result["_no_visible_change"] = True
                    slog.info(
                        "open_url had no visible effect, deferring to verification",
                        url=step.params.get("url", ""),
                    )
                else:
                    actuator_result["success"] = False
                    actuator_result["error"] = (
                        f"{step.action} had no visible effect "
                        f"(screenshot unchanged)"
                    )
```

**Design decisions**:
- Only `open_url` is exempted from the diff-based failure. `click` actions still fail on no visible effect — that's correct behavior for clicks.
- The `_no_visible_change` metadata is available for logging/debugging but doesn't affect the control flow.
- The `open_url` action proceeds to verification (Tier 1 checks URL, Tier 2 checks vision). If the page is already correct, Tier 1 passes. If the page is wrong, Tier 1/2 fails.

#### 4.1.1 Files Modified (P1-2)

| File | Change |
|------|--------|
| `src/automation_agent/orchestrator/agent.py` | Exempt `open_url` from diff-based success override |
| `tests/unit/test_open_url_verification.py` | NEW: tests for open_url diff handling |

---

### 4.2 P1-3: Scroll Verification via JavaScript scrollY

**Approach**: Add a JavaScript `window.scrollY` check to the verifier's Tier 1 for scroll actions. Use `do JavaScript` in Safari via AppleScript. Fall back to actuator success for non-Safari browsers.

#### 4.2.1 Actuator: Add `get_scroll_position`

**File**: `src/automation_agent/actuator/applescript_actuator.py`

```python
def get_scroll_position(self) -> Optional[int]:
    """Get the current scroll position (window.scrollY) from the frontmost browser.

    Supports Safari and Google Chrome via their respective AppleScript APIs.
    Returns the integer scrollY value, or None if not in a browser or
    JavaScript execution fails.
    """
    script = '''
    tell application "System Events"
        set frontApp to name of first application process whose frontmost is true
    end tell
    if frontApp is "Safari" then
        tell application "Safari"
            set scrollY to do JavaScript "window.scrollY" in current tab of front window
            return scrollY
        end tell
    else if frontApp is "Google Chrome" then
        tell application "Google Chrome"
            set scrollY to execute front window's active tab javascript "window.scrollY"
            return scrollY
        end tell
    else
        return "N/A"
    end if
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0 or result.stdout.strip() == "N/A":
            return None
        return int(float(result.stdout.strip()))
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return None
```

**Design decisions**:
- Supports both Safari (`do JavaScript`) and Chrome (`execute ... javascript`) per AC-14 Tier S1. **Chrome possessive syntax validated**: `osascript -e 'tell application "Google Chrome" to execute front window'\''s active tab javascript "1+1"'` compiles and runs (fails only on permission, not syntax). Both browsers require "Allow JavaScript from Apple Events" enabled — `get_scroll_position()` returns `None` on permission error → falls to Tier S2/S3.
- Non-browser apps return "N/A" → `None` → falls through to Tier S2/S3.
- **HammerspoonActuator path**: `HammerspoonActuator` does not implement `get_scroll_position()`. The `getattr(actuator, "get_scroll_position", None)` guard in the verifier returns `None` → Tier S1 skipped → falls to Tier S2 pixel-diff or Tier S3 actuator success.
- 3-second timeout prevents hangs on unresponsive browsers.

#### 4.2.2 Verifier: Tier 1 Scroll Check

**File**: `src/automation_agent/orchestrator/verifier.py`

Add scroll handling in `_verify_tier1`, after the `type_text` check:

```python
        if step.action == "scroll":
            # AC-14 Tier S1: JavaScript scrollY delta (Safari + Chrome)
            get_scroll = getattr(actuator, "get_scroll_position", None)
            if get_scroll is not None:
                scroll_after = get_scroll()
                scroll_before = actuator_result.get("_scroll_y_before")
                if scroll_after is not None and scroll_before is not None:
                    delta = scroll_after - scroll_before
                    direction = step.params.get("direction", "down")
                    if direction == "down" and delta > 0:
                        return (
                            True,
                            f"Scroll down confirmed: scrollY {scroll_before} → {scroll_after} "
                            f"(delta {delta})",
                        )
                    elif direction == "up" and delta < 0:
                        return (
                            True,
                            f"Scroll up confirmed: scrollY {scroll_before} → {scroll_after} "
                            f"(delta {delta})",
                        )
                    elif delta == 0:
                        # Page at boundary — inconclusive, fall to Tier S2
                        pass
                    else:
                        # Scrolled in wrong direction — inconclusive
                        pass

            # AC-14 Tier S2: Screenshot pixel-diff
            # When screenshot_diff is None (dry-run, unconfigured), the key
            # is absent → .get() returns None → this block is skipped → Tier S3.
            pixel_changed = actuator_result.get("_scroll_pixel_changed")
            if pixel_changed is True:
                return (
                    True,
                    "Scroll confirmed: screenshot pixel-diff detected change",
                )

            # AC-14 Tier S3: Actuator success fallback
            if actuator_result.get("success", False):
                return (
                    True,
                    "Scroll accepted: actuator reported success (no JS or pixel signal)",
                )

            return None
```

#### 4.2.3 Orchestrator: Capture scrollY Before Scroll

**File**: `src/automation_agent/orchestrator/agent.py`

In `_dispatch_action`, in the `scroll` branch, capture scrollY before dispatching and pixel-diff after:

```python
            elif action == "scroll":
                # AC-14 Tier S1: Capture scrollY before scroll
                get_scroll = getattr(self.actuator, "get_scroll_position", None)
                scroll_before = get_scroll() if get_scroll is not None else None

                # AC-14 Tier S2: Capture screenshot before scroll for pixel-diff.
                # NOTE: self.screenshot_diff may be None (e.g., dry-run mode,
                # or when ScreenshotDiff is not configured). When None, the
                # `_scroll_pixel_changed` key is NOT set in result, and the
                # verifier's Tier S2 check (`actuator_result.get(...)`) returns
                # None → falls through to Tier S3 (actuator success).
                if self.screenshot_diff:
                    self.screenshot_diff.capture_before()

                direction = params.get("direction", "down")
                # ... existing scroll dispatch code ...

                # Store scrollY_before in result for verifier
                if scroll_before is not None:
                    result["_scroll_y_before"] = scroll_before

                # AC-14 Tier S2: Check pixel-diff after scroll
                if self.screenshot_diff:
                    await asyncio.sleep(0.5)  # Wait for scroll to settle
                    pixel_changed = self.screenshot_diff.screen_changed()
                    result["_scroll_pixel_changed"] = bool(pixel_changed)
                # When screenshot_diff is None: no _scroll_pixel_changed key
                # → verifier Tier S2 `.get()` returns None → falls to Tier S3
```

**Design decisions**:
- **3-tier chain (AC-14)**: Tier S1 (JS scrollY) → Tier S2 (pixel-diff) → Tier S3 (actuator success). Each tier is tried in order; first conclusive result wins.
- JavaScript scrollY works for both Safari and Chrome (see `get_scroll_position` above).
- `delta == 0` is **inconclusive** (not failure). The page may be at the top/bottom boundary. Falls through to Tier S2 pixel-diff.
- Pixel-diff (Tier S2) reuses the existing `screenshot_diff` infrastructure. The pre-scroll screenshot is captured before dispatch, then compared after a 0.5s settle delay.
- Tier S3 (actuator success) is the weakest signal but prevents the current always-fail behavior. The actuator's `scroll()` returns `{"success": True}` if the OS scroll event dispatched.
- The `_scroll_y_before` and `_scroll_pixel_changed` values are passed through `actuator_result` (a dict) to the verifier. No API changes needed.
- `get_scroll_position()` has a 3-second timeout. If it hangs, returns None → Tier S1 skipped → falls to Tier S2/S3.
- Non-browser contexts (Finder, Settings): `get_scroll_position` returns None → Tier S1 skipped → Tier S2 pixel-diff is primary → Tier S3 actuator fallback.

#### 4.2.4 Files Modified (P1-3)

| File | Change |
|------|--------|
| `src/automation_agent/actuator/applescript_actuator.py` | Add `get_scroll_position()` |
| `src/automation_agent/orchestrator/verifier.py` | Add scroll check in `_verify_tier1` |
| `src/automation_agent/orchestrator/agent.py` | Capture scrollY before scroll dispatch |
| `tests/unit/test_scroll_verification.py` | NEW: tests for scrollY-based verification |

---

### 4.3 P2-2: Skill Librarian Activation

**Approach**: Two changes — (1) enable librarian by default, (2) lower thresholds for first-run promotion.

#### 4.3.1 Config Changes

**File**: `src/automation_agent/config.py`

```python
    skill_librarian_enabled: bool = Field(
        default=True,  # Changed from False
        description="Promote high-confidence observations into canonical skills",
    )
    skill_librarian_min_confidence: float = Field(
        default=0.5,  # Changed from 0.7 — fixes dead zone
        description="Minimum Bayesian score for promotion",
        gt=0.0,
        le=1.0,
    )
    skill_librarian_min_observations: int = Field(
        default=2,  # Changed from 5
        description="Minimum observation count before promotion",
        gt=0,
    )
    skill_librarian_min_runs: int = Field(
        default=1,  # Changed from 3
        description="Minimum distinct run_ids before promotion",
        gt=0,
    )
```

**Dead zone fix (AC-16b)**: `learn_from_run` caps observation confidence at 0.6 for replan runs (line 616: `obs.confidence = min(obs.confidence, 0.6)`). With `min_confidence=0.7`, replan observations could never reach promotion threshold — a dead zone. Lowering to 0.5 allows replan observations (capped at 0.6) to pass the promotion gate.

#### 4.3.2 Learning Trigger Fix

**File**: `src/automation_agent/skills/registry.py`

The `_trace_deserves_learning` method returns `False` for clean runs without retries. But `learn_from_run` already has a separate `had_replan` check (line 599). The issue is that `_trace_deserves_learning` is too strict — a run with replans DOES deserve learning, and this is already handled by the `had_replan` short-circuit at line 599.

Verify the existing code path: `learn_from_run` line 599 says:
```python
if not had_replan and not self._trace_deserves_learning(trace):
    return []
```

This means: if `had_replan=True`, learning happens regardless of `_trace_deserves_learning`. If `had_replan=False`, `_trace_deserves_learning` must return `True`. This is correct behavior — we DON'T want to learn from perfectly clean runs (nothing to learn). We DO want to learn from runs with replans (the replan trajectory is valuable).

**No code change needed here.** The bug was that `skill_librarian_enabled` defaults to `False`, preventing the librarian from being instantiated at all. Changing it to `True` (4.3.1) and lowering thresholds is sufficient.

However, verify that the `_trace_deserves_learning` gate doesn't block the *librarian's* `evaluate_run`. Check `promote_from_run` in registry.py line 624-650: it delegates directly to `self._librarian.evaluate_run()` with no `_trace_deserves_learning` gate. Good — the librarian evaluation is independent of the learning gate.

#### 4.3.3 Files Modified (P2-2)

| File | Change |
|------|--------|
| `src/automation_agent/config.py` | `skill_librarian_enabled` → `True`, `min_confidence` → 0.5, `min_observations` → 2, `min_runs` → 1 |
| `tests/unit/test_skill_librarian.py` | Update tests that assert `enabled=False` default |

---

### 4.4 Slice 3 Test Plan

**File**: `tests/unit/test_open_url_verification.py` (NEW)

| Test | Scenario | Expected |
|------|----------|----------|
| `test_open_url_no_diff_proceeds_to_verification` | open_url with no visible change | `actuator_result["success"]` stays True |
| `test_open_url_no_diff_stores_metadata` | open_url with no visible change | `_no_visible_change` is True |
| `test_click_no_diff_still_fails` | click with no visible change | `actuator_result["success"]` set to False |

**File**: `tests/unit/test_scroll_verification.py` (NEW)

| Test | Scenario | Expected |
|------|----------|----------|
| `test_scroll_down_js_confirmed` | scrollY 0 → 500 | Tier S1 pass, evidence shows delta |
| `test_scroll_up_js_confirmed` | scrollY 500 → 200 | Tier S1 pass |
| `test_scroll_boundary_falls_to_pixel` | scrollY 1000 → 1000, pixel_changed=True | Tier S2 pass |
| `test_scroll_boundary_falls_to_actuator` | scrollY delta=0, no pixel change, actuator success | Tier S3 pass |
| `test_scroll_no_js_falls_to_pixel` | get_scroll_position=None, pixel_changed=True | Tier S2 pass |
| `test_scroll_no_signal_actuator_fallback` | no JS, no pixel change, actuator success | Tier S3 pass |
| `test_scroll_before_captured` | scroll dispatched | result contains `_scroll_y_before` |
| `test_scroll_pixel_diff_captured` | scroll with screenshot_diff | result contains `_scroll_pixel_changed` |
| `test_scroll_no_screenshot_diff` | scroll with `self.screenshot_diff=None` | Tier S2 skipped (no `_scroll_pixel_changed` key), falls to Tier S3 actuator |
| `test_get_scroll_position_safari` | frontmost app is Safari | Returns int scrollY |
| `test_get_scroll_position_chrome` | frontmost app is Chrome | Returns int scrollY |
| `test_get_scroll_position_non_browser` | frontmost app is Finder | Returns None |
| `test_get_scroll_position_timeout` | osascript times out | Returns None |
| `test_get_scroll_position_permission_denied` | Chrome/Safari returns JS permission error | Returns None (not crash) |
| `test_scroll_hammerspoon_actuator_skips_s1` | Actuator lacks `get_scroll_position` attr | Tier S1 skipped, falls to Tier S2/S3 |
| `test_scroll_pixel_diff_false_positive` | pixel_changed=True but scrollY delta=0 (e.g., animation) | Tier S1 inconclusive (delta=0), Tier S2 returns True (accepted — pixel diff is best available signal when JS says boundary) |

**File**: `tests/unit/test_skill_librarian.py` (MODIFIED)

| Test | Change |
|------|--------|
| `test_librarian_enabled_by_default` | Assert `AgentConfig().skill_librarian_enabled is True` |
| `test_librarian_lower_thresholds` | Assert min_observations=2, min_runs=1 |
| `test_librarian_min_confidence_lowered` | Assert min_confidence=0.5 (was 0.7 — dead zone fix) |
| `test_replan_obs_above_threshold` | Observation with conf=0.6 (replan cap) passes min_confidence=0.5 gate |

All tests use `_make_config(model_provider="local", grounding_model="", grounding_server_url="", _env_file=None)`.

---

## 5. Cross-Cutting Concerns

### 5.1 Backward Compatibility

| Change | Risk | Mitigation |
|--------|------|------------|
| Site entity extraction | "buy X" with no site could match buy-on-target on keyword "buy" | `extract_site_entity` returns None → no filter → existing routing unchanged |
| buy-on-target keyword "buy" | Could match generic "buy X" prompts via keyword fallback | Keyword fallback confidence = hits/total. "buy" alone = 1/6 = 0.17 < 0.5 threshold → won't match |
| Planner prompt rules 10-11 | Could make non-shopping plans longer than needed | Rules are scoped to "buy/purchase/shop/add to cart" goals only |
| Librarian enabled by default | Could create unwanted skills from every run | Librarian still requires observations (from learning), which requires replans/retries. Clean runs won't trigger |
| open_url diff exemption | Could miss real open_url failures | Tier 1 URL verification catches real failures; diff was causing more false negatives than it caught |
| Scroll Tier S3 actuator fallback | Could accept failed scrolls | Only used when both JS scrollY and pixel-diff are unavailable; better than always-deny |
| Domain verify injection | Could pollute verify text | Uses specific pattern `"browser domain is X"` unlikely to collide; only injected for open_url steps |

### 5.1b AC-19: Regression Safety

All 522 existing tests must pass after all changes. Specifically:
- `tests/unit/test_router_v2.py` — existing routing tests must pass (site filter is additive)
- `tests/unit/test_verifier.py` — existing URL verification tests must pass (domain check is additive)
- `tests/unit/test_planner.py` — existing plan validation tests must pass (truncation check is additive)
- `tests/unit/test_skill_librarian.py` — tests asserting `enabled=False` default must be updated to `True`
- `tests/unit/test_scroll_action.py` — existing scroll tests must pass (Tier S1 is additive to Tier 1)
- `tests/integration/test_scroll_replan_integration.py` — scroll recovery tests must pass

Engineers must run `pytest tests/unit/ tests/integration/ -x -q` before submitting code for adversary review.

### 5.2 Error Messages

All new error messages follow existing patterns (lowercase start, include actual vs expected values):

| Error | Message |
|-------|---------|
| Domain mismatch | `"Browser domain '{actual}' does not match expected domain '{expected}'"` |
| Site filter suppression | Log: `"Suppressing wrong-site candidate"` with skill_id, skill_site, expected_site |
| type_text focus failure | Warning: `"type_text element not found, typing to current focus"` |
| open_url no diff | Info: `"open_url had no visible effect, deferring to verification"` |

### 5.3 Test Infrastructure

- All new test files use `_make_config(model_provider="local", grounding_model="", grounding_server_url="", _env_file=None)`
- All mocks follow `conftest.py` patterns (AsyncMock for async, MagicMock for sync)
- Line length 100 (Black + Ruff), Python 3.11, pytest-asyncio asyncio_mode="auto"
- Test command: `.venv/bin/python -m pytest tests/unit/ -x -q`

### 5.4 Integration Points Between Slices

| Dependency | Provider | Consumer | Interface |
|------------|----------|----------|-----------|
| `extract_site_entity()` | Slice 1 (router.py) | Slice 2 (agent.py, for domain verification) | `extract_site_entity(prompt, known_sites) → Optional[List[str]]` |
| `buy_on_target.md` skill | Slice 1 | Slice 2 (truncation detection needs a skill with interactions) | Skill file in `skills/library/` |
| `site` metadata in skills | Slice 1 (amazon_search.md, buy_on_target.md) | Slice 1 (_filter_by_site) | `Skill.metadata["site"]` |
| `required-keywords` metadata | Slice 1 (buy_on_target.md) | Slice 1 (matcher.py) | `Skill.metadata["required-keywords"]` |
| Domain verify injection | Slice 2 (agent.py `_inject_domain_verification`) | Slice 2 (verifier.py domain regex check) | verify text contains `"browser domain is {domain}"` |
| `get_scroll_position()` | Slice 3 (actuator) | Slice 3 (verifier, agent) | `actuator.get_scroll_position() → Optional[int]` |
| `_scroll_y_before` | Slice 3 (agent dispatch) | Slice 3 (verifier Tier 1) | `actuator_result["_scroll_y_before"]` |
| `_scroll_pixel_changed` | Slice 3 (agent dispatch) | Slice 3 (verifier Tier 1) | `actuator_result["_scroll_pixel_changed"]` |

Slices 1 and 2 share `extract_site_entity` — Engineer 1 writes it, Engineer 2 imports it. No circular dependencies: `router.py` (skills package) is imported by `agent.py` (orchestrator package), which is the existing import direction.

Slice 3 is fully self-contained — no dependencies on Slices 1 or 2.

---

## 6. File Summary

### New Files
| File | Slice | Description |
|------|-------|-------------|
| `src/automation_agent/skills/library/buy_on_target.md` | 1 | Target shopping skill |
| `tests/unit/test_site_routing.py` | 1 | Site extraction + filter tests |
| `tests/unit/test_type_text_focus.py` | 2 | type_text click-to-focus tests |
| `tests/unit/test_open_url_verification.py` | 3 | open_url diff exemption tests |
| `tests/unit/test_scroll_verification.py` | 3 | scrollY-based verification tests |

### Modified Files
| File | Slice | Changes |
|------|-------|---------|
| `src/automation_agent/skills/router.py` | 1 | `extract_site_entity()`, `_KNOWN_SITES`, `_SITE_PATTERNS` |
| `src/automation_agent/skills/registry.py` | 1 | `_filter_by_site()`, site filter in `match()`, AC-4b no-skill fallback |
| `src/automation_agent/skills/matcher.py` | 1 | AC-7 required-keywords check in keyword fallback |
| `src/automation_agent/skills/prompts/route_skill.md` | 1 | Site-awareness rule |
| `src/automation_agent/skills/library/amazon_search.md` | 1 | Add `site: amazon` |
| `src/automation_agent/skills/library/return_target_order.md` | 1 | Add `site: target` |
| `src/automation_agent/planner/prompts/plan_from_prompt.md` | 2 | Rules 10-11 |
| `src/automation_agent/orchestrator/agent.py` | 2, 3 | type_text fix, domain setup, scrollY capture, open_url diff exemption |
| `src/automation_agent/orchestrator/verifier.py` | 2, 3 | Domain verification, scroll Tier 1 |
| `src/automation_agent/actuator/applescript_actuator.py` | 3 | `get_scroll_position()` |
| `src/automation_agent/config.py` | 3 | Librarian defaults |
| `tests/unit/test_router_v2.py` | 1 | Site routing tests |
| `tests/unit/test_planner.py` | 2 | Truncation detection tests |
| `tests/unit/test_verifier.py` | 2 | Domain verification tests |
| `tests/unit/test_skill_librarian.py` | 3 | Updated default assertions |

### Deleted Files
| File | Slice |
|------|-------|
| `src/automation_agent/skills/library/return-walmart-order.md` | 1 |
| `src/automation_agent/skills/library/return-walmart-order-2.md` | 1 |

---

## 7. SOTA Approach Justification

| Fix | SOTA Chosen | Why |
|-----|-------------|-----|
| P0-1 | Entity extraction pre-pass | Simplest, deterministic, no new deps. Semantic router (Aurelio) is overkill for ~10 skills |
| P0-2 | Skyvern phase pattern | Structured phases with per-phase verification maps directly to our skill file format |
| P0-3 | Goal-completion criteria + skill scaffold + ratio-based truncation | Three complementary layers: prompt tells LLM what "done" means, skill provides structure, truncation detector catches LLM laziness |
| P1-1 | Click-to-focus before keystroke | Universal macOS pattern. Simpler than AXFocused or accessibility tree targeting |
| P1-2 | URL-based verification primary | For open_url, URL match is the ground truth. Screenshot diff adds noise for already-loaded pages |
| P1-3 | JavaScript scrollY delta | Deterministic, fast (~10ms), no vision model needed. IntersectionObserver is overkill |
| P2-1 | Delete duplicates | Trivial housekeeping |
| P2-2 | Enable + lower thresholds | Feature exists, just gated too aggressively. No new code needed beyond config changes |
| P2-3 | URL domain check guardrail (AGrail pattern) | Simple, high-value safety check. Invariant Labs' two-layer pattern is heavier than needed |

---

## 8. Specialist Agents Required

| Agent | Slice | Role |
|-------|-------|------|
| **Adversary 1** | 1 | Review site extraction edge cases, skill file format, filter logic |
| **Adversary 2** | 2 | Review truncation thresholds, type_text focus error handling, domain verification data flow |
| **Adversary 3** | 3 | Review open_url exemption scope, scrollY timeout/error handling, librarian threshold trade-offs |

Each adversary must run at least 3 review rounds per the team protocol.
