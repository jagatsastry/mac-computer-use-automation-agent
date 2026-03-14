# Codebase Analysis: Walmart Return Fixes

Deep analysis of all 6 bug locations identified in the Walmart return customer testing gap report.

---

## P0-1: Plan Step Dropping During Parsing

### Root Cause (Two-Part Bug)

**Part A: Silent step dropping in `_parse_plan_response()`**

File: `src/automation_agent/planner/planner.py`, lines 458-465

```python
for s in data["steps"]:
    try:
        steps.append(ActionStep.from_dict(s))
    except ValueError as e:
        # LLM returned an invalid action — skip it rather than crash
        skipped.append(f"{s.get('action', '?')}: {e}")
```

`ActionStep.__post_init__()` (`shared_models.py:98-118`) raises `ValueError` for any action not in the valid set (`click`, `type_text`, `press_key`, `open_url`, `activate_app`, `quit_app`, `scroll`, `observe`, `wait_for_user`, `done`) or any `on_fail` not in (`retry_different`, `replan`, `abort`, `wait_for_user`).

If the LLM emits a step like `{"action": "select", "params": {...}}` or uses a non-aliased action name, the step is silently dropped. The alias map (`shared_models.py:32-51`) handles common misspellings but misses plausible LLM outputs like `select`, `choose`, `fill`, `submit_form`, etc.

The gap report says: "LLM response JSON contained 7 steps but only 6 were executed — the order-finding step was the one dropped." This is consistent with the LLM emitting an unrecognized action for the "find and click the order" step.

**Part B: Fallback plan truncation check is too coarse**

File: `src/automation_agent/orchestrator/agent.py`, lines 1421-1432

```python
@staticmethod
def _is_truncated_plan(plan: ActionPlan, fallback: Optional[ActionPlan]) -> bool:
    interaction_actions = {"click", "type_text", "scroll"}
    plan_has_interaction = any(s.action in interaction_actions for s in plan.steps)
    fallback_has_interaction = any(s.action in interaction_actions for s in fallback.steps)
    return fallback_has_interaction and not plan_has_interaction
```

This only triggers when the LLM plan has ZERO interaction steps. If the LLM plan has even one click (e.g., "Start a return button") but dropped another critical click (e.g., "find the order"), the truncation check passes and the damaged plan executes.

### Exact Code Path

1. LLM returns JSON with N steps, one using unrecognized action
2. `_parse_plan_response()` silently drops that step (line 463-465)
3. `_is_truncated_plan()` sees the remaining plan still has some clicks, returns False
4. Damaged plan executes, missing the critical order-finding step
5. Agent tries "Start a return button" on the orders list page, gets NOT_FOUND

### Fix Direction

- Add more action aliases (`select` -> `click`, `choose` -> `click`, `fill` -> `type_text`, etc.)
- Log a warning (not silent skip) when steps are dropped
- Consider mapping unknown actions to `click` with the action name as element description, rather than dropping

### Integration Points

- `ActionStep.from_dict()` at `shared_models.py:122-146`
- `_ACTION_ALIASES` at `shared_models.py:32-51`
- `_parse_plan_response()` at `planner/planner.py:402-480`
- `_is_truncated_plan()` at `orchestrator/agent.py:1421-1432`

### Test Infrastructure

- Unit tests for `ActionStep.from_dict()` can go in `tests/unit/test_shared_models.py`
- Unit tests for plan parsing in `tests/unit/test_planner.py`
- Existing pattern: `_make_config()` with `grounding_model=""`, `grounding_server_url=""`, `model_provider="local"`

---

## P0-2: Planner Skips Navigation on Stale Screen State

### Root Cause

The planner prompt (`plan_from_prompt.md`) injects `{{screen_description}}` under "Current Screen State" but contains NO instruction telling the LLM to always include navigation steps from the skill template regardless of what's currently on screen.

File: `src/automation_agent/planner/prompts/plan_from_prompt.md`, lines 8-9:
```
## Current Screen State
{{screen_description}}
```

When the screen description says "Walmart orders page is visible in Safari", the LLM sees the skill says "step 1: open_url: walmart.com/orders" and correctly infers the page is already open — so it skips the navigation. The LLM is making a reasonable optimization that happens to be wrong because:

1. The screen state may be stale (leftover from a previous run)
2. The page may need a fresh load to show current data
3. The skill's `open_url` step is a contract, not a suggestion

### Where Screen State Gets Injected

File: `src/automation_agent/orchestrator/agent.py`, lines 286-308:
```python
# 2. Get screen description for context
screen_desc = ""
...
screen_desc = await self.coordinator.describe_screen()
...
# 3. Plan
plan = await self.planner.plan(goal, **plan_kwargs)
```

The orchestrator calls `coordinator.describe_screen()` BEFORE planning and passes it directly. There is no "freshness" check — the screen description is whatever is currently visible.

### Fix Direction

Two complementary approaches:

**Approach A (Prompt fix)**: Add instruction to `plan_from_prompt.md`:
```
IMPORTANT: When a skill template specifies navigation steps (open_url, activate_app),
ALWAYS include them in the plan even if the screen appears to already show the target.
The current screen state may be stale from a previous task.
```

**Approach B (Orchestrator fix)**: When a skill is matched and has an `open_url` step, the orchestrator should prepend it to the plan regardless of what the LLM generated. This is already partially implemented via `_build_skill_fallback_plan()` but the fallback only activates when `_is_truncated_plan()` fires.

### Integration Points

- `plan_from_prompt.md` at `src/automation_agent/planner/prompts/plan_from_prompt.md`
- `_build_plan_prompt()` at `planner/planner.py:298-325`
- `execute()` at `orchestrator/agent.py:286-308` (screen description injection)
- `_build_skill_fallback_plan()` at `orchestrator/agent.py:1448-1486`

---

## P1-1: wait_for_user 120s Timeout When Already Logged In

### Root Cause (Two-Part Bug)

**Part A: Condition extraction only works for "If X, wait for user" syntax**

File: `src/automation_agent/orchestrator/agent.py`, lines 1789-1804

```python
@staticmethod
def _extract_wait_condition(text: str) -> str:
    match = re.match(
        r"^If\s+(.+?),\s*wait for (?:the )?user(?:\s+to\s+.+)?$",
        text.strip(),
        flags=re.IGNORECASE,
    )
```

The skill template says: `on_fail: If a login page appears, use wait_for_user to ask the user to log in, then continue`. But this `on_fail` text is NOT what ends up as the wait step's message.

The fallback plan compiler (`_compile_skill_instruction`, line 1575-1587) creates the wait step from the instruction text, not from the `on_fail` text. The `_parse_skill_steps()` method (line 1521-1543) only parses `- verify:` lines, NOT `- on_fail:` lines. So the `on_fail` directive with the conditional logic is completely lost.

When the LLM generates the plan, it emits something like:
```json
{"action": "wait_for_user", "params": {"message": "Please log in to Walmart"}}
```
This message does NOT match the `"^If\s+..."` regex pattern, so no condition is extracted, and the wait step always waits the full 120s.

**Part B: Screen-change detection is too crude for this scenario**

File: `src/automation_agent/orchestrator/agent.py`, lines 2296-2327

The `_wait_for_user()` method polls for a 2% pixel change every 5 seconds. When the user is already logged in, the orders page is static — no change occurs — so it runs the full 120s timeout. The method returns `success=True` even on timeout (line 2324-2327), so execution continues but 120s is wasted.

### Exact Code Path

1. Skill step 1: `open_url: walmart.com/orders` — succeeds, page loads
2. LLM plan includes `wait_for_user("Please log in")` because the skill mentions login
3. `_wait_for_user()` captures baseline screenshot
4. User is already logged in — orders page is static
5. Polls for 120s, no change detected
6. Returns `success=True` with evidence "Timed out after 120.0s — proceeding anyway"

### Fix Direction

- Parse `on_fail:` directives in `_parse_skill_steps()` alongside `verify:`
- When a wait_for_user step has a condition like "login page appears", extract it and pass as `condition` param
- In `_wait_for_user()`, when `condition` is set and `verify_condition()` returns False, skip immediately (this path already exists at lines 2257-2269 but is never reached because condition is never set)
- Alternative: reduce `_WAIT_TIMEOUT_S` to something less painful (30s) or add an `observe` step before the wait that checks if login is actually needed

### Integration Points

- `_wait_for_user()` at `orchestrator/agent.py:2248-2327`
- `_extract_wait_condition()` at `orchestrator/agent.py:1789-1804`
- `_compile_skill_instruction()` at `orchestrator/agent.py:1545-1609` (wait step creation)
- `_parse_skill_steps()` at `orchestrator/agent.py:1521-1543` (doesn't parse on_fail)
- `verify_condition()` at `vision/coordinator.py` (already exists, unused for this path)
- Constants: `_WAIT_POLL_INTERVAL_S = 5.0`, `_WAIT_TIMEOUT_S = 120.0`, `_WAIT_DIFF_THRESHOLD = 0.02`

---

## P1-2: No Scroll Recovery on NOT_FOUND

### Root Cause (Three-Part Bug)

**Part A: Infeasibility fires immediately on first NOT_FOUND (bypasses retries)**

File: `src/automation_agent/orchestrator/agent.py`, lines 454-472

```python
# AC-4: critical-path absence — immediate trigger
if (
    not result.success
    and step.action == "click"
    and step.params.get("element")
    and result.error
    and "not found" in result.error.lower()
):
    infeas_result = await self._check_infeasibility(
        goal, frustration, step_results, force=True
    )
    if infeas_result is not None:
        ...
        return infeas_result
```

This runs BEFORE `_handle_failure()` (line 512-530). On the first NOT_FOUND for any click step, it calls `_check_infeasibility(force=True)` which asks the LLM if the task is achievable. If the LLM says "infeasible" (which it does when "Start a return button" can't be found on the orders list page), the task aborts immediately with 0 retries and 0 replans.

The `_handle_failure()` with `retry_different` retries and the `_vary_strategy()` retry loop never get a chance to run.

**Part B: `_vary_strategy()` has no scroll-down strategy**

File: `src/automation_agent/orchestrator/agent.py`, lines 3023-3140

The retry strategy generator handles:
- `scroll_to_top` (line 3069-3071) — but only when `reflection_hint == "scroll_to_top"`
- `refine_element_query` (line 3075-3078) — rephrases the element description
- `keyboard_fallback_enter` (line 3083-3084) — presses Enter
- `keyboard_fallback_space` (line 3086) — presses Space

There is NO strategy that scrolls down to look for off-screen elements. When a click element is not found, the agent never tries scrolling the page to reveal it.

**Part C: Skill `on_fail` directives are not parsed by fallback compiler**

File: `src/automation_agent/orchestrator/agent.py`, `_parse_skill_steps()` lines 1521-1543

The skill template says:
```
3. click: Find and click the order that matches "{{item}}"
   - verify: Order details page is visible
   - on_fail: If the item is not visible in the current order list, scroll down to find it
```

But `_parse_skill_steps()` only extracts `- verify:` lines. The `- on_fail:` directives are completely ignored. So even when the skill explicitly says "scroll down", this instruction is lost.

### Exact Code Path

1. Agent executes click for "Order containing 'Crest 3D Whitestrips'"
2. Vision model returns NOT_FOUND (element is below the fold)
3. Execute loop at line 454 checks: `step.action == "click"` and `"not found" in result.error.lower()` — True
4. `_check_infeasibility(force=True)` fires
5. LLM says infeasible: "The element was not found on the current screen"
6. Agent returns failure with 0 retries, 0 replans, 0 scrolls

### Fix Direction

- Add a scroll-down-and-retry strategy in `_vary_strategy()` for click NOT_FOUND (should be attempt 1 or 2)
- Consider NOT firing infeasibility check on first NOT_FOUND — let retries/scroll happen first, then check infeasibility after retries exhausted
- Parse `on_fail:` directives from skill templates in `_parse_skill_steps()` and use them to guide retry strategy
- The `on_fail: scroll` from the skill should translate to a scroll action before retrying the click

### Integration Points

- Infeasibility trigger: `orchestrator/agent.py:454-472` (runs before `_handle_failure`)
- `_handle_failure()`: `orchestrator/agent.py:2935-3021`
- `_vary_strategy()`: `orchestrator/agent.py:3023-3140`
- `_parse_skill_steps()`: `orchestrator/agent.py:1521-1543`
- Scroll action execution: `orchestrator/agent.py:2020-2040`

---

## P2-1: Duplicate Skill Files

### Root Cause

Three files define walmart return skills:

| File | `name:` field | Content |
|------|--------------|---------|
| `return_walmart_order.md` | `return-walmart-order` | **Real** skill — 5 steps, proper params, detailed error recovery |
| `return-walmart-order.md` | `return-walmart-order` | **Stub** — 1 step ("Go to orders"), no `item` param, `trusted: false` |
| `return-walmart-order-2.md` | `return-walmart-order-2` | **Stub** — 1 step ("Do thing"), no params |

File: `src/automation_agent/skills/registry.py`, lines 178-195

```python
for md_file in sorted(path.glob("*.md")):
    ...
    if skill.name in self._skills:
        std_logger.warning(
            "Duplicate skill name '%s': '%s' overwrites previous definition",
            skill.name,
            md_file,
        )
    self._skills[skill.name] = skill
```

Loading order is `sorted()` by filename:
1. `return-walmart-order-2.md` → name `return-walmart-order-2` (unique, stored)
2. `return-walmart-order.md` → name `return-walmart-order` (stored)
3. `return_walmart_order.md` → name `return-walmart-order` (OVERWRITES #2)

Since `_` sorts after `-` in ASCII, `return_walmart_order.md` (the real skill) loads LAST and overwrites the stub. So the real skill actually wins in the current sort order. However, the gap report says the stub was winning — this may depend on the filesystem's locale-aware sorting or a different loading order in production.

Regardless, having duplicate skill names is a latent bug. The stub files should be removed or renamed to have unique names.

### Fix Direction

- Delete the stub files (`return-walmart-order.md` and `return-walmart-order-2.md`) or rename them to have unique `name:` fields
- Add a validation check in `load_from_directory()` that refuses to load duplicate names (fail fast instead of silent overwrite)
- Alternatively, prefer the file with `trusted: true` (or without `trusted: false`) when duplicates exist

### Integration Points

- `load_from_directory()` at `skills/registry.py:167-195`
- Skill files at `src/automation_agent/skills/library/`
- `validate_all()` at `skills/registry.py:528-542` (could add duplicate check)

---

## P2-2: No Screenshots Saved During Execution

### Root Cause

Screenshots are captured in-memory (base64) but only saved to disk in ONE place: Tier 2 vision verification.

File: `src/automation_agent/orchestrator/verifier.py`, lines 142-149:
```python
if screenshot_b64 and self.logger:
    try:
        screenshot_path = self.logger.save_screenshot(
            base64.b64decode(screenshot_b64),
            f"verify_step_{step.action}",
        )
    except Exception:
        pass
```

This is the ONLY call to `save_screenshot()` in the entire execution loop. It only runs during Tier 2 verification, which only runs when Tier 1 (Hammerspoon state query) is inconclusive.

Screenshots are NOT saved during:
- `observe` steps (`agent.py:672-679`) — captures and describes but doesn't save
- NOT_FOUND events — `_find_element()` captures but only saves a debug crop image (`_save_debug_image`, line 2230-2246) to a debug dir, not the event log
- Step execution — the `_execute_action()` method doesn't save screenshots at all
- `wait_for_user` polling — captures baseline and diffs but doesn't save

The `EventLogger` has `save_screenshot()` (line 73-85) and `save_screenshot_file()` (line 87-98) methods ready to use, but they're only called from the verifier.

### Existing Infrastructure

- `EventLogger.save_screenshot(image_data: bytes, name: str) -> str` at `logging/event_logger.py:73-85`
- `EventLogger.save_screenshot_file(source_path: str, name: str) -> str` at `logging/event_logger.py:87-98`
- Screenshots go to `self.screenshots_dir` (a subdirectory of the run log directory)
- `_save_debug_image()` at `orchestrator/agent.py:2230-2246` saves to a `debug/` subdirectory
- `_capture_screenshot()` at `orchestrator/agent.py:2358-2363` is a thin wrapper that returns b64

### Fix Direction

Add `save_screenshot()` calls at key execution points:

1. **After observe steps** (`agent.py:672-679`): Save the screenshot that was just described
2. **On NOT_FOUND** (`agent.py` in `_execute_step` or `_find_element`): Save the full screenshot, not just the debug crop
3. **After each step execution**: Optionally save a post-action screenshot (gated by config flag to avoid disk bloat)
4. **On infeasibility abort**: Save the final screenshot for post-mortem

### Integration Points

- `_execute_step()`: `orchestrator/agent.py:640+` (main step dispatch)
- `observe` handler: `orchestrator/agent.py:672-679`
- `_find_element()`: `orchestrator/agent.py:2050-2080` (NOT_FOUND path)
- `EventLogger`: `logging/event_logger.py:73-98`
- Config: could add `AGENT_SAVE_SCREENSHOTS=true` to `config.py`

---

## Patterns for Test Infrastructure

### Making Config for Tests

All test files use a `_make_config()` helper that pins local settings to avoid `.env` leaks:

```python
def _make_config(**overrides):
    return AgentConfig(
        grounding_model="",
        grounding_server_url="",
        model_provider="local",
        **overrides,
    )
```

### Common Test Mocking Pattern

```python
from unittest.mock import AsyncMock, MagicMock, patch

planner = AsyncMock()
coordinator = AsyncMock()
actuator = MagicMock()
skill_registry = AsyncMock()
config = _make_config()

agent = AutomationAgent(planner, skill_registry, coordinator, actuator, config)
```

### Async Tests

pytest-asyncio with `asyncio_mode = "auto"` — just use `async def test_...()`.

### Existing Test Files

- `tests/unit/test_planner.py` — planner tests
- `tests/unit/test_skill_librarian.py` — skill system tests
- `tests/unit/test_vision_arch_improvements.py` — 79 tests for orchestrator features
- `tests/unit/test_shared_models.py` (if exists) or inline in relevant test files

---

## Summary: Fix Priority vs. Code Complexity

| Bug | Priority | Files to Change | Complexity | Key Risk |
|-----|----------|----------------|------------|----------|
| P0-1 Step dropping | P0 | `shared_models.py`, `planner.py` | Low | Adding aliases is safe; changing silent-skip to warn+map needs care |
| P0-2 Stale screen nav | P0 | `plan_from_prompt.md`, optionally `agent.py` | Low | Prompt change is low risk; orchestrator nav prepend needs testing |
| P1-1 wait_for_user 120s | P1 | `agent.py` (`_parse_skill_steps`, `_wait_for_user`) | Medium | Condition extraction regex is fragile; needs multiple test cases |
| P1-2 No scroll recovery | P1 | `agent.py` (`_vary_strategy`, infeasibility trigger) | Medium-High | Must reorder infeasibility vs. retry logic; scroll strategy needs element re-find |
| P2-1 Duplicate skills | P2 | Delete 2 stub files, optionally `registry.py` | Low | Just file deletion + optional validation |
| P2-2 No screenshots | P2 | `agent.py` (multiple insertion points) | Low | Add save calls; gate behind config flag for disk usage |
