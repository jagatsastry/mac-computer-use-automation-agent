# SOTA Research: Verification Fixes for P0 Bugs

**Date**: 2026-03-12
**Author**: SOTA Researcher (expert-verification-fixes team)
**Scope**: P0 #1 (false-negative vision verification on text field focus) and P0 #2 (element absence detection)

---

## Table of Contents

1. [P0 #1: False-Negative Vision Verification of Text Field Focus](#p0-1)
   - [1.1 How Browser/UI Frameworks Verify Focus](#11-frameworks)
   - [1.2 How Vision-Based Agents Handle Action Verification](#12-vision-agents)
   - [1.3 The "Type-and-Check" Pattern](#13-type-and-check)
   - [1.4 Root Cause Analysis in Our Codebase](#14-root-cause)
2. [P0 #2: Element Absence Detection](#p0-2)
   - [2.1 How Frameworks Handle "Element Not Found"](#21-frameworks)
   - [2.2 Negative Assertion Patterns](#22-negative-assertions)
   - [2.3 How LLM Agents Reason About Dead Ends](#23-llm-agents)
   - [2.4 Graceful Degradation Strategies](#24-graceful-degradation)
3. [Recommendations](#recommendations)

---

<a name="p0-1"></a>
## 1. P0 #1: False-Negative Vision Verification on Text Field Focus

### Problem Statement

The agent clicks a search bar correctly (proven by screenshots showing autocomplete/cursor), but the Tier 2 vision verifier (which asks an LLM "is this condition true?") denies the focus state. This blocks 4/5 customer test scenarios.

**Root cause hypothesis**: The `verify_condition.md` prompt is overly conservative ("Be conservative -- if you're not confident, say NO"), and focus state (blinking cursor, subtle highlight) is one of the hardest visual signals for VLMs to detect reliably.

<a name="11-frameworks"></a>
### 1.1 How Browser/UI Frameworks Verify Focus

| Framework | Focus Verification Method | Mechanism |
|-----------|--------------------------|-----------|
| **Playwright** | `expect(locator).toBeFocused()` | Checks `document.activeElement` via CDP; auto-retries until timeout |
| **Selenium** | `driver.switchTo().activeElement()` then compare | Uses WebDriver protocol to query active element from browser |
| **XCTest (macOS)** | `element.value(forKey: "hasKeyboardFocus") as? Bool` | Queries the **Accessibility API** `kAXFocusedUIElementAttribute` -- NOT vision |
| **Cypress** | `cy.focused().should('have.id', 'myInput')` | DOM query via `document.activeElement` |
| **Appium (macOS)** | `PFUIElement` + `AXFocused` attribute | AX API via `AXUIElementCopyAttributeValue(systemWideElement, kAXFocusedUIElementAttribute)` |

**Key insight**: **No production framework uses vision/screenshots to verify focus state**. Every single one queries a structured API (DOM, Accessibility, WebDriver protocol). This is because:
1. Focus indicators are subtle (thin blue ring, blinking cursor) and vary by OS theme, app, and input type
2. A blinking cursor may not be captured in a static screenshot (depends on timing)
3. Autocomplete dropdowns appearing is a _consequence_ of focus, not focus itself

**Relevance to our agent**: Our Tier 0 (Accessibility) already handles `type_text` verification by checking if the focused element's value contains the expected text. But for `click` actions that target text fields (i.e., the click-to-focus step _before_ typing), Tier 0 returns `None` (inconclusive) because we only check accessibility for `type_text` and `activate_app` actions. Tier 1 is similarly inconclusive for click. So verification falls through to Tier 2 (vision), which fails because VLMs cannot reliably detect focus indicators.

<a name="12-vision-agents"></a>
### 1.2 How Vision-Based Agents Handle Action Verification

| Agent | Verification Strategy | Key Mechanism |
|-------|----------------------|---------------|
| **UFO (Microsoft)** | Observation-Thought-Action loop | AppAgent continuously observes via **AX APIs + vision detectors** in combination. The ReAct loop with "reflection" lets the agent re-observe after each action. Does NOT rely solely on vision for state verification. |
| **WebVoyager** | Screenshot trajectory evaluation | GPT-4V evaluates the _last k screenshots_ post-hoc. 85.3% agreement with humans. This is task-level evaluation, not step-level verification. |
| **Agent-E** | Change observation via DOM diff | Uses `MutationObserver` Web API to detect DOM changes after actions. Provides "linguistic feedback" describing what happened (e.g., "a popup appeared"). This is structural, not visual. |
| **SeeAct** | Finetuned cross-encoder for element selection | Primarily focuses on grounding accuracy pre-action; does not have a dedicated post-action verification loop. |
| **Browser Use** | Not documented | SOTA on WebVoyager (89.1%) but no published verification mechanism details. |

**Key insight**: The most successful agents (UFO, Agent-E) combine structured APIs with vision, never relying on vision alone for state verification. UFO explicitly uses AX APIs alongside vision detectors. Agent-E uses DOM mutation observers.

**Relevance**: Our architecture already has this multi-tier design (Tier 0: AX, Tier 1: actuator state, Tier 2: vision). The gap is that Tier 0 and Tier 1 are too narrow -- they only handle specific action types and fall through to vision too often.

<a name="13-type-and-check"></a>
### 1.3 The "Type-and-Check" Pattern

This is the dominant strategy across all frameworks and SOTA agents:

**Instead of verifying that a text field _gained focus_, verify that the _subsequent action succeeded_.**

| Framework | Pattern |
|-----------|---------|
| **Playwright** | `locator.fill(text)` then `expect(locator).toHaveValue(text)` -- verifies the _text was entered_, not that focus was acquired |
| **Selenium** | `element.sendKeys(text)` then `element.getAttribute("value") == text` -- checks the value, not focus |
| **XCTest** | `textField.typeText("hello")` -- XCTest auto-ensures focus via tap before typing; if `typeText` succeeds, focus was necessarily present |
| **UFO** | AppAgent types text then re-observes screen to see if the text appeared -- the observation loop catches if text was rejected |
| **Agent-E** | DOM diff after `type` action detects whether input value changed |

**Why this works better than focus verification**:
1. **Observable outcome** vs. **latent state**: A text field containing "hello" is visually obvious. A blinking cursor in an empty field is ambiguous.
2. **Composability**: If the goal is "search for X", verifying "search field contains X" is what matters, not "search field has focus".
3. **Resilience**: Even if the click-to-focus step has a subtle verification failure, the type-text step succeeding proves the click worked retroactively.

**The specific fix pattern**: For `click` actions targeting text fields (where the next step is `type_text`), skip or relax the click's postcondition verification and instead gate success on the type_text step's outcome. If typing succeeds, the click necessarily worked.

<a name="14-root-cause"></a>
### 1.4 Root Cause Analysis in Our Codebase

Three contributing factors:

**Factor 1: `verify_condition.md` prompt is pathologically conservative**

```
Be conservative -- if you're not confident, say NO.
```

This instruction causes the VLM to default to "NO" for any ambiguous visual state. Focus indicators (cursor in empty field, subtle blue ring) are inherently ambiguous in screenshots. The prompt should be rewritten to separate "I see evidence against the condition" from "I can't tell."

**Factor 2: No action-type-aware verification shortcuts**

The verifier treats all `click` actions equally. A `click` on a search bar (whose purpose is to gain focus for subsequent typing) should be verified differently from a `click` on a "Submit Order" button. Currently, there's no way to say "this click is a focus-acquisition click, verify it via subsequent typing success."

**Factor 3: Reflection already exists but can't override denial**

`_reflect_failed_action()` asks the VLM "did this actually work?" and can flip `success=True` if the reflection says `"worked": "yes"`. However, this only runs when `visible_effect` is True (screenshot_diff detected change). If the diff module isn't active, or if the subtle focus change isn't detected by diff, reflection never fires. Even when it does fire, the reflection prompt includes `"refocus_text_field"` as a hint option -- showing that this exact scenario (click-to-focus failing verification) was anticipated but the fix was incomplete.

---

<a name="p0-2"></a>
## 2. P0 #2: Element Absence Detection

### Problem Statement

The orchestrator can only retry/replan when an expected UI element isn't found. It can never conclude "this element doesn't exist on this page" and inform the user or take an alternative path. This causes infinite retry loops or meaningless replans.

<a name="21-frameworks"></a>
### 2.1 How Frameworks Handle "Element Not Found"

Frameworks distinguish between three states:

| State | Meaning | Framework Examples |
|-------|---------|--------------------|
| **Not yet loaded** | Element exists but hasn't rendered yet | Selenium: implicit wait; Playwright: auto-waiting; Cypress: retry-ability |
| **Absent from page** | Element does not exist in the current DOM/AX tree | Selenium: `NoSuchElementException` after explicit wait timeout; Playwright: `locator.count() === 0` |
| **Not interactable** | Element exists but is hidden/disabled/covered | Selenium: `ElementNotInteractableException`; Playwright: `locator.isVisible()` returns false |

**Key framework behaviors**:

- **Playwright**: `waitFor({ state: 'detached' })` waits until element is removed from DOM. `expect(locator).toHaveCount(0)` asserts absence. The auto-waiting mechanism retries until timeout, then raises -- the timeout expiry _is_ the absence signal.
- **Selenium**: Explicit wait with `invisibilityOfElementLocated` or `numberOfElementsToBe(0)`. The distinction between "not yet loaded" and "absent" is the **timeout boundary**: after the wait expires, the element is treated as absent.
- **Cypress**: `cy.get(selector).should('not.exist')` automatically retries until timeout. The default timeout (4s for `cy.get`) is the dividing line between "still loading" and "absent".
- **XCTest**: `element.waitForExistence(timeout: 5)` returns `false` if the element doesn't appear within timeout. Developers treat `false` as "absent."
- **UiPath**: Dedicated `Element Exists` activity with `WaitForReady` option that explicitly returns a boolean rather than throwing.

**Key insight**: The universal pattern is **bounded wait with explicit timeout**. After the timeout, "not found" becomes "absent." The timeout value is the knob that separates "still loading" from "doesn't exist."

<a name="22-negative-assertions"></a>
### 2.2 Negative Assertion Patterns

| Pattern | Framework | Usage |
|---------|-----------|-------|
| `should('not.exist')` | Cypress | Assert element was removed from DOM |
| `toHaveCount(0)` | Playwright | Assert zero matching elements |
| `waitForElementToBeRemoved()` | React Testing Library | Async wait for removal with callback |
| `assertElementNotPresent` | Selenium IDE | Verify element is not in page |
| `expect().notExists().exec()` | AskUI | Visual assertion of absence |
| `queryAllByText().toHaveLength(0)` | Testing Library | Query-then-assert pattern (query* never throws) |

**Best practice from React Testing Library** (Kent C. Dodds):
> "Only use the `query*` variants for asserting that an element cannot be found."

The key distinction: `get*` throws on missing elements (for positive assertions), `query*` returns null (for negative assertions). This dual-API pattern avoids the anti-pattern of using try/catch for flow control.

**Relevance**: Our agent currently has only one path: `find_element()` returns `None` when element isn't found, and this triggers the same retry/replan logic as any other failure. There's no semantic distinction between "I looked and it's not there" and "something went wrong during my search."

<a name="23-llm-agents"></a>
### 2.3 How LLM Agents Reason About Dead Ends

**UFO (Microsoft)**:
- The HostAgent maintains a **finite-state machine (FSM)** with explicit terminal states
- If an AppAgent reports that a required UI element doesn't exist, the HostAgent can:
  1. Try a different application
  2. Report the impossibility to the user
  3. Suggest an alternative approach
- The dual-agent architecture naturally separates "I can't find it in this app" (AppAgent) from "maybe we need a different app entirely" (HostAgent)

**Agent-E**:
- Uses **hierarchical backtracking**: if the browser navigation agent can't find an element, the planner has the URL of each previous page and can backtrack
- The planner explicitly re-plans by asking the navigation agent for more information about the current state
- "Oblivious failures" (agent doesn't realize it failed) are documented as a known limitation

**WebVoyager**:
- No explicit dead-end handling
- The agent continues attempting actions until max steps or task completion
- Evaluation is post-hoc (GPT-4V judges the trajectory)

**General patterns from LLM agent failure research (2024-2025)**:
- Agents lack "explicit representations of situational boundaries and their own competence limits"
- Circular retry loops are a documented failure mode (one study found a 9-day, 60,000+ token loop)
- The recommended fix is **explicit termination conditions** and **escalation protocols**

**Relevance**: Our agent's `_handle_failure` has retry (up to `max_retries`), replan, and abort strategies. But there's no "element_absent" exit path that tells the planner "this UI element genuinely doesn't exist on this page, plan around it." The replan just tries again with the same assumption that the element should be there.

<a name="24-graceful-degradation"></a>
### 2.4 Graceful Degradation Strategies

| Strategy | Description | Used By |
|----------|-------------|---------|
| **Bounded retry with escalation** | N retries, then escalate to different strategy (not retry again) | Agent-E, our agent (partially) |
| **Alternative affordance suggestion** | When target is absent, look for a different UI element that achieves the same goal | Our agent (`_suggest_alternative_for_missing_target`), skill error recovery hints |
| **Backtracking** | Return to a known-good state and try a different path | Agent-E (URL history), UFO (FSM) |
| **User escalation** | Report the impossibility and ask for human guidance | UFO (sensitive action confirmation), UiPath (element exists boolean) |
| **Structural absence confirmation** | Use AX tree / DOM to confirm element truly doesn't exist (not just vision failure) | Playwright, Selenium, XCTest -- all use structural queries |
| **Negative observation** | Explicitly tell the planner what ISN'T on the page, not just what is | Novel approach -- not widely implemented |

**The most promising strategy for our agent**: Combine structural absence confirmation (AX tree query) with explicit planner communication. When `find_element()` returns `None`:
1. Confirm with AX tree: is there _any_ element matching the description?
2. If AX confirms absence, report to planner as `element_absent` (distinct from `element_not_found`)
3. Planner receives this signal and can choose: suggest alternative, backtrack, or abort with user message

---

<a name="recommendations"></a>
## 3. Recommendations

### VERDICT: PROCEED

Both P0 bugs have clear, well-established fixes supported by extensive prior art.

### P0 #1 Fix: Multi-Signal Focus Verification + Type-and-Check

**Priority fixes (ranked by impact)**:

1. **Add Tier 0 focus check for `click` actions targeting text fields** (HIGH IMPACT, LOW EFFORT)
   - When a `click` action targets a text field (detectable from step.params or element description containing "search", "input", "field", "text box"), query `kAXFocusedUIElementAttribute` via the existing accessibility bridge
   - If AX reports the focused element's role is a text field, return `(True, "Accessibility confirms text field focused")`
   - This short-circuits before vision is even attempted

2. **Implement "type-and-check" verification bypass** (HIGH IMPACT, MEDIUM EFFORT)
   - When a `click` step fails verification AND the next step in the plan is `type_text`, mark the click as "provisionally passed" and proceed to typing
   - If the type_text step succeeds, retroactively confirm the click
   - If the type_text step fails, THEN report the original click failure
   - This mirrors exactly how Playwright, XCTest, and UFO handle the focus question

3. **Rewrite `verify_condition.md` prompt** (MEDIUM IMPACT, LOW EFFORT)
   - Change from binary YES/NO to ternary YES/NO/UNCLEAR
   - Treat UNCLEAR as "inconclusive, try other verification" rather than failure
   - Add focus-specific guidance: "Signs of text field focus include: blinking cursor, blue highlight ring, autocomplete dropdown appearing, text field appearing selected or active. If any of these are visible, the field is likely focused."

4. **Relax reflection gating** (MEDIUM IMPACT, LOW EFFORT)
   - Currently `_reflect_failed_action` only runs when `visible_effect` is True
   - For click actions on text fields, run reflection regardless of screenshot diff
   - The reflection prompt already includes the `refocus_text_field` hint

### P0 #2 Fix: Three-Phase Absence Model + Planner Communication

**Priority fixes (ranked by impact)**:

1. **Add AX-confirmed absence signal** (HIGH IMPACT, MEDIUM EFFORT)
   - When `find_element()` returns `None`, follow up with AX tree query: "is there ANY element matching this description?"
   - If AX tree confirms absence, annotate the failure as `element_absent` (not just `element_not_found`)
   - Propagate this distinction through `StepResult.error` field

2. **Add `element_absent` to replan context** (HIGH IMPACT, MEDIUM EFFORT)
   - When replanning, include the list of confirmed-absent elements in the replan prompt
   - Example: "The following elements were confirmed absent from the current page: 'Return this item' button, 'Start a return' link. Plan around their absence."
   - This prevents the replanner from generating the same steps that just failed

3. **Implement bounded absence timeout** (MEDIUM IMPACT, LOW EFFORT)
   - After N consecutive `element_not_found` results for the same target across retries, promote to `element_absent`
   - Default N=2 (one initial attempt + one retry = confirmed absent)
   - This catches cases where AX tree is unavailable

4. **Add `abort_with_reason` action** (MEDIUM IMPACT, MEDIUM EFFORT)
   - Allow the planner to emit `abort_with_reason` when it determines the task is impossible given the current UI state
   - Example: `{"action": "abort_with_reason", "params": {"reason": "The 'Return this item' button is not available for this order"}}`
   - Return this as a user-facing message in the `ExecutionResult`

5. **Enrich alternative affordance pipeline** (LOW IMPACT, ALREADY PARTIALLY IMPLEMENTED)
   - `_suggest_alternative_for_missing_target` already exists but only runs for click actions
   - Extend to trigger for any action that fails with `element_absent`
   - Feed alternatives back into the replan as "possible next steps"

### Implementation Order

```
Phase 1 (P0 #1, immediate):
  1.1  Tier 0 focus check for click actions
  1.2  Type-and-check verification bypass
  1.3  verify_condition.md prompt rewrite

Phase 2 (P0 #2, immediate):
  2.1  AX-confirmed absence signal
  2.2  element_absent in replan context
  2.3  Bounded absence timeout

Phase 3 (Hardening):
  3.1  Ternary verification (YES/NO/UNCLEAR)
  3.2  Relaxed reflection gating
  3.3  abort_with_reason action
  3.4  Extended alternative affordance pipeline
```

### Risk Assessment

- **P0 #1 fixes are safe**: Adding a Tier 0 shortcut is additive (doesn't remove existing checks). Type-and-check is a well-tested pattern.
- **P0 #2 requires careful threshold tuning**: Setting the absence timeout too low (N=1) causes false "absent" signals on slow-loading UIs. N=2 is conservative.
- **Prompt changes need regression testing**: The `verify_condition.md` rewrite could affect other verification scenarios. Run the full unit test suite after changes.
