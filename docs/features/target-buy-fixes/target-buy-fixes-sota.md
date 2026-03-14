# SOTA Research: Target Buy Fixes (9 Gaps)

**Date**: 2026-03-13
**Status**: RECOMMENDATION: PROCEED
**Scope**: 9 gaps from Target "buy bedsheets" customer testing (0/3 pass rate)

---

## Table of Contents

1. [P0-1: Skill Router Mismatches Amazon for Target Prompts](#p0-1-skill-router-mismatches)
2. [P0-2: No Target Shopping Skill](#p0-2-no-target-shopping-skill)
3. [P0-3: Plans Too Shallow](#p0-3-plans-too-shallow)
4. [P1-1: type_text Doesn't Focus Element](#p1-1-type_text-focus)
5. [P1-2: open_url False Negative](#p1-2-open_url-false-negative)
6. [P1-3: Scroll Verification Always Fails](#p1-3-scroll-verification)
7. [P2-1: Duplicate Walmart Skill Files](#p2-1-duplicate-walmart-skills)
8. [P2-2: Skill Librarian Not Promoting](#p2-2-skill-librarian)
9. [P2-3: Domain Verification Missing](#p2-3-domain-verification)
10. [Cross-Cutting Analysis: Fix Interactions](#cross-cutting)
11. [Risk Assessment](#risk-assessment)

---

## P0-1: Skill Router Mismatches Amazon for Target Prompts {#p0-1-skill-router-mismatches}

### Problem Landscape

**Codebase evidence**: `amazon_search.md` has `trigger-keywords: [amazon, buy, cheapest, shop, purchase, price, product]` (`skills/library/amazon_search.md:7`). The keyword `buy` matches any purchase prompt regardless of target site.

Two routing paths exist:
1. **LLM router** (`skills/router.py`): Sends all skill cards + user prompt to LLM, gets JSON matches. The router prompt (`skills/prompts/route_skill.md`) says "If a skill is structurally similar but for a different site, label it 'analogical'" — but has NO instruction to extract the user's specified site and use it as a hard constraint.
2. **Keyword matcher** (`skills/matcher.py`): Pure keyword overlap scoring. "buy bed sheet on target" matches `amazon_search` on "buy" (1 hit) and `return_target_order` on "target" (1 hit) — tied, so whichever is iterated first wins. No site-awareness at all.

### SOTA Approaches (Ranked by Relevance)

#### 1. Two-Stage Entity Extraction + Filtering (RECOMMENDED)

**How it works**: Before routing, extract explicit site/store entities from the user prompt. Use this entity as a **hard constraint** that filters candidate skills before scoring.

**Why NOT naive regex**: Simple patterns like `"on {site}"`, `"from {site}"` fail on edge cases:

| User Prompt | Naive `"on {site}"` Extracts | Correct Entity |
|---|---|---|
| "buy bedsheets on sale at target" | "sale" (false positive from "on sale") | "target" |
| "target bed sheets under $50" | nothing (no preposition) | "target" |
| "go to target.com and buy sheets" | nothing (URL form) | "target" |
| "buy sheets from target's website" | "target's" (possessive) | "target" |
| "buy cheap sheets" | nothing | none (ambiguous) |

**Recommended implementation — known-site dictionary approach**:
```python
# Maintain a dictionary of known site names from skill metadata
KNOWN_SITES = {"amazon", "target", "walmart", "google", "yelp", "opentable"}

def extract_site_entity(prompt: str) -> Optional[str]:
    prompt_lower = prompt.lower()
    # 1. Check for domain URLs: "target.com", "amazon.com"
    for site in KNOWN_SITES:
        if f"{site}.com" in prompt_lower:
            return site
    # 2. Check for known site names as whole words (not substrings)
    # Using word boundary check to avoid "targeting" -> "target"
    import re
    for site in KNOWN_SITES:
        if re.search(rf'\b{re.escape(site)}\b', prompt_lower):
            return site
    return None
```

**Why dictionary-based, not NER**: We have ~10 known sites from skill metadata. NER is overkill, introduces model dependency, and is slower. The known-site dictionary is:
- Deterministic and testable
- Auto-derived from skill metadata (each skill's domain/name)
- Zero false positives on known ambiguities ("on sale" can't match — "sale" isn't a known site)
- The word boundary `\b` regex prevents "targeting" from matching "target"

**Tradeoff analysis**:
| Approach | Pros | Cons | Recommendation |
|---|---|---|---|
| Known-site dictionary + `\b` | Deterministic, no dependencies, zero FP on known sites | Only matches sites with skills | **Use this** |
| NER (spaCy/NLTK) | Catches novel site names | Heavy dependency, slower, noisy | Not needed for <20 skills |
| LLM entity extraction | Handles any phrasing | Slow (1-3s), costs tokens | Already failing; fix prompt instead |
| Semantic Router (Aurelio) | Handles paraphrases at scale | Overkill for <20 skills, new dependency | Future if >50 skills |

**Fallback when entity extraction finds nothing**: Fall through to existing LLM router + keyword matcher (no change to behavior for ambiguous prompts like "buy cheap sheets").

**Fallback when entity extraction finds a site but no matching skill exists**: Log warning, fall through to LLM router (which may find an analogical match on another site's skill, e.g., use amazon_search as template for target).

#### 2. LLM Router Prompt Fix (COMPLEMENTARY)

The current router prompt (`skills/prompts/route_skill.md:39`) says: "If a skill is structurally similar but for a different site, label it 'analogical'." This is insufficient — it doesn't tell the LLM to prioritize the user's explicit site mention.

**Add to router prompt**:
```
CRITICAL: If the user explicitly names a website or store (e.g., "on Target", "from Amazon",
"target.com"), you MUST only match skills for that specific site. If no skill exists for
that site, return {"matches": []} rather than matching a skill for a different site.
A skill for Amazon is NOT a match when the user says "on Target", even as analogical.
```

### Recommended Approach

**Both changes together**:
1. `_extract_site_entity(prompt)` using known-site dictionary derived from skill metadata
2. If entity found, filter `skills` dict to only skills matching that domain before calling LLM router or keyword matcher
3. Fix LLM router prompt to explicitly respect site entities
4. Keyword matcher (`matcher.py`) stays as-is — filtering happens before it's called

---

## P0-2: No Target Shopping Skill {#p0-2-no-target-shopping-skill}

### Problem Landscape

Only `return_target_order.md` exists for Target. No buy/search skill. The agent literally cannot execute a Target purchase flow even if routing works correctly.

### SOTA Approaches

#### 1. Skyvern's Purchase Workflow Pattern (RECOMMENDED)

Skyvern breaks purchasing into structured phases: navigate -> search -> filter -> select -> add-to-cart -> checkout. Their Planner Agent decomposes these into individual delegated tasks. Key insight: **each phase has its own verification criteria**.

**Skill template pattern for Target**:
```
Phase 1: Navigate to target.com
  verify: URL contains "target.com"

Phase 2: Search for product
  verify: Search results visible, product listings appear

Phase 3: Apply filters (price range, ratings, etc.)
  verify: Filter applied, results updated

Phase 4: Select product
  verify: Product detail page loaded

Phase 5: Add to cart
  verify: Cart badge updated, confirmation shown

Phase 6: (Optional) Proceed to checkout
  verify: Checkout page loaded
```

#### 2. Agent-E's Domain Skill Primitives

Agent-E defines domain-specific primitive skills reusable across similar tasks. For e-commerce, primitives include: `navigate_to_site`, `search_product`, `apply_filter`, `select_item`, `add_to_cart`, `checkout`. Each primitive has its own DOM distillation strategy.

**Key insight**: The skill should define **what to verify at each step**, not just what to do. Agent-E's planner performs verification "as part of the plan whenever necessary."

#### 3. Emergence AI's Domain Insights

Pre-computed structural knowledge about Target.com (where the search bar is, how filters work, what the cart icon looks like) reduces LLM calls by 50% and improves success by 7%.

### Recommended Approach

Create `buy_on_target.md` skill following the Skyvern phase pattern with mandatory verification at each phase. Include Target-specific UI hints. The skill MUST include `trigger-keywords: [target, buy, shop, purchase, bedsheet, bed sheet]` to ensure the keyword matcher can route to it. The keyword "target" will be the differentiator from amazon_search.

**Important interaction**: The new skill's type_text steps (e.g., "type search query into search box") MUST use the `element` param to specify what to click first. See P1-1 analysis for the call chain.

---

## P0-3: Plans Too Shallow {#p0-3-plans-too-shallow}

### Problem Landscape

The planner generates 3-step plans (activate Safari -> open_url target.com -> done) for "buy X" tasks. It treats opening a URL as task completion.

**Codebase evidence — current planner prompt analysis** (`planner/prompts/plan_from_prompt.md`):

The planner prompt contains:
- `"Keep plans focused -- minimum steps needed"` (line 52) — this actively encourages shallow plans
- NO task-completion criteria for purchase/shopping workflows
- NO instruction distinguishing "navigate to site" from "complete the purchase"
- The `done` action docs say `"Task complete. Params: none."` — no guidance on WHEN to declare done
- The skill context is injected as `{{skill_context}}` with guidance for direct/analogical/generic matches, but no instruction that skill steps define minimum plan depth

**Root cause**: The prompt says "minimum steps needed" without defining what "needed" means for multi-step workflows. The LLM interprets "buy bed sheets on target" as "open target.com" because that's the minimum to start the task, and the prompt doesn't tell it that starting is not completing.

### SOTA Approaches (Ranked)

#### 1. Goal-Completion Criteria in System Prompt (CHEAPEST, DO FIRST)

**Add to `plan_from_prompt.md`**:
```markdown
## Task Completion Criteria
A task is NOT complete when you have only:
- Navigated to a website or opened a URL
- Searched for a product but not selected one
- Viewed search results without acting on them

For purchase/shopping tasks ("buy", "shop", "purchase", "order"):
- Minimum: navigate -> search -> select product -> add to cart
- The plan MUST include steps beyond navigation to fulfill the user's intent
- Do NOT emit `done` until the core user action is completed

For return/refund tasks ("return", "refund"):
- Minimum: navigate -> find order -> initiate return -> confirm
```

This directly addresses the "minimum steps needed" ambiguity.

#### 2. Skill-Guided Planning Depth

When a skill template exists (like `buy_on_target.md`), the planner uses it as a planning scaffold. The skill defines expected phases and verification conditions. The planner generates steps to fulfill each phase.

**Key insight**: Skills solve the depth problem by providing domain-specific task structure. The planner prompt already says "direct matches: Follow the steps closely" — so if the skill has 6 phases, the plan will have ~6+ steps.

**This is why P0-2 and P0-3 are coupled**: Without a skill, the planner has no scaffold and falls back to "minimum steps." With a skill, the planner follows its phases.

#### 3. Hierarchical Planner-Navigator Architecture (Agent-E Pattern — FUTURE)

Agent-E separates planning from execution with a two-tier architecture achieving 73.2% on WebVoyager (16% above prior SOTA). The planner decomposes tasks into sub-goals, maintains URLs for backtracking, and replans on failure.

**Not recommended now**: Requires major architecture change. The combination of (1) + (2) addresses the immediate problem within existing architecture.

### Recommended Approach

**Combination of (1) and (2)**:
1. Add task-completion criteria to `plan_from_prompt.md` — immediate fix, no code changes
2. Create the Target buy skill (P0-2) to serve as planning scaffold
3. The planner prompt's existing "Follow the steps closely" instruction will enforce skill depth

---

## P1-1: type_text Doesn't Focus Element {#p1-1-type_text-focus}

### Problem Landscape

**Codebase evidence — the code ALREADY has click-to-focus, but it's buggy**:

The orchestrator's `_execute_step` method (`orchestrator/agent.py:2105-2136`) already implements click-to-focus for `type_text`:

```python
elif action == "type_text":
    # Click-to-focus: if an element is specified, find and click it first
    element_desc = params.pop("element", None)
    if element_desc and not params.pop("_skip_focus", False):
        try:
            location = await self.coordinator.find_element(
                element_desc, screenshot_b64=screenshot_b64
            )
            if location and hasattr(location, "x") and location.x is not None:
                sx = location.screen_x if location.screen_x is not None else location.x
                sy = location.screen_y if location.screen_y is not None else location.y
                self.actuator.click(sx, sy)
                await asyncio.sleep(max(self.config.action_delay, 0.3))
```

**The call chain is**: Planner generates `type_text` step with `element` param -> Orchestrator._execute_step pops `element` -> calls `coordinator.find_element(element_desc)` to get coordinates -> clicks at coordinates -> waits 300ms -> then calls `actuator.type_text(text)`.

**Analysis — 1 confirmed bug, 1 robustness improvement, 1 non-issue**:

1. **BUG (MUST-FIX): `screenshot_b64` NameError** — The variable `screenshot_b64` referenced at line 2111 is defined earlier in `_execute_step` only for `click` actions that need element finding. For `type_text`, it is not set. This is a **runtime NameError** that crashes the entire click-to-focus path. Without this fix, the `element` param is effectively ignored and `type_text` always sends keystrokes to whatever has focus. This single bug explains the P1-1 symptom.

2. **ROBUSTNESS IMPROVEMENT (NICE-TO-HAVE): Planner sometimes omits `element` param** — The planner prompt (`plan_from_prompt.md:30`) already says "IMPORTANT: Always specify `element` when typing into a specific input field so the agent clicks it first to ensure focus." LLMs sometimes ignore optional instructions. This is NOT a code bug — it is inherent LLM unreliability. Strengthening the prompt or adding examples may help but cannot guarantee compliance. The primary mitigation is ensuring skill templates (which the LLM follows closely for direct matches) always include `element` in their `type_text` steps.

3. **NON-ISSUE: Actuator's `type_text(self, text: str)` takes only text** — This is correct architecture. The actuator is the low-level execution layer; click-to-focus coordination happens in the orchestrator at `agent.py:2106-2127`. No change needed or desired here.

### SOTA Approaches (Ranked)

#### 1. Fix the NameError Bug (MUST-FIX)

Ensure `screenshot_b64` is captured/initialized before the `type_text` block at `agent.py:2105`, not just for `click` actions. This unblocks the existing click-to-focus path that is already correctly implemented.

#### 2. Accessibility-Based Focus Fallback (NICE-TO-HAVE)

If `find_element` fails (vision model can't locate the field), fall back to accessibility API:
```python
# Fallback: use get_accessibility_elements to find text fields
elements = self.actuator.get_accessibility_elements()
text_fields = [e for e in elements if e["role"] in ("AXTextField", "AXTextArea")]
if text_fields:
    self.actuator.click(text_fields[0]["center_x"], text_fields[0]["center_y"])
```

### Recommended Approach

| Priority | Change | Type |
|---|---|---|
| **MUST-FIX** | Fix `screenshot_b64` NameError in orchestrator `_execute_step` | Code bug |
| **NICE-TO-HAVE** | Reinforce `element` param in planner prompt examples | Prompt improvement |
| **SKILL TEMPLATE REQ** | New Target buy skill's `type_text` steps must include `element` | Template discipline |
| **NICE-TO-HAVE** | Add accessibility-based fallback when `find_element` fails | Robustness |

---

## P1-2: open_url False Negative {#p1-2-open_url-false-negative}

### Problem Landscape

**Codebase evidence**: The verifier (`orchestrator/verifier.py:370-423`) ALREADY has URL-based Tier 1 verification for `open_url`:

```python
if step.action == "open_url" and step.params.get("url"):
    # ... checks browser_url, window_title, token matching ...
    if browser_url:
        if expected_lower in actual_lower or actual_lower in expected_lower:
            return (True, f"Browser URL '{browser_url}' matches destination")
```

And the actuator (`actuator/applescript_actuator.py:293-316`) has `_get_browser_url()` supporting Safari, Chrome, and Arc.

**Root cause — screenshot diff overrides actuator success BEFORE verification runs**:

The codebase researcher identified the exact control flow in `orchestrator/agent.py:1059-1112`:

```python
# agent.py:1059-1081 — screenshot diff gate for open_url
if (self.screenshot_diff and step.action in ("click", "open_url")
    and actuator_result.get("success", False)):
    await asyncio.sleep(0.3)  # 300ms wait
    # ...
    visible_effect = self.screenshot_diff.screen_changed()  # line 1073

    if not visible_effect:                                   # line 1077
        actuator_result["success"] = False                   # line 1078 — OVERRIDE
        actuator_result["error"] = "...had no visible effect (screenshot unchanged)"

# agent.py:1086 — early return on failure
if not actuator_result.get("success", False):                # line 1086
    # Returns failure IMMEDIATELY — Tier 1 verifier NEVER RUNS
    return result, _text_field_focused                       # line 1112
```

**The Tier 1 URL verification at `verifier.py:370-423` is correct and would pass** — but it never gets a chance to execute because the screenshot diff at line 1077-1081 overrides `actuator_result["success"]` to `False` when the screen doesn't change (e.g., page already loaded). The early return at line 1086 then skips verification entirely.

**This is NOT a Tier 1 reliability problem. It is a screenshot diff gating problem.**

### SOTA Approaches (Ranked)

#### 1. Skip Screenshot Diff Gate for open_url (RECOMMENDED)

The fix must be at the screenshot diff level, not at Tier 1. For `open_url` actions, the screenshot diff gate is actively harmful — it declares failure for a working action. Two options:

**Option A (Targeted)**: Exclude `open_url` from the screenshot diff gate:
```python
# Change line 1061 from:
if self.screenshot_diff and step.action in ("click", "open_url")
# To:
if self.screenshot_diff and step.action in ("click",)
```
This lets `open_url` proceed to Tier 1 verification, which already correctly checks the browser URL.

**Option B (Defer)**: Keep the screenshot diff but don't override `actuator_result["success"]` for `open_url`. Instead, pass the diff result as metadata so the verifier can use it as a supplementary signal:
```python
if not visible_effect and step.action != "open_url":
    actuator_result["success"] = False
```

Option A is simpler and safer. The Tier 1 URL check is the correct verification for `open_url`.

#### 2. Tier 1 Already Correct — No Changes Needed

The verifier's `_verify_tier1` for `open_url` (`verifier.py:370-423`) already:
- Gets `browser_url` from `actuator.get_state()`
- Compares expected vs actual URL (exact match + token match)
- Checks window title as fallback
- Detects login redirects

This code is correct. The problem is that it never runs.

### Recommended Approach

**MUST-FIX**: Exclude `open_url` from the screenshot diff gate at `agent.py:1061`. This allows the existing (correct) Tier 1 URL verification to run. No changes to the verifier are needed.

---

## P1-3: Scroll Verification Always Fails {#p1-3-scroll-verification}

### Problem Landscape

Vision model can't confirm "content shifted downwards" from a single screenshot. Scroll verification via screenshot comparison is fundamentally unreliable.

### SOTA Approaches (Ranked)

#### 1. JavaScript scrollY Delta Check (RECOMMENDED — WITH BROWSER COVERAGE ANALYSIS)

**Safari**:
```applescript
tell application "Safari"
    do JavaScript "window.scrollY" in current tab of front window
end tell
```

**Chrome**: Chrome does NOT support AppleScript `do JavaScript` natively. However, Chrome has two alternatives:
```applescript
-- Option A: Chrome's built-in AppleScript support
tell application "Google Chrome"
    execute active tab of front window javascript "window.scrollY"
end tell

-- Option B: Use Chrome DevTools Protocol
-- (more complex, requires CDP connection)
```

Chrome's AppleScript dictionary includes `execute javascript` on tab objects, so this DOES work for Chrome. The codebase already distinguishes between Safari and Chrome in `_get_browser_url()` (`actuator/applescript_actuator.py:293-316`), so adding browser-specific JavaScript execution follows the same pattern.

**Firefox**: Firefox does NOT expose JavaScript execution via AppleScript at all. Firefox is rare in our target user base but should be handled gracefully.

**Native macOS apps** (Finder, System Preferences, etc.): These don't have a JavaScript context. Scroll verification for native apps must fall back to:
- **Accessibility API**: Check AXScrollPosition attribute
- **pyautogui scroll confirmation**: The actuator uses `pyautogui.scroll()` which returns success/failure
- **Vision fallback**: Accept the actuator's success result as sufficient (scroll happened if pyautogui didn't error)

**Coverage matrix**:
| App Type | scrollY JS | AX scroll pos | pyautogui result | Vision |
|---|---|---|---|---|
| Safari | YES | Fallback | Fallback | Last resort |
| Chrome | YES (execute javascript) | Fallback | Fallback | Last resort |
| Firefox | NO | YES | Fallback | Last resort |
| Native macOS | NO | YES | Fallback | Last resort |

#### 2. Actuator-Result Fallback (SIMPLEST FOR NON-BROWSER)

The current scroll action (`actuator/applescript_actuator.py:163-197`) uses pyautogui and returns success/failure. For non-browser apps, **trust the actuator result** — if pyautogui.scroll() didn't throw, the scroll happened.

### Recommended Approach

**Tiered scroll verification**:
1. **Browser apps**: Use JavaScript `window.scrollY` delta check (Safari's `do JavaScript`, Chrome's `execute javascript`)
2. **Non-browser apps**: Trust actuator result (pyautogui success = scroll succeeded)
3. **Vision fallback**: Only use if both above are inconclusive

**Implementation**: Add `_get_scroll_position(app_name: str) -> Optional[float]` to the actuator, following the same browser-dispatch pattern as `_get_browser_url()`.

---

## P2-1: Duplicate Walmart Skill Files {#p2-1-duplicate-walmart-skills}

### Problem Landscape

Three Walmart skill files exist:
- `return_walmart_order.md` (original, keywords: `[return, walmart, refund]`)
- `return-walmart-order.md` (duplicate, keywords: `[return, walmart]`)
- `return-walmart-order-2.md` (duplicate, keywords: `[return, walmart]`)

### Recommended Approach

Diff the files. Delete duplicates, keep the one with the most complete content (`return_walmart_order.md` which has `refund` keyword).

---

## P2-2: Skill Librarian Not Promoting Observations {#p2-2-skill-librarian}

### Problem Landscape

**Root cause identified — NOT a bug, a configuration default**:

`config.py:197-199`:
```python
skill_librarian_enabled: bool = Field(
    default=False,
    description="Promote high-confidence observations into canonical skills",
)
```

**`skill_librarian_enabled` defaults to `False`**. The feature is off by default.

**Full call chain analysis (proving the code path is correct)**:
1. `orchestrator/agent.py:612` calls `self._maybe_promote_skill()` after successful task execution
2. `orchestrator/agent.py:3674` calls `getattr(self.skill_registry, "promote_from_run", None)`
3. `skills/registry.py:638-650` calls `self._librarian.evaluate_run()` — but `self._librarian` is only set at `registry.py:90-95`:
   ```python
   if self._config and self._config.skill_librarian_enabled:
       from automation_agent.skills.librarian import SkillLibrarian
       self._librarian = SkillLibrarian(...)
   ```
4. Since `skill_librarian_enabled=False`, `self._librarian` is `None`, and `promote_from_run` returns `None` immediately.

**The promotion pipeline code is complete and correct** — it's just never activated. The full pipeline:
- `_maybe_promote_skill` (agent.py:3655) -> `promote_from_run` (registry.py:624) -> `evaluate_run` (librarian.py:75) -> `_evaluate_run_inner` (librarian.py:109)
- The inner method groups observations, checks thresholds (min_observations=5, min_runs=3, min_confidence=0.7), calls LLM to decide promotion type, generates content, commits (patch_parent or create_sibling), writes history

**The commit `4e7027a` fix** fixed `mark_promoted` key normalization to match librarian grouping — this was a secondary bug that would have caused duplicate promotions even if the librarian was enabled. But the primary issue is the feature being off.

### SOTA Approaches

#### 1. Emergence AI's Domain Insights Pattern

Auto-generates navigational insights from past task executions, stored as long-term memory, reducing LLM calls by 50% on repeat tasks. Our librarian implements the same pattern.

#### 2. Agent-E's Self-Improvement Loop

Agent-E's "agentic self-improvement" distills successful execution traces into reusable patterns. Our librarian's `patch_parent` and `create_sibling` promotions map to this.

### Threshold Analysis

**Direct question: Can the PRD goal of ">=1 new skill after first run" be achieved with current thresholds?**

**Answer: No.** Here is why:

The librarian's qualification logic (`librarian.py:139-146`):
```python
if len(group) < self.config.skill_librarian_min_observations:  # default: 5
    continue
if distinct_runs < self.config.skill_librarian_min_runs:       # default: 3
    continue
if score < self.config.skill_librarian_min_confidence:         # default: 0.7
    continue
```

- `min_runs=3`: A single run produces exactly 1 distinct `run_id`. The librarian requires observations from **3 separate runs** before considering promotion. A first run cannot satisfy this.
- `min_observations=5`: A single run might produce 2-4 observations. Even if `min_runs` were 1, a single run is unlikely to produce 5 observations in one category group.

**To achieve ">=1 skill after first run"**, both thresholds must be lowered:
- `min_runs=1` (allow first-run promotion)
- `min_observations=2` or `3` (realistic for a single run)

**Risk of lowering thresholds**: Premature promotion from noisy single-run observations. Mitigations:
- The LLM decide step (`_decide_promotion_type`) provides a second quality gate
- `_validate_tips()` and `_validate_sibling_md()` catch malformed output
- `trusted: false` flag on auto-promoted siblings requires human review

**Alternative**: Keep `min_runs=3` and redefine the PRD goal as "skill creation after 3+ runs" (conservative but safer). This matches Emergence AI's pattern of requiring multiple task executions to build confidence.

### Confidence Dead Zone Analysis

**Critical finding**: The current thresholds create a dead zone where the primary source of learning observations can never qualify for promotion.

The observation pipeline has two key gates:
1. **`_trace_deserves_learning`** (`registry.py:661-671`): Returns `True` only for runs with `retry_strategies`, `suggested_element`, `reflection` hints, or `wait_for_user`. For non-replan runs, this is the only gate — if it returns `False`, zero observations are produced (`registry.py:599`).
2. **Replan confidence cap** (`registry.py:614-616`): `if had_replan: obs.confidence = min(obs.confidence, 0.6)` — caps all replan observation confidence at 0.6.

The result: replan runs are the primary source of observations (they bypass the `_trace_deserves_learning` gate), but their observations are capped at 0.6. With `min_confidence=0.7` (default), **capped-at-0.6 observations can never qualify**. This is a dead zone — learning-worthy runs produce observations that are systematically excluded.

**Three options**:
- **(a) Lower `min_confidence` to 0.5**: Allows replan observations (capped at 0.6) through while still filtering truly low-quality observations. Preserves the replan discount as a signal of reduced reliability.
- **(b) Remove the 0.6 cap**: Lets replan observations keep their natural scores. But the cap exists for a good reason — replans had at least one failure, so their observations are inherently less reliable. Removing it loses that signal.
- **(c) Both**: Overkill — removes two independent safety mechanisms simultaneously, opens the door to noisy low-confidence observations from failed runs.

**Recommended: (a) — lower `min_confidence` to 0.5.** Reasoning:
- The 0.6 cap is sound engineering — replans had failures, so discounting their observations is appropriate
- `min_confidence=0.5` still filters out observations below 0.5 (truly noisy) while allowing the 0.5–0.6 range (replan observations with decent quality) through
- Three additional quality gates remain: the LLM decide step, structural validation, and the `trusted: false` flag
- This is the minimal change that unblocks the pipeline without removing safety mechanisms

### Recommended Approach

1. **MUST-FIX**: Set `skill_librarian_enabled=True` (config default or env var)
2. **Threshold strategy (match PRD)**: Set `min_runs=1`, `min_observations=2`, `min_confidence=0.5`
   - `min_runs=1`: Enables first-run promotion
   - `min_observations=2`: Realistic for a single run (2-4 observations typical)
   - `min_confidence=0.5`: Unblocks the dead zone — replan observations capped at 0.6 now qualify, while sub-0.5 noise is still filtered
3. Add an integration test that enables the librarian and verifies the full pipeline fires

---

## P2-3: Domain Verification Missing {#p2-3-domain-verification}

### Problem Landscape

The verifier's Tier 1 `open_url` check (`verifier.py:370-423`) validates that the browser URL matches the target URL, but it does NOT check domain alignment with the user's intent. If the skill says "navigate to target.com" but a bug routes to "amazon.com", and the planner generates `open_url("https://amazon.com")`, the verifier happily confirms "URL matches" because it only checks the step's URL param against the browser's current URL.

The missing check is: **does the step's URL param match the expected domain from the skill/user prompt?**

### SOTA Approaches (Ranked)

#### 1. Skill-Domain Guardrail (RECOMMENDED)

**How it works**: Each skill declares its expected domain (derivable from its name or an explicit `domain` field in frontmatter). The verifier checks that any `open_url` step navigates to a URL whose domain matches the skill's expected domain.

```python
def verify_domain_alignment(step_url: str, skill_domain: str) -> bool:
    from urllib.parse import urlparse
    actual = urlparse(step_url).netloc.replace("www.", "").lower()
    expected = skill_domain.lower()
    return expected in actual
```

**Integration point**: `verifier.py`'s `_verify_tier1` for `open_url` — add domain check before the existing URL match logic. This lives in the same code path as P1-2 (open_url verification).

#### 2. AGrail-Style Safety Check (ACL 2025)

AGrail verifies "action alignment with intended tasks" using safety checks generated per-task. Domain verification is a specific instance. AGrail achieves 0% ASR against prompt injection while blocking only 4.4% of benign actions.

### Recommended Approach

Add `expected_domain` to the skill context passed to the verifier. In Tier 1 `open_url` verification, check `urlparse(step.params["url"]).netloc` against `expected_domain`. If mismatch, return `(False, "URL domain mismatch")`.

---

## Cross-Cutting Analysis: Fix Interactions {#cross-cutting}

### P0-1 + P0-2 Dependency (Critical)

The router fix (P0-1) depends on the Target skill (P0-2) existing. Entity extraction will correctly identify "target" as the site, but if no `buy_on_target.md` skill exists, the filtered skill list is empty and routing falls through to the LLM router which may match amazon_search analogically. **P0-2 must land before or with P0-1.**

### P0-2 + P0-3 Dependency (Critical)

The Target skill (P0-2) solves the plan depth problem (P0-3) for skill-matched prompts, because the planner prompt says "direct matches: Follow the steps closely." But the planner prompt fix (P0-3) is still needed for prompts that DON'T match a skill — otherwise any novel "buy X on Y" prompt will still generate shallow plans. **Both must land.**

### P1-1 + P0-2 Interaction (Important)

The new Target buy skill will have `type_text` steps for search queries (e.g., "type '{{product}}' in search box"). These steps MUST include the `element` param (e.g., `"element": "search input field"`) for click-to-focus to work. If the skill template omits `element`, the P1-1 bug persists for Target workflows.

**Mitigation**: Enforce `element` param in skill templates for all `type_text` steps during code review.

### P2-3 + P1-2 Interaction (Different Code Paths, Same Goal)

P1-2 and P2-3 both relate to `open_url` verification but touch DIFFERENT code:
- **P1-2**: Fixes the screenshot diff gate at `agent.py:1059-1081` — excludes `open_url` from the diff check so Tier 1 verification can run
- **P2-3**: Adds domain-alignment check in `verifier.py:370-423` — once Tier 1 runs (unblocked by P1-2), adds expected-domain validation

These are complementary. P1-2 unblocks the code path; P2-3 strengthens the check within that path. The engineer should implement P1-2 first (unblock Tier 1), then P2-3 (add domain check to the now-reachable Tier 1).

### Test Strategy Implications

Integration tests should cover these interaction scenarios:
1. **Routing + Skill**: "buy bed sheets on target" routes to `buy_on_target.md` (not amazon_search)
2. **Skill + Plan Depth**: Skill-guided plan has 5+ steps (not 3)
3. **Plan + type_text focus**: type_text step with `element` param triggers click-to-focus
4. **open_url + domain check**: open_url to target.com passes both URL and domain verification
5. **Scroll + verify**: Scroll action passes JavaScript-based verification

---

## Risk Assessment {#risk-assessment}

### Risks of Recommended Approaches

#### P0-1: Entity Extraction Pre-Pass

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Wrong entity extracted (e.g., "sheets on target" -> "target" correct, but "I want to target cheap deals on amazon" -> "target" wrong) | Low | Medium — routes to wrong skill | Known-site dictionary with `\b` word boundary prevents substring matches. The verb "target" meaning "aim at" is rare in shopping prompts. Can add exclusion list if needed. |
| Entity found but no matching skill | Medium | Low — falls through to LLM router | Explicit fallback: if filtered list empty, log warning and use unfiltered routing |
| Site name collision with common words | Low | Medium | Only sites with actual skills are in the dictionary. "Target" is the riskiest — monitor false-positive rate. |

#### P0-3: Planner Prompt Changes

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Over-planning — plans become too long | Medium | Low — extra steps are harmless if each verifies | Cap plan length at 15 steps (already enforced by token limits) |
| Prompt changes break non-shopping tasks | Low | High | Add shopping-specific criteria only, don't change general "minimum steps" guidance |
| LLM ignores new criteria | Medium | Medium | Test with multiple prompts; the skill scaffold (P0-2) is the primary depth mechanism |

#### P1-3: JavaScript Scroll Verification

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Chrome `execute javascript` syntax wrong | Medium | Medium — scroll verification fails for Chrome | Test on Chrome before shipping; fallback to actuator result |
| Native app scroll can't be verified | Low | Low — trust pyautogui result | Already handled by fallback tier |
| `do JavaScript` requires page to be loaded | Low | Low | Add try/catch, fall back to actuator result |

#### P2-2: Enabling Skill Librarian

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Librarian promotes bad tips | Low | Medium — corrupts skill file | Validation in `_validate_tips()` already exists; `trusted: false` flag on auto-promoted siblings |
| LLM calls add latency to post-run | Medium | Low — runs after task completion, not blocking | Already in `_maybe_promote_skill()` try/except, non-blocking |

---

## Summary: Recommended Approach by Issue

| Issue | Recommended Fix | Complexity | Risk | Dependencies |
|-------|----------------|------------|------|-------------|
| **P0-1** | Known-site dictionary extraction + LLM prompt fix | Medium | Low-Med | Needs P0-2 |
| **P0-2** | Create `buy_on_target.md` skill (Skyvern phase pattern) | Medium | Low | None |
| **P0-3** | Task-completion criteria in planner prompt | Low | Low-Med | Enhanced by P0-2 |
| **P1-1** | Fix screenshot_b64 NameError (MUST-FIX) + strengthen element param (NICE-TO-HAVE) | Low | Low | None |
| **P1-2** | Exclude open_url from screenshot diff gate (MUST-FIX); Tier 1 verifier is correct | Low | Low | None |
| **P1-3** | JS scrollY with browser dispatch + actuator fallback | Medium | Low-Med | None |
| **P2-1** | Delete duplicate Walmart skills | Trivial | None | None |
| **P2-2** | Set `skill_librarian_enabled=True` | Trivial | Low | None |
| **P2-3** | Domain-alignment check in Tier 1 verifier | Low | Low | Same code path as P1-2 |

## Overall Assessment

**RECOMMENDATION: PROCEED**

All 9 issues have evidence-backed solutions grounded in codebase analysis and SOTA research. The P0 critical path (P0-2 -> P0-1 -> P0-3) must ship together. P1/P2 fixes are independent and lower risk. The existing codebase already has partial implementations for P1-1 (click-to-focus in orchestrator) and P1-2 (URL check in Tier 1 verifier) — these need debugging, not new architecture.

---

## References

### Skill Routing / Intent Classification
- [Intent Recognition and Auto-Routing in Multi-Agent Systems](https://gist.github.com/mkbctrl/a35764e99fe0c8e8c00b2358f55cd7fa)
- [Semantic Router — Aurelio AI](https://www.aurelio.ai/semantic-router)
- [Semantic Similarity as an Intent Router (LangChain + Zep)](https://blog.getzep.com/building-an-intent-router-with-langchain-and-zep/)
- [Routing in RAG Driven Applications](https://towardsdatascience.com/routing-in-rag-driven-applications-a685460a7220/)
- [AI Agent Routers: Techniques & Tools — Deepchecks](https://www.deepchecks.com/ai-agent-routers-techniques-best-practices-tools/)
- [The Routing Pattern with LangGraph](https://medium.com/@huzaifaali4013399/the-routing-pattern-build-smart-multi-agent-ai-workflows-with-langgraph-44f177aadf7a)
- [vLLM Semantic Router v0.1 Iris](https://blog.vllm.ai/2026/01/05/vllm-sr-iris.html)

### Browser Automation / Planning Depth
- [Agent-E: Foundational Design Principles in Agentic Systems (arXiv)](https://arxiv.org/html/2407.13032v1)
- [Agent-E SOTA Results on WebVoyager](https://www.emergence.ai/blog/agent-e-sota)
- [Skyvern — AI Browser Automation](https://www.skyvern.com/)
- [How Skyvern Reads and Understands the Web](https://www.skyvern.com/blog/how-skyvern-reads-and-understands-the-web)
- [How to Automate Purchasing Workflows — Skyvern](https://www.skyvern.com/blog/how-to-automate-purchasing-september-2025)
- [Supercharging AI Agents with Web Domain Insights — Emergence AI](https://www.emergence.ai/blog/the-next-frontier-of-web-automation-supercharging-ai-agents-with-web-domain-insights)
- [State-of-the-Art Autonomous Web Agents 2024-2025](https://medium.com/@learning_37638/state-of-the-art-autonomous-web-agents-2024-2025-3d9d93a5dde2)
- [WebArena Benchmark](https://webarena.dev/)

### macOS Accessibility / Focus Management
- [Mac Automation Scripting Guide — Apple Developer](https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/AutomatetheUserInterface.html)
- [Automate Keyboard with Mac](https://danielabaron.me/blog/automate-keyboard-mac/)
- [A Strategy for UI Scripting in AppleScript](https://n8henrie.com/2013/03/a-strategy-for-ui-scripting-in-applescript/)
- [Focus and Click Action AppleScript — MacScripter](https://www.macscripter.net/t/making-focus-and-click-action-applescript-more-efficient/74015)
- [Playwright MCP — Microsoft](https://github.com/microsoft/playwright-mcp)

### Screenshot / Visual Verification
- [Visual AI vs. Pixel-Matching vs. DOM-Based Comparisons — Applitools](https://applitools.com/blog/visual-ai-vs-pixel-matching-dom-based-comparisons/)
- [Visual Diff Algorithm — BrowserStack](https://www.browserstack.com/guide/visual-diff-algorithm-to-improve-visual-testing)
- [Top 16 Visual Testing Tools 2025 — BrowserStack](https://www.browserstack.com/guide/visual-testing-tools)

### Scroll Verification
- [How to Scroll to Element in Playwright — BrowserStack](https://www.browserstack.com/guide/playwright-scroll-to-element)
- [How to Scroll Pages with Playwright — ScrapeOps](https://scrapeops.io/playwright-web-scraping-playbook/nodejs-playwright-how-to-scroll/)
- [Scroll in Playwright Python — TesterCoder](https://testercoder.com/playwright-python/scroll-in-playwright-python/)

### Safety Guardrails / Domain Verification
- [AGrail: Lifelong Agent Guardrail (ACL 2025)](https://arxiv.org/abs/2502.11448)
- [Enhancing Browser Agent Safety with Guardrails — Invariant Labs](https://invariantlabs.ai/blog/enhancing-browser-agent-safety)
- [AI Agent Link Safety — OpenAI](https://openai.com/index/ai-agent-link-safety/)
- [How to Design AI Agent Guardrails — StackAI](https://www.stackai.com/insights/how-to-design-ai-agent-guardrails-best-practices-for-input-validation-output-filtering-and-safety-controls)
