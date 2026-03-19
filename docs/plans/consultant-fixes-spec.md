# Consultant Fixes Spec — 2026-03-16

Fixes 5 findings from codex review: 2 P0, 2 P1, 1 P2.

## Slice 1 (Engineer 1): P0-1 Scroll Recovery + P2-5 Tests

### Problem
`_scroll_recovery` (agent.py:2872) returns a synthetic success after dispatching the action but **skips postcondition verification**. Additionally, `_vary_strategy` (agent.py:3684) still has a `scroll_down_and_retry` strategy that emits a bare `scroll` step — any successful scroll is treated as recovery of the original click/type_text.

### Fix

**A. Remove `scroll_down_and_retry` from `_vary_strategy`** (agent.py:3683-3686):
Delete the block:
```python
if missing_target and not suggested_element and attempt == 1:
    scroll_params = {"direction": "down", "amount": 3}
    return ("scroll_down_and_retry", _retry_step("scroll", scroll_params))
```
This strategy is redundant now that `_scroll_recovery` exists at agent.py:510-538. With this gone, attempt 1 for missing_target without suggested_element falls through to `refine_missing_target_query` at attempt 2 or `replan_missing_target` at attempt 3.

But we need to shift the attempt numbering: the existing `attempt == 2` block at line 3687-3692 should become `attempt == 1`, and `replan_missing_target` should trigger at `attempt >= 2`. Specifically:
```python
if missing_target and not suggested_element:
    if attempt == 1:
        params["element"] = (
            f"{params['element']} (look carefully, may be partially hidden)"
        )
        return ("refine_element_query", _retry_step("click", params))
    if attempt == 2:
        params["element"] = (
            f"{params['element']} (visible on the same relevant card/section only)"
        )
        return ("refine_missing_target_query", _retry_step("click", params))
    return ("replan_missing_target", None)
```

**B. Add postcondition verification to `_scroll_recovery`** (agent.py:2922-2933):
After the action is dispatched, run verification before returning success:
```python
action_result = await self._dispatch_action(step, _pre_resolved_location=find_result)
success = action_result.get("success", False)
if success and step.verify:
    # Run normal postcondition verification
    verify_result = await self._verify_step(step, action_result)
    if verify_result is not None:
        return StepResult(
            step=step,
            success=verify_result[0],
            verification_method="scroll_recovery_verified",
            evidence=verify_result[1],
            retry_strategies_used=[f"scroll_recovery_{i + 1}"],
        )
return StepResult(
    step=step,
    success=success,
    verification_method="scroll_recovery",
    evidence=f"Scroll recovery click after {i + 1} scrolls",
    error=action_result.get("error") if not success else None,
    retry_strategies_used=[f"scroll_recovery_{i + 1}"],
)
```

**C. Tests** — `tests/unit/test_scroll_recovery.py`:
- `test_scroll_recovery_runs_postcondition_verification`: Mock coordinator/actuator, verify that after scroll finds element and dispatches action, the verify step runs.
- `test_scroll_recovery_false_action_not_treated_as_success`: If dispatch succeeds but verify fails, result.success should be False.
- `test_vary_strategy_no_bare_scroll`: Verify `_vary_strategy` never returns a bare scroll step for click actions.
- `test_scroll_recovery_exhaustion_returns_failure`: 3 scrolls with no element found → StepResult(success=False).

### Files
- `src/automation_agent/orchestrator/agent.py` — `_scroll_recovery`, `_vary_strategy`
- `tests/unit/test_scroll_recovery.py` (new)

---

## Slice 2 (Engineer 2): P0-2 Replan Hardening

### Problem
`_replan_and_continue` (agent.py:3769) only reinjects domain verification. It skips:
1. Fallback replacement / truncated-plan detection (lines 331-356)
2. Navigation enforcement (lines 358-362)
3. Click `on_fail` normalization (lines 386-389)
4. Plan validation (lines 404-410)

Also `replan_from_state.md` omits rules 10 (e-commerce completion) and 11 (plan depth).

### Fix

**A. Extract `_harden_plan()` shared helper:**
```python
def _harden_plan(
    self,
    plan: ActionPlan,
    goal: str,
    skill_context: Optional[str],
    fallback_plan: Optional[ActionPlan],
) -> ActionPlan:
    """Apply all plan hardening checks. Used by both initial plan and replan."""
    # 1. Trivial done / truncated plan → fallback replacement
    replace_with_fallback = False
    if self._is_trivial_done_plan(plan) and fallback_plan is not None:
        if not await self._plan_already_satisfied(fallback_plan):
            replace_with_fallback = True
    elif self._is_truncated_plan(plan, fallback_plan):
        replace_with_fallback = True
    if replace_with_fallback and fallback_plan is not None:
        plan = fallback_plan

    # 2. Navigation enforcement
    if skill_context and fallback_plan and not self._is_trivial_done_plan(plan):
        plan = AutomationAgent._ensure_skill_navigation(plan, fallback_plan)

    # 3. Domain verification
    if self._expected_domain:
        self._inject_domain_verification(plan, self._expected_domain)

    # 4. Click on_fail normalization
    if skill_context:
        for step in plan.steps:
            if step.action == "click" and step.on_fail == "abort":
                step.on_fail = "retry_different"

    # 5. Plan validation
    validation_errors = plan.validate()
    if validation_errors:
        # Log but don't abort — replan might have partial value
        slog.warning("replan_validation_issues", errors=validation_errors)

    return plan
```

Note: Since `_plan_already_satisfied` is async, `_harden_plan` must be async too.

**B. Use in `_execute`** — replace lines 331-410 with call to `await self._harden_plan(plan, goal, skill_context, fallback_plan)`. Keep the `_expected_domain` extraction before the call.

**C. Use in `_replan_and_continue`** — after getting `new_plan` from planner (line 3821), call:
```python
fallback_plan = self._build_skill_fallback_plan(goal, skill_context) if skill_context else None
new_plan = await self._harden_plan(new_plan, goal, skill_context, fallback_plan)
```

**D. Port rules to `replan_from_state.md`** — add after line 52:
```markdown
10. **E-commerce goal completion**: For "buy", "purchase", "shop", or "add to cart" goals:
    - Opening a URL is NOT completion. Showing search results is NOT completion.
    - The replan MUST include steps through add-to-cart at minimum.
    - Do NOT end the plan after opening a search URL.
11. **Plan depth**: When a skill template is provided as a prior, your replan MUST cover
    all remaining phases in the skill template. Do not generate a plan shorter than what
    remains unless the current screen state shows the task is partially complete.
```

**E. Tests** — `tests/unit/test_replan_hardening.py`:
- `test_replan_applies_fallback_on_truncated_plan`: Mock planner to return navigation-only plan, verify fallback replacement.
- `test_replan_normalizes_click_on_fail`: Replan with click on_fail=abort → gets normalized to retry_different.
- `test_replan_injects_domain_verification`: Replan steps get domain verification injected.
- `test_harden_plan_validates`: Plan with empty verify field gets logged as warning.

### Files
- `src/automation_agent/orchestrator/agent.py` — new `_harden_plan`, modified `_execute`, modified `_replan_and_continue`
- `src/automation_agent/planner/prompts/replan_from_state.md`
- `tests/unit/test_replan_hardening.py` (new)

---

## Slice 3 (Engineer 3): P1-3 Promotion Gate + P1-4 Tier-1 Matchers

### Problem 1: Promotion Gate
`_maybe_promote_skill` (agent.py:4058) has `if not skill_name or not observations: return`. The `not observations` check blocks promotion when the current run distilled nothing new, even though the librarian loads the full history from disk.

### Fix 1
Change line 4058 from:
```python
if not skill_name or not observations:
    return
```
to:
```python
if not skill_name:
    return
```

The librarian's `evaluate_run` already handles the case where observations is empty — it loads all stored observations from the experience store and evaluates them. This is the correct place for the decision.

Also update the test at `test_skill_librarian_integration.py` that codifies the old no-op behavior.

### Problem 2: open_url Tier-1 Matcher
Line 441: `if expected_lower in actual_lower or actual_lower in expected_lower` — the second condition `actual_lower in expected_lower` means a homepage URL (`https://amazon.com`) matches any deeper expected URL (`https://amazon.com/orders/123`).

### Fix 2
Replace line 441 with host+path compatibility check:
```python
from urllib.parse import urlparse

expected_parsed = urlparse(expected_lower)
actual_parsed = urlparse(actual_lower)
# Host must match
if expected_parsed.netloc and actual_parsed.netloc:
    if expected_parsed.netloc != actual_parsed.netloc:
        return None  # Inconclusive — different hosts
    # Path: actual must be at or deeper than expected
    if actual_parsed.path.rstrip("/").startswith(expected_parsed.path.rstrip("/")):
        return (True, f"Browser URL '{browser_url}' matches destination")
    # Or expected path starts with actual (we navigated deeper)
    if expected_parsed.path.rstrip("/").startswith(actual_parsed.path.rstrip("/")):
        return (True, f"Browser URL '{browser_url}' matches destination")
```

Keep the existing token-matching fallback below for cases where URL structure doesn't match cleanly.

### Problem 3: type_text Tier-1 Matcher
Lines 462-476: Returns `False` on the first populated field that doesn't match, instead of checking all fields.

### Fix 3
Change the loop to check all fields and only fail if all populated fields mismatch:
```python
if step.action == "type_text" and step.params.get("text"):
    expected_text = step.params["text"]
    any_populated = False
    for key in ("focused_value", "focused_text", "selected_text"):
        actual_value = state.get(key)
        if actual_value is None:
            continue
        any_populated = True
        if expected_text in str(actual_value):
            return (True, f"Actuator state {key} contains '{expected_text}'")
    # Only fail if at least one field was populated and none matched
    if any_populated:
        return (False, f"No actuator text field contains '{expected_text}'")
    # All None → inconclusive
```

### Tests
- `test_open_url_homepage_does_not_match_deep_url`: `open_url("https://amazon.com/orders/123")` with actual `https://amazon.com/` → should NOT pass.
- `test_open_url_deeper_actual_matches`: `open_url("https://amazon.com/s?k=shoes")` with actual `https://amazon.com/s?k=shoes&ref=nb` → should pass.
- `test_type_text_succeeds_on_any_matching_field`: state with `focused_value=None, focused_text="hello"` and expected "hello" → pass.
- `test_type_text_fails_only_when_all_fields_populated_and_mismatch`: state with `focused_value="wrong"` and `focused_text="also_wrong"` → fail.
- `test_type_text_inconclusive_when_all_none`: All fields None → returns None.
- `test_promotion_fires_with_empty_current_observations`: Accumulated observations from prior runs should trigger promotion even when current run's observations is empty.

### Files
- `src/automation_agent/orchestrator/agent.py` — `_maybe_promote_skill`
- `src/automation_agent/orchestrator/verifier.py` — `_verify_tier1`
- `tests/unit/test_verifier.py`
- `tests/unit/test_skill_librarian_integration.py`

---

## Test Command
```bash
.venv/bin/python -m pytest tests/unit/ -x -q
.venv/bin/python -m pytest tests/integration/ -x -q
```

Pin `model_provider="local"` in any test `_make_config()` helper to avoid `.env` leak.
