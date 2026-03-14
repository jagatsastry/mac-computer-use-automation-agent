# Architecture Spec: Verification Fixes

**Date**: 2026-03-12
**Author**: Tech Lead (expert-verification-fixes team)
**Status**: Draft
**Inputs**: [Approved PRD](verification-fixes-prd.md), [SOTA Research](verification-fixes-sota.md), [Codebase Analysis](verification-fixes-codebase.md)

---

## 1. System Design Overview

These fixes are **surgical** -- they slot into the existing orchestrator/verifier/planner without redesigning any component. The execute-verify-retry-replan loop is unchanged. We add:

1. A new Tier 0 branch in `StepVerifier` for click actions (AC-2)
2. A type-and-check bypass in the step execution loop (AC-1)
3. Ternary parsing in `verify_condition()` with an `Optional[bool]` return type (AC-3)
4. An absence counter in `_handle_failure` and a new error prefix contract (AC-4)
5. An `{{absent_elements}}` template variable in the replan prompt (AC-5)
6. A `done` + `abort_reason` handler in `_execute_step` (AC-6)
7. Skill template text changes (AC-7/8/9)

No new components. No new action types. No new protocols.

### Shared Constants

A new constant is shared between Slice 1 (AC-1, AC-2) code paths:

```python
# In src/automation_agent/shared_models.py, module level
TEXT_INPUT_AX_ROLES: frozenset[str] = frozenset({
    "AXTextField",
    "AXTextArea",
    "AXSearchField",
    "AXComboBox",
})
```

Fallback keywords for when AX is unavailable (AC-1 only):

```python
# In src/automation_agent/shared_models.py, module level
# Heuristic fallback for when AX is unavailable. English-only; expand for i18n.
TEXT_INPUT_KEYWORDS: frozenset[str] = frozenset({
    "search", "input", "text field", "text box",
    "search bar", "address bar", "url bar",
})
```

Both are `frozenset` for immutability and O(1) lookup. Defined in `shared_models.py` because both the orchestrator (AC-1) and verifier (AC-2) import from there.

---

## 2. Domain Slice Decomposition

### Slice 1 (Engineer 1): P0 #1 -- Verification Bypass for Text Field Clicks

**ACs covered**: AC-1, AC-2, AC-3, AC-10

#### 2.1 Files to Modify

| File | Change |
|------|--------|
| `src/automation_agent/shared_models.py` | Add `TEXT_INPUT_AX_ROLES`, `TEXT_INPUT_KEYWORDS` constants |
| `src/automation_agent/orchestrator/verifier.py` | AC-2: New Tier 0 branch for `click` actions; AC-3: `_verify_tier2` handles `None` from `verify_condition()` |
| `src/automation_agent/vision/coordinator.py` | AC-3: Change `verify_condition()` return type to `Optional[bool]`, ternary parsing |
| `src/automation_agent/protocols.py` | AC-3: Update `ScreenCoordinator.verify_condition` signature to `-> Optional[bool]` |
| `src/automation_agent/orchestrator/agent.py` | AC-1: Type-and-check bypass in the execution loop; AC-1: AX role query after `_dispatch_action`; AC-3: Handle `None` in `_validate_candidate` and `_wait_for_user` |
| `src/automation_agent/vision/prompts/verify_condition.md` | AC-3: Ternary prompt text |
| `tests/unit/test_verifier.py` | AC-2 tests, AC-3 Tier 2 `None` path tests |
| `tests/unit/test_coordinator.py` | AC-3 ternary parsing tests (5+ tests) |
| `tests/unit/test_orchestrator_new.py` | AC-1 type-and-check bypass tests |

#### 2.2 AC-2: Tier 0 Focus Check for Click Actions

**File**: `src/automation_agent/orchestrator/verifier.py`
**Method**: `_verify_tier0` (line 241)

Add a new branch at the END of `_verify_tier0`, after the existing `type_text` handler (line 272-295) but before the final `return None` (line 297). This branch fires for `click` actions only:

```python
# After the existing type_text handler, before the final return None
if step.action == "click":
    try:
        focused = accessibility.get_focused_element()
    except Exception:
        focused = None
    if focused is not None:
        role = getattr(focused, "role", None) or ""
        if role in TEXT_INPUT_AX_ROLES:
            return (
                True,
                f"Accessibility confirms text field focused: {role}",
            )
    return None  # Inconclusive -- let Tier 1/2 decide
```

Import `TEXT_INPUT_AX_ROLES` from `shared_models`.

**Why at the end**: The existing Tier 0 handlers for `activate_app` and `type_text` should take priority. The click-focus check is a new, additive branch. Placing it last ensures no existing behavior is disturbed.

**Why `return None` and not `return (False, ...)`**: If the focused element is not a text field, we don't know whether the click succeeded or not -- it might have clicked a button that doesn't change focus. Only return a conclusive result when we positively detect a text input role.

#### 2.3 AC-1: Type-and-Check Bypass

**File**: `src/automation_agent/orchestrator/agent.py`

Two changes:

**Change A: Capture AX role after click dispatch** (inside `_execute_step`, between `_dispatch_action` and the screenshot_diff gate)

After line 379 (`actuator_result = await self._dispatch_action(step)`), before line 382 (screenshot_diff block), insert:

```python
# AC-1: Capture focused element AX role immediately after click dispatch
_text_field_focused = False
if step.action == "click" and actuator_result.get("success", False):
    _text_field_focused = self._check_text_field_focused()
```

New private method on `AutomationAgent`:

```python
def _check_text_field_focused(self) -> bool:
    """Check if the currently focused element is a text input field.

    Uses accessibility backend if available, returns False otherwise.
    """
    accessibility = getattr(self.coordinator, "accessibility", None)
    if accessibility is None:
        return False
    try:
        focused = accessibility.get_focused_element()
    except Exception:
        return False
    if focused is None:
        return False
    role = getattr(focused, "role", None) or ""
    return role in TEXT_INPUT_AX_ROLES
```

The `_text_field_focused` flag is **local to `_execute_step`** and returned to the caller via a tuple, NOT stored on `StepResult`. Putting an internal orchestrator flag on a shared dataclass is a code smell -- `StepResult` is used across all components and tests. Instead, `_execute_step` returns the flag alongside the result.

**Why not on StepResult**: `StepResult` is a shared model imported by planner, verifier, tests, and logging. A leading-underscore boolean that only the orchestrator's execution loop cares about does not belong there. The tuple return keeps this concern local to the orchestrator.

**Change to `_execute_step` return type**: Currently returns `StepResult`. Change to return `tuple[StepResult, bool]` where the second element is `text_field_focused`. Only the two execution loops (`execute()` and `_replan_and_continue()`) destructure this tuple -- no other caller exists.

At the end of `_execute_step`:

```python
# Normal path (line 482):
return verification, _text_field_focused

# Early return paths (done, wait_for_user, observe, actuator failure):
return result, False  # No text field focus detection on non-click paths
```

All existing call sites of `_execute_step` (3 total: execute() line 233, _replan_and_continue() line 2082, _handle_failure() line 1852) must destructure:

```python
result, text_field_focused = await self._execute_step(i, step, ...)
```

For `_handle_failure` line 1852, the `text_field_focused` value is discarded (retries don't use the bypass -- the bypass only operates at the plan-loop level).

**Early-return path coverage (DE question #3)**: The `_text_field_focused` flag is set between `_dispatch_action()` (line 379) and the screenshot_diff gate (line 382). BOTH the early-return path (line 408-434, actuator/screenshot_diff failure) and the normal path (line 440-482, verification) execute AFTER this point, so `_text_field_focused` is always set before any return. Specifically:

- Screenshot_diff forces `actuator_result["success"] = False` at line 400
- This triggers the early return at line 408-434
- But `_text_field_focused` was already captured at line ~380 (between dispatch and screenshot_diff)
- The early return becomes `return result, _text_field_focused`

This is the critical design point: the AX check runs BEFORE the screenshot_diff gate can force failure, so the flag is always available regardless of which return path fires.

**Change B: Bypass helper method** (shared between `execute()` and `_replan_and_continue()`)

The two loops have different failure semantics:
- `execute()`: `_handle_failure` returning `None` -> call `_replan_and_continue()` and `return` its result
- `_replan_and_continue()`: `_handle_failure` returning `None` -> `break` (no recursive replan)

The helper must NOT contain failure handling -- it only handles the bypass decision and step execution. Failure handling remains in each loop.

```python
async def _try_type_and_check_bypass(
    self,
    i: int,
    step: ActionStep,
    result: StepResult,
    text_field_focused: bool,
    plan: ActionPlan,
    step_results: list[StepResult],
    goal: str,
) -> Optional[tuple[StepResult, StepResult, bool]]:
    """Attempt type-and-check bypass for a failed click on a text field.

    Returns:
        None if bypass is not applicable (caller should use normal failure path).
        (click_result, next_result, next_text_field_focused) if bypass was attempted:
          - click_result: replacement StepResult for the click (success=True if
            next step passed, original failed result if next step also failed)
          - next_result: StepResult for the next step
          - next_text_field_focused: whether the next step also focused a text field
    """
    if not (
        not result.success
        and step.action == "click"
        and (text_field_focused or self._is_text_field_by_keywords(step))
        and i + 1 < len(plan.steps)
        and plan.steps[i + 1].action in ("type_text", "press_key")
    ):
        return None  # Not eligible

    next_step = plan.steps[i + 1]
    next_result, next_tf = await self._execute_step(
        i + 1, next_step, step_results, goal, plan
    )

    if next_result.success:
        # Retroactively confirm the click
        replacement = StepResult(
            step=step,
            success=True,
            verification_method="type_and_check",
            evidence="Verified by subsequent keystroke step success (type-and-check)",
            duration_ms=result.duration_ms,
        )
        return replacement, next_result, next_tf
    else:
        # Both failed -- return original click failure + next failure
        return result, next_result, next_tf
```

**Both-fail path semantics (intentional asymmetry)**: When the bypass fires and both the click and the next step (type_text/press_key) fail, only the CLICK step gets `_handle_failure` treatment (retries, replan). The failed type_text/press_key result is appended to `step_results` but is NOT separately retried. This is intentional:

1. The type_text failure is a *consequence* of the click failure -- if the field wasn't focused, typing into it is expected to fail. Retrying the type_text independently would be pointless without first fixing the click.
2. The click's `_handle_failure` will retry the click with different strategies. If a retry succeeds, the plan loop naturally advances to the type_text step and executes it fresh.
3. If the click exhausts retries and triggers replan, the replanner sees both failures in the history and can generate a completely different approach.

The type_text result in step_results serves as evidence for the replanner, not as a step that needs independent recovery.

> **Limitation (known, accepted)**: The bypass only looks at `plan.steps[i + 1]`. If the planner generates a multi-step click-before-type sequence (e.g., click field, scroll into view, then type), the bypass will not fire because `i + 1` is `scroll`, not `type_text`. This is acceptable for now -- the current planner overwhelmingly generates `click` immediately followed by `type_text`. If future planner prompts produce multi-step pre-type sequences, this should be generalized to scan ahead to the next `type_text`/`press_key` step within a small window.

**Usage in `execute()` loop**:

```python
_skip_next = False
for i, step in enumerate(plan.steps):
    if _skip_next:
        _skip_next = False
        continue
    # ... context_monitor, max_iterations checks ...

    result, text_field_focused = await self._execute_step(i, step, step_results, goal, plan)

    # AC-1: Type-and-check bypass attempt
    bypass = await self._try_type_and_check_bypass(
        i, step, result, text_field_focused, plan, step_results, goal,
    )
    if bypass is not None:
        click_result, next_result, _ = bypass
        step_results.append(click_result)
        step_results.append(next_result)
        iterations += 2  # Both steps counted
        _skip_next = True

        if click_result.success:
            # Bypass succeeded -- both steps passed, skip next in loop
            if self.context_monitor:
                self._record_context(step)
            if next_result.step.action == "done":
                break
            continue

        # Bypass attempted but both failed -- handle click failure
        # (fall through to failure handling below with result = click_result)
        result = click_result
    else:
        step_results.append(result)
        iterations += 1

    # ... existing done/wait_for_user checks ...
    if step.action == "done":
        break
    # ... existing failure handling ...
    if not result.success:
        recovery_result = await self._handle_failure(...)
        # ... existing replan/abort logic, unchanged ...
```

**Usage in `_replan_and_continue()` loop** (line 2079):

Same pattern, except when `_handle_failure` returns `None`, we `break` instead of calling `_replan_and_continue()`:

```python
_skip_next = False
for i, step in enumerate(new_plan.steps):
    if _skip_next:
        _skip_next = False
        continue
    # ... max_iterations check ...

    result, text_field_focused = await self._execute_step(i, step, step_results, goal, new_plan)

    # AC-1: Type-and-check bypass attempt
    bypass = await self._try_type_and_check_bypass(
        i, step, result, text_field_focused, new_plan, step_results, goal,
    )
    if bypass is not None:
        click_result, next_result, _ = bypass
        step_results.append(click_result)
        step_results.append(next_result)
        iterations += 2
        _skip_next = True

        if click_result.success:
            if next_result.step.action == "done":
                break
            continue

        result = click_result
    else:
        step_results.append(result)
        iterations += 1

    if step.action == "done":
        break
    # ... existing wait_for_user, failure handling (break on replan) ...
```

**Fallback when AX is unavailable**:

```python
def _is_text_field_by_keywords(self, step: ActionStep) -> bool:
    """Keyword fallback for text field detection when AX is unavailable."""
    element_desc = str(step.params.get("element", "")).lower()
    return any(kw in element_desc for kw in TEXT_INPUT_KEYWORDS)
```

This is used in `_try_type_and_check_bypass` as the secondary condition when `text_field_focused` is False.

#### 2.4 AC-3: Ternary Verification

**File**: `src/automation_agent/vision/coordinator.py`
**Method**: `verify_condition` (line 611)

Change return type and parsing:

```python
async def verify_condition(
    self, condition: str, screenshot_b64: Optional[str] = None
) -> Optional[bool]:
    """Check if a visual condition holds.

    Returns:
        True if confirmed, False if denied, None if inconclusive.
    """
    if screenshot_b64 is None:
        screenshot_b64 = self.capture.capture_b64()

    prompt = self._load_prompt("verify_condition.md").replace(
        "{{condition}}", condition
    )
    response = await self._call_vision_model(prompt, screenshot_b64)

    response_lower = response.strip().lower()
    if response_lower.startswith("yes"):
        result = True
    elif response_lower.startswith("unclear"):
        result = None
    else:
        result = False

    logger.info(
        "Vision verify",
        condition=condition,
        result=result,
    )
    return result
```

**File**: `src/automation_agent/protocols.py`
**Method**: `ScreenCoordinator.verify_condition` (line 103)

```python
async def verify_condition(
    self, condition: str, screenshot_b64: Optional[str] = None
) -> Optional[bool]:
    """Check if a visual condition is true on the current screen.

    Returns True (confirmed), False (denied), or None (inconclusive).
    """
    ...
```

**Overriding Codebase Analysis Warning**: The codebase analysis doc (`verification-fixes-codebase.md`, Section 9, Pattern to Avoid #1) states: "Don't make verify_condition return anything other than bool -- the entire verification cascade depends on this." This warning is **explicitly overridden** by AC-3 of the approved PRD. The warning was written before the P0 #1 root cause was identified: the binary YES/NO contract is itself the bug. The cascade CAN handle `Optional[bool]` because each tier already returns `None` for inconclusive -- `_verify_tier2` is the only tier that lacked a `None` path. The changes above add that path. The 5+ new unit tests for ternary parsing and the per-caller `None` path tests provide the regression gate that the warning was protecting.

**File**: `src/automation_agent/vision/prompts/verify_condition.md`

Replace entire content:

```
Look at this macOS desktop screenshot and determine if the following condition is currently true:

Is this true? {{condition}}

Respond with ONLY "YES", "NO", or "UNCLEAR".
- YES: You see clear evidence the condition is met
- NO: You see evidence the condition is NOT met (e.g., a different page is visible, the element is clearly in the wrong state)
- UNCLEAR: You cannot determine the condition from the screenshot (e.g., subtle focus indicators, ambiguous state, low resolution)

Prefer YES or NO when evidence exists. Use UNCLEAR only when the screenshot genuinely does not provide enough information.
```

**File**: `src/automation_agent/orchestrator/verifier.py`
**Method**: `_verify_tier2` (line 393)

Each `if result:` check must become a three-way check. The `any_denied` flag must be initialized at the TOP of the method and tracked across ALL 5 call sites -- including the crop-region check (site 1), type_text special case (site 2), and open_url special case (site 3), not just the generic loop (sites 4-5).

Complete rewritten structure:

```python
async def _verify_tier2(
    self, step: ActionStep, coordinator, actuator_result: Dict[str, Any]
) -> Tuple[Optional[Tuple[bool, str]], Optional[str]]:
    """Tier 2: Vision-based verification.

    Returns (result, screenshot_b64) where result is:
      (True, evidence)  -- condition confirmed
      (False, evidence) -- condition denied by at least one check
      None              -- all checks inconclusive (UNCLEAR)
    """
    screenshot_b64 = None
    try:
        screenshot_b64 = await coordinator.capture_screenshot()
    except Exception:
        screenshot_b64 = None

    # AC-3: Track whether ANY condition was explicitly denied (False)
    # vs. all returning None (inconclusive). Initialized here so it
    # covers all 5 call sites, not just the generic loop.
    any_denied = False

    if screenshot_b64:
        # Site 1: Crop-region check for click actions
        region_b64 = self._crop_click_region(
            screenshot_b64,
            actuator_result.get("image_x"),
            actuator_result.get("image_y"),
        )
        if region_b64 is not None:
            local_condition = step.expected_observation.strip() or step.verify
            result = await coordinator.verify_condition(local_condition, screenshot_b64=region_b64)
            if result is True:
                return (
                    (True, f"Vision confirms the clicked region satisfies: {local_condition}"),
                    screenshot_b64,
                )
            if result is False:
                any_denied = True
            # result is None: inconclusive, fall through

        # Site 2: type_text special case
        if step.action == "type_text" and step.params.get("text"):
            expected_text = step.params["text"]
            focused_text_condition = f'The focused text field contains "{expected_text}"'
            result = await coordinator.verify_condition(
                focused_text_condition, screenshot_b64=screenshot_b64,
            )
            if result is True:
                return (
                    (True, f"Vision confirms focused field contains '{expected_text}'"),
                    screenshot_b64,
                )
            if result is False:
                any_denied = True

        # Site 3: open_url special case
        if step.action == "open_url" and step.params.get("url"):
            url_condition = self._build_url_condition(step.params["url"])
            if url_condition:
                result = await coordinator.verify_condition(
                    url_condition, screenshot_b64=screenshot_b64,
                )
                if result is True:
                    return (
                        (True, f"Vision confirms destination page for {step.params['url']}"),
                        screenshot_b64,
                    )
                if result is False:
                    any_denied = True

        # Sites 4-5: Generic conditions (expected_observation, verify)
        for condition in self._tier2_conditions(step):
            result = await coordinator.verify_condition(
                condition, screenshot_b64=screenshot_b64,
            )
            if result is True:
                return ((True, f"Vision confirms: {condition}"), screenshot_b64)
            if result is False:
                any_denied = True
    else:
        # No screenshot -- only generic conditions
        for condition in self._tier2_conditions(step):
            result = await coordinator.verify_condition(condition)
            if result is True:
                return ((True, f"Vision confirms: {condition}"), screenshot_b64)
            if result is False:
                any_denied = True

    # No condition passed. Distinguish denial from inconclusive.
    if any_denied:
        denied_condition = step.expected_observation.strip() or step.verify
        return ((False, f"Vision denies: {denied_condition}"), screenshot_b64)
    # All conditions returned None (UNCLEAR) -- truly inconclusive
    return (None, screenshot_b64)
```

**Return type of `_verify_tier2` changes** from `Tuple[Tuple[bool, str], Optional[str]]` to `Tuple[Optional[Tuple[bool, str]], Optional[str]]`. The first element can now be `None`.

**File**: `src/automation_agent/orchestrator/verifier.py`
**Method**: `verify` (line 45)

The Tier 2 section (lines 136-169) currently always returns from inside the `if coord:` block -- it never falls through to the "no backend" fallback at line 171. With `_verify_tier2` now returning `None` for all-inconclusive, we need to restructure so that `None` falls through to the actuator-result fallback. Full replacement of lines 135-179:

```python
        # Tier 2: Vision verification (slower but more thorough)
        if coord:
            tier2_raw, screenshot_b64 = await self._verify_tier2(
                step, coord, actuator_result
            )
            duration = int((time.monotonic() - start) * 1000)
            screenshot_path = None
            if screenshot_b64 and self.logger:
                try:
                    screenshot_path = self.logger.save_screenshot(
                        base64.b64decode(screenshot_b64),
                        f"verify_step_{step.action}",
                    )
                except Exception:
                    pass

            if tier2_raw is not None:
                # Tier 2 reached a conclusive result (pass or fail)
                emoji = "✅" if tier2_raw[0] else "❌"
                slog.info(
                    f"{emoji} Verified (tier2, vision)",
                    condition=step.verify,
                    passed=tier2_raw[0],
                    duration_ms=duration,
                )
                if self.logger:
                    event_type = EventType.VERIFY_PASS if tier2_raw[0] else EventType.VERIFY_FAIL
                    self.logger.log_event(event_type, f"Tier 2: {tier2_raw[1]}")

                return StepResult(
                    step=step,
                    success=tier2_raw[0],
                    verification_method="vision",
                    evidence=tier2_raw[1],
                    duration_ms=duration,
                    screenshot_path=screenshot_path,
                )
            else:
                # AC-3: All Tier 2 conditions returned UNCLEAR.
                # Fall through to actuator-result fallback below.
                if self.logger:
                    self.logger.log_event(
                        EventType.VERIFY_ESCALATE,
                        "Tier 2 inconclusive (all UNCLEAR), falling back to actuator result",
                    )

        # No verification backend conclusive -- use actuator result
        duration = int((time.monotonic() - start) * 1000)
        return StepResult(
            step=step,
            success=actuator_result.get("success", False),
            verification_method="",
            evidence=f"No verifier conclusive. Actuator: {actuator_result}",
            duration_ms=duration,
        )
```

Key change: the `return StepResult(...)` at the end is now reachable from two paths: (a) no coordinator available (original path), and (b) coordinator available but all tiers inconclusive (new AC-3 path). The evidence message changes from "No verifier available" to "No verifier conclusive" to cover both cases accurately.

**Other callers of `verify_condition()` that need `None` handling**:

| Caller | File:Line | Current behavior | Change needed |
|--------|-----------|-----------------|---------------|
| `_validate_candidate` | agent.py:1576 | `return await ...verify_condition(...)` returns `bool` to caller | Return type changes to `Optional[bool]`. Caller at line 939 does `if is_valid:` -- `None` is falsy, treated as "not validated". **This is safe**: `None` means "can't tell", which should not validate the candidate. No code change needed beyond the type. |
| `_wait_for_user` | agent.py:1212 | `condition_visible = await ...verify_condition(...)` then `if not condition_visible:` | `None` is falsy, so `not None` is `True`, which means "condition not present, skip wait". **This is safe and correct**: if we can't tell whether the wait condition is visible, it's better to proceed with the wait (not skip it). Actually wait -- `not None` is `True`, so it would SKIP the wait. That's wrong. Fix: change to `if condition_visible is False:` |
| `verifier.py _verify_tier2` | 5 call sites | Handled above |
| `vision/__main__.py:71` | CLI tool | `result = asyncio.run(coordinator.verify_condition(...))` then prints. Change print to handle None. |
| `vision/self_test.py:53` | Self-test | `result = await coordinator.verify_condition(...)`. Change to handle None. |

**Critical fix for `_wait_for_user`** (agent.py:1212):

```python
# Before:
condition_visible = await self.coordinator.verify_condition(wait_condition)
# ...
if not condition_visible:

# After:
condition_visible = await self.coordinator.verify_condition(wait_condition)
# ...
if condition_visible is False:
```

This ensures `None` (inconclusive) proceeds to the wait rather than skipping it.

#### 2.5 Slice 1 Testing Strategy

**New tests in `tests/unit/test_coordinator.py`** (AC-3 ternary parsing, 5+ tests):

1. `test_verify_condition_returns_true_on_yes` -- LLM returns "YES" -> `True`
2. `test_verify_condition_returns_true_on_yes_with_explanation` -- LLM returns "YES, the condition..." -> `True`
3. `test_verify_condition_returns_false_on_no` -- LLM returns "NO" -> `False`
4. `test_verify_condition_returns_none_on_unclear` -- LLM returns "UNCLEAR" -> `None`
5. `test_verify_condition_returns_none_on_unclear_with_explanation` -- LLM returns "UNCLEAR - cannot determine..." -> `None`
6. `test_verify_condition_returns_false_on_garbage` -- LLM returns "I think maybe..." -> `False`
7. `test_verify_condition_returns_false_on_empty` -- LLM returns "" -> `False`

These tests mock `_call_vision_model` (the LLM call) but exercise the real parsing logic.

**New tests in `tests/unit/test_verifier.py`** (AC-2 + AC-3 downstream):

8. `test_tier0_click_returns_pass_when_text_field_focused` -- Click action + AX focused element has role=AXTextField -> `(True, ...)`
9. `test_tier0_click_returns_pass_for_search_field` -- role=AXSearchField -> `(True, ...)`
10. `test_tier0_click_returns_none_when_no_focused_element` -- AX returns None -> `None`
11. `test_tier0_click_returns_none_when_non_text_role` -- role=AXButton -> `None`
12. `test_tier0_click_does_not_interfere_with_existing_type_text` -- type_text still handled by existing code
13. `test_tier2_returns_none_when_all_conditions_unclear` -- All `verify_condition` calls return `None` -> tier2 returns `None`
14. `test_tier2_returns_false_when_any_condition_denied` -- At least one returns `False` -> tier2 returns `(False, ...)`
15. `test_verify_falls_through_to_actuator_when_tier2_inconclusive` -- Tier 0 None, Tier 1 None, Tier 2 None -> falls back to actuator result

**New tests in `tests/unit/test_orchestrator_new.py`** (AC-1 bypass):

16. `test_type_and_check_bypass_click_then_type_success` -- Click fails verification, next step is type_text that succeeds -> click retroactively passes
17. `test_type_and_check_bypass_click_then_type_both_fail` -- Both fail -> both failures reported
18. `test_type_and_check_bypass_not_triggered_for_non_text_field` -- Click on button (no AX text field) fails -> normal failure path, no bypass
19. `test_type_and_check_bypass_keyword_fallback` -- No AX backend, but element desc contains "search" -> bypass triggered
20. `test_type_and_check_bypass_not_across_replan` -- Bypass only within single plan execution
21. `test_type_and_check_bypass_screenshot_diff_failure_path` -- screenshot_diff forces failure, but AX detects text field -> bypass still triggers

---

### Slice 2 (Engineer 2): P0 #2 -- Element Absence Detection + Skill Improvements

**ACs covered**: AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10

#### 2.6 Files to Modify

| File | Change |
|------|--------|
| `src/automation_agent/orchestrator/agent.py` | AC-4: Absence counter in `_handle_failure`; AC-5: Collect absent elements for replan; AC-6: `done` + `abort_reason` handler |
| `src/automation_agent/planner/planner.py` | AC-5: Inject `{{absent_elements}}` into replan prompt |
| `src/automation_agent/planner/prompts/replan_from_state.md` | AC-5: Add `{{absent_elements}}` section |
| `src/automation_agent/skills/library/return_amazon_order.md` | AC-7/8/9: Skill template updates |
| `tests/unit/test_orchestrator_new.py` | AC-4/5/6 tests |
| `tests/unit/test_planner.py` | AC-5 replan prompt injection tests |

#### 2.7 AC-4: Absence Counter

**File**: `src/automation_agent/orchestrator/agent.py`
**Method**: `_handle_failure` (line 1821)

Add an absence counter that tracks `find_element` `None` results per original element description within a single retry cycle.

**Data shape**: The counter is a local variable within `_handle_failure`, NOT a persistent agent attribute. It counts how many times the error string starts with `"Element not found:"` across retries for the same step.

```python
async def _handle_failure(self, index, step, result, history, goal, plan, iterations):
    if step.on_fail == "retry_different":
        current_result = result
        # AC-4: Track element-not-found count for absence detection.
        # Keyed on the ORIGINAL element description, not _vary_strategy mutations.
        original_element = step.params.get("element", "")
        not_found_count = 1 if self._is_element_not_found(current_result) else 0

        while current_result.retry_count < step.max_retries:
            strategy, retry_step = self._vary_strategy(step, current_result)
            if retry_step is None:
                # About to escalate to replan -- check absence threshold.
                # _is_element_absent uses AX as bonus confirmation when available,
                # but returns True (absent) when AX is unavailable -- the count
                # threshold alone is sufficient per AC-4.
                if not_found_count >= 2 and original_element and self._is_element_absent(original_element):
                    current_result.error = f"Element absent: {original_element}"
                # ... existing replan escalation ...
                return None
            # ... existing retry logic ...
            retry_result, _ = await self._execute_step(index, retry_step, history, goal, plan)
            retry_result.retry_count = current_result.retry_count + 1
            retry_result.retry_strategies_used = current_result.retry_strategies_used + [strategy]

            if retry_result.success:
                return retry_result

            # AC-4: Increment not-found counter
            if self._is_element_not_found(retry_result):
                not_found_count += 1
            current_result = retry_result

        # Retries exhausted -- check absence before replan escalation
        if not_found_count >= 2 and original_element and self._is_element_absent(original_element):
            current_result.error = f"Element absent: {original_element}"
        # ... existing replan log + return None ...
```

**Note on `_is_element_absent` semantics**: When AX is unavailable, `_is_element_absent` returns `True` (the count threshold is sufficient on its own). When AX IS available and finds a matching element, it returns `False`, which means the error stays as `"Element not found:"` (transient) even though count >= 2. This is correct: if AX can see the element, it's not truly absent -- the vision model just can't find it. The replan should still try.

New helper methods:

```python
@staticmethod
def _is_element_not_found(result: StepResult) -> bool:
    """Check if a step result indicates element-not-found."""
    return bool(result.error and result.error.startswith("Element not found:"))

def _is_element_absent(self, element_description: str) -> bool:
    """Check whether an element is confirmed absent from the current page.

    Uses the accessibility tree as structural confirmation when available.
    When the AX backend is unavailable or errors, returns True (falls back
    to count-only absence detection per AC-4). This fallback-to-True behavior
    is intentional: the counter threshold (N>=2) has already been met before
    this method is called, so AX is a bonus confirmation, not a gate.

    Returns True if element is absent (or AX unavailable), False if AX finds a match.
    """
    accessibility = getattr(self.coordinator, "accessibility", None)
    if accessibility is None:
        return True  # No AX = fall back to count-only
    try:
        elements = accessibility.get_accessibility_elements()
    except Exception:
        return True  # AX failure = fall back to count-only
    if not elements:
        return True  # Empty AX tree = confirmed absent
    # Check if any AX element matches the description
    desc_lower = element_description.lower()
    for el in elements:
        title = (getattr(el, "title", "") or "").lower()
        description = (getattr(el, "description", "") or "").lower()
        if desc_lower in title or desc_lower in description:
            return False  # AX found a match -- NOT absent
    return True  # AX tree searched, no match found
```

**Error prefix contract**: The string `"Element absent:"` is the interface between Slice 2's absence detection and AC-5's replan context injection. This prefix MUST be used exactly as specified.

> **Tech debt (accepted)**: This is a stringly-typed contract -- AC-5's collection logic depends on `startswith("Element absent:")` matching exactly what AC-4 writes. A future improvement would introduce a structured error type (e.g., `StepError(kind=ErrorKind.ELEMENT_ABSENT, detail=...)`) to make this contract type-safe. For now, the string prefix is acceptable because (a) both producer and consumer are in the same file (`agent.py`), (b) the prefix is tested explicitly, and (c) the alternative adds complexity disproportionate to this fix's scope.

#### 2.8 AC-5: Replan Prompt Receives Absence Context

**File**: `src/automation_agent/orchestrator/agent.py`
**Method**: `_replan_and_continue` (line 2008)

Before calling `self.planner.replan(...)`, collect absent elements from step_results:

```python
# AC-5: Collect confirmed-absent elements for replan context
absent_elements = []
for sr in step_results:
    if sr.error and sr.error.startswith("Element absent:"):
        absent_desc = sr.error[len("Element absent:"):].strip()
        if absent_desc and absent_desc not in absent_elements:
            absent_elements.append(absent_desc)
```

Pass to the planner:

```python
new_plan = await self.planner.replan(
    goal,
    screen_desc,
    step_results,
    retry_strategies,
    desktop_context=desktop_context,
    skill_context=replan_ctx,
    absent_elements=absent_elements,  # NEW
)
```

**File**: `src/automation_agent/planner/planner.py`
**Method**: `replan` (line 71) -- add `absent_elements` parameter

```python
async def replan(
    self,
    goal: str,
    screen_description: str,
    history: List[StepResult],
    retry_strategies_used: List[str],
    desktop_context: str = "",
    skill_context: Optional[str] = None,
    absent_elements: Optional[List[str]] = None,  # NEW
) -> ActionPlan:
```

**Method**: `_build_replan_prompt` (line 192) -- add `absent_elements` parameter and inject into template

```python
def _build_replan_prompt(
    self,
    goal: str,
    screen_description: str,
    history: List[StepResult],
    retry_strategies: List[str],
    desktop_context: str = "",
    skill_context: Optional[str] = None,
    absent_elements: Optional[List[str]] = None,  # NEW
) -> str:
    # ... existing code ...

    # AC-5: Absent elements context
    if absent_elements:
        absent_text = (
            "The following UI elements were confirmed absent from the current page:\n"
            + "\n".join(f"- {el}" for el in absent_elements)
            + "\n\nDo not generate steps that depend on these elements. "
            "Consider that the task may be impossible in the current page state. "
            "If the task cannot be completed, emit a done step with abort_reason explaining why."
        )
    else:
        absent_text = ""

    prompt = prompt.replace("{{absent_elements}}", absent_text)
    return prompt
```

**File**: `src/automation_agent/protocols.py`
**Method**: `ActionPlanner.replan` -- add `absent_elements` parameter

```python
async def replan(
    self,
    goal: str,
    screen_description: str,
    history: List[StepResult],
    retry_strategies_used: List[str],
    desktop_context: str = "",
    skill_context: Optional[str] = None,
    absent_elements: Optional[List[str]] = None,  # NEW
) -> ActionPlan:
```

**File**: `src/automation_agent/planner/prompts/replan_from_state.md`

Add after the `{{retry_strategies}}` section:

```markdown
## Confirmed Absent Elements
{{absent_elements}}
```

#### 2.9 AC-6: Graceful Abort via `done` + `abort_reason`

**File**: `src/automation_agent/orchestrator/agent.py`
**Method**: `_execute_step` (line 354)

Change the `done` handler:

```python
# Before (line 354-360):
if step.action == "done":
    return StepResult(
        step=step,
        success=True,
        verification_method="",
        evidence="Task marked as done",
    )

# After:
if step.action == "done":
    abort_reason = step.params.get("abort_reason")
    if abort_reason:
        return StepResult(
            step=step,
            success=False,
            verification_method="",
            evidence=f"Task aborted: {abort_reason}",
            error=abort_reason,
        )
    return StepResult(
        step=step,
        success=True,
        verification_method="",
        evidence="Task marked as done",
    )
```

**Also in `execute()` loop** (line 241-242): The current code breaks on `done` regardless of success. After the break, the success is determined by the last step result. Since the `done` + `abort_reason` StepResult has `success=False`, the final `ExecutionResult` construction at line 312 will see `steps[-1].success == False`. But the current code at line 294-311 assumes we reached this point = success. We need to check:

```python
# After the for loop (line 294):
duration = int((time.monotonic() - start) * 1000)

# Check if last step was a done-with-abort
last_result = step_results[-1] if step_results else None
if last_result and last_result.step.action == "done" and last_result.step.params.get("abort_reason"):
    # AC-6: Graceful abort
    self.logger.log_event(EventType.TASK_FAIL, f"Task aborted: {last_result.error}")
    self.logger.finalize(False, last_result.error)
    return ExecutionResult(
        success=False,
        message=f"Task aborted: {last_result.error}",
        error=last_result.error,
        steps=step_results,
        total_duration_ms=duration,
        iterations=iterations,
        goal=goal,
        run_id=self.logger.run_id,
    )

# ... existing success path ...
```

Same check needed in `_replan_and_continue` (line 2129-2154) after the loop.

#### 2.10 AC-7/8/9: Skill Template Updates

**File**: `src/automation_agent/skills/library/return_amazon_order.md`

Replace current content with:

```markdown
---
name: return-amazon-order
skill-id: return-amazon-order
description: Return an item or package on Amazon
summary: Navigate a retailer's order history, locate a purchased item, and complete a return or refund flow; currently specialized for Amazon.
tags: [ecommerce, return, refund, amazon]
trigger-keywords: [return, send back, refund, amazon]
parameters:
  item:
    type: string
    required: true
    description: What to return
    examples: ["blue headphones", "laptop stand"]
requires:
  os: darwin
success-condition: Return confirmation with label or drop-off instructions visible
max-retries: 3
---

## Steps
1. Use open_url to navigate to https://www.amazon.com/gp/your-account/order-history
   - verify: Amazon orders page or login page visible
2. If login page is visible, wait for user to sign in
   - verify: Orders page loaded with search functionality
3. Ensure the orders page is oriented near the top or otherwise positioned so the order search/filter controls are visible
   - verify: The search/filter area for orders is visible

### Date Filter (if needed)
4. If the order is older than 3 months, click the date filter dropdown (shows "past 3 months" by default).
   Amazon's ONLY date filter options are: "last 30 days", "past 3 months", and specific calendar years (2026, 2025, 2024, ...). There is NO "past 6 months", "past year", or custom date range option. For orders older than 3 months, select the calendar year that contains the order date. For example, an order from "six months ago" relative to March 2026 would be in year 2025.
   - verify: Dropdown closes and filtered orders page loads

### Item Search
5. If {{item}} is a specific product name: Find the "Search all orders" search/filter bar and click it, type "{{item}}" and press Enter
   - verify: Search results visible
6. If {{item}} is vague, temporal, or not a specific product name (e.g., "something I bought last week", "my recent order", "that thing"): Do NOT search for the vague phrase. Instead, use the date filter to narrow to the relevant time period, then scroll through visible orders. If multiple candidate orders are visible and it is ambiguous which one the user means, use wait_for_user to ask the user to clarify which order they want to return.
   - verify: Either a single matching order identified, or user clarification received

### Return Flow
7. Scroll through the search results to find the most recent order containing "{{item}}". Click into the order card or its details view, then locate and click the return-related control (e.g., "Return or Replace Items", "View return options")
   - verify: Return options page visible
8. Select the return reason and continue through the return flow
   - verify: Return method or next return step is visible
9. Complete the remaining return steps until confirmation or drop-off instructions are visible
   - verify: Return confirmation visible

## Error Recovery
- If login page appears at step 1: wait for user to sign in, then continue
- If the orders page opens away from the search controls: scroll up or reposition first, then search again
- If search results require scrolling: use scroll_down to reveal more orders before giving up
- If "Return or Replace Items" is not visible on the matching order card: look for a semantically adjacent affordance on that same order card or order details view, such as "View item", "Order details", or another visible route toward returns
- If the page changes but not into the return flow: observe the new page and continue from the visible order-specific controls instead of assuming the return step is complete
- If return button is below the fold on the order details page: scroll down to reveal return-related controls
- If item not eligible for return (return window closed, item type excluded, or eligibility check fails): emit a done step with abort_reason explaining why the item cannot be returned
- **Non-returnable order detection signals**: If the order details page shows "Manage your subscription" instead of return controls, this is a subscription item that cannot be returned. If "Download" or "Read now" controls are visible, this is digital content that cannot be returned. If no return-related controls are visible after the full order details page has loaded, the item may not be eligible for return. In ALL these cases: stop immediately, do NOT continue scrolling or retrying for return controls. Instead, emit a done step with abort_reason explaining the specific reason the item cannot be returned through the standard flow.
- If the date filter dropdown does not contain the expected year: the year may not be available if the account is newer. Select the oldest available year instead.

## Notes
- Treat the named controls in this skill as likely affordances, not guaranteed literal text
- Prefer staying within the same order card or the details page for that order when choosing alternate actions
- Amazon order search only searches by product name/keyword, NOT by date or temporal phrases
```

#### 2.11 Slice 2 Testing Strategy

**New tests in `tests/unit/test_orchestrator_new.py`**:

AC-4 tests:
1. `test_absence_counter_triggers_after_two_not_found` -- Initial attempt + 1 retry with "Element not found:" -> error becomes "Element absent:"
2. `test_absence_counter_does_not_trigger_on_single_not_found` -- Only 1 "Element not found:" -> stays "Element not found:"
3. `test_absence_counter_keys_on_original_element` -- `_vary_strategy` mutates element desc, but counter still reaches threshold
4. `test_absence_counter_resets_at_replan` -- After replan, counter starts fresh
5. `test_absence_ax_confirmation` -- AX tree queried when counter threshold met; AX confirms absence

AC-5 tests:
6. `test_replan_receives_absent_elements` -- `planner.replan` called with `absent_elements=["Return or Replace Items"]`
7. `test_replan_prompt_contains_absent_element_text` -- The formatted prompt includes "confirmed absent from the current page"
8. `test_replan_no_absent_elements_when_none` -- No absent elements -> `absent_elements=[]` or omitted

AC-6 tests:
9. `test_done_with_abort_reason_returns_failure` -- `done` step with `abort_reason="..."` in the **`execute()` main loop** -> `ExecutionResult(success=False, error="...")`
10. `test_done_without_abort_reason_returns_success` -- Normal `done` step -> `ExecutionResult(success=True)`
11. `test_done_abort_in_replan_returns_failure` -- `done` + `abort_reason` during the **`_replan_and_continue()` loop** -> same failure behavior as #9

**New tests in `tests/unit/test_planner.py`**:

12. `test_replan_prompt_includes_absent_elements_section` -- Verify `{{absent_elements}}` replaced with element list
13. `test_replan_prompt_absent_elements_empty` -- No absent elements -> section is empty string

---

## 3. Data Shape Changes

### New Constants (shared_models.py)

```python
TEXT_INPUT_AX_ROLES: frozenset[str] = frozenset({
    "AXTextField", "AXTextArea", "AXSearchField", "AXComboBox",
})

# Heuristic fallback for when AX is unavailable. English-only; expand for i18n.
TEXT_INPUT_KEYWORDS: frozenset[str] = frozenset({
    "search", "input", "text field", "text box",
    "search bar", "address bar", "url bar",
})
```

### StepResult Changes (shared_models.py)

No new fields on `StepResult`. The text-field-focused flag is returned as a tuple from `_execute_step` and stays local to the orchestrator.

The only StepResult change is adding `"type_and_check"` to the `valid_methods` set in `__post_init__`.

### _execute_step Return Type Change (agent.py)

Return type changes from `StepResult` to `tuple[StepResult, bool]`. The bool indicates whether a text input field was focused after a click action. This is an internal API change within `AutomationAgent` -- no protocol or shared model is affected. All 3 callers (`execute()`, `_replan_and_continue()`, `_handle_failure`) must destructure the tuple.

### ActionPlanner.replan Protocol Change (protocols.py)

New optional parameter: `absent_elements: Optional[List[str]] = None`

### ScreenCoordinator.verify_condition Protocol Change (protocols.py)

Return type: `bool` -> `Optional[bool]`

---

## 4. Error Messages (Exact String Literals)

| Context | String | Used by |
|---------|--------|---------|
| AC-1 bypass success evidence | `"Verified by subsequent keystroke step success (type-and-check)"` | `_execute_step` loop in agent.py |
| AC-1 bypass verification_method | `"type_and_check"` | StepResult.verification_method |
| AC-2 Tier 0 pass | `f"Accessibility confirms text field focused: {role}"` | verifier.py `_verify_tier0` |
| AC-4 absence prefix | `f"Element absent: {original_element}"` | agent.py `_handle_failure` |
| AC-4 not-found prefix (unchanged) | `f"Element not found: {description}"` | agent.py `_dispatch_action` |
| AC-5 replan absence header | `"The following UI elements were confirmed absent from the current page:"` | planner.py `_build_replan_prompt` |
| AC-5 replan absence instruction | `"Do not generate steps that depend on these elements. Consider that the task may be impossible in the current page state. If the task cannot be completed, emit a done step with abort_reason explaining why."` | planner.py `_build_replan_prompt` |
| AC-6 abort evidence | `f"Task aborted: {abort_reason}"` | agent.py `_execute_step` |

**Note**: `"type_and_check"` must be added to `StepResult.__post_init__` valid_methods set:

```python
valid_methods = {"", "accessibility", "actuator_state", "vision", "both", "type_and_check"}
```

---

## 5. File List Per Slice

### Slice 1 (Engineer 1) -- 9 files

| File | Action |
|------|--------|
| `src/automation_agent/shared_models.py` | MODIFY (add constants + `"type_and_check"` to valid_methods) |
| `src/automation_agent/orchestrator/verifier.py` | MODIFY (Tier 0 click branch + Tier 2 None handling) |
| `src/automation_agent/vision/coordinator.py` | MODIFY (ternary parsing) |
| `src/automation_agent/protocols.py` | MODIFY (verify_condition return type) |
| `src/automation_agent/orchestrator/agent.py` | MODIFY (type-and-check bypass + AX role capture + _wait_for_user fix) |
| `src/automation_agent/vision/prompts/verify_condition.md` | MODIFY (ternary prompt) |
| `tests/unit/test_verifier.py` | MODIFY (add 8 tests) |
| `tests/unit/test_coordinator.py` | MODIFY (add 7 tests) |
| `tests/unit/test_orchestrator_new.py` | MODIFY (add 6 tests) |

### Slice 2 (Engineer 2) -- 7 files

| File | Action |
|------|--------|
| `src/automation_agent/orchestrator/agent.py` | MODIFY (absence counter + done abort handler) |
| `src/automation_agent/planner/planner.py` | MODIFY (absent_elements param + prompt injection) |
| `src/automation_agent/planner/prompts/replan_from_state.md` | MODIFY (add {{absent_elements}} section) |
| `src/automation_agent/protocols.py` | MODIFY (replan absent_elements param) |
| `src/automation_agent/skills/library/return_amazon_order.md` | MODIFY (AC-7/8/9 content) |
| `tests/unit/test_orchestrator_new.py` | MODIFY (add 11 tests) |
| `tests/unit/test_planner.py` | MODIFY (add 2 tests) |

### Shared Files (both engineers touch)

- `src/automation_agent/orchestrator/agent.py` -- **Both slices modify `_execute_step`**. Slice 1 changes the return type to `tuple[StepResult, bool]` and adds the AX role check between `_dispatch_action` and screenshot_diff. Slice 2 changes the `done` handler (lines 354-360) to check `abort_reason`. These changes touch different parts of the method but the tuple return change (Slice 1) affects ALL return statements including the `done` handler that Slice 2 modifies. **Merge sequence**: Slice 1 merges first (changes return type). Slice 2 rebases and updates the `done` handler to return `tuple[StepResult, bool]`. The merged `done` handler looks like:

    ```python
    if step.action == "done":
        abort_reason = step.params.get("abort_reason")  # Slice 2
        if abort_reason:                                  # Slice 2
            return StepResult(                            # Slice 2
                step=step,
                success=False,
                verification_method="",
                evidence=f"Task aborted: {abort_reason}",
                error=abort_reason,
            ), False                                      # Slice 1 tuple
        return StepResult(
            step=step,
            success=True,
            verification_method="",
            evidence="Task marked as done",
        ), False                                          # Slice 1 tuple
    ```

    Slice 1 also modifies the execution loops (`execute()` and `_replan_and_continue()`). Slice 2 modifies `_handle_failure` and `_replan_and_continue()` (absent elements collection and done-abort check after loop). The `_replan_and_continue()` changes are in different locations: Slice 1 adds bypass logic inside the loop; Slice 2 adds absent element collection before the `planner.replan()` call and done-abort check after the loop. No conflict.
- `src/automation_agent/protocols.py` -- Slice 1 changes `verify_condition` return type; Slice 2 adds `absent_elements` param to `replan`. **Different methods, no conflict.**
- `tests/unit/test_orchestrator_new.py` -- Both add tests. **Append-only, no conflict.**

---

## 6. Backward Compatibility

### Why all 719+ existing tests continue to pass

1. **AC-2 (Tier 0 click branch)**: Additive-only. New branch fires AFTER existing handlers. Existing tests don't mock `accessibility.get_focused_element()` to return text field roles on click actions, so the branch returns `None` (inconclusive) for all existing tests -- same as today.

2. **AC-1 (type-and-check bypass)**: The bypass only fires when `_text_field_focused` is True AND the next step is `type_text`/`press_key`. Existing tests don't set up the AX mock to report text field focus on click, so `_text_field_focused` is always False. Keyword fallback only fires when element description contains text-field keywords AND next step is type_text -- a pattern not present in existing test plans (which use Calculator button clicks).

3. **AC-3 (ternary verification)**: The `Optional[bool]` return from `verify_condition()` is backward-compatible in tests because:
   - Existing tests mock `coordinator.verify_condition = AsyncMock(return_value=True)` -- returns `True`, same behavior.
   - The `if result:` -> `if result is True:` change in `_verify_tier2` is semantically identical when `result` is `True` or `False` (both bool). The only new case is `None`, which existing mocks never return.
   - `_wait_for_user` fix: `if not condition_visible:` -> `if condition_visible is False:`. Existing tests mock `verify_condition` to return `True`, so `condition_visible` is `True`, `True is False` is `False` -> same skip-or-not decision.

4. **AC-4 (absence counter)**: Only fires when `result.error.startswith("Element not found:")` appears 2+ times. Existing tests that test element-not-found scenarios have mocked results that don't go through the full retry cycle in `_handle_failure`. The counter variable is local and has no effect when its conditions aren't met.

5. **AC-5 (replan absent_elements)**: New optional parameter with default `None`. Existing tests call `planner.replan()` without `absent_elements` -- default `None` means no absent context injected, empty string in template. Existing replan prompt tests check for existing template variables that are unchanged.

6. **AC-6 (done abort)**: Only fires when `step.params.get("abort_reason")` is truthy. Existing test plans use `ActionStep(action="done", params={})` -- no `abort_reason` key, so `.get("abort_reason")` returns `None`, existing behavior preserved.

7. **`_execute_step` tuple return**: All 3 call sites are internal to `AutomationAgent`. Tests that call `_execute_step` directly (if any) need the destructure update, but all existing orchestrator tests call `agent.execute()`, not `_execute_step` directly. The tuple change is invisible to tests.

8. **verify_condition.md prompt**: Existing unit tests mock `coordinator.verify_condition` and never exercise the prompt. The prompt change has no effect on mocked tests. The 5+ new tests specifically cover the new parsing logic.

9. **Evidence string change**: The fallback `StepResult.evidence` changes from `"No verifier available"` to `"No verifier conclusive"` (Section 2.3). This is a **user-visible string change** in the evidence field. Any test that asserts on the exact string `"No verifier available"` will need updating. Existing tests do not assert on this string (they assert on `success` and `verification_method`), so no breakage is expected. However, any downstream log parsers or dashboards that grep for `"No verifier available"` should be updated.

---

## 7. Execution Order

Slice 1 and Slice 2 can be developed **in parallel** -- they modify different methods in the shared files. However, **Slice 1 must merge first** because it changes `_execute_step`'s return type to `tuple[StepResult, bool]`, which affects every return statement in that method -- including the `done` handler that Slice 2 modifies. Slice 2 rebases onto Slice 1 before merging (see Section 5 "Shared Files" for the merged `done` handler).

Recommended integration test: after both slices merge, run the full test suite (`pytest`) to verify AC-10. Then run a dry-run of the Amazon return scenario (Scenario 1) to verify the end-to-end fix.
