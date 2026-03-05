# macOS Desktop Automation Agent

A vision-based desktop automation agent for macOS. Describe tasks in natural language and the agent plans, executes, and verifies each step using screen understanding.

## How It Works

```
User prompt -> Skill matching -> Screen description -> Plan generation
  -> For each step: Execute -> Verify (3-tier) -> Replan on failure
```

The agent uses a 5-component architecture:

| Component | Role |
|-----------|------|
| **Planner** | Generates action plans from natural language via LLM |
| **Vision Coordinator** | Screenshots, screen descriptions, element grounding |
| **Actuator** | Executes desktop actions (click, type, press_key, open_url, etc.) |
| **Skills** | Reusable `.md` automation templates with parameter substitution |
| **Orchestrator** | Coordinates all components; runs the execute-verify loop |

## Requirements

- macOS 11.0 or later
- Python 3.9+
- A vision backend (see [Vision Backends](#vision-backends))

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Copy environment template
cp .env.example .env

# Run
automation-agent "Open Calculator"
```

## Vision Backends

The agent supports multiple vision backends for screen understanding and element grounding. Configure via `AGENT_VISION_SERVER_URL` and `AGENT_VISION_MODEL` in `.env`.

| Backend | Accuracy | Latency | Cost | Setup |
|---------|----------|---------|------|-------|
| **molmo-mlx** (default) | 75% | 10s | Free | `.venv/bin/python scripts/mlx_vlm_server.py --model mlx-community/Molmo-7B-D-0924-3bit --port 8091` |
| qwen3-vl (Ollama) | 70% | 45s | Free | `ollama pull qwen3-vl` |
| molmo2-mlx (5-bit) | 45% | 6s | Free | `.venv-molmo2/bin/python scripts/mlx_vlm_server.py --model mlx-community/Molmo2-8B-5bit --port 8092` |
| claude-sonnet-4 (API) | 25% | 3s | ~$0.005/call | Set `ANTHROPIC_API_KEY` in `.env` |

> Accuracy measured on 20 ScreenSpot samples. Run `python scripts/benchmark_grounding.py --n 20` to reproduce.

**Important:** Run only one MLX model server at a time (memory constraint on Apple Silicon).

### Starting a Local Vision Server

```bash
# Molmo v1 (recommended — best accuracy)
.venv/bin/python scripts/mlx_vlm_server.py --model mlx-community/Molmo-7B-D-0924-3bit --port 8091

# Molmo2 (faster, less accurate)
.venv-molmo2/bin/python scripts/mlx_vlm_server.py --model mlx-community/Molmo2-8B-5bit --port 8092

# Qwen3-VL via Ollama
ollama serve  # then: ollama pull qwen3-vl
```

## Usage

```bash
# Basic usage
automation-agent "Open Calculator"

# Dry run (plan without executing)
automation-agent --dry-run "Return my Amazon order"

# Use Molmo vision backend
automation-agent --molmo "Book a table for 2 tonight"

# Use Hammerspoon actuator (more reliable clicks)
automation-agent --hammerspoon "Open Safari"

# Verbose logging
automation-agent --verbose "Complex task"
```

## Skills

Skills are reusable automation templates in `src/automation_agent/skills/library/`. The agent automatically matches user prompts to skills via LLM routing, extracts parameters, and uses the skill's steps to guide planning.

| Skill | File | Description |
|-------|------|-------------|
| Amazon Return | `return_amazon_order.md` | Return/replace items from Amazon order history |
| Amazon Search | `amazon_search.md` | Search for products on Amazon |
| Google Search | `google_search.md` | Search Google and interact with results |
| Open App | `open_app_and_navigate.md` | Launch apps and navigate to specific views |
| Send iMessage | `send_imessage.md` | Send messages via iMessage |
| Restaurant (OpenTable) | `restaurant_opentable.md` | Book restaurant reservations via OpenTable |
| Restaurant (Yelp) | `restaurant_yelp.md` | Find and reserve restaurants via Yelp |
| Restaurant (Google) | `restaurant_google.md` | Find restaurants and reserve via Google Maps |

### wait_for_user

When a step requires user interaction (e.g., signing in), the agent emits a `wait_for_user` step. The agent:

1. Captures a baseline screenshot
2. Prints the message to the terminal (e.g., `[WAITING] Please sign in to Amazon`)
3. Polls the screen every 5 seconds using pixel-diff comparison
4. Auto-resumes when >2% of pixels change (e.g., after login completes)
5. Times out after 120 seconds and proceeds anyway

No vision model calls are made during the wait — only fast pixel comparison.

## Debugging

### Debug Coordinate Overlay

Every `find_element` call saves a debug screenshot with a red crosshair at the predicted click point. This lets you verify whether the vision model is grounding to the correct UI element.

**Location:** `logs/runs/<run_id>/debug/find_<timestamp>.jpg`

Each image shows:
- Red crosshair + circle at the (x, y) pixel coordinate
- Label with coordinates and element description (e.g., `(440,246) search or filter orders field`)

To find debug images for your most recent run:

```bash
# List most recent debug images
ls -lt logs/runs/*/debug/find_*.jpg | head -5

# Open the latest one
open $(ls -t logs/runs/*/debug/find_*.jpg | head -1)
```

### Run Logs

Each execution creates a structured JSONL log directory:

```
logs/runs/<run_id>/
  events.jsonl      # All structured events (plan, steps, verification, errors)
  debug/
    find_<ts>.jpg   # Debug overlay images for each find_element call
```

Set `AGENT_LOG_DIR` in `.env` to change the log directory (default: `logs/`).

### Verification

The agent uses 3-tier verification after each step:

1. **Tier 1 — Accessibility** (~50ms): Hammerspoon/AppleScript state query
2. **Tier 2 — Vision** (2-5s): Screenshot + LLM verification of the postcondition
3. Falls through tiers; returns first successful verification

Every `ActionStep` must have a non-empty `verify` field — plans without postconditions are rejected.

## Configuration

Settings are loaded from environment variables prefixed with `AGENT_` (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_VISION_SERVER_URL` | `http://localhost:8080` | Vision server endpoint (any OpenAI-compatible server) |
| `AGENT_VISION_MODEL` | `qwen2-vl` | Vision model name |
| `AGENT_TEXT_MODEL` | `gemma2:9b` | Text model for planning |
| `AGENT_MODEL_PROVIDER` | `local` | `local` or `anthropic` |
| `AGENT_LOG_DIR` | `logs/` | Structured event log directory |
| `ANTHROPIC_API_KEY` | — | Required for Claude-based planning/vision |

## Benchmarks

### Grounding Benchmark

Measures vision element-finding accuracy using the ScreenSpot dataset:

```bash
# Quick test (5 samples, all available backends)
python scripts/benchmark_grounding.py --n 5

# Specific backend
python scripts/benchmark_grounding.py --backends molmo-mlx --n 20

# All options
python scripts/benchmark_grounding.py --help
```

Results are saved to `logs/benchmark_grounding_<timestamp>.json`.

### Other Benchmarks

```bash
# Vision description benchmark
python scripts/benchmark_vision.py

# Skill router accuracy benchmark
python scripts/benchmark_skill_router.py
```

## Coordinate Spaces

Each vision model returns coordinates in a different format. The coordinator normalizes all of them:

| Model | Raw Format | Normalization |
|-------|-----------|---------------|
| molmo | 0-100 | divide by 100 |
| molmo2, qwen2.5-vl, qwen3-vl | 0-1000 | divide by 1000 |
| claude-sonnet-* | pixels | divide by image dimensions |

Unknown models raise `ValueError` — no guessing.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all unit tests (522 tests)
pytest tests/unit/

# Run specific test file
pytest tests/unit/test_orchestrator_new.py -v

# Run by marker
pytest -m unit            # Unit tests only
pytest -m "not e2e"       # Skip e2e (requires live desktop)

# Format & lint
black src/ tests/
ruff check src/ tests/
mypy src/
```

### Test Markers

Defined in `pyproject.toml`: `unit`, `integration`, `e2e`, `manual`, `legacy`.

### Project Structure

```
src/automation_agent/
  orchestrator/       # Agent loop, step execution, verification
    agent.py          # Main AutomationAgent class
    verifier.py       # 3-tier step verification
  planner/            # LLM-based action plan generation
  vision/             # Screen understanding and element grounding
    coordinator.py    # Screenshot, describe, find_element, verify
  actuator/           # Desktop action execution
    applescript_actuator.py   # AppleScript/JXA backend
    hammerspoon_actuator.py   # Hammerspoon backend
  skills/             # Skill matching and template system
    library/          # .md skill templates
    registry.py       # Skill registry and matching
    router.py         # LLM-based skill routing
  llm/                # LLM client adapters
  config.py           # Pydantic Settings configuration
  protocols.py        # Protocol classes (duck typing interfaces)
  shared_models.py    # ActionStep, StepResult, ExecutionResult dataclasses
scripts/
  benchmark_grounding.py    # ScreenSpot grounding accuracy benchmark
  benchmark_vision.py       # Vision description benchmark
  benchmark_skill_router.py # Skill router accuracy benchmark
  mlx_vlm_server.py         # MLX vision model server wrapper
tests/
  unit/               # 522 unit tests (all mocked, fast)
  e2e/                # End-to-end tests (requires live macOS desktop)
  integration/        # Integration tests (requires running backends)
```

## macOS Permissions

The agent requires:
- **Accessibility**: For mouse/keyboard control
- **Screen Recording**: For taking screenshots

Grant these in: System Settings > Privacy & Security

## Author

Jagat Pudipeddi

## License

MIT
