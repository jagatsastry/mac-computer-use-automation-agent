# Codebase Research: Verification, Retry/Replan, and Skills Internals

This document provides a deep technical analysis of the verification, retry/replan, and skills subsystems, written to support the P0 verification fixes identified in `docs/amazon-return-customer-report.md`.

---

## 1. Verification Flow (End-to-End)

### Entry Point
`AutomationAgent._execute_step()` in `src/automation_agent/orchestrator/agent.py:337-482`.

After dispatching an action via `_dispatch_action()`, the orchestrator calls:
```python
verification = await self.verifier.verify(step, actuator_result)
```

**Critical gate before verification** (agent.py:408-434): If the actuator itself reported failure (e.g., element not found), verification is **skipped entirely** and the step fails immediately. This is the "BUG 1 FIX" -- vision verification cannot override a real actuator failure.

### StepVerifier.verify() -- The 3-Tier Cascade
File: `src/automation_agent/orchestrator/verifier.py:45-179`

```
verify(step, actuator_result)
  -> If no verify condition and action is done/wait_for_user: return actuator success
  -> If no verify condition and action is anything else: return FAIL (BUG 5 FIX)
  -> Tier 0: Accessibility (_verify_tier0) -- ~0ms
  -> Tier 1: Actuator state (_verify_tier1) -- ~50ms
  -> Tier 2: Vision (_verify_tier2) -- ~2-5s
  -> If no backend available: fall back to actuator result
```

Each tier returns:
- `(True, evidence)` -- conclusive pass, stop here
- `(False, evidence)` -- conclusive fail, stop here
- `None` -- inconclusive, escalate to next tier

**The cascade is short-circuiting**: first conclusive result wins. There is no "majority vote" or combining of tiers.

### Tier 0: Accessibility (verifier.py:241-297)
- Only runs if an accessibility backend is present (non-async, non-None)
- Handles: `activate_app` with app_name, app-related verify keywords, `type_text` with focused element check
- **P0 #1 relevance**: For `type_text`, it checks `accessibility.get_focused_element()` and compares the element's value/title/description to the expected text. Returns `None` (inconclusive) if focused element is None or has no actual_value. **It does NOT check focus state for click actions** -- there's no Tier 0 check for "search field is focused after clicking it."

### Tier 1: Actuator State (verifier.py:299-381)
- Uses `actuator.get_state()` which returns `{app_name, window_title, ...}`
- Handles: `activate_app`, app-keyword verify text, `open_url` (checks browser app + window title), `type_text` (checks focused_value/focused_text/selected_text keys)
- For `click` actions: **always returns None** (inconclusive) -- escalates to Tier 2
- **P0 #1 relevance**: For `type_text`, Tier 1 checks state keys `focused_value`, `focused_text`, `selected_text`. But for a click-on-search-field step, the action is `click`, not `type_text`, so Tier 1 has no handler and returns None.

### Tier 2: Vision (verifier.py:383-470)
- Takes a screenshot and calls `coordinator.verify_condition(condition, screenshot_b64=...)`
- `verify_condition()` (coordinator.py:611-636) sends the condition + screenshot to the vision model and parses a YES/NO response. **Conservative: ambiguous = False** (`response_lower.startswith("yes")`)
- Multiple condition attempts in order:
  1. If click action with image_x/image_y: crop a 400x400 region around click point and verify with `expected_observation` or `verify`
  2. If `type_text`: verify `'The focused text field contains "{text}"'` (special-cased, verifier.py:424-438)
  3. If `open_url`: build URL-token condition and verify
  4. Fall through to `_tier2_conditions(step)` which tries `expected_observation` then `verify`
- **If ALL conditions fail**: returns `(False, f"Vision denies: {condition}")` (verifier.py:469-470)

**P0 #1 root cause**: When the step is `click` on a search field with verify="The search field becomes active with a text cursor":
1. Tier 0: No handler for click actions -> None
2. Tier 1: No handler for click actions -> None
3. Tier 2: Crops around click point, asks vision model if "The search field becomes active with a text cursor" -> **vision model says NO** (false negative)
4. Result: step fails, triggers retry/replan cascade

The vision model cannot reliably detect subtle CSS focus states (blue outline, blinking cursor) at downscaled resolution.

### Tier 2 Special Cases
- **Click region cropping** (verifier.py:407-422, 472-499): Crops a 400x400px region around the click point. If the crop verification passes, it short-circuits. This is the first thing tried for click actions.
- **type_text special handling** (verifier.py:424-438): Creates a custom condition `'The focused text field contains "{text}"'` and verifies against the full screenshot. This runs BEFORE the generic condition fallthrough.
- **open_url special handling** (verifier.py:440-454): Builds URL-token conditions.

### Post-Verification Reflection (agent.py:445-451)
If verification fails BUT `visible_effect` was detected (screenshot diff showed change) AND the coordinator has `reflect_action_outcome`:
```python
verification = await self._reflect_failed_action(step, actuator_result, verification)
```
This asks the vision model "what actually happened?" and can flip `verification.success` to True if the model says `worked=yes`. Also sets `reflection_hint` and `reflection_observed` on the StepResult.

---

## 2. Retry/Replan Flow

### Failure Handling Entry Point
`AutomationAgent.execute()` in agent.py:251-292:
```python
if not result.success:
    recovery_result = await self._handle_failure(i, step, result, ...)
    if recovery_result is None:  # None = replan requested
        replan_result = await self._replan_and_continue(...)
        return replan_result
    else:
        step_results.append(recovery_result)
        if not recovery_result.success:
            # Recovery also failed -- stop
            return ExecutionResult(success=False, ...)
```

### _handle_failure() (agent.py:1821-1887)
Routes based on `step.on_fail`:

| on_fail value | Behavior |
|---------------|----------|
| `retry_different` | Loops through retries via `_vary_strategy()`. If all retries fail, returns `None` (triggers replan) |
| `replan` | Returns `None` immediately (triggers replan) |
| `abort` | Returns the failed result as-is (execution stops) |
| `wait_for_user` | Returns failed result with "waiting for user" evidence |

### _vary_strategy() (agent.py:1889-2006)
Produces genuinely different retry approaches based on step type and attempt number:

**For `click` with element:**
- If element not found + suggested_element exists:
  - Attempt 1: `visible_alternative_affordance` -- use suggested element
  - Attempt 2: `refine_visible_alternative_affordance` -- refined description
  - Attempt 3+: escalate to replan
- If element not found + no suggestion:
  - Attempt 1: `refine_missing_target_query` -- add context to element description
  - Attempt 2+: escalate to replan
- If reflection_hint:
  - `dismiss_modal` -> press Escape
  - `scroll_to_top` -> Cmd+Up pre-key
  - `refine_target` -> add "look carefully" context
- Default (click failed verification):
  - Attempt 1: `refine_element_query` -- add "(look carefully, may be partially hidden)"
  - Attempt 2 (search/filter element): `jump_to_page_top_and_retry_click`
  - Attempt 2 (other): `keyboard_fallback_enter` -- press Return
  - Attempt 3: `keyboard_fallback_space` -- press Space

**For `type_text`:**
- If reflection_hint=`refocus_text_field`: `_clear_first=True`
- Attempt 1: `select_all_then_type` -- Cmd+A then retype
- Attempt 2+: `slow_type_retry` -- character-by-character

**For `open_url`:**
- Attempt 1: `browser_address_bar_fallback` -- Cmd+L, type URL, Return
- Attempt 2+: increasing delay

**For `activate_app`:**
- Attempt 1: `quit_and_relaunch`
- Attempt 2+: `spotlight_launch`

### Alternative Affordance Suggestion (agent.py:1624-1704)
When a click target is not found (`"Element not found:"` error), `_suggest_alternative_for_missing_target()` runs:
1. **Skill error recovery hints first** (`_check_skill_error_recovery`, agent.py:1706-1755): Scans the `## Error Recovery` section of the skill template for lines mentioning the missing target. Extracts quoted alternative from after the colon.
2. **Vision model fallback**: Calls `coordinator.suggest_alternative_affordance()` (if implemented) with the missing target, goal, and screenshot.
3. Sets `result.reflection_hint = "use_alternative_affordance"` and `result.suggested_element = affordance`

### _replan_and_continue() (agent.py:2008-2154)
1. Gets fresh screen description
2. Collects all retry strategies used so far
3. Gets desktop context from ContextMonitor
4. Re-assembles skill_context with updated derived procedure
5. Calls `planner.replan(goal, screen_desc, step_results, retry_strategies, ...)`
6. Applies `ReplanPatch` to `DerivedSkillSession` if present
7. Executes the new plan steps with full failure handling
8. **Does NOT recurse**: if a replan step wants to replan again, it breaks instead of recursing

### Replan Prompt (planner/prompts/replan_from_state.md)
Key sections:
- `{{history}}` -- formatted as `Step N: action(params) -> SUCCESS/FAILED: evidence`
- `{{retry_strategies}}` -- comma-separated strategies tried
- `{{skill_context}}` -- includes derived procedure if available
- **Critical instruction**: "You MUST try a DIFFERENT approach"
- **derived_skill_patch**: LLM is asked to include label replacements, failed assumptions, and successful adaptations

**P0 #2 relevance**: The replan prompt has NO instruction about recognizing that a required UI element is absent from the page. It only says "try a DIFFERENT approach." There is no concept of "this element does not exist and never will, so inform the user." The planner can only retry or try different paths -- it cannot conclude "impossible."

---

## 3. Element Finding

### Entry Point
`AutomationAgent._find_element()` in agent.py:1064-1150

### Flow
1. Capture screenshot
2. If `grounding_router` is set:
   - `grounding_router.find_element(description)` -- MoG routing
   - Returns `GroundingResult` with x, y, strategy_used, confidence
3. Else (legacy path):
   - Get accessibility candidates from actuator
   - Optionally crop screenshot (Rec 4)
   - Call `coordinator.find_element(description, screenshot_b64=..., candidates=...)`
4. Normalize result (image/screen coordinate mapping)
5. Return `FindElementResult` or `None`

### GroundingRouter (grounding_router.py)
- Classifies descriptions by keyword → ordered strategy list
- Tries strategies in order: ACCESSIBILITY, VISION, OCR
- OCR is intentionally disabled (returns None always)
- ACCESSIBILITY: uses `_get_accessibility_matches()` with `find_elements(role, title_contains)` and `_element_match_score()` scoring
- VISION: calls `vision_coordinator.find_element(description)`
- Optional LLM tie-breaker when AX is ambiguous (multiple or zero candidates)

### What Happens When Element Not Found
- `_find_element()` returns `None`
- `_dispatch_action()` returns `{"success": False, "error": f"Element not found: {description}"}`
- `_execute_step()` skips verification, calls `_suggest_alternative_for_missing_target()`
- Result goes to `_handle_failure()` -> `_vary_strategy()`

**P0 #2 gap**: There is NO distinction between "element not found YET" (transient) vs "element does not exist on this page" (permanent). The retry strategy treats both the same: refine query, try alternative, escalate to replan. The system can never conclude "this UI element fundamentally doesn't exist here."

---

## 4. Skill Template Format

### File Location
`src/automation_agent/skills/library/*.md`

### Format (from return_amazon_order.md)
```markdown
---
name: return-amazon-order
skill-id: return-amazon-order
description: Return an item or package on Amazon
summary: ...
tags: [ecommerce, return, refund, amazon]
trigger-keywords: [return, send back, refund, amazon]
parameters:
  item:
    type: string
    required: true
    description: What to return
requires:
  os: darwin
success-condition: Return confirmation with label or drop-off instructions visible
max-retries: 3
---

## Steps
1. Use open_url to navigate to https://...
   - verify: Amazon orders page or login page visible
2. If login page is visible, wait for user to sign in
   - verify: Orders page loaded with search functionality
...

## Error Recovery
- If login page appears at step 1: ...
- If "Return or Replace Items" is not visible: ...

## Notes
- Treat the named controls as likely affordances, not guaranteed literal text
```

### Parameter Substitution
In `SkillRegistryImpl` (not read fully, but usage visible in agent.py):
- `skill_registry.match(goal)` returns `SkillMatchResult` with `skill_name`, `expanded_steps`, `params`, `candidates`, `skill_context`
- `skill_registry.expand(skill_name, params)` does `{{param}}` placeholder substitution
- Expanded steps are passed as `skill_context` to the planner

### Skill Fallback Plan Compilation
`AutomationAgent._build_skill_fallback_plan()` (agent.py:503-541):
- Compiles skill steps into executable `ActionStep` objects
- Handles: `open_url`, `activate_app`, `click`, `type_text`, `press_key`, `wait_for_user`, `done`
- Uses regex pattern matching on instruction text
- Only used when planner returns a trivial "done-only" plan
- Supports stop conditions from goal text ("stop as soon as X")

### Error Recovery Section
`_check_skill_error_recovery()` (agent.py:1706-1755):
- Regex-finds the `## Error Recovery` section
- For each line, checks if `missing_target.lower()` appears in the line
- Extracts quoted text or "click/look for/try/use X" pattern from after the colon
- Returns the first matching suggestion

**P0 #2 relevance**: The error recovery section in `return_amazon_order.md` does handle some absent-element cases:
```
- If "Return or Replace Items" is not visible: look for "View item", "Order details", ...
- If item not eligible for return: abort with message to user
```
But the orchestrator has NO mechanism to actually "abort with message to user" -- there is no `tell_user` action. The skill can describe what to do, but the planner/orchestrator can't execute "inform the user this is impossible."

---

## 5. Test Infrastructure and Patterns

### Test Layout
```
tests/
  conftest.py          -- shared fixtures (mock_planner, mock_coordinator, etc.)
  unit/
    test_orchestrator_new.py  -- orchestrator tests (most relevant)
    test_verifier.py          -- verifier tests (most relevant)
    test_planner.py           -- planner tests
    test_coordinator.py       -- vision coordinator tests
  orchestrator/
    conftest.py          -- legacy fixtures (ActionResult, Observation)
    test_grounding_router.py  -- grounding router tests
  integration/
    ...
  e2e/
    ...
```

### Key Fixtures (tests/conftest.py)

**`mock_planner`**: AsyncMock with `.plan` returning a simple Safari activate+done plan, `.replan` returning similar.

**`mock_coordinator`**: AsyncMock with:
- `find_element` -> `FindElementResult(x=500, y=300, confidence=0.9, source="vision")`
- `describe_screen` -> `"Desktop with Safari open"`
- `verify_condition` -> `True`
- `capture_screenshot` -> base64 fake data

**`mock_actuator`**: MagicMock (sync) with all actions returning `{"success": True, "output": ""}`, `get_state` returns Safari info.

**`mock_skill_registry`**: MagicMock with `match` -> None, `expand` -> None.

**`tmp_log_dir`**: `tmp_path / "test_logs"`, used with `EventLogger(tmp_log_dir)`.

### Config Construction Pattern
```python
def _make_config(**overrides) -> AgentConfig:
    defaults = {"_env_file": None, "anthropic_api_key": "test-key-not-real"}
    defaults.update(overrides)
    return AgentConfig(**defaults)
```

**LANDMINE**: `.env` file leaks `AGENT_MODEL_PROVIDER=anthropic` into pydantic-settings. Tests MUST include `model_provider="local"` in `_make_config()` or validation will fail trying to resolve Anthropic vision model coordinate spaces.

### Agent Construction Pattern
```python
def _make_agent(planner, skill_registry, coordinator, actuator, logger, config=None):
    if config is None:
        config = _make_config()
    return AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
    )
```

### Test Assertion Patterns
- `mock_planner.plan.assert_awaited_once()` -- verify async call
- `mock_actuator.click.assert_called_once_with(x, y)` -- verify sync call
- `result.success is True/False` -- check execution result
- Check `result.steps[-1].evidence` for verification evidence
- Check `EventType.X in [e.event_type for e in logger.events]` for logged events
- `mock_coordinator.verify_condition.call_args` to inspect what was verified

### Async Test Mode
`pyproject.toml` has `asyncio_mode = "auto"` -- all `async def test_*` functions run automatically without `@pytest.mark.asyncio`.

---

## 6. Landmines and Fragile Code

### 1. Pydantic Settings .env Leak
**File**: `src/automation_agent/config.py`
**Issue**: If `.env` contains `AGENT_MODEL_PROVIDER=anthropic`, the config will try to validate the Anthropic vision model against COORDINATE_SPACES. In tests, always pass `model_provider="local"` and `_env_file=None`.

### 2. Accessibility Backend Detection is Fragile
**File**: `verifier.py:181-199` (`_get_accessibility_backend`)
**Issue**: Returns None if methods are async or AsyncMock. The check `"AsyncMock" in type(method).__name__` is a test-time workaround. If you add async accessibility methods, Tier 0 silently disables.

### 3. verify_condition is Binary (YES/NO)
**File**: `coordinator.py:611-636`
**Issue**: Returns only True/False based on `response.startswith("yes")`. There is NO confidence score, NO partial match, NO "maybe" -- just binary. Any verification condition that the vision model finds ambiguous defaults to False (conservative).

### 4. Tier 2 Short-Circuits on First Pass
**File**: `verifier.py:406-462`
**Issue**: The crop-region check runs FIRST for click actions. If the crop passes, later conditions are never checked. If the crop fails, the code falls through to type_text special handling, then open_url, then generic conditions. This ordering matters -- changing it could break existing passing verifications.

### 5. _vary_strategy Attempt Counting
**File**: `agent.py:1889-2006`
**Issue**: `attempt = prev_result.retry_count + 1` -- strategies are selected based on attempt number. Adding new strategies at attempt 1 would shift existing strategies to later attempts. Always add new strategies at NEW attempt numbers or use the reflection_hint path.

### 6. _handle_failure Returns None to Signal Replan
**File**: `agent.py:1821-1887`
**Issue**: `return None` means "escalate to replan." This is a sentinel value. Don't accidentally return None from new failure handling code unless you intend to trigger replan.

### 7. Replan Does Not Recurse
**File**: `agent.py:2094-2108`
**Issue**: If a step in the replanned plan exhausts retries, the code `break`s instead of triggering another replan. This is intentional (prevents infinite replan loops). Only ONE level of replanning is allowed.

### 8. on_fail Default is "retry_different"
**File**: `shared_models.py:79`
**Issue**: `on_fail: str = "retry_different"`. Plans that don't specify `on_fail` will retry 3 times then replan. To abort immediately, plans must explicitly set `on_fail="abort"`.

### 9. Screenshot Diff Gate
**File**: `agent.py:383-403`
**Issue**: If `screenshot_diff` is set and a click has no visible effect, `actuator_result["success"]` is forcibly set to False. This skips verification entirely (the actuator-failure gate at line 408 catches it). This means a click that succeeded but had no visual change (e.g., focusing a field without CSS change) is treated as a failure.

### 10. The Skill Error Recovery Parser is Simplistic
**File**: `agent.py:1706-1755`
**Issue**: `_check_skill_error_recovery` does case-insensitive substring match of missing_target against each line, then extracts the first quoted text after the colon. If the skill line doesn't have the exact missing element name, the hint won't match. Multi-word element descriptions may not match against partially quoted hints.

---

## 7. Integration Points for P0 Fixes

### P0 #1: False-Negative Vision Verification of Text Field Focus

**Where to fix**:
1. **verifier.py `_verify_tier0`** (line 241): Add a handler for `click` actions where the target element description contains text-field-related keywords ("search", "text field", "input", "search bar"). When the click target is a text field, check accessibility for focus state rather than requiring vision to detect it.

2. **verifier.py `_verify_tier2`** (line 383): Add a special case before the generic condition fallthrough: when the step is a `click` and the verify condition mentions "focus" or "cursor" or "active" on a text/search field, use a more lenient verification strategy (e.g., check if ANY change is visible in the click region rather than confirming the exact condition).

3. **agent.py `_execute_step`** (alternative approach): For click-on-text-field steps, skip verification of the click itself and instead immediately proceed to the next step (type_text). The type_text step's verification will catch whether the click actually worked. This is the "verify by consequence" pattern.

**Data flow for fix**:
```
ActionStep(action="click", params={"element": "Search all orders"},
           verify="The search field becomes active with a text cursor")
  -> _dispatch_action: finds and clicks element, returns {success: True, x, y}
  -> [NEW] detect this is a click-on-text-field step
  -> [NEW] use accessibility check or lenient verification
  -> OR: mark as provisionally passed and let next step (type_text) validate
```

### P0 #2: No "Element Absent" / Negative-Result Awareness

**Where to fix**:
1. **shared_models.py**: Add new `on_fail` value `"inform_user"` or new action `"tell_user"`. Or add an `on_absent` field to `ActionStep` that specifies what to do when the expected element doesn't exist.

2. **agent.py `_vary_strategy`** (line 1889): After exhausting retries for "element not found", instead of always escalating to replan, check if the element is fundamentally absent. Could use `coordinator.verify_condition(f'There is no "{element}" button or link visible anywhere on this page')` to distinguish transient vs permanent absence.

3. **replan_from_state.md**: Add instructions for the planner to recognize when a UI element is absent because the page state doesn't support that action (e.g., "if the previous attempts show that a 'Return' button is not present after multiple scrolls, consider that the item may not be eligible for return and use wait_for_user to inform the user").

4. **return_amazon_order.md skill**: Enhance error recovery with more specific absent-element guidance: subscription orders, digital items, expired return windows. Add verify conditions that check for negative signals (e.g., "If 'Manage your subscription' is visible instead of 'Return or replace items'").

**Data flow for fix**:
```
Step fails: "Return or Replace Items" not found after scroll
  -> _vary_strategy: retries exhausted
  -> [NEW] before replan, ask vision: "Is there a return button anywhere on this page?"
  -> Vision: "NO"
  -> [NEW] check skill context for absent-element guidance
  -> [NEW] recognize this as permanent absence, not transient
  -> [NEW] either abort with user message, or pass absence info to replan prompt
  -> Replan prompt [UPDATED] includes: "The return button is absent from this page,
     which may mean this item is not eligible for return"
```

### P1 #4: Skill Template Improvements

**Where to fix**: `src/automation_agent/skills/library/return_amazon_order.md`

Key improvements needed:
1. Document Amazon's actual date filter options (no "past 6 months")
2. Add guidance for vague/unspecified items
3. Add digital order/subscription detection
4. Add return window expiration awareness
5. Enhance error recovery with subscription page signals

---

## 8. Patterns to Follow

1. **Use fixtures from `tests/conftest.py`** -- don't recreate mocks
2. **Always include `_env_file=None` and `model_provider="local"` in test configs**
3. **Keep verification tiers independent** -- each tier should be testable in isolation
4. **Return `None` from tier methods for inconclusive** -- never return False for inconclusive
5. **Use `StepResult` dataclass fields** for passing metadata (reflection_hint, suggested_element, etc.)
6. **New on_fail values must be added to `valid_on_fail` set in `ActionStep.__post_init__`**
7. **New actions must be added to `valid_actions` set in `ActionStep.__post_init__`**
8. **Test both the happy path and the escalation path** for any tier changes
9. **Keep the replan-no-recurse invariant** -- never allow nested replanning

## 9. Patterns to Avoid

1. **Don't make verify_condition return anything other than bool** -- the entire verification cascade depends on this
2. **Don't add async methods to accessibility backends** -- `_get_accessibility_backend` filters them out
3. **Don't change the order of Tier 2 condition checks** without updating tests
4. **Don't use `return None` in `_handle_failure` unless you mean "trigger replan"**
5. **Don't add strategy branches in `_vary_strategy` at existing attempt numbers** -- use new attempt numbers or reflection_hint paths
6. **Don't modify `ActionStep.from_dict()` aliases without updating `__post_init__` valid_actions**
7. **Don't add fields to StepResult without updating `__post_init__` validation** (currently only validates verification_method)
