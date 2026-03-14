# Codebase Analysis: Target Buy Fixes (9 Issues)

## Constraints
- Python 3.11, line length 100 (Black + Ruff)
- pytest-asyncio with `asyncio_mode = "auto"`
- Test configs MUST use: `grounding_model=""`, `grounding_server_url=""`, `model_provider="local"` in `_make_config()` to prevent `.env` leakage
- All communication via Protocol classes (`protocols.py`) + shared dataclasses (`shared_models.py`)

## Test Infrastructure

### Existing Fixtures (tests/conftest.py)
- `mock_planner` — AsyncMock with `plan()`, `replan()`, `check_infeasibility()`
- `mock_coordinator` — AsyncMock with `find_element()`, `describe_screen()`, `verify_condition()`, `capture_screenshot()`, `capabilities()`
- `mock_actuator` — MagicMock with `click()`, `type_text()`, `press_key()`, `activate_app()`, `open_url()`, `quit_app()`, `get_state()`
- `mock_skill_registry` — MagicMock with `match()`, `list_skills()`, `expand()`, `validate_all()`
- `mock_verifier` — AsyncMock with `verify()`
- `tmp_log_dir`, `tmp_skill_dir` — temporary directories
- `sample_action_step`, `sample_action_plan`, `sample_step_result`, `sample_failed_step_result`

### Config Construction Pattern
```python
def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",       # CRITICAL: prevents .env leakage
        "grounding_model": "",            # CRITICAL: prevents grounding router init
        "grounding_server_url": "",       # CRITICAL: prevents grounding router init
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)
```

### Existing Test Files
| File | Tests | Coverage |
|------|-------|----------|
| `tests/unit/test_router_v2.py` | ~30 | Skill router: top-k, parsing, confidence, legacy |
| `tests/unit/test_verifier.py` | ~20 | Tier 1/2 verification, URL token matching |
| `tests/unit/test_planner.py` | ~15 | Plan parsing, validation, prompt building |
| `tests/unit/test_skill_librarian.py` | ~40 | Librarian: grouping, scoring, promotion, rollback |
| `tests/unit/test_scroll_action.py` | ~10 | Scroll dispatch, direction mapping |
| `tests/integration/test_scroll_replan_integration.py` | ~5 | Scroll recovery + replan |
| `tests/unit/test_vision_arch_improvements.py` | 79 | All arch recs |

---

## Issue-by-Issue Analysis

---

### P0-1: Skill Router Matches `amazon-search` for Target Prompts

**Symptom**: "buy bed sheet on target" matches `amazon-search` skill (confidence above 0.5).

**Root Cause (corrected)**: The LLM router prompt (`skills/prompts/route_skill.md`) DOES already instruct (line 39): `"If a skill is structurally similar but for a different site, label it 'analogical'"`. However, this instruction is **buried at the end** of the `Rules:` section (line 35-39), after the JSON response format block (lines 19-30) and the empty-match fallback (lines 32-33). LLMs weight instructions near the top of a prompt more heavily than those at the bottom. The `amazon-search` skill's trigger-keywords (`buy`, `cheapest`, `shop`, `purchase`, `price`, `product`) are all generic shopping terms that create high keyword overlap, and the analogical instruction isn't prominent enough to counteract this.

The prompt also lacks explicit guidance on **confidence reduction** for cross-site matches. It says to label them "analogical" but doesn't say to reduce confidence. The LLM may correctly label a match as "analogical" but still assign it 0.8+ confidence, which clears the `MIN_USEFUL_CONFIDENCE = 0.5` gate.

**Prompt Structure Analysis** (route_skill.md, 40 lines total):
- Lines 1-3: Role definition (skill router)
- Lines 4-10: Match type definitions (direct/analogical/generic) — analogical definition at line 7-8 says "Do NOT assume site-specific labels or buttons are identical" but doesn't say to reduce confidence
- Lines 12-16: Template placeholders (skills_summary, prompt)
- Lines 18-30: JSON response format
- Lines 32-33: Empty match fallback
- Lines 35-39: Rules section — the site-specificity instruction at line 39 is the LAST line of the entire prompt

**Exact Code Locations**:
1. `src/automation_agent/skills/prompts/route_skill.md:39` — Existing but buried analogical instruction
2. `src/automation_agent/skills/prompts/route_skill.md:7-8` — Analogical definition (no confidence guidance)
3. `src/automation_agent/skills/router.py:172-243` — `_parse_response()` doesn't post-validate match_type vs confidence
4. `src/automation_agent/skills/library/amazon_search.md:7` — trigger-keywords include generic "buy", "shop", "purchase"
5. `src/automation_agent/skills/registry.py:343-381` — Stage 2b LLM routing, no post-filter

**Integration Points**:
- `SkillRegistryImpl.match()` → `SkillRouter.route()` → `_parse_response()` → `SkillRouteResult`
- `SkillRouteResult.candidates` → filtered by `MIN_USEFUL_CONFIDENCE` (0.5) in registry
- Result feeds into `AutomationAgent.execute()` at line 244-283

**Fix Options** (ordered by impact):
1. **Prompt restructure**: Move the site-specificity instruction from line 39 to the top of the Rules section (line 35) AND add explicit confidence guidance: "If a skill targets a different site/store than the prompt specifies, label it 'analogical' and reduce confidence to at most 0.3"
2. **Post-parse filter in `_parse_response()`**: After parsing, if `match_type == "analogical"`, cap confidence at 0.3 (code change in router.py:172-243)
3. **Skill metadata**: Add `site` or `domain` field to skill frontmatter, use it for pre-filtering before LLM call

**Existing Patterns**:
- Keyword fallback (`registry.py:383-429`) already has `MIN_USEFUL_CONFIDENCE` gate
- `SkillRouteCandidate.match_type` already distinguishes `direct` vs `analogical` — but nothing in the code acts differently based on match_type after parsing

**Landmines**:
- Changing `route_skill.md` affects ALL skill routing, not just shopping
- `_parse_response()` skips unknown `skill_id`s (line 210-214) — new skill must be loaded first
- `_build_skills_summary()` formats cards for prompt — adding metadata here affects token budget
- Capping analogical confidence too aggressively could break valid cross-site analogical matches (e.g., "return order on Walmart" matching `return_amazon_order` as useful template)

---

### P0-2: No Target Shopping Skill

**Symptom**: No `buy_on_target.md` skill exists. Only `return_target_order.md` (for returns).

**Exact Code Location**: `src/automation_agent/skills/library/` — needs new file

**Existing Pattern to Follow**: `amazon_search.md` and `return_target_order.md` provide templates.

**Skill File Format** (from `skills/loader.py`):
```yaml
---
name: buy-on-target
skill-id: buy-on-target
description: ...
summary: ...
tags: [...]
trigger-keywords: [...]
parameters:
  product:
    type: string
    required: true
    ...
requires:
  apps: [Safari]
  os: darwin
success-condition: ...
max-retries: 3
---

## Steps
1. open_url: https://www.target.com/s?searchTerm={{product}}
   - verify: Target search results page visible
   - on_fail: If login page, wait_for_user
2. ...

## Error Recovery
...

## Notes
...
```

**Integration Points**:
- `SkillRegistryImpl.load_from_directory()` auto-loads from `skills/library/*.md`
- `_build_card()` creates `SkillCard` for router prompt
- `match()` routes to it via LLM or keyword fallback

**Important**: The skill needs trigger-keywords that include "target", "buy", "shop", "purchase" but NOT "amazon" — to avoid the P0-1 collision.

---

### P0-3: Plans Too Shallow (3 Steps: open_url → done)

**Symptom**: Planner generates `activate_app → open_url → done` for "buy X on Target". Misses product selection, add-to-cart, etc.

**Root Cause**: The planner prompt (`plan_from_prompt.md`) doesn't instruct the LLM to decompose "buy" into sub-goals. The skill template (if matched) also only covers search, not the full buy workflow.

**Exact Code Locations**:
1. `src/automation_agent/planner/prompts/plan_from_prompt.md:44-57` — Critical rules section
2. `src/automation_agent/planner/planner.py:298-325` — `_build_plan_prompt()` substitutes template
3. `src/automation_agent/orchestrator/agent.py:1460` — `_is_trivial_done_plan()` (single done step detection)
4. `src/automation_agent/orchestrator/agent.py:1465` — `_is_truncated_plan()` (nav-only plan detection)
5. `src/automation_agent/orchestrator/agent.py:1492-1546` — `_build_skill_fallback_plan()` compiles deterministic fallback

**`_is_truncated_plan()` logic (line 1465-1476)**:
```python
@staticmethod
def _is_truncated_plan(plan: ActionPlan, fallback: Optional[ActionPlan]) -> bool:
    if fallback is None:
        return False
    interaction_actions = {"click", "type_text", "scroll"}
    plan_has_interaction = any(s.action in interaction_actions for s in plan.steps)
    fallback_has_interaction = any(s.action in interaction_actions for s in fallback.steps)
    return fallback_has_interaction and not plan_has_interaction
```
Key: only triggers when fallback has interactions AND LLM plan doesn't. If the skill template is also shallow (no click/type_text/scroll), this guard doesn't fire.

**`_build_skill_fallback_plan()` compiler chain (line 1492-1830)**:
- `_build_skill_fallback_plan()` (line 1492) → `_parse_skill_steps()` (line 1626) → `_compile_skill_instruction()` (line 1654)
- `_parse_skill_steps()` parses numbered lines with regex `r"^\d+\.\s+(.*)$"`, collects `- verify:` and `- on_fail:` metadata
- `_compile_skill_instruction()` (line 1654-1830) pattern-matches instruction text using **11 regex patterns** evaluated in order. Returns `None` at line 1830 for unrecognized patterns → **entire fallback plan becomes None** (aborts).

**Complete regex pattern table** (evaluation order, all `re.IGNORECASE`):

| # | Pattern | Exact Regex / Check | Compiles to |
|---|---------|---------------------|-------------|
| 1 | done/complete | `lower.startswith("use done")` or `lower == "done"` or `lower.startswith("complete task")` | `done` step |
| 2 | Navigate to... | `r"(?i)^navigate to"` strip → if `r"^https?://\S+$"` → open_url; else → click | `open_url` or `click` |
| 3 | Wait for user | `"wait for user" in lower` or `"wait for the user" in lower` | `wait_for_user` |
| 4 | Conditional wait | `lower.startswith("if ")` and `"wait for user" in lower` | `wait_for_user` |
| 5 | Open app | `r"^(?:Use activate_app to open\|Open)\s+(.+?)(?:\s+app)?(?:\s+and navigate to\s+(https?://\S+))?$"` | `activate_app` + optional `open_url` |
| 6 | Use open_url | `r"^Use open_url to navigate to\s+(https?://\S+)$"` | `open_url` |
| 7 | Find X click "Y" | `r'^Find\s+(.+?)\s+and click\s+"([^"]+)"$'` | `click` (element=`"Y" for X`) |
| 8 | Find X click it | `r"^Find\s+(.+?)\s+and click\s+(?:it\|them)$"` | `click` |
| 9 | Click on/the X | `r"^(?:Click on\|Click the\|Click)\s+(.+)$"` | `click` |
| 10 | Type X + Enter | `r'^Type\s+"?(.+?)"?\s+(?:in the .+?\s+)?(?:and\|then)\s+press\s+Enter$'` | `type_text` + `press_key` |
| 11 | Press keys | `r"^Press\s+(.+?)(?:\s+to\s+.+)?$"` | `press_key` |
| — | **Anything else** | Falls through all 11 patterns → line 1830 `return None` | **`None`** → fallback aborted |

**Critical implications for P0-2 skill template design** (tested against the regex table):
- `"Sort results by price (low to high)"` → does NOT match any pattern ("Sort" is not Click/Find/Navigate/Open/Type/Press/done). **Returns `None`, fallback aborted.**
- `"Select the first product"` → does NOT match ("Select" is not "Click"). **Returns `None`, fallback aborted.**
- `"Add item to cart"` → does NOT match ("Add" is not recognized). **Returns `None`, fallback aborted.**
- `"Scroll down to find..."` → does NOT match (no scroll pattern in compiler). **Returns `None`, fallback aborted.**

**REQUIREMENT for P0-2 skill authors**: All skill steps MUST use recognized verb patterns:
- Instead of "Sort results by price" → `"Click on the 'Sort by: Price (low to high)' dropdown option"`
- Instead of "Select the first product" → `"Click on the first product in search results"`
- Instead of "Add item to cart" → `"Click on the 'Add to cart' button"`
- Instead of "Scroll down to find..." → NOT compilable. Use `"Find the product listing and click it"` (pattern #8) instead.
- Note: `scroll` has NO recognized pattern. Scroll actions can only come from the LLM planner, not the deterministic fallback.

**Interaction between LLM plan and fallback when P0-1/P0-2/P0-3 are all fixed**:

After fixes, two plans coexist:
1. **LLM plan**: Improved by P0-3 prompt fix — should now include interaction steps (click, type_text)
2. **Fallback plan**: Compiled from new `buy_on_target.md` skill template — full workflow if steps use recognized patterns

Plan selection logic in `execute()` (line 310-337):
```python
plan = await self.planner.plan(goal, **plan_kwargs)
fallback_plan = self._build_skill_fallback_plan(goal, skill_context)
if self._is_trivial_done_plan(plan) and fallback_plan is not None:
    # replace if not already satisfied
elif self._is_truncated_plan(plan, fallback_plan):
    replace_with_fallback = True  # only if LLM has NO interactions but fallback does
if replace_with_fallback:
    plan = fallback_plan
# Then: _ensure_skill_navigation() adds missing nav steps from fallback (line 342-343)
```

**`_is_truncated_plan()` completeness gap**: The check uses `any(s.action in {"click", "type_text", "scroll"} for s in plan.steps)` — presence of ANY single interaction step means "not truncated." An LLM plan with `open_url → click "search" → done` (one click, no add-to-cart) passes the truncation check. The fallback (with full search → select → add-to-cart workflow) is NOT used.

**`_ensure_skill_navigation()` (line 342-343)**: Runs after plan selection. Ensures the LLM plan includes navigation steps from the fallback (open_url, activate_app). Does NOT inject missing interaction steps (click, type_text). So even after this pass, an LLM plan with interactions but incomplete workflow runs as-is.

**Risk**: After all three P0 fixes, if the LLM generates a plan with some interactions but missing key workflow steps (e.g., clicks product but doesn't add to cart), `_is_truncated_plan()` won't catch it, and the comprehensive fallback won't replace it. Recovery depends on `_replan_and_continue()` at runtime.

**Mitigating factors**:
1. The planner prompt fix (P0-3) should produce reasonably complete plans for well-defined goals like "buy X on Target"
2. `_replan_and_continue()` (line 3387-3486) re-assesses screen state and generates new steps when stuck — it's designed for exactly this scenario
3. The skill context is passed to the planner, so the LLM sees the full skill template and should plan accordingly

**Net assessment**: This is a **known limitation**, not a bug. The truncation detector is a safety net for egregiously bad plans (nav-only), not a completeness validator. Runtime replanning handles partial-plan scenarios. However, if testing reveals the LLM consistently produces incomplete plans despite the prompt fix, a more sophisticated completeness check (e.g., comparing interaction step count or required-action coverage against the fallback) could be added as a P1 enhancement.

**Integration Points**:
- `execute()` calls `_is_truncated_plan()` (line 323-330) which checks if fallback has interactions but LLM plan doesn't
- `_build_skill_fallback_plan()` parses skill steps text to create a fallback `ActionPlan`
- If truncated, plan is replaced with fallback (line 331-337)
- `_ensure_skill_navigation()` (line 342-343) adds missing nav steps but not interaction steps

**Existing Safeguards**:
- `_is_truncated_plan()` catches "nav only, no interaction" patterns — but only presence, not completeness
- `plan.validate()` checks for empty verify fields but not plan depth
- `_replan_and_continue()` handles runtime recovery for incomplete plans

**Fix Options**:
1. **Planner prompt enhancement**: Add instruction like "For e-commerce 'buy' goals, the plan MUST include: search → filter/sort → select product → add to cart. Do NOT stop at showing search results."
2. **Skill template fix**: Make `buy_on_target.md` (P0-2) include the full workflow through add-to-cart — the fallback compiler will then generate a deep fallback plan (if steps use recognized patterns)
3. **Plan depth validation** (P1 enhancement if needed): Compare LLM plan's interaction step count against fallback's. If LLM has < 50% of fallback's interactions, treat as truncated.

**Landmines**:
- Adding too many constraints to `plan_from_prompt.md` bloats token usage for ALL tasks
- The Ollama GBNF schema (`_OLLAMA_FORMAT_SCHEMA` at planner.py:139-158) doesn't constrain step count
- `_build_skill_fallback_plan()` compiles from skill steps text — if skill template is comprehensive, the fallback is also comprehensive
- Skill step text MUST match `_compile_skill_instruction()` regex patterns exactly (see table above). Any unrecognized instruction returns `None` and aborts the entire fallback
- `scroll` has no compiler pattern — scroll-dependent workflows cannot use deterministic fallback

---

### P1-1: type_text Doesn't Focus Target Element Before Typing

**Symptom**: Text typed into wrong browser tab because `type_text` uses AppleScript `keystroke` which sends to whatever has focus.

**Root Cause**: In `_dispatch_action()` (agent.py:2105-2136), the click-to-focus code exists but has a **critical bug**: line 2111 references `screenshot_b64` which is **NOT defined** in the `_dispatch_action()` method scope. The variable `screenshot_b64` is only set in `_find_element()` (a separate method, line 2195). This means the click-to-focus `find_element` call would raise `NameError` or reference a wrong scope variable.

**Exact Code Locations**:
1. `src/automation_agent/orchestrator/agent.py:2105-2136` — `_dispatch_action()` type_text branch
2. `src/automation_agent/orchestrator/agent.py:2110-2111` — BUG: `screenshot_b64` undefined
3. `src/automation_agent/actuator/applescript_actuator.py:65-68` — `type_text()` uses `keystroke` (no focus control)
4. `src/automation_agent/planner/prompts/plan_from_prompt.md:30` — Documents `element` param for `type_text`

**The Bug in Detail**:
```python
elif action == "type_text":
    element_desc = params.pop("element", None)
    if element_desc and not params.pop("_skip_focus", False):
        try:
            location = await self.coordinator.find_element(
                element_desc, screenshot_b64=screenshot_b64  # <-- NameError!
            )
```
The `screenshot_b64` variable is never assigned in `_dispatch_action()`. It's defined in `_find_element()` at line 2195, which is a completely separate method.

**Fix**: Capture a screenshot at the top of the type_text branch before calling `find_element`, similar to how `_find_element()` does it.

**Integration Points**:
- `_dispatch_action()` is called from `_execute_step()` (line 1045)
- `coordinator.find_element()` expects optional `screenshot_b64` param
- After clicking, the code waits `action_delay` before typing (line 2117)
- `_clear_first` and `_slow_type` params are also handled in this branch

**Test Infrastructure**:
- `mock_coordinator.find_element` returns `FindElementResult(x=500, y=300, confidence=0.9, source="vision")`
- `mock_actuator.type_text` returns `{"success": True, "output": ""}`
- `mock_actuator.click` returns `{"success": True, "output": ""}`

---

### P1-2: open_url "No Visible Effect" False Negative

**Symptom**: When navigating to a URL the browser already shows, the screenshot diff sees no change and declares failure.

**Root Cause**: In `_execute_step()` (agent.py:1059-1081), after dispatching `open_url`, the code captures a post-action screenshot and compares with a pre-action screenshot. If pixels haven't changed (because page was already loaded), `visible_effect` is False, and `actuator_result["success"]` is overwritten to False.

**Exact Code Locations**:
1. `src/automation_agent/orchestrator/agent.py:1040-1042` — Pre-action screenshot capture for diff
2. `src/automation_agent/orchestrator/agent.py:1059-1081` — Post-action diff check
3. `src/automation_agent/orchestrator/agent.py:1077-1081` — Overwrites success to False when no visible effect
4. `src/automation_agent/orchestrator/verifier.py:370-423` — Tier 1 open_url verification (URL/title match)

**The Bug in Detail**:
```python
# Line 1059-1081
if (
    self.screenshot_diff
    and step.action in ("click", "open_url")
    and actuator_result.get("success", False)
):
    await asyncio.sleep(0.3)
    ...
    visible_effect = self.screenshot_diff.screen_changed()
    ...
    if not visible_effect:
        actuator_result["success"] = False  # <-- FALSE NEGATIVE
        actuator_result["error"] = (
            f"{step.action} had no visible effect (screenshot unchanged)"
        )
```

The diff check overrides the actuator's success before Tier 1 verification can check the URL.

**Fix**: For `open_url`, skip the diff-based failure if Tier 1 URL verification would pass. Or: don't override `actuator_result["success"]` for `open_url` — only use the diff as a signal for Tier 2.

**Full Retry Path (traced for "failed 4 times" scenario)**:
1. `_execute_step()` dispatches `open_url` → actuator succeeds
2. Screenshot diff check (line 1059-1081) → no visible change → `actuator_result["success"] = False`
3. Line 1086 gates: `if not actuator_result.get("success", False)` → returns `StepResult(success=False)` **before verification** (line 1122 never reached)
4. `execute()` loop receives failure → `_handle_failure()` (line 3176-3262) → delegates to `_vary_strategy()` (line 3264-3384)
5. `_vary_strategy()` for open_url (line 3356-3362):
   - Attempt 1: sets `_address_bar_fallback=True` → `_open_url_via_address_bar()` (line 2803-2811): Cmd+L, type URL, Enter
   - Attempt 2+: sets `_pre_delay=2.0*attempt` → longer wait then retry
6. Each retry ALSO hits the same screenshot diff gate (line 1059-1081) if the page is already showing the correct content → perpetual false negative → explains "failed 4 times"

**Key insight**: The retry strategies (address bar fallback, extended delay) are sound, but they don't help because the failure occurs AFTER the action succeeds — at the screenshot diff check, which fires before Tier 1 URL verification has a chance to confirm the URL is correct.

**Integration Points**:
- `screenshot_diff` is `None` by default (only set when a `ScreenshotDiff` object is injected)
- In production, `screenshot_diff` is created in `__main__.py` — so this bug only manifests with the screenshot diff feature enabled
- Tier 1 verifier (verifier.py:370-423) checks browser URL and window title — if the page is already correct, Tier 1 would pass
- But line 1086 returns failure BEFORE verification is reached (line 1122)

**Landmines**:
- The `actuator_result["success"] = False` at line 1078 gates the ENTIRE rest of `_execute_step()` — if it fires, verification is skipped entirely (line 1086-1112 returns immediately)
- The same diff check applies to `click` actions — be careful not to break click verification
- `_open_url_via_address_bar()` at line 2803-2811 uses Cmd+L → type_text → Enter, which itself can fail if the address bar doesn't focus properly

---

### P1-3: Scroll Verification Always Fails

**Symptom**: Vision model can't confirm "visible content shifted downwards" after scroll. 4 consecutive scroll attempts all failed verification.

**Root Cause**: The scroll action is dispatched (agent.py:2157-2177) and succeeds at the actuator level, but the verifier sends the scroll step's `verify` text to the vision model. Verify text like "visible content has shifted downwards" is too abstract for the vision model to confirm from a single screenshot.

**Exact Code Locations**:
1. `src/automation_agent/orchestrator/agent.py:2157-2177` — Scroll dispatch in `_dispatch_action()`
2. `src/automation_agent/orchestrator/verifier.py:454-556` — Tier 2 vision verification
3. `src/automation_agent/orchestrator/agent.py:2503-2580` — `_scroll_recovery()` (scrolls + retries find_element)
4. `src/automation_agent/orchestrator/agent.py:1122` — `verifier.verify()` call after dispatch

**Integration Points**:
- Scroll dispatches via `self.actuator.scroll()` which uses `pyautogui.scroll()`
- The verifier's Tier 1 (`_verify_tier1`) returns `None` for scroll (inconclusive, line 441)
- Tier 2 sends `step.verify` to `coordinator.verify_condition()` — vision model sees a static screenshot
- `_scroll_recovery()` (line 2503) is a separate recovery mechanism that scrolls + re-finds element

**Fix Options**:
1. **Diff-based scroll verification**: Compare before/after screenshots to detect pixel change > threshold → scroll succeeded
2. **Skip vision verification for scroll**: Accept actuator success for scroll actions (scroll always "succeeds" if the actuator call returns success)
3. **Better verify text**: Teach planner to use verify conditions that reference content, not scroll state

**Landmines**:
- `_scroll_recovery()` already works without verification — it just scrolls and tries to find the element. The problem is when scroll is a plan step WITH a verify condition that the vision model can't confirm
- The `_SCROLL_SETTLE_S = 1.0` delay in `_scroll_recovery()` already handles lazy loading

---

### P2-1: Duplicate Walmart Skill Files

**Symptom**: `return-walmart-order.md` and `return-walmart-order-2.md` (both stubs) conflict with `return_walmart_order.md` (real template). Causes "Duplicate skill name" error.

**Exact Code Location**:
1. `src/automation_agent/skills/library/return-walmart-order.md` — stub, `name: return-walmart-order`
2. `src/automation_agent/skills/library/return-walmart-order-2.md` — stub, `name: return-walmart-order-2`
3. `src/automation_agent/skills/library/return_walmart_order.md` — real template
4. `src/automation_agent/skills/registry.py:184-191` — Duplicate name detection logs error

**Fix**: Delete both stub files. They have `trusted: false` and minimal content.

**Integration Points**:
- `load_from_directory()` iterates `sorted(path.glob("*.md"))` — sorted alphabetically
- `return-walmart-order-2.md` loads first (dash before underscore), then `return-walmart-order.md`, then `return_walmart_order.md`
- First loaded wins; duplicates are skipped with error log

**Landmines**:
- These files are staged in git (`git status` shows them as `A` — added). Deleting them from working tree is sufficient; they'll show as deleted in the next commit.

---

### P2-2: Skill Librarian Not Creating New Skills

**Symptom**: After execution, no new skill files created. No observations promoted by librarian.

**Root Cause**: `skill_librarian_enabled` defaults to `False` in config.py (line 198). Even if enabled, the thresholds are high: min_observations=5, min_runs=3, min_confidence=0.7.

**Exact Code Locations**:
1. `src/automation_agent/config.py:197-199` — `skill_librarian_enabled: bool = False`
2. `src/automation_agent/config.py:207-216` — `skill_librarian_min_observations: int = 5`, `min_runs: int = 3`
3. `src/automation_agent/skills/registry.py:85-99` — Librarian created only if `skill_librarian_enabled` AND `experience_store` exists
4. `src/automation_agent/skills/librarian.py:86-88` — `evaluate_run()` early-returns if not enabled
5. `src/automation_agent/orchestrator/agent.py:612-621` — `_maybe_promote_skill()` call site

**Integration Points**:
- `execute()` calls `_maybe_learn_skill_run()` (line 603) → `registry.learn_from_run()` → `SkillDistiller.distill()`
- Then calls `_maybe_promote_skill()` (line 612) → `registry.promote_from_run()` → `SkillLibrarian.evaluate_run()`
- Librarian groups observations by `(category, category)` key (line 312-313) — uses `_normalize_key()` for text normalization
- `_compute_score()` uses Bayesian scoring (alpha/beta counts)
- `mark_promoted()` delegates to `experience_store.mark_promoted()` which normalizes keys

**Root Cause Chain (full depth)**:
The failure to create new skills has **two independent gates**, both of which must pass:

1. **Gate 1: `learn_from_run()` (registry.py:586-622)** — generates observations
   - Line 599: `if not had_replan and not self._trace_deserves_learning(trace): return []`
   - `had_replan=True` BYPASSES `_trace_deserves_learning()` check → learning IS triggered for replan runs
   - `_trace_deserves_learning()` (line 661-671) checks for: `retry_strategies_used`, `suggested_element`, `reflection_hint/observed`, or `wait_for_user` actions. A clean run with no retries returns `False` → no observations generated
   - In Scenario 1 (Target buy), there WAS a replan → `had_replan=True` → Gate 1 passes
   - But even when Gate 1 passes, observations have confidence capped at 0.6 (line 614-616): `if had_replan: obs.confidence = min(obs.confidence, 0.6)`

2. **Gate 2: `promote_from_run()` → `librarian.evaluate_run()` (librarian.py:75-103)** — promotes observations to skills
   - Line 86-88: Early return if `skill_librarian_enabled` is False → **this is the PRIMARY blocker**
   - `skill_librarian_enabled` defaults to `False` (config.py:198)
   - Even if enabled, thresholds are high: `min_observations=5`, `min_runs=3`, `min_confidence=0.7`
   - Since had_replan caps confidence at 0.6 (Gate 1), and min_confidence is 0.7 (Gate 2), **observations from replan runs can NEVER reach promotion threshold** — even with the librarian enabled

**Fix Options** (must address both gates):
1. **Enable the librarian**: Set `skill_librarian_enabled = True` in config.py (or via env `AGENT_SKILL_LIBRARIAN_ENABLED=true`)
2. **Lower thresholds**: `min_observations=2`, `min_runs=1`, `min_confidence=0.5` (must be ≤ 0.6 to allow replan observations)
3. **Remove the 0.6 confidence cap** for had_replan OR raise it to match the librarian threshold
4. **Ensure clean-run observations**: Relax `_trace_deserves_learning()` to also trigger for new/unseen task patterns (not just failure-driven learning)

**Landmines**:
- `_group_observations()` (librarian.py:301-313) groups by `(normalized_category, normalized_category)` — the key is `(cat, cat)` where cat is lowercased, punctuation-stripped. But `_load_promotion_history()` (line 336-354) reads `observation_keys` from history.jsonl which stores `[cat, cat]` lists. The `mark_promoted()` call at line 740 passes `[best_key]` where `best_key` is `(cat, cat)`. This was recently fixed (commit 4e7027a) for key normalization.
- `_trace_deserves_learning()` is a static method that checks if ANY step had retries, reflections, etc. A perfectly clean run returns `False` → no learning occurs.
- The 0.6 confidence cap vs 0.7 min_confidence creates a **dead zone** where replan observations are generated but can never be promoted

---

### P2-3: Verification Doesn't Check Domain Correctness

**Symptom**: Agent navigated to Amazon when user said "buy bed sheet on Target". Verification passed because it checked the step's expected URL (amazon.com, from the wrong skill), not the user's intended domain (target.com).

**Root Cause Clarification**: This is NOT a bug in `_url_tokens()` or Tier 1 verification logic. The verification system works correctly for what it's given — it checks the step's expected URL against the actual browser URL. The problem is **upstream**: the wrong skill was matched (P0-1), which generated an Amazon URL in the step, and verification correctly confirmed that Amazon URL was reached.

The real ask is a NEW capability: **goal-level domain validation** — checking that the browser domain matches the user's intent, not just the step's expected URL. This is fundamentally different from fixing broken verification.

**`_url_tokens()` works correctly** (verifier.py:230-242):
- For `https://www.target.com/s?searchTerm=bed+sheet`: tokens = `["target"]`
- For `https://www.amazon.com/s?k=bed+sheet`: tokens = `["amazon"]`
- Token match at line 410: `any(token in actual_lower for token in tokens)` — "target" is NOT in an amazon URL → correctly fails
- The scenario in the gap report was: expected=amazon (from wrong skill) + actual=amazon → correctly passes. The verifier isn't wrong; the input is wrong.

**Exact Code Locations**:
1. `src/automation_agent/orchestrator/verifier.py:230-242` — `_url_tokens()` — works correctly
2. `src/automation_agent/orchestrator/verifier.py:370-423` — Tier 1 open_url verification — works correctly for given step
3. `src/automation_agent/orchestrator/agent.py:244-283` — Where goal/prompt context is available but NOT passed to verifier

**Scope Clarification**: This requires a NEW capability, not a fix to existing logic:
1. Extract intended domain from goal/prompt (e.g., "on Target" → target.com)
2. Pass goal context to verifier (currently verifier only sees `ActionStep`)
3. Add goal-domain check as pre-condition in Tier 1

**Integration Points**:
- `StepVerifier.verify()` receives `ActionStep` + `actuator_result` — no goal/prompt context
- The orchestrator (`agent.py`) has access to the original goal but doesn't pass it to verifier
- Adding goal context to `StepVerifier` requires API change: `verify(step, actuator_result, goal=None)` or injecting goal at construction time
- `actuator.get_state()` (applescript_actuator.py:318-374) already returns `browser_url` — the data for domain checking is available in Tier 1

**Engineering Scope Impact**: This is a P2 feature, not a P2 bugfix. It requires:
- Verifier API change (add goal context)
- Domain extraction logic (entity extraction from natural language)
- Decision on what to do when domain mismatch is detected (fail step? replan?)

---

## Cross-Cutting Analysis

### Issue Interdependencies
Several fixes interact and must be sequenced carefully:

1. **P0-1 → P0-2 → P0-3**: Fixing the router (P0-1) only helps if a Target skill exists (P0-2). The Target skill must have deep steps (P0-3) or the truncated-plan guard won't fire. These three fixes form a dependency chain: P0-2 skill file must exist and be loaded BEFORE P0-1 router fix is tested, and P0-3 planner prompt must be updated BEFORE the skill's fallback plan can be evaluated.

2. **P1-1 + P1-2**: Both occur in `_execute_step()` / `_dispatch_action()` in `agent.py`. P1-1 (type_text focus) adds a screenshot capture call at the top of the type_text branch. P1-2 (open_url false negative) modifies the screenshot diff gate at lines 1059-1081. Both touch the same method and same control flow — coordinate to avoid merge conflicts.

3. **P2-2 + P0-2**: Even after fixing the librarian (P2-2), it won't auto-create a Target skill because the system needs at least `min_observations` (5) from successful runs. P0-2 (manual skill creation) must happen first. The librarian fix is about future skill evolution, not initial creation.

4. **P2-3 depends on P0-1**: If P0-1 is fixed (correct skill routing), P2-3 becomes less critical because the step's expected URL will already match the user's intent. P2-3 is defense-in-depth, not a primary fix.

### `actuator.get_state()` Capabilities and Limitations
Several fixes depend on what `get_state()` can report. Full analysis:

**What `get_state()` provides** (applescript_actuator.py:318-374):
- `app_name`: frontmost app name (via System Events)
- `app_bundle`: bundle identifier
- `window_title`: front window title
- `browser_url`: current URL (Safari, Chrome, Arc only — NOT Firefox)
- `window_x/y/w/h`: window position and size

**What `get_state()` CANNOT provide**:
- **Scroll position**: No `scrollY` / `pageYOffset` access. The AppleScript actuator has no JavaScript execution capability — `_get_browser_url()` uses per-browser AppleScript (`tell application "Safari" to return URL of front document`), not `do JavaScript`.
- **Page content / DOM**: No access to page text, element visibility, or DOM state
- **Tab count or list**: Only accesses "front document" / "active tab"
- **Login state**: No cookie/session inspection

**Impact on fixes**:
- **P1-3 (scroll verification)**: SOTA suggests `do JavaScript "window.scrollY"` for scroll position detection. This would require adding JavaScript execution capability to `_get_browser_url()` or a new method `_get_scroll_position()`. Safari supports `do JavaScript` in AppleScript; Chrome requires `execute javascript` via its own scripting bridge. Firefox has no AppleScript JS API. This is a significant actuator extension.
- **P1-2 (open_url)**: `get_state()` already provides `browser_url` for Tier 1 verification. The fix is to check `browser_url` BEFORE the screenshot diff gate, not after.
- **P2-3 (domain check)**: `browser_url` is available from `get_state()` — domain extraction is straightforward if the verifier has access.

### Risk Assessment

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R1 | Router prompt change (P0-1) breaks existing skill matches | HIGH | Unit test all existing skills with current prompts before/after change |
| R2 | `_compile_skill_instruction()` fails for new Target skill step patterns | MEDIUM | New skill MUST use recognized patterns (see P0-3 compiler list). Test fallback plan compilation for new skill |
| R3 | Screenshot diff gate fix (P1-2) removes useful false-positive detection for click actions | MEDIUM | Fix must be scoped to `open_url` only — preserve existing `click` diff behavior |
| R4 | `screenshot_b64` fix in type_text (P1-1) adds latency from extra screenshot capture | LOW | Already ~2-5s per screenshot — one extra in type_text path is acceptable |
| R5 | Enabling librarian (P2-2) with low thresholds auto-promotes bad observations | LOW | Start with `min_confidence=0.5` (not lower), require `min_runs=2` |
| R6 | Adding `do JavaScript` to actuator (P1-3) fails on Firefox | LOW | Firefox already returns empty for `_get_browser_url()` — extend same pattern (graceful degradation) |

## Cross-Cutting Concerns

### Skill File Format
All skills in `src/automation_agent/skills/library/*.md` follow:
- YAML frontmatter with: name, skill-id, description, summary, tags, trigger-keywords, parameters, requires (apps, os), success-condition, max-retries
- Optional: parent-skill-id, trusted
- Body sections: `## Steps`, `## Error Recovery`, `## Notes`, optional `## Learned Tips`
- Step format: `N. action: description` + `   - verify: condition` + optional `   - on_fail: recovery`

### _make_config() Pattern for Tests
Every test file that uses `AgentConfig` MUST prevent `.env` leakage:
```python
def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",
        "grounding_model": "",
        "grounding_server_url": "",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)
```
Without `model_provider="local"`, the `.env` file's `AGENT_MODEL_PROVIDER=anthropic` leaks into test config, causing tests to try real API calls.

### AutomationAgent Construction for Tests
```python
def _make_agent(actuator=None, config=None) -> AutomationAgent:
    planner = AsyncMock()
    skill_registry = MagicMock()
    skill_registry.match = AsyncMock(return_value=None)
    skill_registry.learn_from_run = AsyncMock(return_value=[])
    skill_registry.promote_from_run = AsyncMock(return_value=None)
    coordinator = AsyncMock()
    coordinator.capabilities = MagicMock(return_value=frozenset())
    coordinator.capture_screenshot = AsyncMock(return_value="base64data")
    if config is None:
        config = _make_config()
    if actuator is None:
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.scroll = MagicMock(return_value={"success": True})
    return AutomationAgent(planner, skill_registry, coordinator, actuator, config)
```

### Key Dependencies
- `structlog` — structured logging throughout
- `pydantic-settings` — config with env prefix `AGENT_`
- `pyautogui` — click and scroll (in actuator)
- `PIL/Pillow` — screenshot cropping and diff
- `httpx` — async HTTP for LLM calls
- `yaml` — skill file parsing

### Event Logging
All significant actions log via `EventLogger` using `EventType` enums from `logging/models.py`. Key types: `TASK_START`, `TASK_COMPLETE`, `TASK_FAIL`, `SKILL_MATCH`, `SKILL_NO_MATCH`, `PLAN_START`, `PLAN_COMPLETE`, `STEP_START`, `STEP_COMPLETE`, `STEP_RETRY`, `STEP_REPLAN`, `VERIFY_PASS`, `VERIFY_FAIL`, `ACTION_START`, `ACTION_COMPLETE`, `ELEMENT_FOUND`, `ELEMENT_NOT_FOUND`, `REPLAN_START`, `REPLAN_COMPLETE`.
