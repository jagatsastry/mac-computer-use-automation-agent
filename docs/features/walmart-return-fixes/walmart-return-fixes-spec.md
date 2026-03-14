# Architecture Spec: Walmart Return Fixes

**Date**: 2026-03-13
**Status**: Draft
**PRD**: [walmart-return-fixes-prd.md](walmart-return-fixes-prd.md)
**SOTA**: [walmart-return-fixes-sota.md](walmart-return-fixes-sota.md)
**Codebase**: [walmart-return-fixes-codebase.md](walmart-return-fixes-codebase.md)

---

## Domain Slice Decomposition (2 Engineers)

### Engineer 1 — Plan Robustness (P0-1 + P0-2 + P2-1)

Fixes plan parsing, navigation enforcement, and skill registry hygiene.

**Files to modify:**
- `src/automation_agent/shared_models.py` — expand `_ACTION_ALIASES`, add smart unknown-action fallback
- `src/automation_agent/planner/planner.py` — add step-drop detection and WARNING logging in `_parse_plan_response()`
- `src/automation_agent/planner/prompts/plan_from_prompt.md` — add skill-mandated navigation instruction
- `src/automation_agent/planner/prompts/replan_from_state.md` — same navigation instruction
- `src/automation_agent/orchestrator/agent.py` — add navigation prepend safety net in `execute()`
- `src/automation_agent/skills/registry.py` — add duplicate name validation in `load_from_directory()`
- `tests/unit/test_plan_step_preservation.py` — **new file**, unit tests for Engineer 1

**Files to delete:**
- `src/automation_agent/skills/library/return-walmart-order.md` (stub)
- `src/automation_agent/skills/library/return-walmart-order-2.md` (stub)

---

### Engineer 2 — Execution Recovery (P1-1 + P1-2 + P2-2)

Fixes wait_for_user timeout, scroll recovery, and screenshot persistence.

**Files to modify:**
- `src/automation_agent/orchestrator/agent.py` — fix infeasibility timing, add scroll recovery, fix wait condition extraction, add screenshot saves
- `src/automation_agent/config.py` — add `save_step_screenshots` config flag
- `tests/unit/test_execution_recovery.py` — **new file**, unit tests for Engineer 2

---

## Fix Specifications

### Fix 1: Expand Action Aliases (AC-1, P0-1)

**File**: `src/automation_agent/shared_models.py`
**Location**: `_ACTION_ALIASES` dict at lines 32-53

**Change**: Add these entries to `_ACTION_ALIASES`:

```python
# AC-1: Additional LLM-probable action names
"select": "click",
"choose": "click",
"tap": "click",
"pick": "click",
"submit": "click",
"submit_form": "click",
"press": "press_key",
"fill": "type_text",
"fill_in": "type_text",
"input": "type_text",
"write": "type_text",
"go_to": "open_url",
"browse": "open_url",
"visit": "open_url",
"look": "observe",
"check": "observe",
"inspect": "observe",
"end": "done",
"stop": "done",
```

**Rationale**: The existing alias map at line 39 already has `"enter_text": "type_text"` and `"navigate": "open_url"`. These additions cover the remaining LLM-probable action names from the gap report. `select`, `choose`, `fill`, `submit` were the specific actions seen in the failing customer runs.

---

### Fix 2: Step-Drop Detection and Smart Fallback (AC-1, P0-1)

**File**: `src/automation_agent/planner/planner.py`
**Location**: `_parse_plan_response()` at lines 458-480

**Change**: After the existing parsing loop (line 458-465), add step-drop detection and a smart fallback for truly unknown actions.

**Current code** (lines 458-465):
```python
steps = []
skipped = []
for s in data["steps"]:
    try:
        steps.append(ActionStep.from_dict(s))
    except ValueError as e:
        skipped.append(f"{s.get('action', '?')}: {e}")
```

**New code** — replace lines 458-465 with:
```python
steps = []
skipped = []
for s in data["steps"]:
    try:
        steps.append(ActionStep.from_dict(s))
    except ValueError as e:
        # AC-1: Smart fallback — map unknown actions instead of dropping
        # This is a LAST-RESORT fallback. Fix 1's alias map should catch
        # all common LLM action names. This only fires for truly novel actions.
        action_name = s.get("action", "")
        params = s.get("params", {})

        # Guard: actions that sound like waits/scrolls should NOT become clicks
        _no_click_keywords = {"wait", "scroll", "delay", "sleep", "pause"}
        if any(kw in action_name.lower() for kw in _no_click_keywords):
            skipped.append(f"{action_name}: {e} (refused click fallback)")
            continue

        if "text" in params:
            fallback_action = "type_text"
        else:
            fallback_action = "click"
        logger.warning(
            "Unknown action '%s' mapped to '%s' (best-effort fallback)",
            action_name,
            fallback_action,
            original_error=str(e),
            step_description=s.get("verify", ""),
        )
        # Preserve the step with the fallback action
        s_copy = dict(s)
        s_copy["action"] = fallback_action
        try:
            steps.append(ActionStep.from_dict(s_copy))
        except ValueError:
            # Even the fallback failed — truly skip
            skipped.append(f"{action_name}: {e}")
```

After the loop (before line 467 `if not steps:`), add step-count assertion:
```python
raw_count = len(data["steps"])
parsed_count = len(steps)
if parsed_count < raw_count:
    dropped = raw_count - parsed_count
    logger.warning(
        "Plan step count mismatch: LLM returned %d steps but only %d parsed "
        "(%d dropped). Skipped: %s",
        raw_count,
        parsed_count,
        dropped,
        "; ".join(skipped),
    )
```

**Error messages**:
- `"Unknown action '%s' mapped to '%s' (best-effort fallback)"` — WARNING
- `"Plan step count mismatch: LLM returned %d steps but only %d parsed..."` — WARNING

---

### Fix 3: Skill-Mandated Navigation Instruction (AC-2, P0-2)

**File**: `src/automation_agent/planner/prompts/plan_from_prompt.md`
**Location**: After line 9 (end of `## Current Screen State` section)

**Change**: Add the following text between the `## Current Screen State` and `## Skill Priors` sections:

```markdown
**IMPORTANT**: When a skill template specifies navigation steps (open_url, activate_app),
you MUST include them in the plan even if the screen appears to already show the target page.
The current screen state may be stale from a previous task. Skill navigation steps are a
contract, not a suggestion. Always navigate fresh.
```

**File**: `src/automation_agent/planner/prompts/replan_from_state.md`
**Location**: After line 12 (end of `## Current Screen State` section)

**Change**: Add the same instruction text.

---

### Fix 4: Navigation Prepend Safety Net (AC-2, P0-2)

**File**: `src/automation_agent/orchestrator/agent.py`
**Location**: `execute()` method, after line 334 (`plan = fallback_plan`) — AFTER the replace_with_fallback block completes.

**Why after line 334**: `fallback_plan` is created at line 309. The truncation/fallback logic (lines 310-334) may replace `plan` entirely with `fallback_plan`. The navigation prepend must run AFTER this block to avoid: (a) referencing `fallback_plan` before it exists, and (b) prepending nav to a plan that's about to be replaced anyway.

**Change**: Insert after line 334 (after the `plan = fallback_plan` assignment and before the `slog.info("Plan generated"...)` at line 335):

```python
# AC-2: Ensure skill-mandated navigation is present
if skill_context and fallback_plan:
    plan = AutomationAgent._ensure_skill_navigation(plan, fallback_plan)
```

**New method** — add to `AutomationAgent` class as `@staticmethod`:

```python
@staticmethod
def _ensure_skill_navigation(
    plan: ActionPlan,
    fallback_plan: ActionPlan,
) -> ActionPlan:
    """Prepend skill-mandated navigation if the LLM plan omits it.

    Checks whether the first navigation step from the fallback (skill) plan
    is present in the first 3 steps of the LLM plan. If missing, prepends it.
    """
    nav_actions = {"open_url", "activate_app"}

    # Find the first nav step in the fallback plan
    fallback_nav = None
    for step in fallback_plan.steps:
        if step.action in nav_actions:
            fallback_nav = step
            break

    if fallback_nav is None:
        return plan  # Skill has no navigation — nothing to enforce

    # Check if the LLM plan already has a nav step in its first 3 steps.
    # DE review: 3 is sufficient because no current skill has more than 1
    # pre-navigation step (e.g., observe). If skills grow more complex with
    # multiple pre-nav setup steps, increase this or scan the full plan.
    head = plan.steps[:3]
    has_nav = any(s.action in nav_actions for s in head)
    if has_nav:
        return plan

    # Prepend the skill's navigation step
    logger.warning(
        "LLM plan missing skill-mandated navigation — prepending",
        nav_action=fallback_nav.action,
        nav_params=fallback_nav.params,
    )
    new_steps = [fallback_nav] + list(plan.steps)
    return ActionPlan(
        steps=new_steps,
        goal=plan.goal,
        skill_name=plan.skill_name,
        raw_llm_response=plan.raw_llm_response,
        planning_duration_ms=plan.planning_duration_ms,
        token_usage=plan.token_usage,
        replan_patch=plan.replan_patch,
    )
```

**Design decision** (from PRD review round 2): We check for ANY `open_url`/`activate_app` in the first 3 steps rather than exact URL matching. This covers the actual failure mode (LLM completely omits navigation) without brittleness from URL normalization differences.

---

### Fix 5: wait_for_user Smart Skip (AC-3, P1-1)

**File**: `src/automation_agent/orchestrator/agent.py`

#### Part A: Improve `_extract_wait_condition()`

**Location**: `_extract_wait_condition()` at lines 1789-1804

**Change**: Add additional regex patterns after the existing `^If\s+...` match:

```python
@staticmethod
def _extract_wait_condition(text: str) -> str:
    """Extract a visibility predicate from a conditional wait instruction."""
    stripped = text.strip()

    # Pattern 1: "If X, wait for user..."  (existing)
    match = re.match(
        r"^If\s+(.+?),\s*wait for (?:the )?user(?:\s+to\s+.+)?$",
        stripped,
        flags=re.IGNORECASE,
    )
    if match:
        condition = match.group(1).strip().rstrip(".")
        condition = re.sub(r"(?i)\bappears?\b", "is visible", condition)
        condition = re.sub(r"\s+", " ", condition).strip()
        if not re.search(
            r"(?i)\b(?:is|are|visible|shown|loaded|frontmost)\b",
            condition,
        ):
            condition = f"{condition} is visible"
        return condition

    # Pattern 2: Login/sign-in variants (DE review: broadened from "Please log in" only)
    # Matches: "Please log in to X", "Log in to X", "Sign in to X",
    #          "Please sign in to X", "You need to log in to X"
    login_match = re.match(
        r"^(?:Please\s+)?(?:You\s+(?:need|may need)\s+to\s+)?(?:log|sign)\s+in\s+to\s+(.+?)(?:\s+first)?$",
        stripped,
        flags=re.IGNORECASE,
    )
    if login_match:
        site = login_match.group(1).strip().rstrip(".")
        return f"{site} login page is visible"

    # Pattern 3: "Please complete X" / "Complete X" -> "X form is visible"
    complete_match = re.match(
        r"^(?:Please\s+)?complete\s+(.+)$",
        stripped,
        flags=re.IGNORECASE,
    )
    if complete_match:
        task = complete_match.group(1).strip().rstrip(".")
        return f"{task} form is visible"

    return ""
```

#### Part B: Add pre-wait observe check in `_wait_for_user()`

**Location**: `_wait_for_user()` at lines 2248-2327

**Change**: Before the polling loop (between lines 2269 and 2271), add an observe-based check. Also reduce the default timeout for unconditional waits.

After the existing condition check block (line 2269), add:
```python
# AC-3: If no condition was extracted, use a shorter timeout
effective_timeout = self._WAIT_TIMEOUT_S
if not wait_condition:
    effective_timeout = min(self._WAIT_TIMEOUT_S, 30.0)
```

Then replace `self._WAIT_TIMEOUT_S` on line 2297 with `effective_timeout`:
```python
while elapsed < effective_timeout:
```

And update the timeout log on line 2322:
```python
slog.warning("wait_for_user timed out", timeout_s=effective_timeout)
```

And the print on line 2273:
```python
print(f"  (will auto-resume when screen changes, timeout {effective_timeout}s)")
```

---

### Fix 6: Parse on_fail Directives from Skill Templates (AC-3 + AC-4)

**File**: `src/automation_agent/orchestrator/agent.py`
**Location**: `_parse_skill_steps()` at lines 1521-1543

**Change**: Extend parsing to also capture `- on_fail:` lines. Return `(instruction, verify, on_fail)` tuples.

```python
@staticmethod
def _parse_skill_steps(skill_context: str) -> list[tuple[str, str, str]]:
    """Parse numbered skill text into (instruction, verify, on_fail) tuples."""
    steps: list[tuple[str, str, str]] = []
    instruction: Optional[str] = None
    verify = ""
    on_fail = ""

    for raw_line in skill_context.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        step_match = re.match(r"^\d+\.\s+(.*)$", line)
        if step_match:
            if instruction is not None:
                steps.append((instruction, verify, on_fail))
            instruction = step_match.group(1).strip()
            verify = ""
            on_fail = ""
            continue
        if instruction and line.lower().startswith("- verify:"):
            verify = line.split(":", 1)[1].strip()
        elif instruction and line.lower().startswith("- on_fail:"):
            on_fail = line.split(":", 1)[1].strip()

    if instruction is not None:
        steps.append((instruction, verify, on_fail))
    return steps
```

**Impact on callers**: The only call site is `_build_skill_fallback_plan()` at line 1470. Update the unpacking:

```python
# Before:
for instruction, verify in self._parse_skill_steps(primary_section):
    action_steps = self._compile_skill_instruction(instruction, verify)

# After:
for instruction, verify, on_fail in self._parse_skill_steps(primary_section):
    action_steps = self._compile_skill_instruction(instruction, verify)
    # Apply on_fail metadata to the LAST compiled step (the one that will
    # fail and need recovery — not the first, which may be a navigation step)
    if action_steps and on_fail:
        last_step = action_steps[-1]
        if "scroll" in on_fail.lower():
            last_step.params["_scroll_recovery"] = True
            last_step.params["_max_scrolls"] = 3
        if "wait_for_user" in on_fail.lower() or "log in" in on_fail.lower():
            wait_condition = self._extract_wait_condition(on_fail)
            if wait_condition:
                # Insert a conditional wait_for_user step AFTER the current step
                wait_step = ActionStep(
                    action="wait_for_user",
                    params={"message": on_fail, "condition": wait_condition},
                    verify="",
                    on_fail="abort",
                )
                action_steps.append(wait_step)
```

**Design note (DE review)**: `on_fail` metadata is applied to the LAST step in the compiled list because that is the step whose failure triggers the recovery. For example, `_compile_skill_instruction("click: Find order")` returns `[ActionStep(action="click", ...)]` — the click step is both first and last. For compound instructions like "Open Safari and navigate to X", the list is `[activate_app, open_url]` — the `on_fail` applies to `open_url` (the step that can fail on navigation).

---

### Fix 7: Scroll Recovery Before Infeasibility (AC-4, P1-2)

**File**: `src/automation_agent/orchestrator/agent.py`

#### Part A: Gate infeasibility behind scroll recovery

**Location**: Lines 454-472 (the `AC-4: critical-path absence` block)

**Change**: Replace the immediate infeasibility check with a scroll recovery loop:

```python
# AC-4: On element NOT_FOUND, attempt scroll recovery before infeasibility
# DE review: Extended from click-only to also cover type_text with element param,
# since form fields below the fold hit the same NOT_FOUND pattern.
_scrollable_actions = {"click", "type_text"}
if (
    not result.success
    and step.action in _scrollable_actions
    and step.params.get("element")
    and result.error
    and "not found" in result.error.lower()
):
    # Try scroll recovery first
    max_scrolls = step.params.get("_max_scrolls", 3)
    scroll_result = await self._scroll_recovery(
        step, result, step_results, goal, max_scrolls
    )
    if scroll_result is not None:
        if scroll_result.success:
            # Scroll recovery found the element — use this result
            result = scroll_result
        else:
            # Scroll exhausted — NOW check infeasibility
            infeas_result = await self._check_infeasibility(
                goal, frustration, step_results, force=True
            )
            if infeas_result is not None:
                infeas_result.total_duration_ms = int(
                    (time.monotonic() - start) * 1000
                )
                infeas_result.iterations = iterations
                infeas_result.goal = goal
                infeas_result.run_id = self.logger.run_id
                return infeas_result
```

#### Part B: New `_scroll_recovery()` method

**Add to `AutomationAgent` class**:

**Class constant** — add to `AutomationAgent` alongside existing `_WAIT_POLL_INTERVAL_S`, `_WAIT_TIMEOUT_S`:
```python
_SCROLL_SETTLE_S: float = 1.0  # seconds to wait after scroll for lazy-loaded content
```

```python
async def _scroll_recovery(
    self,
    step: ActionStep,
    initial_result: StepResult,
    history: list,
    goal: str,
    max_scrolls: int = 3,
) -> Optional[StepResult]:
    """Attempt to find an element by scrolling down before declaring failure.

    Returns:
        StepResult with success=True if element found after scrolling.
        StepResult with success=False if max_scrolls exhausted.
        None if scroll recovery is not applicable.
    """
    element_desc = step.params.get("element", "")
    if not element_desc:
        return None

    for i in range(max_scrolls):
        slog.info(
            "Scroll recovery attempt",
            attempt=i + 1,
            max_scrolls=max_scrolls,
            element=element_desc,
        )
        self.logger.log_event(
            EventType.STEP_RETRY,
            f"Scroll recovery {i + 1}/{max_scrolls} for '{element_desc}'",
            data={"strategy": "scroll_down_and_retry", "scroll_attempt": i + 1},
        )

        # Scroll down half a viewport
        scroll_step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 3},
            verify="",
            on_fail="abort",
        )
        await self._dispatch_action(scroll_step)
        # DE review: Use class constant for testability (mock or override in tests)
        await asyncio.sleep(self._SCROLL_SETTLE_S)  # Wait for lazy-loaded content

        # Retry finding the element
        find_result = await self._find_element(element_desc)
        if find_result is not None:
            # Element found — execute the click directly via _dispatch_action,
            # passing the pre-resolved location to AVOID a redundant second
            # _find_element call (DE review: _dispatch_action calls _find_element
            # again internally unless _pre_resolved_location is provided).
            slog.info(
                "Scroll recovery succeeded",
                attempt=i + 1,
                element=element_desc,
            )
            action_result = await self._dispatch_action(
                step, _pre_resolved_location=find_result
            )
            success = action_result.get("success", False)
            return StepResult(
                step=step,
                success=success,
                verification_method="",
                evidence=f"Scroll recovery click after {i + 1} scrolls",
                error=action_result.get("error") if not success else None,
                retry_strategies_used=[f"scroll_recovery_{i + 1}"],
            )

    slog.warning(
        "Scroll recovery exhausted",
        max_scrolls=max_scrolls,
        element=element_desc,
    )
    return StepResult(
        step=step,
        success=False,
        verification_method="",
        evidence=f"Element '{element_desc}' not found after {max_scrolls} scroll attempts",
        error=f"Element not found after {max_scrolls} scrolls: {element_desc}",
    )
```

#### Part C: Add scroll_down_and_retry to `_vary_strategy()`

**Location**: `_vary_strategy()` at lines 3045-3086

**Change**: Insert a new strategy branch for click NOT_FOUND before the existing `missing_target` handling. After line 3049 (`search_click = ...`), add:

```python
# AC-4: Scroll down as first recovery strategy for NOT_FOUND
if missing_target and not suggested_element and attempt == 1:
    scroll_params = {"direction": "down", "amount": 3}
    return ("scroll_down_and_retry", _retry_step("scroll", scroll_params))
```

This inserts scroll-down as the first retry strategy when an element is not found and there's no suggested alternative.

---

### Fix 8: Duplicate Skill Cleanup (AC-5, P2-1)

#### Part A: Delete stub files

**Delete**:
- `src/automation_agent/skills/library/return-walmart-order.md`
- `src/automation_agent/skills/library/return-walmart-order-2.md`

**Keep**: `src/automation_agent/skills/library/return_walmart_order.md` (the real 5-step skill)

#### Part B: Add duplicate name validation

**File**: `src/automation_agent/skills/registry.py`
**Location**: `load_from_directory()` at lines 184-190

**Change**: Replace the silent overwrite with an error log + skip:

```python
if skill.name in self._skills:
    # DE review: Skill dataclass has no source_path field (verified).
    # Use the md_file path from the loading loop directly.
    std_logger.error(
        "Duplicate skill name '%s': '%s' conflicts with previous definition — "
        "skipping duplicate",
        skill.name,
        md_file,
    )
    continue  # Skip duplicate instead of overwriting
```

---

### Fix 9: Screenshot Persistence (AC-6, P2-2)

**File**: `src/automation_agent/orchestrator/agent.py`

#### Part A: Save screenshot after observe steps

**Location**: `_execute_step()`, observe handler at lines 672-679

**Change**: After `desc = await self.coordinator.describe_screen()` (line 673), add:

```python
# AC-6: Save observe screenshot
if self.config.save_step_screenshots:
    try:
        obs_b64 = await self._capture_screenshot()
        if obs_b64:
            self.logger.save_screenshot(
                base64.b64decode(obs_b64),
                f"step_{index:02d}_observe",
            )
    except Exception:
        pass
```

#### Part B: Save full screenshot on NOT_FOUND

**Location**: `_find_element()` at approximately line 2085 (the `return None` path when element is not found)

**Change**: Before returning None, save the full screenshot:

```python
# AC-6: Save full screenshot on NOT_FOUND
if self.config.save_step_screenshots and screenshot_b64:
    try:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", description)[:40]
        self.logger.save_screenshot(
            base64.b64decode(screenshot_b64),
            f"not_found_{slug}",
        )
    except Exception:
        pass
return None
```

#### Part C: Save post-action screenshot

**Location**: `_execute_step()`, after the action is executed and verified but before returning the result. Find the main return path for successful steps.

**Change**: Add post-action screenshot save:

```python
# AC-6: Save post-action screenshot
if self.config.save_step_screenshots:
    try:
        post_b64 = await self._capture_screenshot()
        if post_b64:
            path = self.logger.save_screenshot(
                base64.b64decode(post_b64),
                f"step_{index:02d}_post_{step.action}",
            )
            # Write path into result for event log linkage
            # DE review: StepResult.screenshot_path exists (shared_models.py:235)
            result.screenshot_path = path
    except Exception:
        pass
```

#### Part D: Config flag

**File**: `src/automation_agent/config.py`
**Location**: In the `AgentConfig` class, add after the existing config fields:

```python
save_step_screenshots: bool = Field(
    default=True,
    description="Save screenshots at each step (observe, NOT_FOUND, post-action)",
)
```

Environment variable: `AGENT_SAVE_STEP_SCREENSHOTS=true` (default true).

**Known limitation (DE review)**: No automatic disk cleanup or budget cap. A 10-step run with scroll recovery may produce ~20 screenshots (~6MB). Over many runs, disk usage grows unboundedly. This is acceptable for a P2 observability fix — log rotation / cleanup should be addressed as a follow-up if automated runs become frequent. The config flag allows users to disable if disk is a concern.

---

## Testing Strategy

### Test Config Pattern

All tests use the standard `_make_config()` helper:

```python
def _make_config(**overrides):
    return AgentConfig(
        grounding_model="",
        grounding_server_url="",
        model_provider="local",
        **overrides,
    )
```

### Engineer 1 Tests: `tests/unit/test_plan_step_preservation.py`

```
Test cases:

1. test_action_aliases_select_maps_to_click
   - Feed ActionStep.from_dict({"action": "select", ...})
   - Assert step.action == "click"

2. test_action_aliases_fill_maps_to_type_text
   - Feed ActionStep.from_dict({"action": "fill", "params": {"text": "hello"}})
   - Assert step.action == "type_text"

3. test_action_aliases_submit_maps_to_click
   - Feed ActionStep.from_dict({"action": "submit", ...})
   - Assert step.action == "click"

4. test_action_aliases_go_to_maps_to_open_url
   - Feed ActionStep.from_dict({"action": "go_to", "params": {"url": "..."}})
   - Assert step.action == "open_url"

5. test_parse_plan_no_steps_dropped
   - Build LLM response with steps: ["observe", "select", "fill", "click", "done"]
   - Call _parse_plan_response()
   - Assert len(plan.steps) == 5
   - Assert plan.steps[1].action == "click"  (select -> click)
   - Assert plan.steps[2].action == "type_text"  (fill -> type_text)

6. test_parse_plan_unknown_action_falls_back_to_click
   - LLM response with action "wiggle" (no text param)
   - Assert step mapped to click, not dropped

7. test_parse_plan_unknown_action_with_text_falls_back_to_type_text
   - LLM response with action "scribble" and text param
   - Assert step mapped to type_text

8. test_parse_plan_step_count_warning
   - Mock logger, feed plan where one step truly cannot be parsed even as fallback
   - Assert warning logged with step count mismatch

9. test_skill_navigation_prepend_when_missing
   - Create plan with no open_url in first 3 steps
   - Create fallback plan with open_url step 1
   - Call AutomationAgent._ensure_skill_navigation(plan, fallback)  (@staticmethod)
   - Assert plan.steps[0].action == "open_url"

10. test_skill_navigation_not_prepended_when_present
    - Create plan with open_url as step 1
    - Call AutomationAgent._ensure_skill_navigation(plan, fallback)  (@staticmethod)
    - Assert plan unchanged (no duplicate nav)

11. test_prompt_contains_navigation_instruction
    - Read plan_from_prompt.md content
    - Assert "contract, not a suggestion" in content

12. test_replan_prompt_contains_navigation_instruction
    - Read replan_from_state.md content
    - Assert "contract, not a suggestion" in content

13. test_duplicate_skill_names_raises_error
    - Create two skill files with same name: field
    - Call load_from_directory()
    - Assert second file is skipped (not loaded)
    - Assert ERROR logged

14. test_single_skill_loads_clean
    - Load directory with only return_walmart_order.md
    - Assert no warnings/errors
    - Assert skill loaded correctly
```

### Engineer 2 Tests: `tests/unit/test_execution_recovery.py`

```
Test cases:

1. test_extract_wait_condition_if_pattern
   - Input: "If a login page appears, wait for user"
   - Assert returns "a login page is visible"

2. test_extract_wait_condition_please_log_in
   - Input: "Please log in to Walmart"
   - Assert returns "Walmart login page is visible"

3. test_extract_wait_condition_please_complete
   - Input: "Please complete the verification"
   - Assert returns "the verification form is visible"

4. test_extract_wait_condition_unrecognized
   - Input: "Do something random"
   - Assert returns ""

5. test_wait_for_user_skips_when_condition_not_present
   - Create agent with mock coordinator
   - coordinator.verify_condition = AsyncMock(return_value=False)
   - Call _wait_for_user() with step containing condition "login page is visible"
   - Assert returns immediately with success=True
   - Assert "Skipped wait" in evidence

6. test_wait_for_user_unconditional_uses_short_timeout
   - Create agent, mock sleep/capture
   - Call _wait_for_user() with no condition extracted
   - Assert effective timeout is 30s, not 120s

7. test_scroll_recovery_finds_element_after_2_scrolls
   - Mock _find_element to return None twice, then FindElementResult
   - Mock _dispatch_action (for scroll and click)
   - Call _scroll_recovery(step, result, [], goal, max_scrolls=3)
   - Assert success=True
   - Assert _dispatch_action called 3 times (2 scrolls + 1 click)

8. test_scroll_recovery_exhausted_returns_failure
   - Mock _find_element to always return None
   - Call _scroll_recovery(step, result, [], goal, max_scrolls=3)
   - Assert success=False
   - Assert "not found after 3 scrolls" in error

9. test_infeasibility_not_fired_before_scroll_recovery
   - Mock agent with click step that gets NOT_FOUND
   - Mock _scroll_recovery to return success
   - Run execute loop logic
   - Assert _check_infeasibility was NOT called

10. test_infeasibility_fires_after_scroll_exhausted
    - Mock _scroll_recovery to return failure
    - Mock _check_infeasibility to return infeasible
    - Assert infeasibility returned after scroll exhaustion

11. test_parse_skill_steps_extracts_on_fail
    - Input: skill text with "- on_fail: scroll down to find it"
    - Assert tuple[2] == "scroll down to find it"

12. test_parse_skill_steps_missing_on_fail
    - Input: skill text with verify but no on_fail
    - Assert tuple[2] == ""

13a. test_on_fail_scroll_sets_scroll_recovery_param (DE review addition)
    - Input: skill text with "- on_fail: If the item is not visible, scroll down to find it"
    - Build fallback plan via _build_skill_fallback_plan()
    - Assert the click step has params["_scroll_recovery"] == True
    - Assert the click step has params["_max_scrolls"] == 3

13b. test_on_fail_login_generates_wait_step (DE review addition)
    - Input: skill text with "- on_fail: If a login page appears, use wait_for_user"
    - Build fallback plan via _build_skill_fallback_plan()
    - Assert a wait_for_user step is inserted after the open_url step
    - Assert the wait step has a "condition" param

13c. test_fallback_plan_from_real_walmart_skill (DE review addition)
    - Load the actual return_walmart_order.md skill file
    - Call _build_skill_fallback_plan("Return Crest Whitestrips on Walmart", skill_text)
    - Assert plan has 5+ steps
    - Assert step 1 is open_url
    - Assert the click step for order has _scroll_recovery=True
    - Assert a wait_for_user step exists with login condition

13d. test_scroll_recovery_for_type_text_not_found (DE review addition)
    - Create type_text step with element="address field"
    - Mock _find_element to return None then FindElementResult
    - Call _scroll_recovery with the type_text step
    - Assert scroll recovery works identically to click

14. test_screenshot_saved_on_observe
    - Mock agent with save_step_screenshots=True
    - Execute observe step
    - Assert save_screenshot called with "step_00_observe"

14. test_screenshot_saved_on_not_found
    - Mock _find_element path to NOT_FOUND
    - Assert save_screenshot called with "not_found_*" name

15. test_screenshot_not_saved_when_disabled
    - Mock agent with save_step_screenshots=False
    - Execute observe step
    - Assert save_screenshot NOT called

16. test_config_save_step_screenshots_default_true
    - Create AgentConfig()
    - Assert config.save_step_screenshots is True
```

### Integration Test: `tests/unit/test_execution_recovery.py` (at bottom)

```
test_full_scroll_recovery_integration:
   - Create agent with mocked components
   - Set up a 5-step plan from skill: [open_url, observe, click(item), click(return), done]
   - Mock find_element to return NOT_FOUND for step 3 on first 2 calls, success on 3rd
   - Mock actuator to succeed for all actions
   - Execute the full plan
   - Assert: all 5 steps executed
   - Assert: scroll actions logged in event stream
   - Assert: no infeasibility abort
   - Assert: screenshots saved for observe step
```

---

## Key Constraints

- **Python 3.11**, line length 100 (Black + Ruff)
- **Ruff rules**: E, W, F, I, B, C4, UP (ignores E501, B008)
- **pytest-asyncio** with `asyncio_mode = "auto"` — use `async def test_...()`
- **Test config**: `_make_config()` must include `grounding_model=""`, `grounding_server_url=""`, `model_provider="local"`
- **No .env leaks**: `.env` may contain `AGENT_MODEL_PROVIDER=anthropic` — always pin `model_provider="local"` in test config

---

## PRD Review Adjustments

These adjustments were made during PRD review (3 rounds):

1. **AC-1 fallback**: Smart fallback — `click` if no `text` param, `type_text` if `text` param present. PRD said universal `click` fallback.
2. **AC-3 + AC-4 overlap**: Single `_parse_skill_steps()` change returns `(instruction, verify, on_fail)` tuples, used by both fixes.
3. **AC-2 detection**: Check for ANY nav action in first 3 steps rather than exact URL matching.
4. **AC-4 timing**: New `_scroll_recovery()` method runs inside execute loop before infeasibility, avoids mutating ActionStep.
5. **AC-3 test semantics**: `verify_condition()` returns `False` = condition not present = skip wait. PRD had inverted logic.
6. **AC-3 unconditional timeout**: Reduced to 30s (from 120s) when no condition is extracted.
