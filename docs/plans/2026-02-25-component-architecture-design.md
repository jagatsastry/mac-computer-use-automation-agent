# macOS Automation Agent — Component Architecture Design

**Date:** 2026-02-25
**Author:** Jagat Pudipeddi
**Status:** Implemented
**Built with:** Claude Code (Opus 4.6)

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Component A: Planner](#3-component-a-planner)
4. [Component B: Skill Registry](#4-component-b-skill-registry)
5. [Component C: Vision Coordinator](#5-component-c-vision-coordinator)
6. [Component D: Actuator](#6-component-d-actuator)
7. [Component E: Orchestrator](#7-component-e-orchestrator)
8. [Cross-Cutting: Logging & Instrumentation](#8-cross-cutting-logging--instrumentation)
9. [Multi-Step Execution Model](#9-multi-step-execution-model)
10. [Testing & Verification Strategy](#10-testing--verification-strategy)
11. [Project Structure](#11-project-structure)
12. [Implementation Notes](#12-implementation-notes)
13. [Implementation Plan](#13-implementation-plan)

---

## 1. Overview

### What We're Building

A modular macOS desktop automation agent that:
- Accepts natural language prompts (e.g., "return my blue headphones on Amazon")
- Decomposes them into multi-step action plans
- Executes each step by finding UI elements on screen and clicking/typing
- Verifies every step actually worked before proceeding
- Logs everything for debugging and auditability

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| LLM for planning | Anthropic Claude API | Best reasoning for multi-step decomposition |
| Vision model | Molmo (OpenRouter or local) | Best coordinate grounding for UI elements |
| Actuator | 3 backends: HammerspoonBridge, Hammerspoon CLI, AppleScript | `create_actuator()` factory auto-selects best available backend |
| Skill format | Markdown files (LLM-interpreted) | Human-readable, versionable, no code needed to add skills |
| Verification | Hammerspoon state + vision screenshot diff | Two-tier: fast state check, then visual confirmation |
| Architecture | 5 independent components + shared logging | Each independently runnable and testable |

### What Changed From the Prototype

The prototype (current codebase) had:
- Tangled dependencies between orchestrator, actions, and perception
- Hardcoded prompts inside Python files
- Multiple actuator backends (AppleScript, Hammerspoon, PyAutoGUI) with no clear primary
- Vision coordinate handling via heuristic guessing
- No closed-loop verification
- No skill/recipe system

The redesign fixes all of these. Additionally:
- Multiple actuator backends are now unified behind a single `Actuator` protocol with automatic backend selection, rather than eliminated to one.
- Legacy `actions/` package (AppleScript, Hammerspoon, PyAutoGUI action classes) has been completely removed.

---

## 2. Architecture

### System Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                         USER PROMPT                                  │
│        "Return my blue headphones order from Amazon"                 │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    (E) ORCHESTRATOR                                   │
│                                                                       │
│  ┌─────────────┐     ┌──────────────┐     ┌────────────────┐        │
│  │ Match skill? │────▶│ Expand skill │────▶│ Plan with      │        │
│  │ (B) Registry │ yes │ + fill params│     │ skill context  │        │
│  └──────┬──────┘     └──────────────┘     │ (A) Planner    │        │
│         │ no                               └───────┬────────┘        │
│         └──────────────────────────────────────────▶│                 │
│                                                     │                 │
│                              ┌───────────────────── ▼ ──────┐        │
│                              │    STEP-BY-STEP LOOP         │        │
│                              │                               │        │
│                              │  ┌─────────┐                 │        │
│                              │  │ Step N   │                 │        │
│                              │  └────┬─────┘                 │        │
│                              │       │                       │        │
│                              │       ▼                       │        │
│                              │  Need coords?                 │        │
│                              │  ┌────┴────┐                  │        │
│                              │  │yes      │no                │        │
│                              │  ▼         ▼                  │        │
│                              │ (C)Vision  (D)Actuator        │        │
│                              │ find_elem  execute            │        │
│                              │  │         │                  │        │
│                              │  └────┬────┘                  │        │
│                              │       ▼                       │        │
│                              │  VERIFY (two-tier)            │        │
│                              │  ┌─────────────────┐         │        │
│                              │  │Tier 1: HS state │         │        │
│                              │  │  (fast, ~50ms)  │         │        │
│                              │  └───────┬─────────┘         │        │
│                              │     confirmed? ──yes──▶ next  │        │
│                              │          │no/ambiguous        │        │
│                              │          ▼                    │        │
│                              │  ┌──────────────────┐        │        │
│                              │  │Tier 2: Vision    │        │        │
│                              │  │  screenshot      │        │        │
│                              │  │  (slow, ~2-5s)   │        │        │
│                              │  └───────┬──────────┘        │        │
│                              │     verified? ──yes──▶ next   │        │
│                              │          │no                  │        │
│                              │          ▼                    │        │
│                              │  on_fail: retry_different/    │        │
│                              │           replan/abort        │        │
│                              │                               │        │
│                              └───────────────────────────────┘        │
│                                                                       │
│  ALL EVENTS ──▶ EventLogger (events.jsonl + trace.md + screenshots)  │
└──────────────────────────────────────────────────────────────────────┘
```

### Component Dependency Graph

```
                  ┌─────────────┐
                  │ EventLogger │  (injected into every component)
                  └──────┬──────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
   ┌───────────┐  ┌────────────┐  ┌───────────┐
   │(A)Planner │  │(C)Vision   │  │(D)Actuator│
   │           │  │Coordinator │  │           │
   │ Claude API│  │ Molmo API  │  │3 backends │
   └─────┬─────┘  └─────┬──────┘  └─────┬─────┘
         │               │               │
         │         ┌─────┴──────┐        │
         │         │(B)Skill    │        │
         │         │Registry    │        │
         │         │(filesystem)│        │
         │         └─────┬──────┘        │
         │               │               │
         └───────────────┼───────────────┘
                         │
                  ┌──────▼──────┐
                  │(E)Orchestr- │
                  │ator/Agent   │
                  │             │
                  │ Wires A-D   │
                  │ together    │
                  └─────────────┘

  Arrows mean "depends on" / "calls into"
  A, B, C, D have ZERO dependencies on each other
  E depends on all of A, B, C, D
  EventLogger depends on nothing
```

### Data Flow: Prompt to Completion

```
User: "Return my blue headphones from Amazon"
  │
  │  ┌─────────────────────────────────────────────────┐
  ├─▶│ (B) Skill Registry                              │
  │  │   match("Return my blue headphones from Amazon") │
  │  │   → Skill: return_amazon_order                   │
  │  │   → Params: {item: "blue headphones"}            │
  │  │   → Expanded markdown with {{item}} filled in    │
  │  └─────────────────────┬───────────────────────────┘
  │                        │
  │  ┌─────────────────────▼───────────────────────────┐
  ├─▶│ (A) Planner                                      │
  │  │   Input: prompt + expanded skill markdown         │
  │  │   Output: ActionPlan with 7 static steps          │
  │  │   Step 1: open_url(amazon.com/orders)             │
  │  │   Step 2: observe (check for login)               │
  │  │   Step 3: click(element="search bar")             │
  │  │   Step 4: type("blue headphones") + key(enter)    │
  │  │   Step 5: click(element="matching order")         │
  │  │   Step 6: click(element="Return button")          │
  │  │   Step 7: observe (check for confirmation)        │
  │  └─────────────────────┬───────────────────────────┘
  │                        │
  │  ┌─────────────────────▼───────────────────────────┐
  └─▶│ (E) Orchestrator — execute step by step          │
     │                                                   │
     │  Step 1: open_url ─────────────▶ (D) Actuator    │
     │    verify: HS state → Safari frontmost ✓          │
     │                                                   │
     │  Step 2: observe ──────────────▶ (C) Vision      │
     │    screen shows login page                        │
     │    → wait_for_user (pause 23s)                    │
     │    → observe again → orders page loaded ✓         │
     │                                                   │
     │  Step 3: click search bar                         │
     │    (C) find_element("search bar") → (450, 79)     │
     │    (D) click(450, 79)                              │
     │    verify: Vision → "search bar is focused" ✓     │
     │                                                   │
     │  Step 4: type + enter                              │
     │    (D) type("blue headphones")                     │
     │    (D) key(enter)                                  │
     │    verify: Vision → "search results visible" ✓    │
     │                                                   │
     │  Step 5: click matching order                      │
     │    (C) find_element("blue headphones order")       │
     │    (D) click(x, y)                                 │
     │    verify: Vision → "order detail page" ✓         │
     │                                                   │
     │  Step 6: click Return button                       │
     │    (C) find_element("Return or Replace Items")     │
     │    (D) click(x, y)                                 │
     │    verify: Vision → "return flow started" ✓       │
     │                                                   │
     │  Step 7: observe → confirmation page               │
     │    (C) verify("return confirmation visible") ✓    │
     │                                                   │
     │  → ExecutionResult(success=True, 7 steps, 47s)    │
     └───────────────────────────────────────────────────┘
```

---

## 3. Component A: Planner

### Purpose

Convert a natural language prompt (optionally with skill context) into a structured `ActionPlan`, and provide mid-execution replanning when the screen shows something unexpected.

### Interface

```python
class ActionPlanner(Protocol):
    async def plan(self, prompt: str, skill_context: Optional[str] = None) -> ActionPlan:
        """Initial planning: prompt → structured action steps."""
        ...

    async def replan(self, goal: str, history: List[StepResult],
                     screen_description: str) -> ActionStep:
        """Mid-execution replanning: what happened + what's on screen → next step."""
        ...
```

### Internal Structure

```
planner/
├── __init__.py
├── __main__.py             # CLI: python -m automation_agent.planner "..."
├── self_test.py            # Self-test with real Claude API
├── planner.py              # ActionPlanner implementation
├── models.py               # ActionStep, ActionPlan, StepResult
└── prompts/
    ├── plan_from_prompt.md # System prompt for initial planning
    └── replan_from_state.md# System prompt for mid-execution replanning
```

### Data Models

```python
@dataclass
class ActionStep:
    action: str              # "click", "type_text", "press_key", "open_url",
                             # "activate_app", "observe", "wait_for_user", "done"
    params: Dict[str, Any]   # action-specific parameters
    verify: str              # natural language verification condition (MANDATORY
                             # except for done, wait_for_user, observe actions)
    on_fail: str             # "retry_different" | "replan" | "abort" | "wait_for_user"
                             # ("retry_different" emphasizes strategy-changing retries)
    max_retries: int         # default 3

    # _ACTION_ALIASES dict provides auto-correction for common LLM misspellings
    # e.g., "typetext" → "type_text", "presskey" → "press_key"

@dataclass
class ActionPlan:
    goal: str                # the user's original goal
    steps: List[ActionStep]  # ordered list of steps
    success_condition: str   # natural language: what does "done" look like?
    current_index: int       # which step we're on (mutable during execution)

    def validate(self) -> None:
        """Rejects plans where non-terminal steps have empty verify fields.
        Exempts done, wait_for_user, and observe from the verify requirement."""

@dataclass
class StepResult:
    step: ActionStep
    success: bool
    verification_method: str  # "hammerspoon_state" | "vision" | "both" | "actuator_only"
    evidence: str             # what we observed that confirmed/denied success
    error: Optional[str]
    duration_ms: int
    screenshot_path: Optional[str]
    retry_count: int = 0
    retry_strategies_used: List[str] = field(default_factory=list)
```

### CLI

```bash
# Plan a prompt
python -m automation_agent.planner "Return my blue headphones from Amazon"

# Plan with skill context
python -m automation_agent.planner "Return my blue headphones" \
  --skill-context skills/library/return_amazon_order.md

# Replan given current state
python -m automation_agent.planner replan \
  --goal "Return blue headphones on Amazon" \
  --screen "Amazon login page with email field" \
  --history '[{"action":"open_url","success":true}]'

# Dry run: show prompt without calling LLM
python -m automation_agent.planner "Open Safari" --dry-run
```

### Prompt Design

The planning prompt lives in `prompts/plan_from_prompt.md` and instructs Claude to output JSON:

```
You are a macOS desktop automation planner. Given a user's goal,
produce a step-by-step action plan as JSON.

Available actions:
- open_url: {url: string}
- activate_app: {app_name: string}
- click: {element: string} — will be resolved to coordinates at runtime
- type_text: {text: string}
- press_key: {keys: ["cmd", "c"]}
- observe: {} — take screenshot and describe screen
- wait_for_user: {message: string} — pause for human intervention
- done: {} — goal achieved

Each step MUST include:
- verify: what should be true on screen after this step succeeds (MANDATORY except for done, wait_for_user, observe)
- on_fail: what to do if verification fails ("retry_different", "replan", "abort")

Output format:
{
  "goal": "...",
  "success_condition": "...",
  "steps": [...]
}
```

### Implementation Details

- Uses `structlog` for token/duration logging in both `plan()` and `replan()`
- `replan()` measures `planning_duration_ms` and logs it
- Prompt file contradiction about verify exemptions has been fixed (done/wait_for_user/observe are explicitly exempted)
- 429 rate-limit retry handling for Claude API calls

---

## 4. Component B: Skill Registry

### Purpose

Load markdown skill templates from disk, match user prompts to skills, extract parameters, and expand templates. Gives the Planner a head start instead of reasoning from scratch.

### Interface

```python
@dataclass
class Skill:
    name: str                  # filename without .md
    trigger: str               # natural language trigger description
    parameters: Dict[str, str] # param_name → description
    steps_markdown: str        # raw steps section (with {{params}})
    success_condition: str
    notes: str

@dataclass
class ExpandedSkill:
    skill: Skill
    filled_steps: str          # steps with {{params}} replaced
    user_params: Dict[str, str]

class SkillRegistry:
    def load_all(self) -> None: ...
    def match(self, prompt: str) -> Optional[Tuple[Skill, Dict[str, str]]]: ...
    def expand(self, skill: Skill, params: Dict[str, str]) -> ExpandedSkill: ...
    def list_skills(self) -> List[Skill]: ...
    def validate_all(self) -> List[str]: ...  # returns list of errors
```

### Skill File Format

```markdown
# Return Amazon Order

## Trigger
Return an item, order, or package on Amazon

## Parameters
- **item**: What to return (e.g., "blue headphones", "order #112-456")

## Steps
1. Open Safari and navigate to https://www.amazon.com/gp/your-account/order-history
2. If login page appears, wait for user to sign in
3. Find the search/filter bar on the orders page and click it
4. Type "{{item}}" and press Enter
5. Find the order matching "{{item}}" in the search results and click on it
6. Find the "Return or Replace Items" button and click it
7. Follow the return flow until confirmation page appears

## Success Condition
The screen shows a return confirmation with a return label or drop-off instructions.

## Notes
- Amazon's UI changes frequently. If a step fails, observe the screen and adapt.
- Some items may not be eligible for return.
```

### Matching Flow

```
User prompt: "I need to return those blue Sony headphones from Amazon"
                │
                ▼
        ┌───────────────┐
        │ Keyword Match  │  (fast, no LLM)
        │                │
        │ Scan triggers: │
        │ "return" +     │
        │ "Amazon" found │
        │ in trigger of  │
        │ return_amazon   │
        │ _order.md      │
        └───────┬────────┘
                │ match found
                ▼
        ┌───────────────┐
        │ Extract Params │  (regex + heuristics)
        │                │
        │ item = "blue   │
        │ Sony headphones│
        │ " (text after  │
        │ "return" before│
        │ "from Amazon") │
        └───────┬────────┘
                │
                ▼
        ┌───────────────┐
        │ Expand Template│
        │                │
        │ {{item}} →     │
        │ "blue Sony     │
        │ headphones"    │
        └───────┬────────┘
                │
                ▼
        ExpandedSkill ready → passed to Planner as context
```

### CLI

```bash
python -m automation_agent.skills list              # List all skills
python -m automation_agent.skills match "return Amazon headphones"  # Match
python -m automation_agent.skills expand return_amazon_order --item "blue headphones"
python -m automation_agent.skills validate           # Check all .md files parse correctly
```

### Implementation Details

- Default OS is `""` (empty string = all platforms), not `"darwin"`
- Param extraction preserves original case (uses `re.IGNORECASE` for matching)
- `expand()` uses single-pass `re.sub` with callback to prevent template injection
- Strips unexpanded placeholders with warning
- Warns on duplicate skill names during loading

---

## 5. Component C: Vision Coordinator

### Purpose

Screenshot capture, UI element location (via Molmo vision model), screen description, and condition verification. This is the agent's "eyes."

### Interface

```python
@dataclass
class ElementLocation:
    x: int                    # pixel coordinate
    y: int                    # pixel coordinate
    confidence: str           # "high" | "medium" | "low"
    description: str          # what the model found
    raw_response: str         # raw model output (for debugging)

@dataclass
class ScreenState:
    description: str          # natural language description of screen
    frontmost_app: str        # from Hammerspoon get_state()
    window_title: str         # from Hammerspoon get_state()
    screenshot_path: Path     # saved JPEG for audit trail
    timestamp: datetime

class ScreenCoordinator(Protocol):
    async def capture(self) -> str:
        """Take screenshot, return base64-encoded JPEG."""

    async def find_element(self, description: str) -> Optional[ElementLocation]:
        """Find UI element, return pixel coordinates."""

    async def describe_screen(self) -> ScreenState:
        """Full screen description using vision + Hammerspoon state."""

    async def verify_condition(self, condition: str) -> bool:
        """Ask vision model: does screen show X? Returns True/False."""
```

### Coordinate Pipeline

This is the most failure-prone part of the system. The pipeline is explicit, with no heuristic guessing:

```
                    Screenshot (Retina 2x)
                    3024 × 1964 raw pixels
                    Logical: 1512 × 982
                            │
                            ▼
                ┌───────────────────────┐
                │ Compress to JPEG      │
                │ Base64 encode         │
                │ (target < 4.5MB)      │
                └───────────┬───────────┘
                            │
                            ▼
                ┌───────────────────────┐
                │ Vision Model (Molmo)  │
                │                       │
                │ Prompt: "Point to the │
                │ Submit button"        │
                │                       │
                │ Response: varies by   │
                │ model (see below)     │
                └───────────┬───────────┘
                            │
                            ▼
                ┌───────────────────────────────────────┐
                │ Parse Raw Response                     │
                │                                        │
                │ Molmo:  "point(0.45, 0.12)"           │
                │         → (0.45, 0.12) normalized 0-1 │
                │                                        │
                │ Qwen:   "<box>(450,120,500,140)</box>" │
                │         → center (475, 130) in 0-1000 │
                │                                        │
                │ Claude: "The button is at (680, 118)"  │
                │         → (680, 118) in pixels         │
                └───────────┬───────────────────────────┘
                            │
                            ▼
                ┌───────────────────────────────────────┐
                │ Convert to Logical Pixel Coordinates   │
                │                                        │
                │ DECLARED coordinate spaces (no guessing)│
                │                                        │
                │ COORDINATE_SPACES = {                  │
                │   "molmo": "normalized_0_1",           │
                │   "qwen3-vl": "normalized_0_1000",     │
                │   "claude-*": "pixel",                 │
                │ }                                      │
                │                                        │
                │ Molmo example:                         │
                │   (0.45, 0.12) × (1512, 982)          │
                │   = pixel (680, 118)                   │
                │                                        │
                │ Unknown model → REFUSE (raise error)   │
                └───────────┬───────────────────────────┘
                            │
                            ▼
                ┌───────────────────────────────────────┐
                │ ElementLocation(                       │
                │   x=680, y=118,                        │
                │   confidence="high",                   │
                │   description="Submit button",         │
                │   raw_response="point(0.45, 0.12)"    │
                │ )                                      │
                └───────────────────────────────────────┘
```

### CLI

```bash
python -m automation_agent.vision describe           # Describe current screen
python -m automation_agent.vision find "Submit button"  # Find element → coords
python -m automation_agent.vision verify "Calculator shows 4"  # Check condition
python -m automation_agent.vision capture -o shot.jpg   # Save screenshot
```

### Implementation Details

- `_get_active_model()` method selects coordinate space per provider at call time
- Coordinates are clamped: `min(int(x * width), width - 1)`
- `_validate_model()` raises `ValueError` for unknown models (no heuristic guessing)
- `describe_screen()` accepts optional `hammerspoon_state` dict for merging
- `_enforce_size_limit()` with quality steps 85 -> 70 -> 50 -> resize for screenshots
- Image mode conversion handles LA, CMYK, I modes (not just RGB/RGBA)

---

## 6. Component D: Actuator

### Purpose

Execute physical actions on macOS (click, type, key press, app control) via one of three backends: HammerspoonBridgeActuator (HTTP bridge), HammerspoonActuator (hs CLI), or AppleScriptActuator (osascript fallback). The `create_actuator()` factory auto-selects the best available backend. Also provides fast state queries for verification.

### Interface

```python
@dataclass
class ActuatorResult:
    success: bool
    output: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"success": self.success, "output": self.output, "error": self.error}

class Actuator(Protocol):
    def click(self, x: int, y: int) -> Dict[str, Any]: ...
    def type_text(self, text: str) -> Dict[str, Any]: ...
    def press_key(self, keys: List[str]) -> Dict[str, Any]: ...
    def activate_app(self, app_name: str) -> Dict[str, Any]: ...
    def open_url(self, url: str) -> Dict[str, Any]: ...
    def quit_app(self, app_name: str) -> Dict[str, Any]: ...
    def get_state(self) -> Dict[str, Any]: ...
    def is_available(self) -> bool: ...
```

Note: All methods are **synchronous** (not async) and return `Dict[str, Any]` rather than `ActuatorResult`.

### 3-Backend Architecture

```
actuator/
├── __init__.py              # create_actuator() factory, selects best backend
├── actuator.py              # HammerspoonActuator (hs CLI + Lua templates)
├── bridge_actuator.py       # HammerspoonBridgeActuator (HTTP bridge + osascript fallback)
├── applescript_actuator.py  # AppleScriptActuator (pure osascript)
├── models.py                # ActuatorResult
├── lua_templates/            # Templates for hs CLI backend
│   ├── click.lua
│   ├── type_text.lua
│   ├── press_key.lua
│   ├── activate_app.lua
│   ├── open_url.lua
│   ├── quit_app.lua
│   └── get_state.lua
├── __main__.py
└── self_test.py
```

### Backend Selection Logic

```
create_actuator() priority:
1. HammerspoonBridgeActuator — if HTTP bridge at localhost:27741 is responding
2. HammerspoonActuator — if `hs` CLI is found in PATH
3. AppleScriptActuator — always available on macOS (uses /usr/bin/osascript)
```

### HammerspoonBridgeActuator Capabilities

The bridge actuator provides everything the hs CLI backend does, PLUS:
- **Vision-powered operations:** `describe_screen()`, `find_element()`, `check_condition()`, `execute_goal()`
- **Accessibility-aware fallback:** when Hammerspoon lacks accessibility permissions, automatically falls back to osascript for keyboard/mouse input
- **Accessibility permission caching** with 60-second TTL

### Lua Template System (hs CLI backend)

Instead of building Lua strings in Python (error-prone), each action has a `.lua` template:

```
actuator/
└── lua_templates/
    ├── click.lua           # hs.eventtap.leftClick(hs.geometry.point({{x}}, {{y}}))
    ├── type_text.lua       # hs.eventtap.keyStrokes({{text}})
    ├── press_key.lua       # key combo with modifier mapping
    ├── activate_app.lua    # hs.application.launchOrFocus({{app_name}})
    ├── open_url.lua        # hs.urlevent.openURL({{url}})
    ├── quit_app.lua        # app:kill()
    └── get_state.lua       # JSON: {app_name, window_title, window_frame}
```

### State Query for Fast Verification

```lua
-- lua_templates/get_state.lua
local app = hs.application.frontmostApplication()
local win = app and app:focusedWindow()
return hs.json.encode({
    app_name = app and app:name() or "",
    app_bundle = app and app:bundleID() or "",
    window_title = win and win:title() or "",
    window_frame = win and hs.json.encode(win:frame()) or ""
})
```

Returns individual flat fields (not nested `window_frame`):
```json
{
  "app_name": "Safari",
  "app_bundle": "com.apple.Safari",
  "window_title": "Amazon.com: Your Orders",
  "window_x": 0,
  "window_y": 25,
  "window_w": 1512,
  "window_h": 957
}
```

### CLI

```bash
python -m automation_agent.actuator status            # Check Hammerspoon
python -m automation_agent.actuator click 500 300     # Click at coords
python -m automation_agent.actuator type "hello"      # Type text
python -m automation_agent.actuator key cmd+c         # Key combo
python -m automation_agent.actuator activate Safari   # Bring app to front
python -m automation_agent.actuator open-url "https://example.com"
python -m automation_agent.actuator state             # Get screen state JSON
```

---

## 7. Component E: Orchestrator

### Purpose

Wire components A-D together. Run the plan-observe-act-verify loop. Handle replanning, retries, and error recovery.

### Interface

```python
class AutomationAgent:
    def __init__(
        self,
        planner: ActionPlanner,
        skills: SkillRegistry,
        coordinator: ScreenCoordinator,
        actuator: Actuator,
        event_logger: EventLogger,
        max_iterations: int = 30,
    ): ...

    async def execute(self, prompt: str) -> ExecutionResult: ...
```

### Agent Loop State Machine

```
                        ┌──────────┐
                        │  START   │
                        └────┬─────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  MATCH SKILL    │
                    │  (B) Registry   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  PLAN           │
                    │  (A) Planner    │
                    │  with/without   │
                    │  skill context  │
                    └────────┬────────┘
                             │
               ┌─────────────▼─────────────┐
               │                            │
               │     EXECUTION LOOP         │
               │                            │
               │  ┌──────────────────┐      │
               │  │ Get next step    │◀──┐  │
               │  └───────┬──────────┘   │  │
               │          │              │  │
               │          ▼              │  │
               │   ┌──────────────┐     │  │
               │   │step == "done"│─yes─┼──┼──▶ SUCCESS
               │   └──────┬───────┘     │  │
               │          │no           │  │
               │          ▼              │  │
               │   ┌──────────────┐     │  │
               │   │step ==       │     │  │
               │   │"wait_for_    │─yes─┼──┼──▶ PAUSE ──▶ resume ──┐
               │   │ user"        │     │  │                        │
               │   └──────┬───────┘     │  │                        │
               │          │no           │  │◀───────────────────────┘
               │          ▼              │  │
               │   ┌──────────────┐     │  │
               │   │step ==       │     │  │
               │   │"observe"     │─yes─┼──┼──▶ (C) describe_screen()
               │   └──────┬───────┘     │  │   then REPLAN with new info
               │          │no           │  │
               │          ▼              │  │
               │   ┌──────────────┐     │  │
               │   │Need coords?  │     │  │
               │   │(element in   │     │  │
               │   │ params)      │     │  │
               │   └──┬───────┬───┘     │  │
               │    yes│     no│         │  │
               │      ▼       │         │  │
               │   (C) find   │         │  │
               │   _element() │         │  │
               │      │       │         │  │
               │   found?     │         │  │
               │   ┌──┴──┐    │         │  │
               │  no│   yes│   │         │  │
               │   ▼     ▼   │         │  │
               │  REPLAN  merge│         │  │
               │          coords         │  │
               │          │   │         │  │
               │          └───▼─────┐   │  │
               │          (D) Actuator   │  │
               │          execute()      │  │
               │              │          │  │
               │              ▼          │  │
               │         VERIFY          │  │
               │     ┌────────────┐      │  │
               │     │Tier 1: HS  │      │  │
               │     │get_state() │      │  │
               │     └─────┬──────┘      │  │
               │      confirmed?         │  │
               │      ┌──┴──┐            │  │
               │    yes│  no/ambig       │  │
               │      │    ▼             │  │
               │      │ ┌──────────┐     │  │
               │      │ │Tier 2:   │     │  │
               │      │ │Vision    │     │  │
               │      │ │verify()  │     │  │
               │      │ └────┬─────┘     │  │
               │      │  verified?       │  │
               │      │  ┌──┴──┐         │  │
               │      │yes│  no│         │  │
               │      │   │    ▼         │  │
               │      │   │ on_fail?     │  │
               │      │   │ ┌──┬──┬──┐   │  │
               │      │   │ │  │  │  │   │  │
               │      │   │ retry replan │  │
               │      │   │ │  │     abort   │
               │      │   │ │  │     │   │  │
               │      │   │ │  │     ▼   │  │
               │      │   │ │  │  FAILURE│  │
               │      │   │ └──┘         │  │
               │      └───┼──────────────┘  │
               │          │ next step       │
               │          └─────────────────┘
               │                            │
               │  iteration >= max?          │
               │  ──yes──▶ FAILURE           │
               └────────────────────────────┘
```

### ExecutionResult

```python
@dataclass
class ExecutionResult:
    success: bool
    goal: str
    steps: List[StepResult]      # every step with evidence
    message: str                 # human-readable summary
    error: Optional[str]
    total_duration_ms: int
    iterations: int
    llm_calls: int               # how many LLM calls were made
    screenshots_saved: int       # how many screenshots captured
    run_id: str                  # links to logs/traces/run-{id}.md
```

### CLI

```bash
# Full execution
python -m automation_agent.orchestrator "Return my blue headphones from Amazon"

# Dry run: plan only
python -m automation_agent.orchestrator "Open Calculator" --dry-run

# Step-by-step: pause after each step
python -m automation_agent.orchestrator "Search Google for weather" --step

# Replay a previous run's plan
python -m automation_agent.orchestrator replay logs/traces/run-r-abc123.md
```

### Implementation Details

- Actuator failure short-circuits verification (does not verify a failed action)
- Proper retry loop respecting `max_retries`
- `plan.validate()` called before execution
- `_vary_strategy()` produces meaningfully different strategies for all action types
- `wait_for_user` step handling in the replan loop
- Verifier returns error StepResult for non-terminal steps with empty verify

---

## 8. Cross-Cutting: Logging & Instrumentation

### Three Layers

```
┌────────────────────────────────────────────────────────┐
│ Layer 1: STRUCTURED EVENT LOG (machine-readable)        │
│                                                         │
│ File: logs/events.jsonl                                 │
│ Format: one JSON object per line                        │
│ Content: every action, observation, decision,           │
│          verification, error across all components      │
│ Purpose: post-hoc analysis, metrics, debugging          │
│                                                         │
│ Example line:                                           │
│ {"timestamp":"2026-02-25T10:00:02Z",                    │
│  "run_id":"r-abc123",                                   │
│  "component":"planner",                                 │
│  "event_type":"decision",                               │
│  "step_number":0,                                       │
│  "data":{"action":"plan","model":"claude-sonnet-4-20250514",│
│          "prompt_tokens":1200,"response_tokens":340,    │
│          "plan_steps":7},                               │
│  "duration_ms":1100}                                    │
├────────────────────────────────────────────────────────┤
│ Layer 2: EXECUTION TRACE (human-readable)               │
│                                                         │
│ File: logs/traces/run-{run_id}.md                       │
│ Format: Markdown with embedded screenshots              │
│ Content: narrative of what happened at each step,       │
│          including LLM prompts/responses, coordinate    │
│          conversions, verification results              │
│ Purpose: "read this to understand why the agent did X"  │
├────────────────────────────────────────────────────────┤
│ Layer 3: COMPONENT DEBUG LOG (developer-facing)         │
│                                                         │
│ File: logs/debug.log                                    │
│ Format: standard Python logging with rotation (10MB)    │
│ Content: low-level details (HTTP calls, Lua scripts,    │
│          raw subprocess output, coordinate math)        │
│ Purpose: debugging specific component failures          │
└────────────────────────────────────────────────────────┘
```

### EventLogger — Shared Across All Components

```python
class EventLogger:
    def __init__(self, run_id: str, log_dir: Path): ...

    def log_event(self, component: str, event_type: str,
                  data: Dict, duration_ms: int = 0): ...

    def log_trace(self, markdown: str): ...

    def save_screenshot(self, image_b64: str, label: str) -> Path: ...

    def step(self, n: int): ...

    def finalize(self, result: ExecutionResult): ...

    @classmethod
    def for_standalone(cls, component: str) -> "EventLogger":
        """Create a logger for standalone component CLI usage."""
        ...
```

Every component receives an `EventLogger` at construction and logs through it. The Orchestrator creates the logger and passes it down to all components.

### What Gets Logged (Per Component)

```
┌─────────────────────────────────────────────────────────────────┐
│  Component  │  Events Logged                                    │
├─────────────┼───────────────────────────────────────────────────┤
│  Planner    │  • LLM call: model, prompt tokens, response      │
│             │    tokens, duration, raw response                  │
│             │  • Plan created: number of steps, goal            │
│             │  • Replan triggered: reason, new step             │
├─────────────┼───────────────────────────────────────────────────┤
│  Skills     │  • Skill matched: name, params extracted          │
│             │  • No match found                                  │
│             │  • Template expanded: filled text                  │
├─────────────┼───────────────────────────────────────────────────┤
│  Vision     │  • Screenshot captured: path, size, quality       │
│             │  • find_element: query, raw response,             │
│             │    parsed coords, coordinate space, pixel result, │
│             │    confidence                                      │
│             │  • describe_screen: full description, app state   │
│             │  • verify_condition: condition, YES/NO, evidence  │
│             │  • LLM call: model, tokens, duration              │
├─────────────┼───────────────────────────────────────────────────┤
│  Actuator   │  • Action executed: type, params, Lua script,    │
│             │    exit code, stdout, stderr, duration             │
│             │  • State queried: full state dict                  │
│             │  • Error: Hammerspoon failure details              │
├─────────────┼───────────────────────────────────────────────────┤
│  Orchestr.  │  • Execution started: prompt, skill match         │
│             │  • Step started: number, action, params            │
│             │  • Verification result: method, pass/fail,        │
│             │    evidence                                        │
│             │  • Retry triggered: step, attempt number           │
│             │  • Replan triggered: reason, new step              │
│             │  • Wait for user: started, resumed (duration)      │
│             │  • Execution completed: success, total time,       │
│             │    steps executed, LLM calls                       │
└─────────────┴───────────────────────────────────────────────────┘
```

### Execution Trace Example

```markdown
# Execution Trace: r-abc123
**Prompt:** Return my blue headphones from Amazon
**Skill:** return_amazon_order (item="blue headphones")
**Started:** 2026-02-25T10:00:01Z
**Status:** SUCCESS (7 steps, 47.2s)
**LLM calls:** 6 (planner: 3, vision: 3)
**Screenshots:** 8

---

## Step 1: open_url
- **Action:** open_url("https://amazon.com/orders")
- **Lua:** `hs.urlevent.openURL("https://amazon.com/orders")`
- **Actuator:** success (85ms)
- **Verify tier 1:** HS state → app=Safari, title="Loading..." → inconclusive
- **Verify tier 2:** Vision → "Amazon page or login visible" → YES (2.3s)
- **Screenshot:** ![step1](screenshots/r-abc123-step1-verify.jpg)
- **Result:** PASS

## Step 2: observe
- **Screen:** Amazon login page. Email field visible.
- **Screenshot:** ![step2](screenshots/r-abc123-step2-observe.jpg)
- **Decision:** wait_for_user("Please sign in to Amazon")
- **Wait:** 23.1s (user signed in)
- **Post-wait observation:** Orders page loaded, search bar visible.
- **Result:** PASS (user completed login)

## Step 3: click(element="search bar")
- **Vision find_element:** "orders search bar"
  - Model: molmo
  - Raw: "point(0.35, 0.08)"
  - Space: normalized_0_1
  - Conversion: (0.35, 0.08) × (1512, 982) = pixel(529, 79)
  - Confidence: high
- **Action:** click(529, 79)
- **Lua:** `hs.eventtap.leftClick(hs.geometry.point(529, 79))`
- **Actuator:** success (42ms)
- **Verify tier 1:** HS state → app=Safari, title="Your Orders" → inconclusive
- **Verify tier 2:** Vision → "search bar is focused" → YES (1.9s)
- **Screenshot:** ![step3](screenshots/r-abc123-step3-verify.jpg)
- **Result:** PASS

...continues for all steps...
```

### Log Directory Layout

```
logs/
├── events.jsonl                     # Append-only structured event log
├── debug.log                        # Rotating debug log (10MB × 5)
├── traces/
│   ├── run-r-abc123.md              # One trace per execution
│   ├── run-r-def456.md
│   └── ...
└── screenshots/
    ├── r-abc123-step1-verify.jpg    # Named by run + step + purpose
    ├── r-abc123-step2-observe.jpg
    ├── r-abc123-step3-find.jpg
    └── ...
```

### Analysis Scripts

```bash
# Analyze a single run
python scripts/analyze_run.py r-abc123
# Output:
#   Run r-abc123: "Return my blue headphones from Amazon"
#   Status: SUCCESS (7 steps, 47.2s)
#   LLM calls: 6 (planner: 3, vision: 3) — 8,400 tokens
#   Verifications: 7 passed, 0 failed, 2 retried
#   Slowest step: Step 2 wait_for_user (23.1s)
#   Trace: logs/traces/run-r-abc123.md

# Find failure patterns across runs
python scripts/analyze_failures.py --last 20
# Output:
#   20 runs, 3 failures:
#   - r-xyz789: "Element not found: Return button" (step 6)
#   - r-uvw012: "Max iterations" (stuck in login loop)
#   - r-stu345: "Hammerspoon timeout" (hs hung 30s)
```

---

## 9. Multi-Step Execution Model

### Two Types of Steps

```
┌─────────────────────────────────────────────────────────┐
│  STATIC STEPS                                            │
│  Known upfront from skill template + planner             │
│                                                          │
│  Examples:                                               │
│  - open_url("https://amazon.com/orders")                │
│  - type_text("{{item}}")                                │
│  - press_key(["enter"])                                 │
│                                                          │
│  These come from the initial plan() call.                │
│  The planner converts skill markdown → JSON steps.       │
├─────────────────────────────────────────────────────────┤
│  DYNAMIC STEPS                                           │
│  Decided at runtime based on screen observation          │
│                                                          │
│  Examples:                                               │
│  - "I see a login page" → wait_for_user                 │
│  - "I see the Return button" → click(element=...)       │
│  - "I see a captcha" → wait_for_user("Solve captcha")   │
│  - "The page is still loading" → wait 2 seconds         │
│                                                          │
│  These come from replan() calls during execution.        │
│  The planner sees history + current screen → next step.  │
└─────────────────────────────────────────────────────────┘
```

### Replanning Decision Tree

```
Step N executed → verification failed
                      │
                      ▼
              ┌───────────────┐
              │ on_fail value │
              └───┬───┬───┬───┘
                  │   │   │
          ┌───────┘   │   └───────┐
          ▼           ▼           ▼
   "retry_         "replan"     "abort"
   different"         │           │
          │           ▼           ▼
          ▼         Capture     Return
    retries < max?  screen →    FAILURE
    ┌──┴──┐        call         with
   yes   no       planner.     evidence
    │     │       replan()
    ▼     ▼           │
  vary   escalate     ▼
  strat- to         New step
  egy    "replan"   from LLM
                        │
                        ▼
                    Execute
                    new step
```

### Login/Auth Handling

```
    ┌──────────────────────────┐
    │ Step executed or observed │
    └────────────┬─────────────┘
                 │
                 ▼
    ┌──────────────────────────┐
    │ Vision detects login     │
    │ page / auth wall /       │
    │ captcha / 2FA prompt     │
    └────────────┬─────────────┘
                 │
                 ▼
    ┌──────────────────────────┐
    │ Orchestrator creates     │
    │ wait_for_user step:      │
    │                          │
    │ "Please sign in to       │
    │  Amazon. Press Enter     │
    │  when done."             │
    └────────────┬─────────────┘
                 │
                 ▼
    ┌──────────────────────────┐
    │ Poll every 3 seconds:    │
    │                          │
    │ 1. get_state() → check   │
    │    if window title       │
    │    changed               │
    │                          │
    │ 2. If changed:           │
    │    verify_condition(     │
    │    "login page is gone") │
    │                          │
    │ 3. If verified → resume  │
    │    If not → keep waiting │
    │                          │
    │ 4. Timeout after 5min    │
    │    → abort               │
    └────────────┬─────────────┘
                 │
                 ▼
    ┌──────────────────────────┐
    │ Resume execution from    │
    │ next step in plan        │
    └──────────────────────────┘
```

---

## 10. Testing & Verification Strategy

### Testing Pyramid

```
                    ╱╲
                   ╱  ╲
                  ╱ G  ╲         Manual test scripts
                 ╱ 3-5  ╲        (human observes screen)
                ╱ scripts ╲       Run: python scripts/run_*.py
               ╱───────────╲
              ╱     F       ╲     Top-level E2E integration
             ╱   5-10 tests  ╲    (real LLM + real Hammerspoon + real screen)
            ╱  pytest -m e2e  ╲   Run: pytest -m e2e
           ╱───────────────────╲
          ╱   Per-component     ╲  Component integration (self_test.py)
         ╱   integration tests   ╲ (real deps for that component, mocked neighbors)
        ╱   5-10 per component    ╲ Run: python -m automation_agent.X.self_test
       ╱   pytest -m integration   ╲
      ╱─────────────────────────────╲
     ╱     Per-component unit tests  ╲  Fast, all mocked
    ╱     282 total (see breakdown)     ╲ Run: pytest -m unit
   ╱     pytest -m unit (< 5 seconds)  ╲
  ╱──────────────────────────────────────╲
```

### Test Categories and What They Prove

```
┌────────────────────────────────────────────────────────────────────┐
│ CATEGORY         │ WHAT IT PROVES                                  │
├──────────────────┼────────────────────────────────────────────────┤
│ Unit tests       │ Component logic is correct IN ISOLATION          │
│ (mocked deps)    │ Data models serialize/deserialize correctly      │
│                  │ Error handling works for known failure modes     │
│                  │ Edge cases handled (empty input, bad JSON, etc) │
│                  │                                                  │
│                  │ Does NOT prove: real API calls work,             │
│                  │ Hammerspoon actually clicks, vision model        │
│                  │ returns usable coordinates                       │
├──────────────────┼────────────────────────────────────────────────┤
│ Component        │ Component works with its REAL dependencies      │
│ integration      │ Planner: real Claude API returns valid plans     │
│ (self_test.py)   │ Vision: real screenshot + real Molmo returns    │
│                  │   coordinates that make sense                    │
│                  │ Actuator: real Hammerspoon clicks/types          │
│                  │ Skills: real .md files load and parse            │
│                  │                                                  │
│                  │ Does NOT prove: components work TOGETHER,        │
│                  │ end-to-end flows complete                        │
├──────────────────┼────────────────────────────────────────────────┤
│ Top-level E2E    │ Full system works end-to-end for real tasks     │
│ (pytest -m e2e)  │ Prompt → skill match → plan → execute → verify  │
│                  │ Multi-step flows complete                       │
│                  │ Verification catches failures                   │
│                  │ Logging produces complete audit trail            │
│                  │                                                  │
│                  │ Does NOT prove: works for YOUR specific task     │
│                  │ (infinite variety of websites/apps)              │
├──────────────────┼────────────────────────────────────────────────┤
│ Manual scripts   │ Works for specific real-world scenarios          │
│ (scripts/)       │ Human can see what's happening                  │
│                  │ Catches visual/UX issues tests can't            │
│                  │ Provides confidence for demo/production use      │
└──────────────────┴────────────────────────────────────────────────┘
```

### Closed-Loop Verification: How We Know It Worked

This is the most important section. The central problem: **how do we know the agent actually did what it claims?**

#### The Verification Pipeline

Every step goes through this pipeline before being marked as "success":

```
Step executed by Actuator
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ TIER 1: Hammerspoon State Query (~50ms)                             │
│                                                                      │
│ Query: actuator.get_state()                                         │
│ Returns: {app_name, window_title, window_frame}                     │
│                                                                      │
│ Checks:                                                              │
│ ┌───────────────────────────────────────────────────────────────┐   │
│ │ Step action      │ State check                    │ Result    │   │
│ ├───────────────────┼────────────────────────────────┼──────────┤   │
│ │ activate_app(X)  │ app_name == X                   │ pass/fail│   │
│ │ open_url(...)    │ app_name in known_browsers      │ pass/fail│   │
│ │ quit_app(X)      │ app_name != X                   │ pass/fail│   │
│ │ click(x,y)       │ (can't check with state alone)  │ ambiguous│   │
│ │ type_text(...)   │ (can't check with state alone)  │ ambiguous│   │
│ │ press_key(...)   │ (can't check with state alone)  │ ambiguous│   │
│ └───────────────────┴────────────────────────────────┴──────────┘   │
│                                                                      │
│ If result = "pass"     → StepResult(success=True, method="hs_state")│
│ If result = "fail"     → StepResult(success=False, method="hs_state")│
│ If result = "ambiguous"→ proceed to Tier 2                          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ ambiguous
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ TIER 2: Vision Verification (~2-5s)                                  │
│                                                                      │
│ 1. Take screenshot                                                   │
│ 2. Ask vision model: "Does the screen show: {step.verify}?"         │
│ 3. Parse YES/NO response                                             │
│                                                                      │
│ Example:                                                             │
│   step.verify = "search bar is focused with cursor blinking"         │
│   Vision response: "YES, the search bar at the top of the page      │
│                     has an active cursor in it"                       │
│   → StepResult(success=True, method="vision",                        │
│                evidence="search bar has active cursor")              │
│                                                                      │
│ Example (failure):                                                   │
│   step.verify = "order detail page is visible"                       │
│   Vision response: "NO, the screen still shows search results"       │
│   → StepResult(success=False, method="vision",                       │
│                evidence="screen shows search results, not detail")   │
│                                                                      │
│ The screenshot is ALWAYS saved regardless of pass/fail.              │
│ The evidence string is ALWAYS populated.                             │
└─────────────────────────────────────────────────────────────────────┘
```

#### Evidence Chain

Every `StepResult` carries evidence that can be audited after the fact:

```
ExecutionResult
 └── steps: List[StepResult]
      └── StepResult
           ├── step: ActionStep        # what was attempted
           ├── success: bool           # did it work
           ├── verification_method: str # HOW we verified
           │    "hammerspoon_state"    # Tier 1 check
           │    "vision"               # Tier 2 check
           │    "both"                 # Both tiers used
           │    "actuator_only"        # No verify condition on step
           ├── evidence: str           # WHAT we observed
           │    "app_name=Safari, title=Amazon.com: Orders"
           │    "search bar has active cursor"
           │    "screen shows search results, not order detail"
           ├── error: Optional[str]    # WHY it failed
           ├── screenshot_path: str    # WHERE to look
           └── duration_ms: int        # HOW LONG it took
```

#### What "Closed Loop" Means Concretely

```
WITHOUT closed loop (the prototype):
  agent.click(500, 300)
  # Did it click the right thing? Who knows.
  # Agent says "success" because subprocess returned 0.
  # But it may have clicked empty space, wrong button, or a popup.

WITH closed loop (this design):
  agent.click(500, 300)
  → HS state: {app=Safari, title="Your Orders"} → ambiguous (title didn't change)
  → Vision: "Does screen show: order detail page?" → NO
  → Evidence: "Screen still shows search results list"
  → StepResult(success=False, evidence="still on search results")
  → on_fail="replan" → planner sees: "click didn't reach order detail"
  → planner says: "Try scrolling down, the order might be below the fold"
  → New step: press_key(["pagedown"]) then re-attempt click
```

### Unit Test Strategy Per Component

#### (A) Planner Unit Tests

```
┌─────────────────────────────────────────────────────────────────┐
│ Test                              │ What it verifies             │
├───────────────────────────────────┼─────────────────────────────┤
│ test_plan_returns_action_plan     │ plan() returns ActionPlan    │
│                                   │ with correct fields          │
│ test_plan_with_skill_context      │ skill context appears in     │
│                                   │ LLM prompt                   │
│ test_plan_without_skill           │ works without skill context  │
│ test_plan_malformed_json          │ graceful error on bad JSON   │
│ test_plan_empty_steps             │ error if LLM returns []      │
│ test_replan_returns_action_step   │ replan() returns single step │
│ test_replan_includes_history      │ history appears in LLM call  │
│ test_replan_includes_screen       │ screen desc in LLM call      │
│ test_prompt_file_loading          │ .md prompts load from disk   │
│ test_prompt_file_missing          │ clear error if file missing  │
│ test_plan_step_has_verify         │ every step has verify field  │
│ test_plan_step_has_on_fail        │ every step has on_fail       │
├───────────────────────────────────┼─────────────────────────────┤
│ MOCK: LLM client (returns canned │                              │
│       JSON responses)             │                              │
│ REAL: prompt loading, JSON parsing│                              │
└───────────────────────────────────┴─────────────────────────────┘
```

#### (B) Skill Registry Unit Tests

```
┌─────────────────────────────────────────────────────────────────┐
│ Test                              │ What it verifies             │
├───────────────────────────────────┼─────────────────────────────┤
│ test_load_skill_from_string       │ parses .md format correctly  │
│ test_load_skill_missing_section   │ error on malformed skill     │
│ test_match_keyword_hit            │ "return amazon" → match      │
│ test_match_keyword_miss           │ "open calculator" → no match │
│ test_match_extracts_params        │ "return blue headphones"     │
│                                   │ → {item: "blue headphones"}  │
│ test_expand_fills_template        │ {{item}} → "blue headphones" │
│ test_expand_missing_param         │ error if required param gone │
│ test_list_skills                  │ returns all loaded skills    │
│ test_validate_all_valid           │ no errors on valid library   │
│ test_validate_catches_errors      │ reports malformed files      │
│ test_load_from_directory          │ loads all .md files in dir   │
│ test_empty_directory              │ returns empty list           │
├───────────────────────────────────┼─────────────────────────────┤
│ MOCK: filesystem (in-memory       │                              │
│       skill strings)              │                              │
│ REAL: markdown parsing, regex     │                              │
└───────────────────────────────────┴─────────────────────────────┘
```

#### (C) Vision Coordinator Unit Tests

```
┌─────────────────────────────────────────────────────────────────┐
│ Test                              │ What it verifies             │
├───────────────────────────────────┼─────────────────────────────┤
│ test_find_element_molmo_coords    │ Molmo "(0.45, 0.12)" →      │
│                                   │ pixel(680, 118)              │
│ test_find_element_qwen_coords     │ Qwen "(450, 120)" →         │
│                                   │ pixel(680, 118)              │
│ test_find_element_not_found       │ "not found" → returns None   │
│ test_find_element_low_confidence  │ ambiguous → confidence=low   │
│ test_coordinate_space_unknown     │ unknown model → raises error │
│ test_describe_screen              │ merges vision + HS state     │
│ test_verify_condition_yes         │ "YES" → True                 │
│ test_verify_condition_no          │ "NO" → False                 │
│ test_verify_condition_ambiguous   │ "maybe" → False (conservative)│
│ test_capture_returns_b64          │ screenshot → valid base64    │
│ test_capture_compression          │ large image → under 4.5MB   │
│ test_screenshot_saved_to_disk     │ file written at returned path│
│ test_coord_conversion_retina      │ handles 2x Retina screens   │
│ test_coord_conversion_non_retina  │ handles 1x screens          │
├───────────────────────────────────┼─────────────────────────────┤
│ MOCK: vision model client,        │                              │
│       screenshot capture (returns  │                              │
│       known test image), HS state  │                              │
│ REAL: coordinate math, base64     │                              │
│       encoding, image compression  │                              │
└───────────────────────────────────┴─────────────────────────────┘
```

#### (D) Actuator Unit Tests

```
┌─────────────────────────────────────────────────────────────────┐
│ Test                              │ What it verifies             │
├───────────────────────────────────┼─────────────────────────────┤
│ test_click_generates_lua          │ click(500,300) → correct Lua │
│ test_type_text_escapes_quotes     │ "he said \"hi\"" → safe Lua │
│ test_type_text_escapes_backslash  │ "path\\to" → safe Lua       │
│ test_type_text_escapes_newlines   │ "line1\nline2" → safe Lua   │
│ test_press_key_single             │ ["enter"] → correct Lua     │
│ test_press_key_combo              │ ["cmd","c"] → correct Lua   │
│ test_press_key_multi_modifier     │ ["cmd","shift","s"] → Lua   │
│ test_activate_app                 │ "Safari" → correct Lua      │
│ test_open_url                     │ url → correct Lua           │
│ test_quit_app                     │ "Safari" → correct Lua      │
│ test_get_state_parses_json        │ HS JSON → Python dict       │
│ test_get_state_empty_response     │ no window → safe defaults   │
│ test_is_available_found           │ hs in PATH → True           │
│ test_is_available_not_found       │ no hs → False               │
│ test_hs_cli_error                 │ exit code 1 → result.error  │
│ test_hs_cli_timeout               │ hang → timeout error        │
│ test_lua_template_loading         │ templates load from disk     │
│ test_lua_template_missing         │ clear error if file missing  │
├───────────────────────────────────┼─────────────────────────────┤
│ MOCK: subprocess (hs CLI)          │                              │
│ REAL: Lua template rendering,      │                              │
│       JSON parsing, string escaping│                              │
└───────────────────────────────────┴─────────────────────────────┘
```

#### (E) Orchestrator Unit Tests

```
┌─────────────────────────────────────────────────────────────────┐
│ Test                              │ What it verifies             │
├───────────────────────────────────┼─────────────────────────────┤
│ test_skill_matched_passes_context │ matched skill → planner gets │
│                                   │ expanded skill as context    │
│ test_no_skill_plans_from_scratch  │ no match → planner called    │
│                                   │ without context              │
│ test_step_with_element_calls_find │ click(element=X) → vision   │
│                                   │ find_element called          │
│ test_element_not_found_replans    │ find→None → replan() called │
│ test_verify_tier1_pass            │ HS state confirms → done     │
│ test_verify_tier1_fail            │ HS state denies → fail       │
│ test_verify_tier1_ambiguous_      │ ambiguous → escalate to      │
│   escalates_to_tier2              │ vision tier 2                │
│ test_verify_tier2_pass            │ vision confirms → done       │
│ test_verify_tier2_fail_retry      │ vision denies + on_fail=     │
│                                   │ retry_different → vary strat │
│ test_verify_tier2_fail_replan     │ vision denies + on_fail=     │
│                                   │ replan → call replan()       │
│ test_verify_tier2_fail_abort      │ vision denies + on_fail=     │
│                                   │ abort → return failure       │
│ test_max_retries_then_escalate    │ 3 retries exhausted → replan │
│ test_max_iterations_returns_fail  │ 30 iterations → failure      │
│ test_wait_for_user_pauses         │ wait step → poll until done  │
│ test_done_step_returns_success    │ "done" action → success      │
│ test_execution_result_has_evidence│ all steps have evidence field│
│ test_events_logged                │ event logger called for each │
│                                   │ step, verification, decision │
│ test_screenshots_saved            │ screenshots saved at each    │
│                                   │ verification step            │
├───────────────────────────────────┼─────────────────────────────┤
│ MOCK: ALL of planner, skills,     │                              │
│       vision, actuator             │                              │
│ REAL: orchestrator logic, state    │                              │
│       machine, retry/replan flow   │                              │
└───────────────────────────────────┴─────────────────────────────┘
```

### Component Self-Tests (Integration, In Isolation)

Each component has a `self_test.py` that tests against real dependencies:

```
┌────────────────────────────────────────────────────────────────────────┐
│ Component  │ self_test.py exercises              │ Real deps           │
├────────────┼─────────────────────────────────────┼────────────────────┤
│ Planner    │ Send real prompt to Claude API       │ Anthropic API      │
│            │ Verify response is valid ActionPlan  │                    │
│            │ Send replan with screen description  │                    │
│            │ Verify response is valid ActionStep  │                    │
│            │ Measure latency and token usage      │                    │
├────────────┼─────────────────────────────────────┼────────────────────┤
│ Skills     │ Load all skills from library/        │ Filesystem         │
│            │ Validate all parse correctly         │                    │
│            │ Match known prompts to expected skill│                    │
│            │ Expand with params, verify output    │                    │
├────────────┼─────────────────────────────────────┼────────────────────┤
│ Vision     │ Capture real screenshot              │ Screen capture,    │
│            │ Call real Molmo to find element       │ Molmo API          │
│            │ Verify returned coords are on screen │                    │
│            │ Call verify_condition with real screen│                    │
│            │ Capture + describe current screen    │                    │
├────────────┼─────────────────────────────────────┼────────────────────┤
│ Actuator   │ Check Hammerspoon available          │ Hammerspoon        │
│            │ get_state() → valid dict             │                    │
│            │ activate_app("Finder") → verify      │                    │
│            │ open_url → verify browser opens      │                    │
│            │ type_text → verify (in a text field) │                    │
│            │ press_key(escape) → verify           │                    │
├────────────┼─────────────────────────────────────┼────────────────────┤
│ Orchestr.  │ Simple task: "Open Calculator"       │ ALL real deps      │
│            │ Verify Calculator opens (HS + vision)│                    │
│            │ Check all steps have evidence         │                    │
│            │ Check events.jsonl has entries        │                    │
│            │ Check trace .md was generated         │                    │
└────────────┴─────────────────────────────────────┴────────────────────┘
```

### Top-Level E2E Tests (F)

```python
# tests/e2e/test_e2e_simple_tasks.py

@pytest.mark.e2e
async def test_open_calculator():
    """Open Calculator, verify it's frontmost."""
    agent = build_real_agent()  # real everything
    result = await agent.execute("Open Calculator")

    # 1. Agent claims success
    assert result.success, f"Agent failed: {result.error}"

    # 2. Evidence exists for every step
    for step_result in result.steps:
        assert step_result.evidence, f"Step {step_result.step.action} has no evidence"
        assert step_result.verification_method != "actuator_only", \
            f"Step {step_result.step.action} was not independently verified"

    # 3. Independent verification (outside the agent)
    actuator = HammerspoonActuator(event_logger=EventLogger.for_standalone("test"))
    state = await actuator.get_state()
    assert state["app_name"] == "Calculator", \
        f"Calculator not frontmost. Got: {state['app_name']}"

    # 4. Logs exist
    assert Path(f"logs/traces/run-{result.run_id}.md").exists()
    events = load_events(result.run_id)
    assert len(events) > 0
    assert any(e["event_type"] == "verification" for e in events)


@pytest.mark.e2e
async def test_calculator_2_plus_2():
    """Open Calculator, compute 2+2, verify display shows 4."""
    agent = build_real_agent()
    result = await agent.execute("Open Calculator and compute 2 plus 2")

    assert result.success

    # Independent verification: read Calculator display via vision
    coordinator = build_real_coordinator()
    verified = await coordinator.verify_condition(
        "The Calculator app display shows the number 4"
    )
    assert verified, "Calculator does not show 4"


@pytest.mark.e2e
async def test_safari_navigation():
    """Open Safari, navigate to example.com, verify page loaded."""
    agent = build_real_agent()
    result = await agent.execute("Open Safari and go to example.com")

    assert result.success

    # Independent verification
    actuator = build_real_actuator()
    state = await actuator.get_state()
    assert "Safari" in state["app_name"]
    assert "example" in state["window_title"].lower()
```

### Failure Diagnosis: When Something Goes Wrong

The logging and evidence system is designed so that when a test fails, you can pinpoint exactly where and why:

```
TEST FAILURE: test_calculator_2_plus_2
  AssertionError: Calculator does not show 4

DIAGNOSIS FLOW:

1. Check ExecutionResult:
   result.success = True (agent THOUGHT it succeeded)
   result.steps = [
     StepResult(action="activate_app", success=True,
                method="hs_state", evidence="app_name=Calculator"),
     StepResult(action="click", params={element:"2 button"}, success=True,
                method="vision", evidence="2 button was clicked"),
     StepResult(action="click", params={element:"plus button"}, success=True,
                method="vision", evidence="plus button was clicked"),
     StepResult(action="click", params={element:"2 button"}, success=True,
                method="vision", evidence="2 button was clicked"),
     StepResult(action="click", params={element:"equals button"}, success=True,
                method="vision", evidence="equals was clicked"),
   ]

2. But independent verification says display != 4.
   Something went wrong. Let's check the trace:

   logs/traces/run-r-abc123.md:
     Step 3: click(element="plus button")
       Vision find_element: "plus button on calculator"
       Raw response: "point(0.52, 0.65)"
       Pixel: (786, 638)
       ← PROBLEM: (786, 638) is actually the "minus" button!
       Verification: "plus button was clicked" → YES
       ← PROBLEM: vision model confirmed wrong thing

3. Root cause: Molmo misidentified minus as plus.
   The coordinate was correct for minus, not plus.
   Verification was too vague ("plus button was clicked" didn't
   check the actual result on the display).

4. Fix: Make verify conditions check OUTCOME not ACTION:
   Instead of: verify="plus button was clicked"
   Use:        verify="calculator display shows 2 + with cursor"
```

### Failure Taxonomy

```
┌─────────────────────────────────────────────────────────────────────┐
│ WHERE IT FAILS       │ HOW TO DETECT              │ HOW TO DEBUG    │
├──────────────────────┼───────────────────────────┼─────────────────┤
│ Skill matching       │ Wrong skill matched, or    │ Check events:   │
│                      │ no match when expected     │ skill_matched   │
│                      │                            │ event in jsonl  │
├──────────────────────┼───────────────────────────┼─────────────────┤
│ Planning             │ Plan has wrong/missing     │ Check trace:    │
│                      │ steps                      │ LLM prompt +    │
│                      │                            │ response in     │
│                      │                            │ trace .md       │
├──────────────────────┼───────────────────────────┼─────────────────┤
│ Coordinate finding   │ Element found at wrong     │ Check trace:    │
│ (vision)             │ location, or not found     │ raw_response,   │
│                      │                            │ parsed_coords,  │
│                      │                            │ pixel result +  │
│                      │                            │ screenshot      │
├──────────────────────┼───────────────────────────┼─────────────────┤
│ Coordinate space     │ Coords wildly off (e.g.    │ Check trace:    │
│ conversion           │ clicking top-left when     │ coordinate_space│
│                      │ target is bottom-right)    │ and conversion  │
│                      │                            │ math            │
├──────────────────────┼───────────────────────────┼─────────────────┤
│ Actuator execution   │ Hammerspoon returns error  │ Check events:   │
│                      │ or exit code != 0          │ lua_script,     │
│                      │                            │ exit_code,      │
│                      │                            │ stderr          │
├──────────────────────┼───────────────────────────┼─────────────────┤
│ Verification (false  │ Agent says success but     │ Compare step    │
│ positive)            │ independent check fails    │ evidence vs     │
│                      │                            │ independent     │
│                      │                            │ check. Review   │
│                      │                            │ verify condition│
│                      │                            │ — too vague?    │
├──────────────────────┼───────────────────────────┼─────────────────┤
│ Verification (false  │ Step actually worked but   │ Check screenshot│
│ negative)            │ verification says no       │ — does it show  │
│                      │                            │ expected state? │
│                      │                            │ Vision model    │
│                      │                            │ misread screen? │
├──────────────────────┼───────────────────────────┼─────────────────┤
│ Replanning           │ Agent loops or makes       │ Check trace:    │
│                      │ nonsensical decisions      │ replan events,  │
│                      │                            │ history passed  │
│                      │                            │ to LLM, LLM    │
│                      │                            │ response        │
├──────────────────────┼───────────────────────────┼─────────────────┤
│ Login/auth wall      │ Agent stuck waiting or     │ Check trace:    │
│                      │ doesn't detect login       │ observation     │
│                      │                            │ screenshots,    │
│                      │                            │ wait events     │
├──────────────────────┼───────────────────────────┼─────────────────┤
│ Max iterations       │ Agent exhausts loop        │ Read full trace │
│                      │ without completing         │ — usually a     │
│                      │                            │ replan loop or  │
│                      │                            │ stuck state     │
└──────────────────────┴───────────────────────────┴─────────────────┘
```

### Manual Test Scripts (G)

```bash
# Each script: announce → act → verify → assert → summary

python scripts/run_calculator_test.py
# [1/6] Opening Calculator...
#   ✓ Calculator is frontmost (HS state: app_name=Calculator)
# [2/6] Clicking '2'...
#   ✓ Found '2' at (786, 520), confidence=high
#   ✓ Clicked successfully
# [3/6] Clicking '+'...
#   ✓ Found '+' at (910, 520), confidence=high
#   ✓ Clicked successfully
# [4/6] Clicking '2' again...
#   ✓ Clicked successfully
# [5/6] Clicking '='...
#   ✓ Clicked successfully
# [6/6] Verifying display shows '4'...
#   ✓ Vision confirms: "display shows 4"
#   Screenshot: logs/screenshots/calc-test-final.jpg
#
# ✅ ALL CHECKS PASSED (6/6)
# Trace: logs/traces/run-calc-test.md

python scripts/run_safari_navigation.py
# [1/4] Opening Safari...
#   ✓ Safari is frontmost
# [2/4] Navigating to example.com...
#   ✓ URL opened
# [3/4] Waiting for page load...
#   ✓ Page loaded (title contains "Example")
# [4/4] Verifying page content...
#   ✓ Vision confirms: "Example Domain page is visible"
#
# ✅ ALL CHECKS PASSED (4/4)

python scripts/run_skill_dry_run.py return_amazon_order --item "blue headphones"
# Skill: return_amazon_order
# Parameters: item="blue headphones"
# Plan (7 steps):
#   1. open_url("https://amazon.com/orders")
#      verify: "Amazon page or login visible"
#   2. observe → check for login
#      verify: null (observation step)
#   3. click(element="search bar")
#      verify: "search bar is focused"
#   4. type_text("blue headphones") + press_key(["enter"])
#      verify: "search results visible"
#   5. click(element="matching order")
#      verify: "order detail page visible"
#   6. click(element="Return or Replace Items")
#      verify: "return flow started"
#   7. observe → check for confirmation
#      verify: "return confirmation visible"
# (DRY RUN — no actions executed)
```

### Test Configuration

```toml
# pyproject.toml
[tool.pytest.ini_options]
markers = [
    "unit: fast, all dependencies mocked, < 5s total",
    "integration: component-level, real deps for that component",
    "e2e: full system, needs macOS + Hammerspoon + LLM APIs",
    "manual: requires human observation of screen",
]
testpaths = ["tests"]
asyncio_mode = "auto"

# Run categories:
# pytest -m unit                    # CI-safe, fast
# pytest -m integration             # Needs API keys + Hammerspoon
# pytest -m e2e                     # Needs full environment
# pytest                            # Everything
```

---

## 11. Project Structure

```
src/automation_agent/
├── __init__.py
├── __main__.py                  # Top-level CLI entry point
├── config.py                    # Pydantic settings
│
├── logging/                     # Cross-cutting instrumentation
│   ├── __init__.py
│   ├── event_logger.py          # EventLogger class
│   ├── models.py                # Event dataclass
│   └── analysis.py              # Post-hoc analysis helpers
│
├── planner/                     # (A) Action Planner
│   ├── __init__.py
│   ├── __main__.py              # python -m automation_agent.planner
│   ├── self_test.py             # python -m automation_agent.planner.self_test
│   ├── planner.py               # ActionPlanner class
│   ├── models.py                # ActionStep, ActionPlan, StepResult
│   └── prompts/
│       ├── plan_from_prompt.md
│       └── replan_from_state.md
│
├── skills/                      # (B) Skill Registry
│   ├── __init__.py
│   ├── __main__.py              # python -m automation_agent.skills
│   ├── self_test.py             # python -m automation_agent.skills.self_test
│   ├── registry.py              # SkillRegistry class
│   ├── loader.py                # Parse .md skill files
│   ├── matcher.py               # Match prompts to skills
│   ├── models.py                # Skill, ExpandedSkill
│   └── library/                 # Skill definition files
│       ├── return_amazon_order.md
│       ├── google_search.md
│       ├── send_imessage.md
│       └── open_app_and_navigate.md
│
├── vision/                      # (C) Vision Coordinator
│   ├── __init__.py
│   ├── __main__.py              # python -m automation_agent.vision
│   ├── self_test.py             # python -m automation_agent.vision.self_test
│   ├── coordinator.py           # ScreenCoordinator class
│   ├── capture.py               # Screenshot capture + encoding
│   ├── models.py                # ElementLocation, ScreenState
│   └── prompts/
│       ├── find_element.md
│       ├── describe_screen.md
│       └── verify_condition.md
│
├── actuator/                    # (D) Actuator — 3 backends
│   ├── __init__.py              # create_actuator() factory, selects best backend
│   ├── __main__.py              # python -m automation_agent.actuator
│   ├── self_test.py             # python -m automation_agent.actuator.self_test
│   ├── actuator.py              # HammerspoonActuator (hs CLI + Lua templates)
│   ├── bridge_actuator.py       # HammerspoonBridgeActuator (HTTP bridge + osascript fallback)
│   ├── applescript_actuator.py  # AppleScriptActuator (pure osascript)
│   ├── models.py                # ActuatorResult
│   └── lua_templates/
│       ├── click.lua
│       ├── type_text.lua
│       ├── press_key.lua
│       ├── activate_app.lua
│       ├── open_url.lua
│       ├── quit_app.lua
│       └── get_state.lua
│
└── orchestrator/                # (E) Orchestrator
    ├── __init__.py
    ├── __main__.py              # python -m automation_agent.orchestrator
    ├── self_test.py             # python -m automation_agent.orchestrator.self_test
    ├── agent.py                 # AutomationAgent class
    ├── verifier.py              # StepVerifier (two-tier verification)
    └── models.py                # ExecutionResult

tests/
├── conftest.py                  # Shared fixtures
├── unit/
│   ├── test_planner.py              # 34 tests (ActionPlanner)
│   ├── test_skill_registry.py       # 40 tests (SkillRegistry)
│   ├── test_coordinator.py          # 34 tests (ScreenCoordinator)
│   ├── test_actuator.py             # 59 tests (HammerspoonActuator, hs CLI)
│   ├── test_applescript_actuator.py  # 24 tests (AppleScriptActuator)
│   ├── test_bridge_actuator.py      # 43 tests (HammerspoonBridgeActuator + osascript fallback)
│   ├── test_orchestrator_new.py     # 23 tests (AutomationAgent)
│   ├── test_verifier.py             # 18 tests (StepVerifier)
│   └── test_event_logger.py         # ~8 tests, real filesystem
│   # Total: 282 passing, 3 skipped
├── integration/
│   ├── test_planner_int.py      # Real Claude API
│   ├── test_skills_int.py       # Real filesystem
│   ├── test_coordinator_int.py  # Real screenshot + mock vision
│   ├── test_actuator_int.py     # Real Hammerspoon
│   └── test_orchestrator_int.py # All real, simple tasks
├── e2e/
│   ├── test_e2e_simple.py       # "Open Calculator"
│   ├── test_e2e_multi_step.py   # "Search Google for X"
│   └── test_e2e_skills.py       # Skill-based tasks
└── workflows/                   # (preserved from prototype if needed)

scripts/
├── run_calculator_test.py       # Manual: Calculator 2+2=4
├── run_safari_navigation.py     # Manual: Navigate to URL
├── run_skill_dry_run.py         # Manual: Expand skill, print plan
├── run_full_scenario.py         # Manual: Multi-step with recording
├── analyze_run.py               # Post-hoc: analyze single run
└── analyze_failures.py          # Post-hoc: failure patterns

logs/
├── events.jsonl                 # Structured event log (append-only)
├── debug.log                    # Rotating debug log
├── traces/                      # One .md per execution run
└── screenshots/                 # Saved screenshots for audit
```

---

## 12. Implementation Notes

Key decisions made during implementation that deviate from or augment the original design:

1. **3 actuator backends** instead of Hammerspoon-only: `HammerspoonBridgeActuator` (HTTP bridge to hs.claude server on port 27741), `HammerspoonActuator` (hs CLI with Lua templates), and `AppleScriptActuator` (osascript fallback). The `create_actuator(config)` factory auto-selects the best available backend in priority order: Bridge > HS CLI > AppleScript.

2. **osascript fallback** for accessibility-restricted scenarios: The bridge actuator detects when Hammerspoon lacks accessibility permissions and automatically falls back to osascript for keyboard/mouse input actions. Permission status is cached with a 60-second TTL to avoid repeated checks.

3. **Mandatory postconditions** with exemptions for done/wait_for_user/observe: The `verify` field on `ActionStep` is a required `str` (not `Optional[str]`). The `ActionPlan.validate()` method enforces this, but exempts terminal and observational actions. The `on_fail` field uses `"retry_different"` instead of `"retry"` to emphasize that retries must use meaningfully different strategies.

4. **Strategy-changing retries** (not blind repetition): The orchestrator's `_vary_strategy()` method produces meaningfully different retry strategies for all action types, rather than simply re-executing the same action.

5. **Template injection prevention** in skill expansion: `expand()` uses single-pass `re.sub` with a callback function to prevent template injection attacks where parameter values contain `{{placeholders}}`. Unexpanded placeholders are stripped with a warning.

6. **Accessibility permission caching** with 60-second TTL: The bridge actuator caches whether Hammerspoon has accessibility permissions to avoid repeated subprocess calls for each action.

7. **Legacy actions/ package removal**: The old `AppleScript`, `Hammerspoon`, and `PyAutoGUI` action classes in the `actions/` directory were completely deleted after migrating `__main__.py` to the new component architecture. All functionality is now provided by the 3-backend actuator layer.

---

## 13. Implementation Plan

### Build Order (Followed as Planned)

```
Phase 0: Shared foundations (EventLogger, models, config) ✓
    │
    ├── no dependencies
    │
    ▼
Phase 1: Components A-D in parallel ✓
    │
    ├── (A) Planner       ─── independent ✓
    ├── (B) Skill Registry ─── independent ✓
    ├── (C) Vision         ─── independent ✓
    └── (D) Actuator       ─── independent ✓
    │
    ▼
Phase 2: Component E (Orchestrator) — depends on A-D ✓
    │
    ▼
Phase 3: Top-level E2E tests + manual scripts ✓
    │
    ▼
Phase 4: Analysis tools + documentation ✓
```

### Team Assignment

```
Team Lead:         Shared foundations + orchestration + E2E tests + adversary coordination
Agent A (planner): planner/ + tests/unit/test_planner.py + self_test.py
Agent B (skills):  skills/ + tests/unit/test_skill_registry.py + self_test.py
Agent C (vision):  vision/ + tests/unit/test_coordinator.py + self_test.py
Agent D (actuator):actuator/ + tests/unit/test_actuator.py + self_test.py
Agent E (orch):    orchestrator/ + tests/unit/test_orchestrator.py + e2e + scripts

Each component had a fixer agent + adversary agent. The adversary review
process found 35 bugs across all 5 components, all of which were fixed
and verified. Legacy actions/ package was removed after migrating
__main__.py to the new component architecture.
```

### Definition of Done Per Component

A component is "done" when:
1. Implementation passes all unit tests (mocked)
2. `__main__.py` CLI works for all subcommands
3. `self_test.py` passes against real dependencies
4. Events are logged to `EventLogger` for every action/decision
5. Execution trace includes all relevant details

### Definition of Done For the System

The system is "done" when:
1. All unit tests pass: `pytest -m unit` (< 5 seconds)
2. All integration tests pass: `pytest -m integration`
3. All E2E tests pass: `pytest -m e2e`
4. Manual scripts pass: `run_calculator_test.py`, `run_safari_navigation.py`
5. Every `StepResult` in E2E tests has `verification_method != "actuator_only"`
6. `logs/traces/` contains readable traces for all test runs
7. `logs/events.jsonl` can be queried to reproduce any failure
8. `analyze_run.py` produces meaningful output for any run_id

---

*End of design document.*
