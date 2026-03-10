# macOS Desktop Automation Agent

Vision-guided desktop automation for macOS. Give the agent a natural-language goal, it can match an optional skill, plan steps, execute them on the desktop, and verify progress after each action.

## Current Reality

- The runtime is now generic. There is no bespoke restaurant workflow path.
- Planning still uses Anthropic Claude today. The project is not fully local yet.
- The only active actuator backend is `AppleScriptActuator`. There is no live Hammerspoon backend in the current code.
- Validated on March 10, 2026: `pytest -q -m 'not e2e'` -> `1198 passed, 3 skipped, 5 deselected`.

## Architecture

```text
user prompt
  -> optional skill match
  -> screen description
  -> plan generation
  -> execute step
  -> verify step
  -> replan on failure
```

Core components:

| Component | Role |
|-----------|------|
| Planner | Generates structured action plans from the prompt |
| Vision coordinator | Screenshots, descriptions, grounding, and vision verification |
| Actuator | Executes app activation, typing, keypresses, URL opens, clicks, and state queries |
| Skills | Optional markdown priors under `src/automation_agent/skills/library/` |
| Orchestrator | Runs the execute / verify / replan loop |

## Requirements

- macOS 11.0 or later
- Python 3.9+
- Accessibility permission for the terminal or IDE running the agent
- Screen Recording permission for screenshot-based perception
- `ANTHROPIC_API_KEY` or `AGENT_ANTHROPIC_API_KEY`
- Optional: an OpenAI-compatible local vision server if you want local vision instead of Anthropic vision

## Quick Start

Install the repo and the Anthropic extra:

```bash
pip install -e ".[dev,anthropic]"
cp .env.example .env
```

Grant macOS permissions to the terminal app you are using:

- Privacy & Security -> Accessibility
- Privacy & Security -> Screen Recording

Set the Anthropic key:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Fastest working path: use Claude for both planning and vision.

```bash
automation-agent --provider anthropic "Open Safari and go to news.ycombinator.com"
```

If you already have a local OpenAI-compatible vision server, point the agent at it. Planning still uses Anthropic in this mode.

```bash
AGENT_MODEL_PROVIDER=local \
AGENT_VISION_SERVER_URL=http://localhost:8091 \
AGENT_VISION_MODEL=mlx-community/Molmo-7B-D-0924-4bit \
automation-agent "Open Safari and search for weather in San Francisco"
```

Dry run:

```bash
automation-agent --dry-run "Return my Amazon order"
```

## Runtime Notes

- `AppleScriptActuator` is the current action backend.
- App activation, URL opening, keypresses, typing, and window state queries go through `osascript`.
- Coordinate clicks go through `pyautogui` inside the actuator.
- If `AGENT_USE_ACCESSIBILITY=true`, the agent can use the macOS Accessibility API for faster structured grounding and verification.
- `AGENT_MODEL_PROVIDER` currently changes the vision backend in practice. The planner still calls Anthropic directly.

## Skills

The skill system remains, but only as optional priors. The agent can plan without any skill match.

Current bundled skill templates:

- `amazon_search.md`
- `google_search.md`
- `open_app_and_navigate.md`
- `return_amazon_order.md`
- `send_imessage.md`
- `restaurant_google.md`
- `restaurant_opentable.md`
- `restaurant_yelp.md`

The restaurant templates are still available as skill hints, but they are not backed by special-case runtime code anymore.

## Verification

Verification is layered and depends on what backends are enabled:

1. Accessibility state checks when the Accessibility bridge is available
2. Actuator state checks via `get_state()`
3. Vision verification using the current vision backend

Every non-terminal action step is expected to carry a postcondition.

## Logs and Debugging

Each run writes structured artifacts under `logs/runs/<run_id>/`:

```text
logs/runs/<run_id>/
  events.jsonl
  trace.md
  screenshots/
  debug/
```

- `events.jsonl` is the machine-readable event stream
- `trace.md` is the readable execution trace
- `screenshots/` stores saved run screenshots
- `debug/find_*.jpg` stores click-target overlays for grounding debug

The rolling process log lives at `logs/automation_agent.log`.

## Useful Commands

```bash
# Run the non-E2E suite
pytest -q -m "not e2e"

# Check the actuator directly
python -m automation_agent.actuator status
python -m automation_agent.actuator state

# Show the live overlay during a run
automation-agent --status-ui overlay "Open Safari"
```

## More Docs

- Current setup: `docs/automation.md`
- Current runbook: `docs/QUICKSTART.md`
- Current repo status: `IMPLEMENTATION_STATUS.md`
- Deep architecture walkthrough with stale sections clearly labeled: `docs/LIFE_OF_A_PROMPT.md`
