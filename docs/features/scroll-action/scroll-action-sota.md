# SOTA Research: Scroll Actions & Dependent Step Failure Handling

## Problem 1: Scroll Actions in Desktop Automation Agents

### Problem Landscape

Desktop automation agents must scroll to interact with content beyond the visible viewport. This is a deceptively complex action because:

1. **No universal API**: Unlike click (which targets a coordinate), scroll requires direction, magnitude, and sometimes a target element or coordinate.
2. **Verification is hard**: After scrolling, the agent must confirm the scroll succeeded without a stable anchor point (content has moved).
3. **Platform fragmentation**: macOS (CGEvent/Hammerspoon), Windows (SendInput/UIA), Linux (xdotool/XInput) all have different scroll primitives.
4. **Continuous vs. discrete**: Some UIs expect smooth/pixel scrolling; others expect discrete "line" or "page" increments.

### SOTA Approaches (Ranked by Relevance)

#### 1. Anthropic Computer Use (computer_20250124) -- MOST RELEVANT

**Action spec:**
```json
{
  "action": "scroll",
  "coordinate": [x, y],
  "scroll_direction": "up" | "down" | "left" | "right",
  "scroll_amount": 3
}
```

- Direction: 4 cardinal directions (up/down/left/right)
- Amount: Integer units (scroll "clicks", not pixels)
- Coordinate: Optional [x, y] for scroll target location
- Implementation: xdotool on Linux in their reference demo
- Status: Still flagged as "early-stage capability" with known limitations

**Trade-offs:** Clean parameterization. Scroll amount is abstract ("clicks") not pixels, which is more portable but less precise. No smooth scrolling. No element-targeted scroll.

#### 2. OpenAI CUA (Computer-Using Agent)

**Action spec:**
```json
{
  "type": "scroll",
  "x": 512,
  "y": 384,
  "scroll_x": 0,
  "scroll_y": -3
}
```

- Uses pixel coordinates for scroll position
- scroll_x / scroll_y for bidirectional scrolling (negative = down/right)
- Coordinate is where the cursor is positioned before scrolling

**Trade-offs:** More expressive (supports diagonal scroll via combined scroll_x + scroll_y). But pixel-based amounts are platform-dependent. Coordinate is mandatory (scroll always targets a point).

#### 3. OpenCUA (SOTA on OSWorld, 37.3%)

**Action spec:** scroll is a coordinate action alongside click, rightClick, doubleClick, moveTo, dragTo. Unified across 3 OS platforms and 200+ applications.

**Trade-offs:** Best benchmark performance. Unified cross-platform spec. Large training dataset (AgentNet). But requires their foundation model (OpenCUA-7B/32B/72B).

#### 4. Microsoft UFO (Windows-focused)

- Uses pywinauto `wheel_scroll()` on Windows
- Hybrid approach: tries native UIA scroll first, falls back to GUI scroll
- 10 control types include ScrollBar as a recognized element
- Sandboxed virtual desktop for safety

**Trade-offs:** Deep OS integration via UIA APIs gives reliable scrolling on Windows. Not applicable to macOS. The "try API first, fall back to GUI" pattern is excellent and transferable.

#### 5. OSWorld Benchmark

- `pyautogui.scroll(clicks)` with hardcoded amounts (e.g., scroll up 200 units)
- Atomic action: scroll is one of {mouse, keyboard, scroll, drag, CLI}
- PyAutoGUI-based, cross-platform

**Trade-offs:** Simple and proven. But hardcoded amounts don't adapt to different scroll sensitivities or page layouts.

#### 6. WebArena / BrowserGym

- Discrete actions: `scroll_up`, `scroll_down` (fixed amounts)
- Also: `scrollintoview <selector>` for element-targeted scrolling
- Accessibility tree provides element IDs for precise targeting

**Trade-offs:** `scrollintoview` is the gold standard for web -- but only works in browsers. Desktop agents lack equivalent APIs (though accessibility frameworks can sometimes provide element positions).

#### 7. Scrapybara Unified Action Space

- Composable scroll action with direction and amount
- Translation layer between model-specific formats (Anthropic vs OpenAI)
- Supports multiple models via Act SDK

**Trade-offs:** Good abstraction layer. But adds complexity. The translation approach is useful for multi-model support.

#### 8. Hammerspoon (macOS native)

```lua
hs.eventtap.scrollWheel({horizontal, vertical}, modifiers, "line" | "pixel")
```

- Native macOS scroll events via CGEvent
- Supports both "line" (discrete) and "pixel" (smooth) units
- Horizontal and vertical in a single call
- Modifier keys (cmd, alt, shift, ctrl, fn) supported

**Trade-offs:** Most native approach for macOS. Direct access to CGEvent scroll. Supports both scroll modes. Already in our actuator fallback chain.

### Scroll Verification Approaches

| Approach | Latency | Reliability | Notes |
|----------|---------|-------------|-------|
| **Screenshot diff** | 2-5s | Medium | Compare before/after screenshots pixel-by-pixel. Fails on static headers/footers. |
| **Accessibility tree diff** | ~100ms | High (when available) | Compare AX tree elements before/after. Detect new elements appearing or scroll offset changes. |
| **Content hash** | ~50ms | High | Hash visible text content before/after. If hash changed, scroll likely succeeded. |
| **Postcondition check** | 2-5s | High | Vision LLM verifies "can you see element X now?" after scroll. Expensive but reliable. |
| **State machine** (Agent-SAMA) | ~100ms | High | FSM tracks app state. If state transition matches expected scroll result, verified. |

**Recommended:** Tiered approach matching our existing 3-tier verification:
1. **Tier 1**: Accessibility tree diff (fast, check if new elements appeared)
2. **Tier 2**: Screenshot diff with content hash (medium, detect visual change)
3. **Tier 3**: Vision LLM postcondition check (slow but definitive)

### Common Failure Modes

1. **Scroll does nothing**: Element not scrollable, wrong coordinate, or focus not on scrollable area
2. **Over-scroll**: Scrolled past the target content; agent cannot find what it expected
3. **Bounce-back**: Elastic scrolling on macOS returns to original position
4. **Infinite scroll**: Page loads more content, changing the layout
5. **Modal/overlay blocking**: Scroll intercepted by a popup or overlay
6. **Wrong element scrolled**: Multiple scrollable areas on screen; wrong one received the event

---

## Problem 2: Dependent Step Failure Handling

### Problem Landscape

When step N in a multi-step plan fails, step N+1 may depend on N's success (e.g., "click menu item" depends on "open menu"). Executing N+1 anyway wastes time, may cause errors, and can leave the system in an unrecoverable state.

**The core challenge:** Dependencies between steps are usually implicit. The planner generates a linear list of steps, not a dependency graph. Detecting that step 3 depends on step 2's output requires either:
- Explicit dependency annotation (DAG)
- LLM-based inference at runtime
- Heuristic rules based on action types

### SOTA Approaches (Ranked by Relevance)

#### 1. Agent-SAMA: State-Aware Preconditions (AAAI 2026) -- MOST RELEVANT

**Mechanism:** Constructs per-app Finite State Machine (FSM) during execution. Each state has preconditions and postconditions. Before executing step N+1, checks if the current app state matches the expected precondition.

**Key innovation:** Mentor Agent analyzes execution data (action history, errors, transitions) at task end and extracts:
- Action sequences annotated with preconditions
- Guidance cues (natural language tips) for future tasks

**Results:** +12% success rate, +6.5% action accuracy, +7% satisfaction score over baseline.

**Applicability to us:** HIGH. We already have postconditions (mandatory `verify` field on every ActionStep). Adding preconditions is a natural extension. Our 3-tier verifier can check preconditions before execution.

#### 2. ActionEngine: State Machine Memory (Feb 2026) -- HIGHLY RELEVANT

**Mechanism:** Two-agent architecture:
- **Crawling Agent**: Builds state-machine graph offline (nodes = page states, edges = actions)
- **Execution Agent**: Plans from memory, synthesizes executable programs

**Failure recovery:** Execution failures trigger vision-based re-grounding that:
1. Repairs the failed action
2. Updates the state-machine memory for future runs

**Applicability to us:** MEDIUM-HIGH. The crawling agent concept maps to our skill learning system. The re-grounding on failure maps to our replan mechanism. The memory update maps to skill distillation.

#### 3. Voyager: Iterative Prompting with Self-Verification -- RELEVANT

**Mechanism:** On failure:
1. Include error message + environment state in prompt
2. GPT-4 acts as critic: "Did the program achieve the task?"
3. If no: critic provides specific suggestions
4. Agent retries with critic feedback

**Key pattern:** The critic is separate from the executor. This prevents the agent from being blind to its own failures.

**Applicability to us:** MEDIUM. Our StepVerifier already acts as a critic. But we could improve by passing richer failure context to the replanner (not just "step failed" but "step failed because the menu was not open, which means step 2's click did not register").

#### 4. DAG-Plan: Wave-Based Execution with Typed Failures -- RELEVANT

**Mechanism:**
- Steps organized into waves based on dependencies
- Wave N+1 never starts until wave N completes
- Three failure types: `transient` (retry), `needs_replan` (regenerate plan), `escalate` (abort to user)
- Interface contracts between steps (producer-consumer agreements)

**Applicability to us:** MEDIUM. Full DAG is overkill for our linear plans. But typed failures and the "never proceed if predecessor failed" rule are directly applicable.

#### 5. LangGraph: Graph-Native Error Recovery -- RELEVANT

**Mechanism:**
- Retry policies per node: max_attempts, wait_between, backoff_strategy
- Errors surfaced as typed objects in state graph
- Error handling nodes route failures conditionally
- Checkpoints after each step for rewind

**Applicability to us:** LOW-MEDIUM. We're not using LangGraph. But the patterns are transferable: typed errors, conditional routing on failure, checkpoints.

#### 6. SWE-Agent: MCTS Backtracking -- LESS RELEVANT

**Mechanism:** Monte Carlo Tree Search for solution exploration. Backtracking to previous states when a path fails. Experience bank for reusing past solutions.

**Applicability to us:** LOW. MCTS is for code generation, not GUI automation. The search space is different. But the "backtrack on failure" concept is universal.

#### 7. BabyAGI: Dynamic Task Reprioritization -- LESS RELEVANT

**Mechanism:** Three-agent loop: executor, task creator, task prioritizer. Prioritizer considers dependencies. New tasks created based on results of previous tasks.

**Applicability to us:** LOW. BabyAGI's open-ended task generation doesn't map well to our structured action plans. But the idea of re-prioritizing remaining steps after a failure is useful.

### Failure Detection Approaches

| Approach | Detection Speed | Accuracy | Notes |
|----------|----------------|----------|-------|
| **Postcondition check** | Immediate | High | Check verify condition right after step. We already do this. |
| **Precondition check** | Before next step | High | Check if app state is valid for next step. Agent-SAMA pattern. |
| **Stepwise confidence** | During step | Medium | Agent verbalizes confidence (0-1) per step. Low confidence triggers verification. |
| **Error detection at distance** | Delayed (N+k steps) | Low | Don't realize step N failed until step N+3. Very bad -- must avoid. |
| **Failure attribution** | Post-hoc | Low (14.2%) | ICML 2025: only 14.2% accuracy pinpointing failure step. Research-grade only. |

### Common Failure Modes

1. **Silent failure**: Step "succeeds" but didn't actually do what was intended (e.g., clicked wrong button)
2. **Cascading execution**: Steps N+1, N+2, N+3 all execute and fail because N failed
3. **State corruption**: Failed step left app in unexpected state; subsequent steps make it worse
4. **Infinite retry**: Agent retries the same failed step without changing approach
5. **Replan divergence**: Replanner generates plan that conflicts with partially-executed state

---

## Recommended Approach for Our Context

### Problem 1: Scroll Action

**RECOMMENDATION: PROCEED with Anthropic-style parameterization + Hammerspoon execution**

1. **Action parameters** (matching Anthropic + OpenAI conventions):
   ```python
   @dataclass
   class ActionStep:
       action: str = "scroll"
       direction: str  # "up" | "down" | "left" | "right"
       amount: int = 3  # scroll clicks (not pixels)
       x: Optional[int] = None  # target coordinate
       y: Optional[int] = None  # target coordinate
   ```

2. **Execution via Hammerspoon** (preferred) or PyAutoGUI (fallback):
   - Hammerspoon: `hs.eventtap.scrollWheel({0, -amount}, {}, "line")` for down
   - PyAutoGUI: `pyautogui.scroll(-amount, x=x, y=y)` for fallback

3. **Verification** (3-tier, matching existing pattern):
   - Tier 1: Accessibility tree diff (check for new elements)
   - Tier 2: Screenshot content hash diff (did visible content change?)
   - Tier 3: Vision LLM postcondition ("Is [target] now visible?")

4. **Add to Actuator protocol**: scroll(direction, amount, x, y) method

### Problem 2: Dependent Step Failure

**RECOMMENDATION: PROCEED with precondition-gated execution + abort-on-failure in replan loop**

1. **Immediate fix** (addresses the HIGH SEVERITY bug found in codebase research):
   - In `_replan_and_continue()` at `agent.py:1958-1966`: break on ANY step failure in replan loop, not just `abort`. Currently, `retry_different` and `skip` fall through to execute dependent steps.

2. **Precondition checking** (Agent-SAMA pattern):
   - Before executing step N+1, verify that step N's postcondition holds
   - If the previous step's `verify` condition is not met, skip step N+1 and trigger replan
   - This catches both explicit failures (step returned error) and silent failures (step "succeeded" but didn't achieve goal)

3. **Typed failure responses** (DAG-Plan pattern):
   - `transient`: Retry the same step (network timeout, element not yet loaded)
   - `needs_replan`: Abort remaining steps, replan from current state
   - `abort`: Stop entirely, return failure to user

4. **Richer failure context to replanner** (Voyager pattern):
   - Include not just "step N failed" but WHY it failed
   - Include what the screen looks like now
   - Include which subsequent steps were skipped and why

### Implementation Priority

| Priority | Change | Complexity | Impact |
|----------|--------|------------|--------|
| P0 | Fix replan loop break-on-failure bug | Low | High (fixes cascading execution) |
| P1 | Add scroll to Actuator protocol | Low | Medium (enables scroll actions) |
| P1 | Implement Hammerspoon scroll execution | Low | Medium (native scroll) |
| P2 | Add precondition checking before steps | Medium | High (catches silent failures) |
| P2 | Screenshot diff for scroll verification | Medium | Medium (scroll verification) |
| P3 | Typed failure responses | Medium | Medium (better error routing) |
| P3 | Richer failure context to replanner | Low | Medium (better replans) |
