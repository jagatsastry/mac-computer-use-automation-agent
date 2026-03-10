> Historical deep-dive with some stale implementation details.
> In the current code, `create_actuator()` returns `AppleScriptActuator`, there is no `--restaurant-only` path, and restaurant skills are optional priors rather than bespoke runtime flows. Read this file as an architecture narrative, not exact runtime truth.

# Life of a Prompt

**Tracing "Return the blue headphones I bought on Amazon" through every component**

This document follows a single user prompt from CLI entry through all 5 components of the macOS Automation Agent, showing every function call, data transformation, and decision point in deep detail.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Phase 0 — CLI Bootstrap](#phase-0--cli-bootstrap)
3. [Phase 1 — Component Initialization](#phase-1--component-initialization)
4. [Phase 2 — Orchestrator: execute()](#phase-2--orchestrator-execute)
   - [2A — Skill Matching](#phase-2a--skill-matching)
   - [2B — Screen Description](#phase-2b--screen-description)
   - [2C — Planning](#phase-2c--planning)
5. [Phase 3 — Step Execution Loop](#phase-3--step-execution-loop)
6. [Phase 4 — Verification](#phase-4--verification)
7. [Phase 5 — Element Finding (Click Steps)](#phase-5--click-step-with-element-finding)
8. [Phase 6 — Failure Handling and Replanning](#phase-6--failure-handling-and-replanning)
9. [Phase 7 — Result Return](#phase-7--result-return)
10. [Complete Data Flow Diagram](#complete-data-flow-summary)
11. [Key Data Structures](#key-data-structures)
12. [Essential Files Reference](#essential-files-reference)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        USER PROMPT                                   │
│     "Return the blue headphones I bought on Amazon"                  │
└──────────────────────┬───────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  __main__.py  │  CLI Bootstrap: parse args, load config, wire deps   │
└──────────────────────┬───────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR (agent.py)                            │
│                                                                      │
│  ┌─────────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │   SKILL      │  │ PLANNER  │  │ VISION   │  │    ACTUATOR      │  │
│  │  REGISTRY    │  │          │  │COORDINATOR│  │                  │  │
│  │             │  │ Claude   │  │          │  │  Hammerspoon     │  │
│  │ .md skills  │  │  API     │  │ Local or │  │  Bridge (HTTP)   │  │
│  │ keyword     │  │          │  │ Claude   │  │  → hs CLI        │  │
│  │ matching    │  │ Plan →   │  │ vision   │  │  → AppleScript   │  │
│  │ {{param}}   │  │ Replan   │  │          │  │                  │  │
│  └──────┬──────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘  │
│         │              │             │                  │            │
│         └──────────────┴─────────────┴──────────────────┘            │
│                              │                                       │
│                    ┌─────────▼──────────┐                            │
│                    │   STEP VERIFIER    │                            │
│                    │ Tier 1: HS State   │                            │
│                    │ Tier 2: Vision     │                            │
│                    └────────────────────┘                            │
└──────────────────────────────────────────────────────────────────────┘
```

The agent is a 5-component pipeline that communicates through **Protocol classes** (`protocols.py`) and **shared dataclasses** (`shared_models.py`). No inheritance — pure duck-typing via `@runtime_checkable` protocols.

| Component | Implementation Class | Protocol |
|---|---|---|
| CLI / Entry | `__main__.py` | — |
| Orchestrator | `AutomationAgent` | — |
| Planner | `ActionPlannerImpl` | `ActionPlanner` |
| Vision | `ScreenCoordinatorImpl` | `ScreenCoordinator` |
| Skills | `SkillRegistryImpl` | `SkillRegistry` |
| Actuator | `HammerspoonBridgeActuator` / `HammerspoonActuator` / `AppleScriptActuator` | `Actuator` |
| Verifier | `StepVerifier` | `Verifier` |

---

## Phase 0 — CLI Bootstrap

**File:** `src/automation_agent/__main__.py`

### Entry Point: `main()` — Line 230

The OS executes the `automation-agent` console script, which calls `main()`.

**Step 0.1 — Argument parsing** (`line 232`)
```python
args = parse_args()   # from .cli — argparse
# args.prompt = "Return the blue headphones I bought on Amazon"
validate_args(args)   # checks for required prompt field
```

**Step 0.2 — Configuration loading** (`lines 236–241`)
```python
config = load_config(args.config)   # config.py line 262
# If no --config file: AgentConfig() uses pydantic_settings with AGENT_ env prefix
# Reads .env in cwd, then AGENT_* env vars
# Key defaults:
#   model_provider = ModelProvider.LOCAL ("local")
#   vision_server_url = "http://localhost:8080"
#   vision_model = "qwen3-vl"
#   anthropic_model = "claude-sonnet-4-20250514"
#   max_iterations = 20
#   screenshot_resolution = (1024, 768)
apply_cli_overrides(config, args)   # --provider, --vision-model, etc.
configure_logging(config)
```

**Step 0.3 — Async dispatch** (`line 254`)
```python
exit_code = asyncio.run(
    run_agent(args.prompt, config, args.dry_run)
)
```

The prompt is NOT a restaurant prompt (no keywords match), so execution falls through to the main agent path.

```
┌─────────────┐     ┌─────────────┐     ┌────────────────┐
│  parse_args  │────▶│ load_config  │────▶│ asyncio.run()  │
│  argparse    │     │ pydantic     │     │ run_agent()    │
└─────────────┘     └─────────────┘     └────────────────┘
```

---

## Phase 1 — Component Initialization

**File:** `src/automation_agent/__main__.py`, `run_agent()` lines 78–227

```
run_agent()
    │
    ├── ActionPlannerImpl(config)
    │     └── loads planner/prompts/*.md templates
    │
    ├── SkillRegistryImpl()
    │     └── loads all skills/library/*.md files
    │         ├── amazon_search.md
    │         ├── google_search.md
    │         ├── return_amazon_order.md
    │         ├── send_imessage.md
    │         └── open_app_and_navigate.md
    │
    ├── ScreenCoordinatorImpl(config)
    │     ├── ScreenCapture(1024x768)
    │     └── validates vision_model in COORDINATE_SPACES
    │
    ├── create_actuator(config)
    │     └── tries: Bridge HTTP → hs CLI → AppleScript
    │
    └── AutomationAgent(planner, registry, coordinator, actuator, config)
          └── StepVerifier(actuator, coordinator, logger)
```

### Step 1.1 — Planner instantiation (`line 98`)
```python
planner = ActionPlannerImpl(config)
# Sets self._prompts_dir = Path(__file__).parent / "prompts"
```

### Step 1.2 — Skill registry instantiation (`line 99`)
```python
skill_registry = SkillRegistryImpl()
# __init__: self._skills = {}
# self._skill_dir = Path(__file__).parent / "library"
# Immediately calls self.load_from_directory(self._skill_dir)
```

Inside `load_from_directory()` (`registry.py` line 30):
- Iterates every `.md` file in `skills/library/` in sorted order
- For each file, calls `load_skill_from_file(md_file)` from `loader.py`
- Checks `_os_matches(skill.requires.os)` — only loads darwin skills on macOS
- Stores in `self._skills[skill.name]` dictionary

This loads `return_amazon_order.md` into the registry as key `"return-amazon-order"`.

### Step 1.3 — Vision coordinator instantiation (`line 100`)
```python
coordinator = ScreenCoordinatorImpl(config)
# __init__:
#   self.capture = ScreenCapture(config.screenshot_resolution)  ← (1024, 768)
#   self._validate_model()  ← ensures vision_model is in COORDINATE_SPACES
#   self._grounding_enabled = bool(config.grounding_model)
```

`_validate_model()` checks against:
```python
COORDINATE_SPACES = {
    "molmo": "normalized_0_1",
    "qwen3-vl": "normalized_0_1000",
    "qwen2.5-vl": "normalized_0_1000",
    "qwen2-vl": "normalized_0_1000",
    "claude-sonnet-4-20250514": "pixel",
}
```

### Step 1.4 — Actuator creation (`line 101`)
```python
actuator = create_actuator(config)   # actuator/__init__.py
```

Fallback chain:
```
┌─────────────────────┐   unavailable   ┌──────────────────┐   unavailable   ┌──────────────────┐
│ HammerspoonBridge   │───────────────▶│ HammerspoonCLI   │───────────────▶│ AppleScript      │
│ HTTP localhost:27741│                │ hs -c 'lua...'   │                │ osascript        │
│ ★ Selected          │                │                  │                │                  │
└─────────────────────┘                └──────────────────┘                └──────────────────┘
```

### Step 1.5 — AutomationAgent assembly (`lines 136–145`)
```python
agent = AutomationAgent(
    planner=planner,
    skill_registry=skill_registry,
    coordinator=coordinator,
    actuator=actuator,
    config=config,
    screenshot_diff=screenshot_diff,   # may be None
    context_monitor=context_monitor,   # may be None
    grounding_router=grounding_router, # may be None
)
# Inside __init__: creates StepVerifier(actuator, coordinator, logger)
```

### Step 1.6 — Execution dispatch (`line 191`)
```python
result = await agent.execute(prompt)
# prompt = "Return the blue headphones I bought on Amazon"
```

---

## Phase 2 — Orchestrator: execute()

**File:** `src/automation_agent/orchestrator/agent.py`, lines 42–211

```python
async def execute(self, goal: str) -> ExecutionResult:
    start = time.monotonic()
    self.logger.log_event(EventType.TASK_START, f"Goal: {goal}", data={"goal": goal})
    step_results: list[StepResult] = []
    iterations = 0
```

### Phase 2A — Skill Matching

**Files:** `registry.py`, `matcher.py`, `loader.py`

```
"Return the blue headphones I bought on Amazon"
    │
    ▼
SkillRegistryImpl.match(goal)
    │
    ▼
match_skill(prompt, all_skills)          ◄── matcher.py
    │
    ├── Score each skill by keyword hits:
    │   return_amazon_order: "return"(1) + "amazon"(1) = 2  ★ WINNER
    │   amazon_search:       "amazon"(1)                = 1
    │   google_search:       0
    │   send_imessage:       0
    │   open_app_navigate:   0
    │
    ▼
extract_params(prompt, skill)            ◄── matcher.py
    │
    ├── Strategy 1: quoted strings → none found
    ├── Strategy 2: single required param ("item")
    │   Remove trigger keywords: "return", "amazon"
    │   Remove filler words: "the", "on", "I"
    │   Result: "blue headphones bought"
    │
    ▼
SkillRegistryImpl.expand("return-amazon-order", {"item": "blue headphones bought"})
    │
    ├── re.sub(r"{{item}}", "blue headphones bought", steps_text)
    │
    ▼
Returns: {
    "skill_name": "return-amazon-order",
    "expanded_steps": "1. Open Safari and navigate to ...\n4. Type \"blue headphones bought\"...",
    "params": {"item": "blue headphones bought"}
}
```

#### Keyword scoring detail

```python
def match_skill(prompt: str, skills: List[Skill]) -> Optional[Tuple[Skill, Dict[str, str]]]:
    prompt_lower = "return the blue headphones i bought on amazon"
    best_skill = None
    best_score = 0

    for skill in skills:
        score = 0
        for keyword in skill.trigger_keywords:
            if keyword.lower() in prompt_lower:
                score += 1
        if score > best_score:
            best_score = score
            best_skill = skill
```

For `return-amazon-order`, the `trigger-keywords` from YAML frontmatter: `[return, send back, refund, amazon]`

- `"return"` → present in `"return the..."` → score += 1
- `"send back"` → not present → 0
- `"refund"` → not present → 0
- `"amazon"` → present in `"...on amazon"` → score += 1

**Final score: 2** — highest among all skills.

#### Parameter extraction

```python
def extract_params(prompt: str, skill: Skill) -> Dict[str, str]:
    # Strategy 1: quoted strings → none found
    quoted = re.findall(r'"([^"]+)"', prompt)   # empty
    # Strategy 2: single required param → strip trigger keywords + fillers
    remaining = "Return the blue headphones I bought on Amazon"
    remaining = re.sub("return", "", remaining, flags=re.IGNORECASE)
    # → " the blue headphones I bought on Amazon"
    remaining = re.sub("amazon", "", remaining, flags=re.IGNORECASE)
    # → " the blue headphones I bought on "
    # Remove fillers → "blue headphones bought"
    params["item"] = "blue headphones bought"
```

#### Skill expansion

```python
def expand(self, skill_name: str, params: Dict[str, str]) -> Optional[str]:
    skill = self._skills["return-amazon-order"]
    text = skill.steps_text   # The ## Steps section from the .md file
    # Single-pass regex replacement:
    text = re.sub(r"\{\{(\w+)\}\}", _replace_placeholder, text)
    # All "{{item}}" → "blue headphones bought"
    return text
```

---

### Phase 2B — Screen Description

**Files:** `coordinator.py`, `capture.py`

```
describe_screen()
    │
    ├── ScreenCapture.capture_b64()
    │     ├── screencapture -x /tmp/xxx.png     (macOS native)
    │     ├── PIL.resize(1024, 768, LANCZOS)    (downscale for vision model)
    │     ├── Convert RGBA/P → RGB              (JPEG compat)
    │     ├── _enforce_size_limit()             (quality 85→70→50, resize 75% if >4.5MB)
    │     └── base64.b64encode(jpeg_bytes)
    │
    ├── _load_prompt("describe_screen.md")
    │
    └── _call_vision_model(prompt, screenshot_b64)
          ├── If anthropic: AsyncAnthropic → messages.create()
          │     model="claude-sonnet-4-20250514", max_tokens=1024
          │     Retry on 429/529: 1s, 2s, 4s, 8s backoff
          │
          └── If local: POST {vision_server_url}/v1/chat/completions
                OpenAI-compatible payload with image_url base64 data URI

    Returns: "macOS desktop with Finder in the foreground, Safari dock icon visible"
```

### Screenshot pipeline detail

```python
def capture(self) -> bytes:
    # 1. Create temp PNG file
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = f.name
    try:
        # 2. macOS screencapture (silent, -x = no sound)
        subprocess.run(["screencapture", "-x", tmp_path], check=True)
        # 3. Resize to 1024x768 via Lanczos
        img = Image.open(tmp_path)
        img = img.resize((1024, 768), Image.LANCZOS)
        # 4. Convert color modes for JPEG
        if img.mode in ("RGBA", "P", "LA", "CMYK", "I"):
            img = img.convert("RGB")
        # 5. Enforce 4.5MB limit
        return self._enforce_size_limit(img)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
```

---

### Phase 2C — Planning

**Files:** `planner.py`, `planner/prompts/plan_from_prompt.md`

```
ActionPlannerImpl.plan(goal, screen_desc, skill_context)
    │
    ├── _build_plan_prompt()
    │     ├── Load plan_from_prompt.md template
    │     ├── Replace {{goal}} → "Return the blue headphones I bought on Amazon"
    │     ├── Replace {{screen_description}} → vision model output
    │     └── Replace {{skill_context}} → expanded return_amazon_order.md steps
    │
    ├── _call_llm(prompt)
    │     ├── anthropic.AsyncAnthropic(api_key=...)
    │     ├── messages.create(model="claude-sonnet-4-20250514", max_tokens=4096)
    │     ├── Retry on 429/529: 1s, 2s, 4s, 8s backoff (5 attempts total)
    │     └── Returns {"content": "```json{...}```", "usage": {...}}
    │
    ├── _parse_plan_response(response, goal)
    │     ├── Strip ```json fences
    │     ├── json.loads()
    │     ├── ActionStep.from_dict() for each step
    │     │     └── _ACTION_ALIASES: "key_press"→"press_key", "navigate"→"open_url"
    │     └── Returns ActionPlan(steps=[...], goal=goal)
    │
    └── plan.validate()
          └── Every non-terminal step MUST have non-empty verify field
```

#### The assembled LLM prompt (paraphrased)

```
You are a macOS desktop automation planner. Given a user goal, produce a JSON action plan.

## User Goal
Return the blue headphones I bought on Amazon

## Current Screen State
[description from vision model]

## Skill Context (if available)
1. Open Safari and navigate to https://www.amazon.com/gp/your-account/order-history
   - verify: Amazon orders page or login page visible
2. If login page appears, wait for user to sign in
   ...
4. Type "blue headphones bought" and press Enter
   ...

## Available Actions
- activate_app, click, type_text, press_key, open_url, ...

## CRITICAL RULES
1. Every step MUST have a non-empty "verify" field...

## Response Format
Respond with ONLY valid JSON ...
```

#### Typical resulting ActionPlan

```python
ActionPlan(
  goal="Return the blue headphones I bought on Amazon",
  steps=[
    ActionStep(action="activate_app",  params={"app_name":"Safari"},
               verify="Safari is the frontmost application",
               on_fail="retry_different"),

    ActionStep(action="open_url",
               params={"url":"https://www.amazon.com/gp/your-account/order-history"},
               verify="Amazon orders page or login page visible",
               on_fail="retry_different"),

    ActionStep(action="wait_for_user",
               params={"message":"Please sign in to Amazon if prompted"},
               verify="", on_fail="abort"),

    ActionStep(action="click",
               params={"element":"search bar or filter field"},
               verify="Search bar is focused",
               on_fail="retry_different"),

    ActionStep(action="type_text",
               params={"text":"blue headphones"},
               verify="Search results visible",
               on_fail="retry_different"),

    ActionStep(action="press_key",
               params={"keys":["return"]},
               verify="Search results filtered for blue headphones",
               on_fail="retry_different"),

    ActionStep(action="click",
               params={"element":"Return or Replace Items button"},
               verify="Return options page visible",
               on_fail="replan"),

    ActionStep(action="done", params={}, verify="", on_fail="abort"),
  ]
)
```

---

## Phase 3 — Step Execution Loop

**File:** `orchestrator/agent.py`, lines 115–196

```
for each step in plan.steps:
    │
    ├── Guard: iterations < max_iterations (20)?
    │
    ├── context_monitor.update_cheap() (if available)
    │
    ├── _execute_step(i, step, history, goal, plan)
    │     │
    │     ├── [if click] screenshot_diff.capture_before()
    │     │
    │     ├── _dispatch_action(step) ──────────────────────────────────┐
    │     │     ├── activate_app → actuator.activate_app(app_name)    │
    │     │     ├── click+element → _find_element() → actuator.click()│
    │     │     ├── click+coords → actuator.click(x, y)               │
    │     │     ├── type_text → actuator.type_text(text)              │
    │     │     ├── press_key → actuator.press_key(keys)              │
    │     │     └── open_url → actuator.open_url(url)                 │
    │     │                                                            │
    │     ├── [if click] screenshot_diff.region_changed()?            │
    │     │     └── No change → mark actuator_result as failed        │
    │     │                                                            │
    │     ├── [action_delay for activate_app/open_url/quit_app]       │
    │     │                                                            │
    │     └── StepVerifier.verify(step, actuator_result)              │
    │           ├── Tier 1: Hammerspoon state (~50ms)                 │
    │           └── Tier 2: Vision screenshot (2-5s)                  │
    │                                                                  │
    ├── step.action == "done"? → break                                │
    ├── step.action == "wait_for_user"? → continue                    │
    │                                                                  │
    └── !result.success? → _handle_failure()                          │
          ├── retry_different → _vary_strategy(), retry up to 3x      │
          ├── replan → _replan_and_continue()                         │
          └── abort → return failure                                   │
```

### Tracing Step 0: `activate_app({"app_name": "Safari"})`

**`_dispatch_action()`** routes to:
```python
result = self.actuator.activate_app(params.get("app_name", ""))
# → HammerspoonBridgeActuator.activate_app("Safari")
```

**`HammerspoonBridgeActuator.activate_app()`** (`bridge_actuator.py`):
```python
def activate_app(self, app_name: str) -> Dict[str, Any]:
    result = self._action("activate_app", {"appName": "Safari"})
    return ActuatorResult(success=result.get("success", False), ...).to_dict()
```

**`_action()`** sends HTTP:
```python
def _action(self, action: str, params=None) -> Dict[str, Any]:
    payload = {"action": "activate_app", "params": {"appName": "Safari"}}
    with httpx.Client(timeout=15) as client:
        resp = client.post("http://localhost:27741/action", json=payload)
        resp.raise_for_status()
        return resp.json()
    # Returns {"success": True, "result": "Safari activated"}
```

```
Python                     Hammerspoon Bridge              macOS
  │                              │                           │
  │ POST /action                 │                           │
  │ {"action":"activate_app",    │                           │
  │  "params":{"appName":        │                           │
  │           "Safari"}}         │                           │
  │─────────────────────────────▶│                           │
  │                              │ hs.application.          │
  │                              │   launchOrFocus("Safari") │
  │                              │──────────────────────────▶│
  │                              │                           │ Safari
  │                              │◀──────────────────────────│ activated
  │ {"success": true}            │                           │
  │◀─────────────────────────────│                           │
```

After action, `action_delay` of 0.5s allows the app to settle:
```python
if step.action in ("activate_app", "open_url", "quit_app"):
    await asyncio.sleep(self.config.action_delay)   # 0.5 seconds
```

---

## Phase 4 — Verification

**File:** `orchestrator/verifier.py`

### `StepVerifier.verify()` — 3-tier system

```
StepVerifier.verify(step, actuator_result)
    │
    ├── step.verify empty? → use actuator_result.success directly
    │
    ├── TIER 1: Hammerspoon State Query (~50ms)
    │     │
    │     ├── actuator.get_state()
    │     │     GET http://localhost:27741/state
    │     │     → {"app_name":"Safari", "window_title":"Amazon.com", ...}
    │     │
    │     ├── activate_app? → check state["app_name"] matches
    │     │     "Safari" contains "safari"? → YES
    │     │     Return: (True, "Frontmost app is 'Safari'")
    │     │
    │     ├── click/type_text? → Return: None (INCONCLUSIVE)
    │     │     (clicks don't change app name — can't verify this way)
    │     │
    │     └── open_url? → check window_title for URL keywords
    │
    │   Tier 1 conclusive? → Return StepResult immediately
    │
    └── TIER 2: Vision Screenshot Verification (2-5s)
          │
          ├── coordinator.verify_condition(step.verify)
          │     ├── capture_b64() → fresh screenshot
          │     ├── Load verify_condition.md template
          │     │     "Is '{{condition}}' true? Answer YES or NO."
          │     ├── _call_vision_model(prompt, screenshot)
          │     └── response.startswith("yes")? → True/False
          │
          ├── Save screenshot to logs/runs/{run_id}/
          │
          └── Return StepResult(
                success=...,
                verification_method="vision" or "hammerspoon_state",
                evidence="Vision confirms: ..." or "Frontmost app is ...",
                screenshot_path="..."
              )
```

### Example: Tier 1 for `activate_app("Safari")`

```python
def _verify_tier1(self, step, actuator) -> Optional[Tuple[bool, str]]:
    state = actuator.get_state()
    # → {"app_name": "Safari", "app_bundle": "com.apple.Safari", ...}

    if step.action == "activate_app" and step.params.get("app_name"):
        expected_app = "Safari"
        actual_app = state.get("app_name", "")   # "Safari"
        if "safari" in "safari":   # case-insensitive match
            return (True, "Frontmost app is 'Safari' (expected 'Safari')")
```

**Result:** `(True, evidence)` — Tier 1 is conclusive. **No vision call needed.** ~52ms.

### Example: Tier 2 for `click({"element": "search bar"})`

Tier 1 returns `None` for click actions (inconclusive). Falls through to Tier 2:

```python
async def _verify_tier2(self, step, coordinator) -> Tuple[bool, str]:
    result = await coordinator.verify_condition(step.verify)
    # step.verify = "Search bar is focused"
    # Takes fresh screenshot, sends to vision model with YES/NO prompt
    if result:
        return (True, "Vision confirms: Search bar is focused")
    else:
        return (False, "Vision denies: Search bar is focused")
```

**Conservative parsing:** Only `"YES"` prefix → True. Everything else (ambiguous, partial) → False.

---

## Phase 5 — Click Step with Element Finding

**Files:** `agent.py`, `coordinator.py`

For step: `click({"element": "Return or Replace Items button"})`

```
_dispatch_action(step)
    │
    ├── "element" in params?
    │     YES → _find_element("Return or Replace Items button")
    │
    ▼
_find_element(description)
    │
    ├── grounding_router available?
    │     └── grounding_router.find_element(description) → (x, y, strategy)
    │
    └── Otherwise: coordinator.find_element(description)
          │
          ├── FAST PATH: Accessibility API
          │     accessibility.find_element_by_description(description)
          │     → if found and has center coords → return immediately
          │
          └── SLOW PATH: Vision Model
                │
                ├── capture_b64() → screenshot
                ├── Load find_element.md prompt
                │     "Find {{element_description}} on screen"
                │
                ├── If grounding model configured:
                │     _call_grounding_model() → parse coordinates
                │
                └── Otherwise:
                      _call_vision_model() → parse coordinates
                      │
                      ├── Model response: "FOUND: x=523, y=412"
                      │
                      ├── _parse_coordinates(response)
                      │     regex: r"FOUND:\s*x=([0-9.]+),\s*y=([0-9.]+)"
                      │
                      └── _convert_coordinates(raw_x, raw_y, model, w, h)
                            │
                            ├── qwen3-vl (normalized 0-1000):
                            │     x = int(523/1000 * 1024) = 535
                            │     y = int(412/1000 * 768)  = 316
                            │
                            ├── molmo (normalized 0-1):
                            │     x = int(0.512 * 1024) = 524
                            │     y = int(0.536 * 768)  = 412
                            │
                            └── claude (pixel): x=523, y=412 directly
```

Then `actuator.click(535, 316)` is called via the bridge:
```python
POST http://localhost:27741/action
{"action": "click", "params": {"x": 535, "y": 316}}
```

### Screenshot diff verification for clicks

```python
# Before action:
if self.screenshot_diff and step.action == "click":
    self.screenshot_diff.capture_before()

# ... action executes ...

# After action:
if self.screenshot_diff and step.action == "click" and actuator_result.get("success"):
    await asyncio.sleep(0.3)   # brief wait for UI to update
    if not self.screenshot_diff.region_changed(click_x, click_y):
        actuator_result["success"] = False
        actuator_result["error"] = "Click had no visible effect (screenshot unchanged)"
```

This catches phantom clicks (clicking empty space) before the slower vision verification runs.

---

## Phase 6 — Failure Handling and Replanning

**File:** `orchestrator/agent.py`

```
Step fails
    │
    ├── on_fail == "retry_different"?
    │     │
    │     └── while retry_count < max_retries (3):
    │           ├── _vary_strategy(step, result) → modified params
    │           ├── _execute_step() with modified params
    │           └── success? → return result
    │
    │     Retries exhausted → signal REPLAN (return None)
    │
    ├── on_fail == "replan"?
    │     └── signal REPLAN immediately (return None)
    │
    └── on_fail == "abort"?
          └── return failure result (terminates execution)
```

### Strategy variation by action type

```
┌──────────────┬───────────┬─────────────────────────────────────────────┐
│ Action       │ Attempt   │ Strategy                                    │
├──────────────┼───────────┼─────────────────────────────────────────────┤
│ click        │ 1         │ refine_element_query (add hint text)        │
│              │ 2         │ keyboard_fallback                           │
│              │ 3+        │ fresh_screenshot_retry                      │
├──────────────┼───────────┼─────────────────────────────────────────────┤
│ type_text    │ 1         │ click_to_focus_first                        │
│              │ 2         │ slow_type_retry (individual keystrokes)     │
├──────────────┼───────────┼─────────────────────────────────────────────┤
│ press_key    │ 1         │ delayed_key_press (0.5s pre-delay)          │
│              │ 2+        │ extended_delay_key_press (increasing delay) │
├──────────────┼───────────┼─────────────────────────────────────────────┤
│ activate_app │ 1         │ quit_and_relaunch                           │
│              │ 2+        │ spotlight_launch                            │
└──────────────┴───────────┴─────────────────────────────────────────────┘
```

### Replan flow

```
_replan_and_continue()
    │
    ├── coordinator.describe_screen() → fresh screen state
    │
    ├── Collect all retry_strategies_used from step_results
    │
    ├── planner.replan(goal, screen_desc, history, retry_strategies)
    │     │
    │     ├── _build_replan_prompt()
    │     │     ├── Load replan_from_state.md template
    │     │     ├── {{goal}} → original goal
    │     │     ├── {{screen_description}} → current screen
    │     │     ├── {{history}} →
    │     │     │     "Step 0: activate_app → SUCCESS: Frontmost app is Safari"
    │     │     │     "Step 1: open_url → SUCCESS: Amazon page visible"
    │     │     │     "Step 2: click → FAILED: Element not found"
    │     │     └── {{retry_strategies}} → "refine_element_query, keyboard_fallback"
    │     │
    │     ├── CRITICAL instruction in template:
    │     │     "You MUST try a DIFFERENT approach. Do NOT repeat failed actions."
    │     │
    │     ├── _call_llm() → Claude API → new JSON plan
    │     └── _parse_plan_response() → new ActionPlan
    │
    └── Execute new plan steps (no recursive replan)
          └── Returns ExecutionResult(success=last_step.success, ...)
```

---

## Phase 7 — Result Return

**Files:** `orchestrator/agent.py`, `__main__.py`

### Success path

```python
return ExecutionResult(
    success=True,
    message="Task completed",
    steps=step_results,       # List[StepResult] with evidence
    total_duration_ms=duration,
    iterations=iterations,
    goal="Return the blue headphones I bought on Amazon",
    run_id=self.logger.run_id,
)
```

### CLI output

```python
# In run_agent():
if result.success:
    print(f"\n[SUCCESS] Task completed")
    print(f"\nActions executed:")
    for i, sr in enumerate(result.steps, 1):
        status = "OK" if sr.success else "FAIL"
        print(f"  {i}. [{status}] {sr.step.action}: {sr.step.params}")
    print(f"\nCompleted in {result.iterations} iteration(s)")
    return 0   # → sys.exit(0)
else:
    print(f"\n[FAILED] {result.message}")
    # ... print errors ...
    return 1   # → sys.exit(1)
```

### Event log output

Every action, verification, and decision is logged to:
```
logs/runs/{run_id}/
├── trace.md          # Human-readable step-by-step narrative
├── events.jsonl      # Machine-readable structured events
└── screenshots/      # Per-step screenshots from verification
```

---

## Complete Data Flow Summary

```
CLI: "Return the blue headphones I bought on Amazon"
    │
    ▼ __main__.py:main() [line 230]
    │   parse_args() → args.prompt
    │   load_config() → AgentConfig (pydantic settings, AGENT_ env vars)
    │   apply_cli_overrides(config, args)
    │   asyncio.run(run_agent(prompt, config))
    │
    ▼ __main__.py:run_agent() [line 78]
    │   ActionPlannerImpl(config)         → planner/prompts/ templates
    │   SkillRegistryImpl()               → loads all skills/library/*.md
    │   ScreenCoordinatorImpl(config)     → ScreenCapture(1024x768), validates model
    │   create_actuator(config)           → HammerspoonBridgeActuator (priority 1)
    │   AutomationAgent(planner, registry, coordinator, actuator, config)
    │     └─ StepVerifier(actuator, coordinator, logger)
    │
    ▼ AutomationAgent.execute(goal) [agent.py:42]
    │
    ├── SKILL MATCH
    │   SkillRegistryImpl.match(goal)     [registry.py:71]
    │     └─ match_skill(prompt, skills)  [matcher.py:9]
    │          score "return"=1, "amazon"=1 → best_score=2
    │          → return-amazon-order skill matched
    │     └─ extract_params(prompt, skill) [matcher.py:50]
    │          no quoted strings → strip keywords + fillers
    │          → {"item": "blue headphones bought"}
    │     └─ SkillRegistryImpl.expand("return-amazon-order", params) [registry.py:95]
    │          re.sub("{{item}}", "blue headphones bought", steps_text)
    │     Returns: {"skill_name":"return-amazon-order", "expanded_steps":..., "params":{...}}
    │
    ├── SCREEN DESCRIPTION
    │   ScreenCoordinatorImpl.describe_screen() [coordinator.py:460]
    │     └─ ScreenCapture.capture_b64()  [capture.py:99]
    │          screencapture -x → PIL.resize(1024,768) → JPEG → base64
    │     └─ _call_vision_model(describe_prompt, screenshot_b64) [coordinator.py:186]
    │          → Anthropic API or local OpenAI-compatible API
    │          → natural language screen description
    │
    ├── PLANNING
    │   ActionPlannerImpl.plan(goal, screen_desc, skill_context) [planner.py:26]
    │     └─ _build_plan_prompt() → substitutes {{goal}}, {{screen_description}}, {{skill_context}}
    │     └─ _call_llm(prompt) → Claude API, retry on 429/529
    │     └─ _parse_plan_response() → strip fences, json.loads, ActionStep.from_dict()
    │     └─ plan.validate() → every non-terminal step has verify
    │
    └── STEP EXECUTION LOOP [agent.py:115]
        │
        ├── For each ActionStep:
        │     _execute_step() [agent.py:213]
        │       ├── [click] screenshot_diff.capture_before()
        │       ├── _dispatch_action(step) [agent.py:315]
        │       │     activate_app → actuator.activate_app()
        │       │     click+element → _find_element() → coordinator.find_element()
        │       │                      → accessibility or vision → (x,y)
        │       │                   → actuator.click(x, y)
        │       │     type_text → actuator.type_text()
        │       │     press_key → actuator.press_key()
        │       │     open_url → actuator.open_url()
        │       │
        │       │     Bridge actuator: POST http://localhost:27741/action
        │       │     Fallback: cliclick/Quartz/osascript
        │       │
        │       ├── [click] screenshot_diff.region_changed()
        │       ├── [activate_app/open_url] asyncio.sleep(action_delay)
        │       └── StepVerifier.verify(step, actuator_result) [verifier.py:36]
        │               ├── Tier 1: actuator.get_state() → app/window check
        │               └── Tier 2: coordinator.verify_condition() → vision YES/NO
        │
        ├── On failure:
        │     _handle_failure() [agent.py:386]
        │       retry_different → _vary_strategy() → up to 3 retries
        │       replan → _replan_and_continue()
        │       abort → return failure
        │
        └── On replan:
              _replan_and_continue() [agent.py:521]
                describe_screen() → fresh state
                planner.replan(goal, history, retry_strategies)
                  "MUST try DIFFERENT approach"
                Execute new plan (no recursive replan)

    ▼ run_agent(): print [SUCCESS/FAILED], return exit code
    ▼ main() → sys.exit(exit_code)
```

---

## Key Data Structures

### `ActionStep` (`shared_models.py`)
```python
@dataclass
class ActionStep:
    action: str        # "click" | "type_text" | "press_key" | "open_url" |
                       # "activate_app" | "quit_app" | "observe" | "wait_for_user" | "done"
    params: Dict[str, Any]   # {"app_name":"Safari"} | {"element":"..."} | {"x":535,"y":316}
    verify: str        # MANDATORY: "Safari is the frontmost application"
    on_fail: str       # "retry_different" | "replan" | "abort" | "wait_for_user"
    max_retries: int   # default 3
```

### `ActionPlan` (`shared_models.py`)
```python
@dataclass
class ActionPlan:
    steps: List[ActionStep]
    goal: str
    skill_name: Optional[str]
    raw_llm_response: Optional[str]
    planning_duration_ms: int
    token_usage: Optional[Dict[str, int]]   # {"input_tokens": N, "output_tokens": M}
```

### `StepResult` (`shared_models.py`)
```python
@dataclass
class StepResult:
    step: ActionStep
    success: bool
    verification_method: str    # "accessibility" | "hammerspoon_state" | "vision" | "both"
    evidence: str               # MANDATORY — what was observed
    error: Optional[str]
    duration_ms: int
    screenshot_path: Optional[str]
    retry_count: int
    retry_strategies_used: List[str]
    timestamp: datetime
```

### `ExecutionResult` (`shared_models.py`)
```python
@dataclass
class ExecutionResult:
    success: bool
    message: str
    steps: List[StepResult]
    error: Optional[str]
    total_duration_ms: int
    iterations: int       # total steps + retries executed
    goal: str
    run_id: str           # UUID for correlating JSONL log entries
```

### `Skill` (`skills/models.py`)
```python
@dataclass
class Skill:
    name: str                         # "return-amazon-order"
    description: str                  # "Return an item or package on Amazon"
    trigger_keywords: List[str]       # ["return", "send back", "refund", "amazon"]
    parameters: Dict[str, SkillParam] # {"item": SkillParam(required=True, ...)}
    requires: SkillRequirements       # SkillRequirements(apps=["Safari"], os="darwin")
    success_condition: str            # "Return confirmation visible"
    max_retries: int                  # 3
    steps_text: str                   # Markdown ## Steps section
    error_recovery_text: str          # Markdown ## Error Recovery section
    notes_text: str                   # Markdown ## Notes section
    raw_content: str                  # Full .md file
```

---

## Essential Files Reference

| File | Role | Key Functions |
|---|---|---|
| `__main__.py` | CLI entry, component wiring | `main()`, `run_agent()`, `apply_cli_overrides()` |
| `orchestrator/agent.py` | Main orchestration loop | `execute()`, `_execute_step()`, `_dispatch_action()`, `_handle_failure()`, `_replan_and_continue()` |
| `orchestrator/verifier.py` | 3-tier verification | `verify()`, `_verify_tier1()`, `_verify_tier2()` |
| `skills/registry.py` | Skill loading and expansion | `match()`, `expand()`, `load_from_directory()` |
| `skills/matcher.py` | Keyword scoring + param extraction | `match_skill()`, `extract_params()` |
| `skills/loader.py` | YAML+Markdown parser | `parse_skill_file()`, `_split_frontmatter()`, `_extract_section()` |
| `skills/models.py` | Skill data model | `Skill`, `SkillParam`, `SkillRequirements` |
| `skills/library/return_amazon_order.md` | Skill template | YAML frontmatter + `{{item}}` placeholder |
| `planner/planner.py` | LLM-based planning | `plan()`, `replan()`, `_build_plan_prompt()`, `_call_llm()`, `_parse_plan_response()` |
| `planner/prompts/plan_from_prompt.md` | Planning prompt template | `{{goal}}`, `{{screen_description}}`, `{{skill_context}}` |
| `planner/prompts/replan_from_state.md` | Replan prompt template | `{{history}}`, `{{retry_strategies}}` |
| `actuator/__init__.py` | Actuator factory | `create_actuator()`: bridge → CLI → AppleScript |
| `actuator/bridge_actuator.py` | HTTP bridge actuator | `click()`, `type_text()`, `press_key()`, `activate_app()`, `get_state()` |
| `actuator/actuator.py` | hs CLI actuator | `_execute_lua()`, `_render_template()` |
| `vision/coordinator.py` | Vision model coordination | `find_element()`, `describe_screen()`, `verify_condition()`, `_convert_coordinates()` |
| `vision/capture.py` | Screenshot capture | `capture()`, `capture_b64()`, `_enforce_size_limit()` |
| `shared_models.py` | Core data models | `ActionStep`, `ActionPlan`, `StepResult`, `ExecutionResult`, `_ACTION_ALIASES` |
| `protocols.py` | Component interfaces | `ActionPlanner`, `ScreenCoordinator`, `Actuator`, `SkillRegistry`, `Verifier` |
| `config.py` | Configuration | `AgentConfig`, `load_config()`, `ModelProvider` |

---

**All source files referenced are under `src/automation_agent/`.**
