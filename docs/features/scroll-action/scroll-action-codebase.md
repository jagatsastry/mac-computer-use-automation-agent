# Scroll Action & On-Fail/Replan Codebase Research

## 1. Scroll Action — Full Implementation Trace

### 1.1 Valid Action Registration

**`src/automation_agent/shared_models.py:83-94`** — `ActionStep.__post_init__()`:
`"scroll"` is in the `valid_actions` set. Any `ActionStep(action="scroll", ...)` passes validation.

**`src/automation_agent/shared_models.py:33-34`** — Action aliases:
```python
"scroll_down": "scroll",
"scroll_up": "scroll",
```
LLMs that emit `scroll_down` or `scroll_up` are auto-corrected to `scroll` by `ActionStep.from_dict()`.

### 1.2 Planner Prompt

**`src/automation_agent/planner/prompts/plan_from_prompt.md:29`**:
```
- `scroll`: Scroll the page. Params: `direction` ("up", "down", "left", "right"),
  `amount` (number of scroll clicks, default 3). Optional: `x`, `y` (coordinates to scroll at)
```

**`src/automation_agent/planner/prompts/replan_from_state.md`**:
Scroll is **NOT** listed in the replan prompt. The replan prompt does not enumerate available actions at all — it relies on the LLM already knowing them from the initial plan. This is acceptable because the replan prompt says "Respond with ONLY valid JSON" with the same step format.

### 1.3 Orchestrator Dispatch

**`src/automation_agent/orchestrator/agent.py:999-1016`** — `_dispatch_action()`:
```python
elif action == "scroll":
    direction = params.get("direction", "down")
    amount = int(params.get("amount", 3))
    clicks = amount if direction == "up" else -amount
    if direction in ("left", "right"):
        clicks = amount if direction == "right" else -amount
        result = self.actuator.scroll(
            clicks, x=params.get("x"), y=params.get("y"), horizontal=True,
        )
    else:
        result = self.actuator.scroll(
            clicks, x=params.get("x"), y=params.get("y"),
        )
```

**Direction-to-clicks mapping**:
- `"up"` → positive clicks (scroll up)
- `"down"` → negative clicks (scroll down)
- `"right"` → positive clicks, horizontal=True
- `"left"` → negative clicks, horizontal=True

Default: `direction="down"`, `amount=3`.

### 1.4 Actuator Implementation

**`src/automation_agent/actuator/applescript_actuator.py:139-169`** — `scroll()`:
```python
def scroll(self, clicks, x=None, y=None, horizontal=False):
    pyautogui.FAILSAFE = False
    if horizontal:
        pyautogui.hscroll(clicks, x=x, y=y)
    else:
        pyautogui.scroll(clicks, x=x, y=y)
    # Returns ActuatorResult.to_dict()
```

Uses `pyautogui.scroll()` / `pyautogui.hscroll()`. Positive clicks = up/right, negative = down/left. This matches the pyautogui convention and the orchestrator's direction mapping.

### 1.5 Protocol Gap

**`src/automation_agent/protocols.py`**: The `Actuator` protocol does **NOT** declare `scroll()`. This means:
- `scroll()` works because the concrete `AppleScriptActuator` has it
- But `@runtime_checkable` protocol checks won't verify scroll support
- The mock_actuator fixture in `tests/conftest.py:246-264` does **NOT** include `scroll`

### 1.6 Sibling Project Reference

**`~/workspace/macos-desktop-actions/src/desktop_actions/commands/scroll.py`**:
- Same pyautogui approach: `pyautogui.scroll(clicks, x=x, y=y)` / `pyautogui.hscroll()`
- Adds **coordinate bounds validation** against `pyautogui.size()` — our implementation does not
- Uses `pyautogui.FAILSAFE = False` — same as ours
- Sign convention matches: positive = up, negative = down

### 1.7 Retry Strategy Gap

**`src/automation_agent/orchestrator/agent.py:1761-1878`** — `_vary_strategy()`:
The method has explicit branches for `click`, `type_text`, `press_key`, `open_url`, `activate_app`, `quit_app` — but **NO branch for `scroll`**. A failed scroll falls through to the generic fallback at line 1877:
```python
else:
    params["_pre_delay"] = 0.5 * attempt
    return (f"generic_retry_with_delay_{attempt}", _retry_step(step.action, params))
```
This just adds a delay — it doesn't try a different scroll amount or position.

---

## 2. On-Fail / Replan Behavior — Full Flow Trace

### 2.1 on_fail Field

**`src/automation_agent/shared_models.py:79`**: Default is `"retry_different"`.
Valid values: `{"retry_different", "replan", "abort", "wait_for_user"}`.

### 2.2 Main Execution Loop (execute())

**`src/automation_agent/orchestrator/agent.py:203-276`** — The main `for i, step in enumerate(plan.steps)` loop:

```python
for i, step in enumerate(plan.steps):
    # ... max_iterations check ...
    result = await self._execute_step(i, step, step_results, goal, plan)
    step_results.append(result)
    iterations += 1

    if step.action == "done":
        break
    if step.action == "wait_for_user":
        continue

    if not result.success:
        recovery_result = await self._handle_failure(
            i, step, result, step_results, goal, plan, iterations
        )
        if recovery_result is None:
            # None = replan requested
            replan_result = await self._replan_and_continue(...)
            return replan_result
        else:
            step_results.append(recovery_result)
            iterations += 1
            if not recovery_result.success:
                if step.on_fail == "abort":
                    return ExecutionResult(success=False, ...)
                # *** BUG: Falls through to next step! ***
```

### 2.3 The On-Fail Bug — Why It's Broken

**The critical issue is at `agent.py:260-276`**: When `recovery_result.success` is False and `step.on_fail != "abort"`:

1. `_handle_failure()` returns a non-None `recovery_result` (e.g., for `wait_for_user` on_fail, or when `abort` returns the result as-is)
2. If `recovery_result.success` is False, the code only checks `step.on_fail == "abort"` to decide whether to stop
3. **For ALL other on_fail values**, the loop falls through to the next step via `continue` (implicit — no break/return)

**But wait** — let's trace `_handle_failure()` more carefully:

**`agent.py:1693-1759`** — `_handle_failure()`:

| on_fail value | Behavior | Returns |
|--------------|----------|---------|
| `"retry_different"` | Loops through retries. If all fail, returns `None` (signals replan) | `None` (replan) or successful `StepResult` |
| `"replan"` | Immediately returns `None` | `None` (replan) |
| `"abort"` | Returns the failure result as-is | Failed `StepResult` |
| `"wait_for_user"` | Returns a failed StepResult with evidence | Failed `StepResult` |

So the actual flow for each on_fail:

- **`retry_different`**: Retries → if all fail → returns `None` → triggers replan. **This works correctly.** The replan path exits the loop via `return replan_result`.
- **`replan`**: Returns `None` → triggers replan. **Works correctly.**
- **`abort`**: Returns failed result → `recovery_result.success` is False → `step.on_fail == "abort"` is True → returns failure. **Works correctly.**
- **`wait_for_user`**: Returns failed result → `recovery_result.success` is False → `step.on_fail == "abort"` is False → **falls through to next step**. This is the only value that has the bug, but it's an edge case.

**The REAL on_fail problem is different**: When `retry_different` exhausts retries, it ALWAYS escalates to replan (returns `None`). But the replan loop at `agent.py:1943-1966` (`_replan_and_continue()`) has its own issue:

**`agent.py:1958-1966`** — The replan execution loop:
```python
if not result.success:
    if step.on_fail == "abort":
        break
    # Don't recurse into another replan — just record the failure
    self.logger.log_event(...)
    # *** Falls through to next step in replan! ***
```

**This is the bug**: In the replan loop, when a step fails and `on_fail != "abort"`, it **continues to the next step**. There's no dependency checking — if step 2 of a replan fails, step 3 still executes even if step 3 depends on step 2.

### 2.4 The Core Problem Statement

The main loop handles failure well because it escalates to replan. But:

1. **In `_replan_and_continue()`**: Failed non-abort steps are logged but execution continues to subsequent dependent steps. This means if "Click the search box" fails, "Type search query" still executes, which is meaningless.

2. **No dependency tracking**: The system has no concept of step dependencies. Every step after a failure blindly executes.

3. **No "skip remaining" behavior**: When a critical step fails in a replan, there's no way to abort the remaining steps cleanly — only `on_fail="abort"` with an explicit `break` stops execution.

### 2.5 Where to Fix

The fix should be in `_replan_and_continue()` at **`agent.py:1958-1966`**. When a step fails in the replan loop:
- If `on_fail == "abort"` → break (already works)
- If `on_fail == "replan"` → break (can't recurse, so should abort)
- If `on_fail == "retry_different"` → **should break** instead of continuing, since we're already in a replan and can't replan again
- If `on_fail == "wait_for_user"` → keep current behavior (user intervention)

Additionally, the **main execution loop** at `agent.py:260-276` has a subtle issue: after `_handle_failure()` returns a non-None failed result for non-abort on_fail values, the loop continues. This only affects `wait_for_user` on_fail (since `retry_different` always returns None on exhaustion, and `replan`/`abort` are handled). But it could be tightened.

---

## 3. Verification After Scroll

Scroll steps go through the standard verification path in `_execute_step()`:
- `_dispatch_action()` returns `{"success": True, "output": "Scrolled down 3 clicks"}`
- Since `actuator_result["success"]` is True, verification proceeds
- `verifier.verify(step, actuator_result)` runs the 3-tier check
- For scroll, Tier 1 (actuator state) is usually inconclusive (no app/URL change)
- Falls through to Tier 2 (vision) which checks the `step.verify` condition

---

## 4. Test Infrastructure

### 4.1 Test Fixtures

**`tests/conftest.py`** — Shared fixtures:
- `mock_planner` — `AsyncMock` with `.plan` and `.replan`
- `mock_coordinator` — `AsyncMock` with `.find_element`, `.describe_screen`, `.verify_condition`, `.capture_screenshot`
- `mock_actuator` — `MagicMock` with `.click`, `.type_text`, `.press_key`, `.activate_app`, `.open_url`, `.quit_app`, `.get_state`
  - **Missing**: `.scroll` is NOT mocked
- `mock_skill_registry` — `AsyncMock` with `.match`, `.list_skills`, `.expand`, `.validate_all`
- `tmp_log_dir` — temp directory for EventLogger
- `_make_config()` helper in test files — creates `AgentConfig` with `_env_file=None, anthropic_api_key="test-key-not-real"`

### 4.2 Test File Patterns

**`tests/unit/test_orchestrator_new.py`** — Main orchestrator tests (71KB):
- Uses `_make_config()`, `_make_plan()`, `_make_agent()` helpers
- All components mocked, EventLogger is real
- Test classes: `TestSkillMatching`, `TestElementFinding`, `TestExecutionLoop`, `TestRetryStrategies`, `TestBugFixes`, etc.
- Pattern: set up mock returns → create agent → `await agent.execute("goal")` → assert results and mock calls

**Key test patterns for on_fail**:
- `test_verify_fail_retry_different` (line 341) — Tests retry_different triggers retry
- `test_verify_fail_replan` (line 372) — Tests replan is triggered
- `test_verify_fail_abort` (line 420) — Tests abort returns failure
- `test_element_not_found_triggers_replan` (line 735) — Tests replan on element not found

### 4.3 Key Gotcha

**`.env` leaks**: The `.env` file may contain `AGENT_MODEL_PROVIDER=anthropic` which pydantic-settings picks up. All test helpers must pass `_env_file=None` to `AgentConfig` to prevent this. The `_make_config()` pattern handles this.

---

## 5. Code Style & Constraints

From `pyproject.toml`:
- Python 3.11 target
- Line length: 100 (Black + Ruff)
- Ruff rules: E, W, F, I, B, C4, UP (ignores E501, B008)
- pytest-asyncio with `asyncio_mode = "auto"` — no need for `@pytest.mark.asyncio`
- Test markers: `unit`, `integration`, `e2e`, `manual`, `legacy`

---

## 6. Summary of Gaps Found

| Gap | Location | Severity |
|-----|----------|----------|
| `scroll` not in Actuator protocol | `protocols.py` | Low (works but not type-safe) |
| `scroll` not in mock_actuator fixture | `tests/conftest.py` | Medium (tests will fail if scroll mock is needed) |
| No scroll-specific retry strategy | `agent.py:_vary_strategy()` | Low (generic fallback works) |
| Scroll not listed in replan prompt | `replan_from_state.md` | Low (LLM still knows scroll from initial plan) |
| **Replan loop continues after step failure** | `agent.py:1958-1966` | **HIGH** — dependent steps execute after failure |
| Main loop falls through for wait_for_user on_fail | `agent.py:260-276` | Low (edge case) |
