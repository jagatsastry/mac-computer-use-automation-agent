# Architecture Spec: Scroll Action Tests + On-Fail/Replan Fix

## Domain Slices

### Slice 1: Scroll Action Tests (Engineer 1)

**New file:** `tests/unit/test_scroll_action.py`

Tests:

```python
# --- shared_models.py tests ---

def test_scroll_is_valid_action():
    """ActionStep(action="scroll") should not raise."""
    step = ActionStep(action="scroll", params={"direction": "down"}, verify="Page scrolled")
    assert step.action == "scroll"

def test_scroll_down_alias_via_from_dict():
    """from_dict should alias 'scroll_down' -> 'scroll'."""
    step = ActionStep.from_dict({"action": "scroll_down", "params": {}, "verify": "scrolled"})
    assert step.action == "scroll"

def test_scroll_up_alias_via_from_dict():
    step = ActionStep.from_dict({"action": "scroll_up", "params": {}, "verify": "scrolled"})
    assert step.action == "scroll"

def test_scroll_down_direct_is_invalid():
    """Using 'scroll_down' directly (not via from_dict alias) should raise."""
    with pytest.raises(ValueError):
        ActionStep(action="scroll_down", params={}, verify="x")

# --- actuator tests ---

@patch("pyautogui.scroll")
def test_actuator_scroll_down(mock_scroll):
    actuator = AppleScriptActuator()
    result = actuator.scroll(clicks=-3)
    mock_scroll.assert_called_once_with(-3, x=None, y=None)
    assert result["success"] is True
    assert "down" in result["output"]

@patch("pyautogui.scroll")
def test_actuator_scroll_up(mock_scroll):
    actuator = AppleScriptActuator()
    result = actuator.scroll(clicks=3)
    mock_scroll.assert_called_once_with(3, x=None, y=None)
    assert result["success"] is True
    assert "up" in result["output"]

@patch("pyautogui.hscroll")
def test_actuator_scroll_horizontal(mock_hscroll):
    actuator = AppleScriptActuator()
    result = actuator.scroll(clicks=3, horizontal=True)
    mock_hscroll.assert_called_once_with(3, x=None, y=None)
    assert result["success"] is True
    assert "right" in result["output"]

@patch("pyautogui.scroll")
def test_actuator_scroll_with_coordinates(mock_scroll):
    actuator = AppleScriptActuator()
    result = actuator.scroll(clicks=-5, x=100, y=200)
    mock_scroll.assert_called_once_with(-5, x=100, y=200)
    assert result["success"] is True

@patch("pyautogui.scroll", side_effect=Exception("display not available"))
def test_actuator_scroll_failure(mock_scroll):
    actuator = AppleScriptActuator()
    result = actuator.scroll(clicks=-3)
    assert result["success"] is False
    assert "display not available" in result["error"]

# --- orchestrator dispatch tests ---
# (Use the _make_config() + mock pattern from existing tests)

async def test_dispatch_scroll_down():
    """Orchestrator maps direction='down', amount=3 -> clicks=-3."""
    # Setup agent with mock actuator
    step = ActionStep(action="scroll", params={"direction": "down", "amount": 3}, verify="scrolled")
    # Assert actuator.scroll called with clicks=-3, horizontal=False

async def test_dispatch_scroll_up():
    step = ActionStep(action="scroll", params={"direction": "up", "amount": 5}, verify="scrolled")
    # Assert actuator.scroll called with clicks=5

async def test_dispatch_scroll_default_amount():
    """When amount is not specified, default to 3."""
    step = ActionStep(action="scroll", params={"direction": "down"}, verify="scrolled")
    # Assert actuator.scroll called with clicks=-3

async def test_dispatch_scroll_left():
    step = ActionStep(action="scroll", params={"direction": "left", "amount": 2}, verify="scrolled")
    # Assert actuator.scroll called with clicks=-2, horizontal=True

async def test_dispatch_scroll_right():
    step = ActionStep(action="scroll", params={"direction": "right", "amount": 2}, verify="scrolled")
    # Assert actuator.scroll called with clicks=2, horizontal=True

async def test_dispatch_scroll_with_coordinates():
    step = ActionStep(action="scroll", params={"direction": "down", "amount": 3, "x": 500, "y": 300}, verify="scrolled")
    # Assert actuator.scroll called with x=500, y=300

# --- prompt tests ---

def test_plan_prompt_mentions_scroll():
    prompt_path = Path("src/automation_agent/planner/prompts/plan_from_prompt.md")
    text = prompt_path.read_text()
    assert "scroll" in text.lower()
    assert "direction" in text

def test_replan_prompt_mentions_scroll():
    prompt_path = Path("src/automation_agent/planner/prompts/replan_from_state.md")
    text = prompt_path.read_text()
    assert "scroll" in text.lower()
```

**Files modified:**
- `src/automation_agent/planner/prompts/replan_from_state.md` — Add available actions section (including scroll)

**Files created:**
- `tests/unit/test_scroll_action.py`

---

### Slice 2: On-Fail/Replan Fix (Engineer 2)

**Problem:** In `_replan_and_continue()` (agent.py:1942-1966), the replan execution loop handles failures differently from the main `execute()` loop:
- Main loop: calls `_handle_failure()` which retries, then escalates to replan
- Replan loop: only checks `on_fail == "abort"`, otherwise continues to next step

**Fix:** Refactor `_replan_and_continue()` to use the same failure handling as the main loop, but with a depth limit to prevent infinite replan recursion.

**Implementation:**

In `_replan_and_continue()`, replace lines 1958-1966:

```python
# CURRENT (broken):
if not result.success:
    if step.on_fail == "abort":
        break
    # Don't recurse into another replan — just record the failure
    self.logger.log_event(...)

# FIXED:
if not result.success:
    recovery_result = await self._handle_failure(
        i, step, result, step_results, goal, new_plan, iterations
    )
    if recovery_result is None:
        # Replan requested, but we're already in a replan.
        # Log and stop rather than recurse.
        self.logger.log_event(
            EventType.STEP_REPLAN,
            f"Replan step {i} exhausted retries; stopping replan execution",
            step_index=i,
        )
        break  # Stop executing remaining steps
    else:
        step_results.append(recovery_result)
        iterations += 1
        if not recovery_result.success:
            if step.on_fail == "abort":
                break
            # Non-abort failure after recovery: stop executing
            # remaining steps since they likely depend on this one
            self.logger.log_event(
                EventType.STEP_REPLAN,
                f"Replan step {i} failed after recovery; stopping",
                step_index=i,
            )
            break
```

**Key design decision:** When a step fails in the replan loop, we **stop executing remaining steps** rather than continuing. This is the fix for the "dependent steps continue after failure" bug. We do NOT recursively replan (that risks infinite loops).

**Tests in:** `tests/unit/test_replan_failure_handling.py`

```python
async def test_replan_retries_failed_step():
    """Replan loop should retry failed steps before giving up."""

async def test_replan_stops_after_exhausted_retries():
    """After retries exhaust in replan, should stop — not continue to next step."""

async def test_replan_stops_on_abort():
    """on_fail='abort' in replan should stop immediately."""

async def test_replan_no_infinite_recursion():
    """Replan inside replan should NOT trigger another replan."""

async def test_replan_success_path_unchanged():
    """Successful replan steps still work as before."""

async def test_main_loop_triggers_replan_on_failure():
    """Main execute loop triggers replan when retry_different exhausted."""
```

**Files modified:**
- `src/automation_agent/orchestrator/agent.py` — Fix `_replan_and_continue()` failure handling

**Files created:**
- `tests/unit/test_replan_failure_handling.py`

---

## Integration Tests

**File:** `tests/integration/test_scroll_integration.py`

```python
async def test_scroll_plan_validates():
    """A plan containing scroll steps passes validation."""

async def test_scroll_step_executes_and_verifies():
    """Scroll step goes through full execute → verify pipeline."""

async def test_scroll_in_multi_step_plan():
    """Plan with click → scroll → click executes all steps."""

async def test_failed_step_triggers_replan_not_continue():
    """When step N fails, step N+1 does not execute if replan is triggered."""
```

## replan_from_state.md Change

Add an available actions section between the "Strategies Already Tried" section and the "CRITICAL" section:

```markdown
## Available Actions
- `activate_app`: Launch or bring an app to front. Params: `app_name` (string)
- `click`: Click a UI element. Params: `element` (string description) or `x`, `y` (coordinates)
- `type_text`: Type text. Params: `text` (string)
- `press_key`: Press key combination. Params: `keys` (list of strings)
- `open_url`: Open URL in browser. Params: `url` (string)
- `quit_app`: Quit an application. Params: `app_name` (string)
- `scroll`: Scroll the page. Params: `direction` ("up", "down", "left", "right"), `amount` (number of scroll clicks, default 3). Optional: `x`, `y` (coordinates to scroll at)
- `observe`: Take a screenshot and describe what's on screen. Params: none
- `wait_for_user`: Pause and wait for user action. Params: `message` (string)
- `done`: Task complete. Params: none
```
