# Manual Test Plan: Amazon Return Flow + Scroll Action + Replan Fix

## Overview

This plan validates three changes end-to-end using the "Return the latest Tylenol order on Amazon" flow -- the real user task that originally exposed the replan bug.

Tested features:
1. **Scroll action** -- the agent can scroll to find off-screen elements
2. **Replan fix** -- when a replan step fails, dependent steps stop (no cascading)
3. **Main loop fix** -- same pattern in `execute()`, returns failure instead of falling through
4. **Inter-step verification** -- every action is verified before the next step proceeds

---

## CRITICAL: Inter-Step Verification (applies to ALL tests)

The agent uses 3-tier verification (Hammerspoon state query, then vision screenshot, then fallthrough). Every test must confirm that verification is not just "action succeeded at the actuator level" but "the expected world state actually changed."

### What to check in every test run

**1. Plan quality -- meaningful verify fields**

In `events.jsonl`, find the `PLAN_COMPLETE` event and examine each step's `verify` field:

| Step action | BAD verify (actuator-level) | GOOD verify (world-state) |
|---|---|---|
| `open_url` (Amazon orders) | "URL opened" | "Amazon orders page visible" or "Order history loaded" |
| `click` (search bar) | "Click performed" | "Search bar is focused" or "Cursor in search field" |
| `type_text` ("Tylenol") | "Text typed" | "Search results for Tylenol visible" |
| `scroll` (down) | "Scrolled 3 clicks" | "Tylenol order card visible" or "More orders visible" |
| `click` (Return button) | "Button clicked" | "Return options page visible" or "Return reason selector shown" |
| `click` (reason dropdown) | "Dropdown clicked" | "Return method or next step visible" |

If ANY step has a trivial/actuator-level verify like "text was typed" or "click performed", report as: **BUG: Weak verification at step N -- verify field does not confirm world-state change.**

**2. Verification actually executes and gates the next step**

In `events.jsonl`, for each step look for this sequence:
```
ACTION_COMPLETE  (step N action ran)
VERIFY_*         (step N verification ran -- should be VERIFY_PASS or VERIFY_FAIL)
STEP_START       (step N+1 begins ONLY after verify passed)
```

Check:
- [ ] Every `ACTION_COMPLETE` is followed by a `VERIFY_*` event before the next `STEP_START`
- [ ] No `STEP_START` for step N+1 appears without a preceding `VERIFY_PASS` for step N
- [ ] If `VERIFY_FAIL` occurs, the next event is `STEP_RETRY` or `STEP_REPLAN`, NOT `STEP_START` for the next step

**3. Verification catches real failures, not just actuator success**

Specific scenarios to watch for:
- Agent types "Tylenol" + Enter but the page doesn't load results (slow network) -- verify should FAIL and trigger retry, not proceed to scroll
- Agent clicks "Return or Replace Items" but a modal/popup blocks the page -- verify should detect the return page did NOT load
- Agent scrolls down but the page was already at the bottom (no new content) -- verify should detect the target element is still not visible

**4. Grep commands for verification audit**

```bash
RUN_ID=$(ls -t logs/runs/ | head -1)

# Show all verify events in order
grep -E "VERIFY_PASS|VERIFY_FAIL" "logs/runs/$RUN_ID/events.jsonl"

# Show action-then-verify pairs (should always alternate)
grep -E "ACTION_COMPLETE|VERIFY_|STEP_START|STEP_RETRY|STEP_REPLAN" "logs/runs/$RUN_ID/events.jsonl"

# Find any step where ACTION_COMPLETE is followed directly by STEP_START (missing verify = BUG)
# Manual inspection: look for two consecutive STEP_START lines with no VERIFY in between
```

---

## Prerequisites

| Requirement | How to verify |
|---|---|
| macOS Accessibility permission for terminal | System Settings > Privacy & Security > Accessibility -- terminal app listed |
| macOS Screen Recording permission for terminal | System Settings > Privacy & Security > Screen Recording -- terminal app listed |
| `ANTHROPIC_API_KEY` exported | `echo $ANTHROPIC_API_KEY` shows `sk-ant-...` |
| Agent installed | `pip install -e ".[dev,anthropic]"` succeeded |
| Amazon logged in | Open https://www.amazon.com/gp/your-account/order-history in Safari manually -- orders visible without login prompt |
| A Tylenol order exists in history | Visible in order history (any status) |
| No other automation running | Close any Hammerspoon scripts, keyboard maestro, or other automation tools |

---

## Test 1: Happy Path -- Full Return Flow

**Goal**: Confirm the agent can navigate Amazon, find a Tylenol order, and initiate a return.

### Setup
1. Open Terminal
2. `cd /Users/jagatp/workspace/macos-automation-agent`
3. Close all browser windows (clean slate)

### Command
```bash
.venv/bin/automation-agent --provider anthropic --status-ui overlay "Return the latest Tylenol order on Amazon"
```

> **HEADS UP: Step away from your keyboard and mouse now. The agent will take control of your screen for 1-3 minutes.**

### What to observe

| Step | Expected behavior | What to watch for | Inter-step verification checkpoint |
|---|---|---|---|
| Skill match | Overlay shows "Matched skill: return-amazon-order" | Check that skill params include `item: Tylenol` | N/A |
| Navigate to orders | Safari opens to Amazon order history URL | Agent uses `open_url`, not manual navigation | **VERIFY**: "Amazon orders page visible" -- agent must confirm the page loaded before searching |
| Login gate | If login page appears, agent should pause (`wait_for_user`) | If it does NOT pause and tries to interact with login form -- **BUG** | **VERIFY**: "Orders page loaded" or "Login page visible" -- agent must identify which state it is in |
| Click search bar | Agent clicks the search/filter bar | Watch for correct element targeting | **VERIFY**: "Search bar is focused" -- agent must confirm focus before typing |
| Type "Tylenol" + Enter | Agent types and submits search | Watch for keystroke accuracy | **VERIFY**: "Search results for Tylenol visible" -- agent must confirm results loaded, NOT just "text was typed" |
| Scroll to find order | If Tylenol order is not in viewport, agent scrolls down | **KEY TEST**: verify scroll action fires | **VERIFY**: "Tylenol order card visible" -- agent must confirm the target appeared after scrolling |
| Enter return flow | Agent clicks "Return or Replace Items" or equivalent | May need to scroll within order card | **VERIFY**: "Return options page visible" -- agent must confirm page transition before selecting reason |
| Select reason | Agent picks a return reason from dropdown | Watch for correct dropdown interaction | **VERIFY**: "Return method visible" or "Next return step visible" |
| Confirmation | Return label or drop-off instructions visible | Agent should end with `done` action | **VERIFY**: "Return confirmation visible" |

### Pass criteria
- Agent reaches return confirmation or drop-off instructions
- No cascading failures (agent doesn't execute steps after a failure)
- Overlay shows clear step progression
- **Every step's verify field describes world-state, not actuator success** (see Inter-Step Verification section above)
- **Every ACTION_COMPLETE is followed by VERIFY_PASS before the next STEP_START** in `events.jsonl`

### Log verification
After the run, find the latest run directory:
```bash
ls -lt logs/runs/ | head -3
```

Check `trace.md`:
```bash
cat logs/runs/<run_id>/trace.md
```

Verify:
- [ ] Each step has a `verify` field (mandatory postconditions)
- [ ] If scroll was used, it appears as `action: scroll` with `direction` and `amount` params
- [ ] No step executed after a failed step without retry/replan in between
- [ ] Final status is `success: true`

Check `events.jsonl`:
```bash
cat logs/runs/<run_id>/events.jsonl | grep -i scroll
cat logs/runs/<run_id>/events.jsonl | grep -i replan
```

---

## Test 2: Scroll Required -- Order Below the Fold

**Goal**: Force the agent to scroll to find the Tylenol order.

### Setup
1. Open Safari to https://www.amazon.com/gp/your-account/order-history
2. Ensure the Tylenol order is NOT visible in the initial viewport (scroll down to verify it exists, then scroll back to top)
3. If Tylenol appears immediately, place several other orders first or use the filter to show a time range where Tylenol is further down

### Command
```bash
.venv/bin/automation-agent --provider anthropic --status-ui overlay --verbose "Return the latest Tylenol order on Amazon"
```

> **HEADS UP: Step away from your keyboard and mouse now.**

### What to observe
- The agent MUST scroll at some point to locate the Tylenol order
- Watch the overlay for `scroll` action(s)
- The agent should NOT give up if the element is not found in the first viewport

### Inter-step verification checkpoints for scroll
- After each scroll action, the agent must verify whether the target element is now visible
- The verify field after a scroll should be something like "Tylenol order card visible" or "More order results visible" -- NOT "scrolled 3 clicks"
- If the scroll verify FAILS (target still not visible), the agent should scroll again or replan -- NOT skip to the next step
- After a successful scroll-then-verify, the NEXT step (e.g., click on the Tylenol order) should only start after VERIFY_PASS

### Pass criteria
- [ ] At least one `scroll` action appears in `events.jsonl`
- [ ] Agent successfully finds and interacts with the Tylenol order after scrolling
- [ ] `trace.md` shows the scroll step with verification
- [ ] Scroll step's `verify` field describes world-state ("element now visible"), not actuator output ("scrolled N clicks")
- [ ] `VERIFY_PASS` event appears after the scroll's `ACTION_COMPLETE` and before the next `STEP_START`

### Failure indicators
- Agent reports "Element not found" for Tylenol order without attempting to scroll -- **SCROLL NOT WORKING**
- Agent scrolls but in the wrong direction (up instead of down) -- **DIRECTION BUG**
- Agent scrolls endlessly without stopping -- **NO EXIT CONDITION**
- Scroll step has trivial verify like "Page scrolled" with no world-state check -- **WEAK VERIFICATION**
- Agent proceeds to click after scroll without verifying the target is visible -- **MISSING INTER-STEP VERIFY**

---

## Test 3: Replan Fix -- Trigger a Step Failure

**Goal**: Verify that when a step fails during replan, the agent stops executing dependent steps.

### How to trigger
This test requires causing a step to fail mid-execution. Options:

**Option A: Network interruption**
1. Start the agent normally
2. After it navigates to Amazon and begins the return flow, temporarily disable Wi-Fi
3. The next click/navigation should fail
4. Re-enable Wi-Fi

**Option B: Page change**
1. While the agent is executing, manually navigate the browser to a different page (e.g., google.com)
2. This will cause the next verify step to fail (expected element not found)
3. The agent should replan

**Option C: Nonexistent item**
```bash
.venv/bin/automation-agent --provider anthropic --status-ui overlay "Return the latest Purple Unicorn Blanket order on Amazon"
```
This should fail at the "find order" step since no such order exists.

> **HEADS UP: Step away from your keyboard and mouse now.**

### What to observe

| Behavior | Expected | Bug if... |
|---|---|---|
| Step fails | Agent logs failure, enters retry loop | Agent silently moves to next step |
| Retries exhaust | Agent escalates to replan | Agent continues without replanning |
| Replan step fails | Agent STOPS executing remaining steps | Agent continues to next replan step (THE BUG WE FIXED) |
| Final result | `success: false` with clear error message | `success: true` despite failure, or no result at all |

### Inter-step verification as the failure trigger
The reason steps fail in this test is because VERIFICATION catches the problem. Specifically:
- The agent executes an action (e.g., "click Return button")
- The 3-tier verifier takes a screenshot and checks if the expected state occurred
- Verification FAILS because the expected element/page is not present
- This triggers retry, then replan

If verification is weak/trivial, the failure might not be detected at all, and the agent would wrongly proceed. So this test also validates that verification is strict enough to catch real problems.

Check: In `events.jsonl`, the `VERIFY_FAIL` event should contain evidence like "Return options page not visible" -- not just "verification timed out."

### Log verification
```bash
# Look for replan events
cat logs/runs/<run_id>/events.jsonl | grep -E "STEP_REPLAN|REPLAN_START|REPLAN_COMPLETE"

# Verify no steps executed after the replan failure
cat logs/runs/<run_id>/events.jsonl | grep "STEP_" | tail -10
```

Key checks:
- [ ] `REPLAN_START` event exists (replan was triggered)
- [ ] After `"Replan step N exhausted retries; stopping replan execution"` or `"Replan step N failed after recovery; stopping"`, NO further `STEP_START` events appear
- [ ] `ExecutionResult.success` is `false`
- [ ] The `message` says "Failed after replan" (not "Completed after replan")

---

## Test 4: Main Loop Fix -- Failure Returns Immediately

**Goal**: Verify the main `execute()` loop returns failure instead of falling through when recovery fails.

### Command
Use an impossible task to trigger rapid failure:
```bash
.venv/bin/automation-agent --provider anthropic --status-ui overlay "Click the invisible button on the Amazon orders page"
```

> **HEADS UP: Step away from your keyboard and mouse now.**

### What to observe
- Agent plans a click action for a nonexistent element
- Click fails (element not found)
- Retries with different strategies (visible_alternative_affordance, etc.)
- Retries exhaust -> replan triggered
- Replan also fails -> agent returns `ExecutionResult(success=False)`

### Pass criteria
- [ ] Agent does NOT execute steps beyond the failed one (no cascading)
- [ ] `ExecutionResult.success` is `false`
- [ ] Total execution time is reasonable (under 2 minutes, not hanging)

---

## Test 5: Edge Case -- Not Logged Into Amazon

**Goal**: Verify the agent handles the login gate correctly.

### Setup
1. Open Safari
2. Go to https://www.amazon.com and sign out
3. Clear cookies if needed

### Command
```bash
.venv/bin/automation-agent --provider anthropic --status-ui overlay "Return the latest Tylenol order on Amazon"
```

> **HEADS UP: Step away from your keyboard and mouse now.**

### Expected behavior
- Agent navigates to order history URL
- Amazon redirects to login page
- Agent should recognize this and either:
  - Use `wait_for_user` action with a message like "Please sign in"
  - Attempt to type credentials (unlikely/undesirable -- should NOT have credentials)
- Per the skill template step 2: "If login page is visible, wait for user to sign in"

### Pass criteria
- [ ] Agent pauses with `wait_for_user` on the login page
- [ ] Overlay shows a message asking user to log in
- [ ] After user logs in, agent continues (if `wait_for_user` is properly implemented)

### Failure indicators
- Agent tries to interact with login form fields -- **SHOULD NOT ATTEMPT LOGIN**
- Agent reports success without ever reaching orders -- **FALSE POSITIVE**
- Agent hangs indefinitely without a user-visible message -- **MISSING wait_for_user**

---

## Test 6: Edge Case -- CAPTCHA or Bot Detection

**Goal**: Verify graceful handling when Amazon shows CAPTCHA.

This is hard to trigger deterministically. If you encounter it during any test:

### What to observe
- Agent should recognize it cannot proceed (visual verification fails)
- Agent should either `wait_for_user` or fail gracefully
- Agent should NOT spin trying to click through the CAPTCHA

### Failure indicators
- Agent enters infinite retry loop on CAPTCHA page
- Agent clicks randomly on the CAPTCHA image
- Agent reports success while stuck on CAPTCHA

---

## Log Inspection Checklist (run after every test)

For every test run, check these in the run log directory:

```bash
RUN_ID=$(ls -t logs/runs/ | head -1)
echo "Inspecting run: $RUN_ID"
```

### trace.md
```bash
cat "logs/runs/$RUN_ID/trace.md"
```
- [ ] Steps are numbered sequentially
- [ ] Each step shows action, params, verify, and result
- [ ] Failed steps show retry attempts
- [ ] Replan section appears if replanning occurred

### events.jsonl
```bash
cat "logs/runs/$RUN_ID/events.jsonl" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || cat "logs/runs/$RUN_ID/events.jsonl"
```
- [ ] `TASK_START` event has the correct goal
- [ ] `SKILL_MATCH` event shows `return-amazon-order`
- [ ] `PLAN_COMPLETE` event has reasonable step count (5-10 steps)
- [ ] No `ACTION_ERROR` events for scroll actions (scroll works cleanly)
- [ ] `TASK_COMPLETE` or `TASK_FAIL` event at the end

### Inter-step verification audit
```bash
# Show the action -> verify -> next-step sequence
grep -E "ACTION_COMPLETE|VERIFY_PASS|VERIFY_FAIL|STEP_START|STEP_RETRY|STEP_REPLAN" "logs/runs/$RUN_ID/events.jsonl"
```
- [ ] Every `ACTION_COMPLETE` is followed by a `VERIFY_PASS` or `VERIFY_FAIL` (no missing verifications)
- [ ] No `STEP_START` for step N+1 appears without `VERIFY_PASS` for step N first
- [ ] `VERIFY_FAIL` is always followed by `STEP_RETRY` or `STEP_REPLAN`, never by next `STEP_START`
- [ ] Verify evidence strings describe world-state (e.g., "Search results visible", "Return page loaded"), not actuator output (e.g., "text typed", "click performed")
- [ ] If any step has a trivial/weak verify field, report as a bug with the step index and the verify text

### Screenshots
```bash
ls "logs/runs/$RUN_ID/screenshots/" 2>/dev/null
ls "logs/runs/$RUN_ID/debug/" 2>/dev/null
```
- [ ] Screenshots captured at verification points
- [ ] Debug images show `find_element` crops (if element finding was used)

---

## Summary Matrix

| Test | What it validates | Expected result | Inter-step verification check |
|---|---|---|---|
| Test 1: Happy path | Full flow works end-to-end | Return confirmation reached | Every step has world-state verify; ACTION->VERIFY->STEP sequence holds |
| Test 2: Scroll required | Scroll action finds off-screen elements | Scroll events in log, order found | Scroll verify confirms target visible, not just "scrolled N clicks" |
| Test 3: Replan fix | Failed replan steps stop execution | No cascading, `success: false` | VERIFY_FAIL triggers retry/replan, not next step |
| Test 4: Main loop fix | Failed recovery returns failure | `success: false`, no hanging | VERIFY_FAIL on impossible element stops execution |
| Test 5: Not logged in | Login gate handled | `wait_for_user` on login page | Verify detects "login page" vs "orders page" |
| Test 6: CAPTCHA | Bot detection handled | Graceful failure or wait | Verify detects CAPTCHA page, does not report success |

## Reporting Bugs

For any failure, capture:
1. The exact command run
2. The `run_id` (from logs/runs/)
3. The full `trace.md`
4. The full `events.jsonl`
5. Screenshots from the debug directory
6. A description of what happened vs. what was expected

Do NOT attempt to fix bugs -- report them with steps to reproduce.
