# macOS Desktop Automation Agent

Vision-guided desktop automation for macOS. Give the agent a natural-language goal, it can match an optional skill, plan steps, execute them on the desktop, and verify progress after each action.

## Quick Run

```bash
# With Gemini (recommended — fast planning + vision)
AGENT_MODEL_PROVIDER=gemini AGENT_GEMINI_API_KEY=... \
  .venv/bin/python -m automation_agent --status-ui overlay --verbose-overlay "return my listerine amazon order"

# With OpenAI GPT
AGENT_MODEL_PROVIDER=openai AGENT_OPENAI_API_KEY=... \
  .venv/bin/python -m automation_agent --status-ui overlay "Open Calculator and compute 42 times 7"

# With local Molmo vision + Gemini planning (hybrid)
AGENT_PLANNING_MODEL=gemini:gemini-2.5-flash \
AGENT_GROUNDING_MODEL_PROVIDER=local:mlx-community/Molmo-7B-D-0924-3bit \
  .venv/bin/python -m automation_agent --status-ui overlay "search amazon for wireless mouse"

# With Claude
AGENT_MODEL_PROVIDER=anthropic AGENT_ANTHROPIC_API_KEY=... \
  .venv/bin/python -m automation_agent --status-ui overlay "Open Safari and go to news.ycombinator.com"

# Dry run (no execution)
.venv/bin/python -m automation_agent --dry-run "Return my Amazon order"
```

After each run, a detailed report is saved to `logs/runs/{run_id}/report.md` with:
- LLM calls (which model, prompt summary, response summary, duration)
- Step timeline (precondition, action, verify tier, pre/post app state)
- Plans generated (initial + replans with full step details)
- Detailed step narrative (element finding, verification, retries)

## Current Reality

- **Multi-provider**: Supports Gemini, OpenAI/GPT, Anthropic/Claude, and local Molmo — configurable per step
- **Per-step model routing**: Different LLM for planning vs grounding vs verification (e.g., `AGENT_PLANNING_MODEL=gemini:gemini-2.5-flash`)
- **Browser-agnostic**: No hardcoded Safari/Chrome — uses system default browser, treats all browsers as interchangeable
- **Precondition/Action/Verify**: Each step has three phases, chained (step N's verify = step N+1's precondition)
- The only active actuator backend is `AppleScriptActuator`

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
      -> precondition check (assert pre-state before action)
      -> verify step (3-tier: AX state -> actuator/JS -> vision)
      -> infeasibility detection (frustration score -> planner advisory)
      -> replan on failure
  -> success condition gate (verify skill goal before accepting "done")
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
- At least one LLM API key: `AGENT_GEMINI_API_KEY`, `AGENT_OPENAI_API_KEY`, or `AGENT_ANTHROPIC_API_KEY`
- Optional: a local OpenAI-compatible vision server (Molmo) for free local grounding

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

Set at least one API key:

```bash
# Pick one (or more for per-step routing)
export AGENT_GEMINI_API_KEY="..."      # Gemini (recommended)
export AGENT_OPENAI_API_KEY="sk-..."   # GPT
export AGENT_ANTHROPIC_API_KEY="sk-ant-..."  # Claude
```

See "Quick Run" section above for example commands.

## Runtime Notes

- `AppleScriptActuator` is the current action backend.
- App activation, URL opening, keypresses, typing, and window state queries go through `osascript`.
- Coordinate clicks go through `pyautogui` inside the actuator.
- If `AGENT_USE_ACCESSIBILITY=true`, the agent can use the macOS Accessibility API for faster structured grounding and verification.
- `AGENT_MODEL_PROVIDER` sets the global LLM provider. Per-step overrides: `AGENT_PLANNING_MODEL`, `AGENT_GROUNDING_MODEL_PROVIDER`, `AGENT_VERIFICATION_MODEL`, `AGENT_SCREEN_DESCRIPTION_MODEL`, `AGENT_REFLECTION_MODEL` (each accepts `provider:model` syntax).
- Keyboard shortcuts for browser chrome: clicking "address bar" sends `Cmd+L` instead of 29s vision grounding.
- JS injection for browser state: `focused_value`, `page_title`, `page_heading` enable sub-100ms Tier 1 verification.

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
  events.jsonl       # Machine-readable event stream
  trace.md           # Readable execution trace
  report.md          # Detailed run report (plans, steps, LLM calls, verification)
  screenshots/       # Saved run screenshots
  debug/             # Click-target overlays for grounding debug
```

The `report.md` is the most useful for debugging — it shows the complete decision trail:
which LLM was called with what prompt, what it returned, what plan was generated,
what each step did, which verification tier was used, and any replanning that occurred.

The rolling process log lives at `logs/automation_agent.log`.

## Useful Commands

```bash
# Run the non-E2E suite
pytest -q -m "not e2e"

# Check the actuator directly
python -m automation_agent.actuator status
python -m automation_agent.actuator state

# Show the live overlay during a run
.venv/bin/python -m automation_agent --status-ui overlay "Open Safari"
```

## More Docs

- Current setup: `docs/guides/automation.md`
- Current runbook: `docs/guides/QUICKSTART.md`
- Current repo status: `IMPLEMENTATION_STATUS.md`
- Deep architecture walkthrough with stale sections clearly labeled: `docs/guides/LIFE_OF_A_PROMPT.md`
