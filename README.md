# macOS Desktop Automation Agent

Vision-guided desktop automation for macOS. Give the agent a natural-language goal, it can match an optional skill, plan steps, execute them on the desktop, and verify progress after each action.

## Current Reality

- The runtime is now generic. There is no bespoke restaurant workflow path.
- Planning still uses Anthropic Claude today. The project is not fully local yet.
- The only active actuator backend is `AppleScriptActuator`. There is no live Hammerspoon backend in the current code.

## Architecture

```text
user prompt
  -> site-entity extraction (deterministic pre-filter for site-specific skills)
  -> optional skill match (embedding retrieval -> LLM re-rank -> keyword fallback)
  -> screen description (world-state document with cumulative context)
  -> plan generation
  -> for each step:
      -> lookahead prediction (destructive steps only, opt-in)
      -> destructive action confirmation (if classified as destructive)
      -> execute step
      -> verify step (3-tier: AX state -> vision -> fallback)
      -> infeasibility detection (frustration score -> planner advisory)
      -> replan on failure
```

Core components:

| Component | Role |
|-----------|------|
| Planner | Generates structured action plans from the prompt; assesses infeasibility |
| Vision coordinator | Screenshots, descriptions, grounding (SoM + dual-res), verification, and lookahead prediction |
| Actuator | Executes app activation, typing, keypresses, URL opens, clicks, and state queries |
| Skills | Optional markdown priors under `src/automation_agent/skills/library/`; embedding-based retrieval |
| Orchestrator | Runs the execute / verify / replan loop with infeasibility detection and destructive action gates |
| Context Monitor | Maintains evolving world-state document with milestones, obstacles, and state diffs |

## Safety Features

- **Infeasibility detection**: Tracks a frustration score (same-state repeats, identical action retries, replans). When thresholds are exceeded, the planner is consulted on whether the task is achievable. Hard-aborts after configurable advisory check limit.
- **Destructive action confirmation**: Actions classified as destructive (via planner flag, keyword match, or type-into-critical-field) trigger a user confirmation prompt before execution. Three modes: `always`, `smart` (default), `never`.
- **Lookahead prediction**: For destructive steps, the VLM predicts the action outcome before execution. If the prediction indicates failure, the step is skipped and the agent replans. Off by default (`AGENT_LOOKAHEAD_ENABLED=true`).
- **Set-of-Mark prompting**: Numbered bounding boxes drawn on screenshots using AX element positions, letting the VLM pick a label instead of predicting raw coordinates. Off by default (`AGENT_SOM_ENABLED=true`).
- **Dual-resolution grounding**: Sends both full-page overview and zoomed crop to the VLM for high-DPI screens. Off by default (`AGENT_DUAL_RESOLUTION_GROUNDING=true`).
- **Embedding-based skill retrieval**: Semantic matching via local embeddings (fastembed + BAAI/bge-small-en-v1.5), with conditional LLM re-rank. Off by default (`AGENT_SKILL_EMBEDDING_ENABLED=true`).
- **World-state document**: Evolving desktop context with cumulative milestones, obstacles, state diffs, and semantic page labels fed to the planner.

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

For embedding-based skill retrieval (optional):

```bash
pip install -e ".[embeddings]"
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
- `buy_on_target.md`
- `google_search.md`
- `restaurant_google.md`
- `restaurant_opentable.md`
- `restaurant_yelp.md`
- `return_amazon_order.md`
- `return_target_order.md`
- `return_walmart_order.md`
- `send_imessage.md`

Skills with a `site` metadata field (e.g., `site: target`) participate in site-entity routing: when the user prompt mentions a specific site ("on target", "from amazon"), only skills matching that site are considered. Skills without a `site` field are treated as generic and are not filtered.

## Verification

Verification is layered and depends on what backends are enabled:

1. **Tier 0**: Accessibility state checks when the Accessibility bridge is available
2. **Tier 1**: Actuator state checks via `get_state()` — includes URL domain verification and scroll verification
3. **Tier 2**: Vision verification using the current vision backend

Scroll actions use a dedicated tiered chain: JS `scrollY` delta (Tier S1) > screenshot pixel-diff (Tier S2) > actuator success fallback (Tier S3).

Domain verification: when a skill specifies a `site` field, `open_url` steps have a domain constraint injected into their verify text (e.g., "AND browser domain is target.com"). The verifier checks that the browser URL domain matches via suffix comparison.

Every non-terminal action step is expected to carry a postcondition.

When verification repeatedly fails, the infeasibility detector tracks frustration signals and can abort early rather than exhausting the full iteration budget.

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

- Current setup: `docs/guides/automation.md`
- Current runbook: `docs/QUICKSTART.md`
- Current repo status: `IMPLEMENTATION_STATUS.md`
- Deep architecture walkthrough with stale sections clearly labeled: `docs/guides/LIFE_OF_A_PROMPT.md`
