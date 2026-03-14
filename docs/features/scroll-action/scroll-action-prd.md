# PRD: Scroll Action Testing + On-Fail/Replan Fix

## Problem Statement
1. The scroll action was added to the agent (4 files) but has zero test coverage — no unit tests, no integration tests.
2. The `replan_from_state.md` prompt does not list available actions (including scroll), so the replanner may not know scroll exists.
3. During real runs, when a critical step fails (e.g., "click Return button" not found on Amazon), the replan loop in `_replan_and_continue()` continues executing subsequent dependent steps instead of stopping — the replan execution loop lacks the same failure-handling logic as the main loop.

## Acceptance Criteria

### Scroll Action Tests
1. Unit test: `ActionStep(action="scroll")` is valid; `ActionStep(action="scroll_down")` raises `ValueError` (alias only works via `from_dict`)
2. Unit test: `ActionStep.from_dict({"action": "scroll_down"})` produces `action="scroll"`; same for `scroll_up`
3. Unit test: `AppleScriptActuator.scroll()` returns success with correct output message for up/down/left/right
4. Unit test: `AppleScriptActuator.scroll()` returns failure when pyautogui raises an exception
5. Unit test: orchestrator `_dispatch_action()` correctly maps `direction` and `amount` params to `actuator.scroll()` calls — verifying clicks sign, horizontal flag, and coordinate passthrough
6. Unit test: orchestrator dispatches scroll with default amount (3) when `amount` param is missing
7. Unit test: `plan_from_prompt.md` contains `scroll` in the available actions section
8. Unit test: `replan_from_state.md` contains `scroll` in available actions (after we add it)
9. Integration test: a plan containing a scroll step can be validated, executed, and verified through the orchestrator

### On-Fail/Replan Fix
10. The replan execution loop in `_replan_and_continue()` must handle step failures the same way as the main `execute()` loop — with retries and replan escalation
11. Unit test: when a replan step fails with `on_fail="retry_different"`, it retries up to `max_retries` before giving up
12. Unit test: when a replan step fails and `on_fail="abort"`, execution stops immediately
13. Unit test: the replan loop does not recurse infinitely (max 1 replan depth)

### Backward Compatibility
14. All 1323+ existing tests must continue to pass

## Out of Scope
- Adding new scroll variants (pinch, zoom, smooth scroll)
- Changes to the planner LLM prompts beyond adding scroll to replan_from_state.md
- Scroll verification strategies (vision-based scroll detection)

## Success Metrics
- 20+ new unit tests for scroll action
- 3+ integration tests for scroll
- 5+ unit tests for on_fail/replan fix
- 0 regressions in existing tests
