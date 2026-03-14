# PRD: Verification Fixes for Amazon Return Workflow

**Date**: 2026-03-12
**Author**: PM (expert-verification-fixes team)
**Status**: Draft
**Inputs**: [SOTA Research](verification-fixes-sota.md), [Codebase Analysis](verification-fixes-codebase.md), [Customer Report](amazon-return-customer-report.md)

---

## Problem Statement

The automation agent fails 4/5 customer test scenarios for the Amazon return workflow. Three root causes account for all failures:

1. **Vision verification falsely denies text field focus state (P0)** -- blocks 4/5 scenarios. The agent clicks the Amazon search bar correctly (screenshots prove focus with autocomplete and cursor visible), but the Tier 2 vision verifier denies "The search field becomes active with a text cursor." No production framework uses vision to verify focus state (SOTA Finding 1.1). The Tier 0 (accessibility) and Tier 1 (actuator state) verifiers have no handler for `click` actions, so verification always falls through to unreliable Tier 2 vision.

2. **The agent cannot recognize when a UI element is permanently absent (P0)** -- blocks 2/5 scenarios. In Scenario 3, the agent scrolled through product recommendations looking for a "Return" button that will never exist on a subscription order. In Scenario 5, the agent searched for a "past 6 months" dropdown option that Amazon does not offer. The retry/replan loop has no concept of "this element confirmed absent from page" -- it can only retry with different queries or replan with the same assumption that the element should exist.

3. **The Amazon return skill template contains inaccurate UI guidance (P1)** -- degrades 2/5 scenarios. The skill does not document Amazon's actual date filter options (years only, no "past 6 months"). It provides no guidance for vague/unspecified item names, causing the planner to search "last week" as a product keyword (Scenario 2). It lacks signals for detecting non-returnable order types like subscriptions (Scenario 3).

---

## Acceptance Criteria

### P0 #1: Text Field Focus Verification

**AC-1: Click-before-type verification bypass.**
When a `click` step fails -- whether from verification denial OR from the screenshot_diff gate forcing `actuator_result["success"] = False` -- AND any subsequent step within the same plan is `type_text` or `press_key` (i.e., a keystroke consumer), the orchestrator must treat the click failure as provisional rather than final. Both failure paths must be covered because the screenshot_diff gate (agent.py:383-403) forces actuator failure BEFORE verification runs, which means verification-level fixes (AC-2, AC-3) never get a chance to fire. The bypass must therefore operate at the step-execution-loop level, AFTER both the screenshot_diff gate and the verification cascade have already produced a failed StepResult.

Detection rule: the click step is eligible for bypass when the accessibility backend reports the focused element has an AX role in `TEXT_INPUT_AX_ROLES` (`AXTextField`, `AXTextArea`, `AXSearchField`, `AXComboBox`) after the click executes. The AX role check must run AFTER the click action dispatches but BEFORE the screenshot_diff gate evaluates, so that the focused-element state is captured while still fresh. If no accessibility backend is available, fall back to keyword matching on the step's element description (containing any of: "search", "input", "text field", "text box", "search bar", "address bar", "URL bar").

The orchestrator must not mutate the original `StepResult` in place. Instead, it must hold the failed `StepResult` aside and proceed to execute the next step. If the next step passes verification, a new replacement `StepResult` for the click must be created with `success=True` and `evidence="Verified by subsequent keystroke step success (type-and-check)"`. If the next step also fails, both the original click `StepResult` (unchanged) and the next step's `StepResult` must be reported. The bypass applies only within a single plan execution, not across replan boundaries. This is the "type-and-check" pattern established in Playwright, XCTest, and UFO (SOTA Finding 1.3).

**AC-2: Accessibility-based focus check for click actions.**
When a `click` action's verification reaches Tier 0, the verifier must query the accessibility backend for the currently focused element's AX role. If the focused element's role is one of `AXTextField`, `AXTextArea`, `AXSearchField`, or `AXComboBox`, Tier 0 must return `(True, "Accessibility confirms text field focused: {role}")` -- short-circuiting before Tier 2 vision is attempted. This check applies to ALL click actions, not just those with text-field keywords in the description -- the AX role is the authoritative signal, not the element description. The canonical AX role list is defined once as a constant (e.g., `TEXT_INPUT_AX_ROLES`) and shared between AC-1 and AC-2. If the accessibility backend is unavailable or returns no focused element, Tier 0 must return `None` (inconclusive) as it does today. This mirrors how XCTest and Appium verify focus via `kAXFocusedUIElementAttribute` (SOTA Finding 1.1).

**AC-3: Ternary verification prompt and API change.**
The `verify_condition.md` prompt must support three outcomes: YES (condition confirmed), NO (condition contradicted by visible evidence), and UNCLEAR (insufficient evidence to determine).

**API change (breaking):** The return type of `coordinator.verify_condition()` changes from `bool` to `Optional[bool]`. The three return values are: `True` (confirmed), `False` (denied), `None` (inconclusive). This is a breaking change to the `ScreenCoordinator` protocol in `protocols.py`. All callers of `verify_condition()` must be updated to handle the `None` case.

**Downstream cascade changes:**
- `_verify_tier2()` in `verifier.py`: When `verify_condition()` returns `None`, `_verify_tier2` must return `None` (inconclusive), NOT `(False, ...)`. This allows the cascade in `verify()` to fall through to the actuator-result fallback.
- `verify()` in `verifier.py`: No change needed -- it already treats `None` from any tier as "escalate to next tier" and falls back to actuator result after all tiers return `None`.
- Any other direct callers of `verify_condition()` (e.g., `_reflect_failed_action`, `_validate_candidate`) must treat `None` as inconclusive and not as confirmation or denial.

**Parsing rule:** The coordinator's `verify_condition()` method must check the LLM response in order: `response_lower.startswith("yes")` returns `True`, `response_lower.startswith("unclear")` returns `None`, anything else returns `False`.

**Prompt change:** The current "Be conservative -- if you're not confident, say NO" instruction must be replaced with: "Answer YES if you see evidence the condition is met. Answer NO if you see evidence the condition is NOT met (e.g., a different page is visible, the element is clearly in the wrong state). Answer UNCLEAR if you cannot determine the condition from the screenshot (e.g., subtle focus indicators, ambiguous state, low resolution)."

**Regression gate:** Because unit tests mock `verify_condition()` and do not exercise the prompt or parsing, AC-3 must include at least 5 new unit tests that verify the ternary parsing logic in `verify_condition()` itself (mocking only the LLM call, not the parsing). Must also include tests for each downstream caller that exercises the `None` code path. Additionally, the full test suite (AC-10) must pass.

### P0 #2: Element Absence Detection

**AC-4: Confirmed-absent vs. not-yet-found distinction.**
The system must distinguish between "element not found yet" (transient) and "element confirmed absent" (permanent).

**Counter scoping:** The absence counter must be keyed on the ORIGINAL step's element description (from `ActionStep.params["element"]` as set by the planner), NOT on the mutated descriptions produced by `_vary_strategy` retries (e.g., `"Search all orders (look carefully, may be partially hidden)"`). `_vary_strategy` appends context hints and refines descriptions on each retry attempt, but all of these mutations target the same original element. The counter must track: "how many times has `find_element` returned `None` for this original step, across all its retry variations?" The counter resets at replan boundaries because a replan may navigate to a different page where the element could exist.

**Absence classification:** An element must be classified as confirmed absent when both conditions are met: (a) `find_element` returned `None` across the initial attempt PLUS at least 1 retry attempt (i.e., 2+ total `None` results within `_handle_failure`/`_vary_strategy` for the same original step), AND (b) if an accessibility backend is available, a final accessibility tree query also fails to find a matching element (structural confirmation). If no accessibility backend is available, condition (a) alone is sufficient.

**Evaluation point:** The counter is evaluated inside `_vary_strategy` when it would otherwise escalate to replan (i.e., return `None`). At that decision point, if the counter threshold is met, the step's `StepResult.error` must be prefixed with `"Element absent:"` instead of `"Element not found:"`. This prefix is the contract between the retry system and the replan system (AC-5).

**AC-5: Replan prompt receives absence context.**
When `_replan_and_continue()` is invoked and any prior step failed with a confirmed-absent element, the replan prompt must include the list of confirmed-absent elements. The prompt must instruct the planner: "The following UI elements were confirmed absent from the current page: [list]. Do not generate steps that depend on these elements. Consider that the task may be impossible in the current page state." This prevents the replanner from regenerating the same failing steps (SOTA Finding 2.3 -- circular retry loops are a documented failure mode).

**AC-6: Graceful abort on impossible task.**
When the planner receives absence context (AC-5) and determines the task cannot be completed (e.g., no return button exists for a subscription order), the planner must be able to emit a `done` step with an `abort_reason` field in its params. Semantics: a `done` step with `abort_reason` present is a **task failure**, not a success. The orchestrator must set `ExecutionResult.success = False` and `ExecutionResult.error = abort_reason`. This is distinct from a normal `done` step (no `abort_reason`) which means success. The `abort_reason` field must be a human-readable string explaining why the task cannot be completed (e.g., "The 'Return or Replace Items' option is not available for this order, which may indicate the item is a subscription or digital purchase and cannot be returned through the standard flow"). This must not require adding a new action type -- the `done` action already exists and the `abort_reason` param is a new optional field within it.

### P1 #3: Skill Template Accuracy

**AC-7: Planner selects correct calendar year for older orders.**
When the user prompt references an order older than 3 months (e.g., "six months ago", "last September"), the planner must NOT generate a step that clicks a non-existent date filter option (e.g., "past 6 months"). Instead, the planner must generate a step that selects the appropriate calendar year from the dropdown. Test: given the prompt "Return the phone case I bought six months ago" with current date 2026-03-12, the plan must include a step to select "2025" from the date filter dropdown, not "past 6 months". The `return-amazon-order.md` skill template must list Amazon's actual date filter options ("last 30 days", "past 3 months", and specific calendar years) and explicitly state that no other options exist.

**AC-8: Planner browses orders instead of keyword-searching vague items.**
When the `{{item}}` parameter is vague, temporal, or does not name a specific product (e.g., "something I bought last week", "my recent order"), the planner must NOT generate a `type_text` step that searches for the vague phrase as a product keyword. Instead, the planner must generate steps that: (a) use the date filter to narrow to the relevant time period, (b) scroll through visible orders, and (c) use `wait_for_user` to ask for clarification if multiple candidates are visible. Test: given the prompt "Return something I bought last week on Amazon", the plan must NOT contain a `type_text` step with text "last week" or "something I bought last week". The `return-amazon-order.md` skill template must include a conditional branch for vague items that instructs this browse-then-clarify behavior.

**AC-9: Agent detects non-returnable orders and aborts with explanation.**
When the agent navigates to an order details page and encounters signals of a non-returnable order type -- specifically: (a) a "Manage your subscription" button visible instead of "Return or Replace Items", (b) "Download" or "Read now" controls indicating digital content, or (c) absence of any return-related controls after the full order details are visible -- the agent must stop and produce an `ExecutionResult` with `success=False` and an error message explaining the item cannot be returned through the standard flow. The agent must NOT continue scrolling or retrying to find return controls. Test: given a plan step that looks for "Return or Replace Items" on a subscription order page where "Manage your subscription" is the only action available, the system must classify the return button as absent (AC-4), the replan must receive this context (AC-5), and the planner must emit `done` with `abort_reason` (AC-6). The `return-amazon-order.md` Error Recovery section must list these specific detection signals.

### Regression Safety

**AC-10: All existing tests pass.**
All existing tests (719+) must continue to pass after these changes. No existing verification behavior must be degraded for non-text-field click actions or for scenarios where element absence detection is not relevant. The Tier 2 vision verifier must remain the final fallback for actions where Tier 0 and Tier 1 are inconclusive and no type-and-check bypass applies.

---

## Out of Scope

- **Changing the vision model or its prompts** (beyond `verify_condition.md` for AC-3). The grounding model, screenshot resolution, and coordinate extraction are not being modified.
- **Adding new action types** (like `tell_user` or `abort_with_reason`). AC-6 uses the existing `done` action with an additional param field, not a new action type.
- **Rewriting the full orchestrator loop.** The execute-verify-retry-replan structure remains. Changes are scoped to: verification tier logic, failure classification, replan prompt content, and skill template text.
- **Browser state management** (clearing autocomplete/cookies between runs). This is a test harness concern, not an agent concern.
- **Page-load wait logic.** While Scenario 5 was partially affected by verification running before page load, this is a general timing issue that predates these fixes and has broader implications.

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Customer Scenario 1 (specific item return) | FAIL at search bar click | Must pass search bar interaction and proceed to return flow |
| Customer Scenario 2 (vague prompt) | FAIL at search bar click | Must pass search bar interaction; must use date browsing not keyword search for vague items |
| Customer Scenario 3 (non-returnable item) | FAIL stuck scrolling for return button | Must detect absence of return controls and inform user the item cannot be returned |
| Customer Scenario 4 (casual refund phrasing) | FAIL at search bar click | Must pass search bar interaction and proceed to return flow |
| Customer Scenario 5 (expired return window) | FAIL on non-existent date filter option | Must select correct calendar year from dropdown; must handle return window expiration |
| Existing test suite | 719+ passing | 719+ passing (AC-10) |

---

## Implementation Notes

These are non-binding suggestions for the engineering team based on SOTA and codebase research.

**AC-1 (type-and-check bypass)**: The bypass logic lives in the step execution loop (not inside `_execute_step`). Critically, the AX role check must run AFTER the click dispatches but BEFORE the screenshot_diff gate (agent.py:383-403), so the focused-element state is captured while fresh. This means inserting the AX role query between `_dispatch_action()` and the screenshot_diff evaluation. The bypass itself operates AFTER both gates have produced a failed StepResult -- it holds the failed result aside, executes the next step, and creates a new replacement StepResult on success. The original StepResult is never mutated. This covers both the screenshot_diff failure path and the verification failure path.

**AC-2 (Tier 0 focus check)**: Add a branch in `verifier.py:_verify_tier0` for `click` actions. After the click executes, query `get_focused_element()` and check the AX role against `TEXT_INPUT_AX_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"}`. This constant is shared with AC-1's detection logic.

**AC-3 (ternary verification)**: Breaking API change. `verify_condition()` return type changes from `bool` to `Optional[bool]`. Update `ScreenCoordinator` protocol in `protocols.py`. Parsing in `coordinator.py:verify_condition()`: `startswith("yes")` -> True, `startswith("unclear")` -> None, else -> False. Downstream: `_verify_tier2` must return `None` (not `(False, ...)`) when `verify_condition()` returns `None`. All other callers (`_reflect_failed_action`, `_validate_candidate`, etc.) must handle `None` as inconclusive. Requires 5+ new unit tests for ternary parsing + tests for each downstream caller's `None` handling.

**AC-4 (absence detection)**: The counter lives in `_handle_failure`, keyed on the ORIGINAL `step.params["element"]` (not the mutated descriptions from `_vary_strategy`). The original description must be captured before `_vary_strategy` modifies it. Counter resets at replan boundaries. Evaluated at the escalation decision point (when `_vary_strategy` would return `None` to trigger replan). AX cross-check at that point is a final structural confirmation.

**AC-5 (replan context)**: The `replan_from_state.md` prompt template already has a `{{history}}` section. Steps with `"Element absent:"` prefix errors should be collected into a new `{{absent_elements}}` template variable or appended to history with distinct formatting.

**AC-6 (graceful abort)**: The `done` action handler in the orchestrator must check `step.params.get("abort_reason")`. If present: `ExecutionResult(success=False, error=abort_reason)`. If absent: existing success behavior. No new action type needed.

---

## Risks

1. **AC-1 lookahead coupling**: The type-and-check bypass couples adjacent steps. If the plan is modified between step execution (e.g., by a replan), the lookahead assumption may be stale. Mitigation: only apply the bypass within a single plan execution, not across replans.

2. **AC-1 screenshot_diff interaction**: The AX role query must be inserted into `_execute_step` between action dispatch and the screenshot_diff gate. This changes the ordering of operations in a sensitive code path. If the AX query is slow or throws, it could delay or block the screenshot_diff evaluation. Mitigation: the AX query should have a short timeout (same as existing AX calls, ~3s) and failures should result in the query returning None (no bypass eligibility), not blocking execution.

3. **AC-3 prompt regression**: Changing `verify_condition.md` from binary to ternary could affect verification outcomes for all action types, not just text field clicks. The `Optional[bool]` return type change touches the `ScreenCoordinator` protocol and all callers. Mitigation: AC-10 requires full test suite passing. The UNCLEAR outcome should be rare -- the prompt guides the model to commit to YES or NO when evidence exists, reserving UNCLEAR for genuinely ambiguous states. New unit tests for each caller's `None` handling provide additional coverage.

4. **AC-4 false absence**: An element classified as "absent" might actually exist but be off-screen or behind a modal. The 2-attempt threshold and accessibility cross-check mitigate this, but edge cases remain. Mitigation: the absence classification is advisory (informs replan context) not terminal (doesn't force abort). The planner retains the option to try alternative paths.

5. **AC-4 counter vs. mutation**: `_vary_strategy` mutates element descriptions on each retry. If the original description is not captured before mutation begins, the counter keys on different strings and never reaches the threshold. Mitigation: AC-4 explicitly requires keying on the original `step.params["element"]`, captured before `_vary_strategy` runs.
