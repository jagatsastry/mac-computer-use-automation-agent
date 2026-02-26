# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

macOS desktop automation agent that uses vision AI to understand screens and execute multi-step automation tasks from natural language prompts. Runs fully local via Ollama (Qwen2-VL for vision, Gemma2 for text) or optionally via Claude API.

## Common Commands

```bash
# Install
pip install -e ".[dev]"

# Run
automation-agent "Open Calculator"
automation-agent --dry-run "Test prompt"
automation-agent --hammerspoon "Open Safari"    # Use Hammerspoon backend
automation-agent --molmo "Complex visual task"  # Use Molmo vision

# Tests
pytest                                    # All tests
pytest tests/unit/                        # Unit tests only (fast, all mocked)
pytest tests/unit/test_planner.py         # Single test file
pytest -k "test_plan_basic"               # Single test by name
pytest -m unit                            # By marker
pytest -m "not e2e"                       # Skip e2e (requires live macOS desktop)

# Linting & formatting
ruff check src/ tests/                    # Lint
black src/ tests/                         # Format
mypy src/                                 # Type check
```

**Test markers**: `unit`, `integration`, `e2e`, `manual`, `legacy` — defined in `pyproject.toml`.

## Architecture: 5-Component Design

All components communicate through **Protocol classes** (`src/automation_agent/protocols.py`) and **shared dataclasses** (`src/automation_agent/shared_models.py`). No inheritance — just duck typing with `@runtime_checkable` protocols.

### Components

| Component | Location | Protocol | Role |
|-----------|----------|----------|------|
| **Planner** | `planner/` | `ActionPlanner` | Generates `ActionPlan` from natural language via LLM |
| **Vision** | `vision/` | `ScreenCoordinator` | Screenshots, screen descriptions, element finding, condition verification |
| **Actuator** | `actuator/` | `Actuator` | Executes desktop actions (click, type, press_key, etc.) |
| **Skills** | `skills/` | `SkillRegistry` | Reusable `.md` automation templates with parameter substitution |
| **Orchestrator** | `orchestrator/` | — | Coordinates all components; runs the execute→verify loop |

### Execution Flow

```
User prompt → Orchestrator.execute()
  → SkillRegistry.match()          # Check for matching skill template
  → ScreenCoordinator.describe()   # Get current screen context
  → ActionPlanner.plan()           # Generate action steps via LLM
  → For each ActionStep:
      → Actuator.execute()         # Perform the action
      → StepVerifier.verify()      # 3-tier verification
      → On failure: replan()       # Retry with execution history
  → ExecutionResult
```

### Key Design Patterns

**Mandatory postconditions**: Every `ActionStep` must have a non-empty `verify` field. Plans fail validation without them.

**3-tier verification** (`orchestrator/verifier.py`):
1. Hammerspoon state query (~50ms) — fast check via actuator
2. Vision screenshot verification (2-5s) — visual confirmation via coordinator
3. Falls through tiers, returns first successful verification

**Actuator fallback chain** (`actuator/__init__.py` → `create_actuator()`):
Bridge HTTP (`localhost:27741`) → `hs` CLI → AppleScript (`osascript`)

**Coordinate space explicitness** (`vision/coordinator.py`): Each vision model maps to a known coordinate format (`molmo` → normalized 0-1, `qwen3-vl` → 0-1000, `claude-sonnet-*` → pixels). Unknown models raise `ValueError` — no guessing.

**Action aliasing** (`shared_models.py`): `ActionStep.from_dict()` auto-corrects common LLM misspellings (e.g., `key_press` → `press_key`).

**Skill file format** (`skills/library/*.md`): YAML frontmatter (name, trigger-keywords, parameters, OS requirements) + Markdown steps with `{{param}}` placeholders and `verify` conditions.

## Configuration

Settings are loaded from environment variables prefixed with `AGENT_` (see `.env.example`). Key settings:
- `AGENT_OLLAMA_HOST` — Ollama endpoint (default `localhost:11434`)
- `AGENT_VISION_MODEL` / `AGENT_TEXT_MODEL` — model names
- `AGENT_LOG_DIR` — structured JSONL event logs with per-run directories

Config is in `src/automation_agent/config.py` using Pydantic Settings.

## LLM Backends

Located in `src/automation_agent/llm/`:
- **OllamaClient** — local Qwen2-VL / Gemma2
- **AnthropicClient** — Claude API (optional `pip install -e ".[anthropic]"`)
- **MolmoVisionClient** — Molmo via OpenRouter or local HuggingFace

## Code Style

- **Line length**: 100 (Black + Ruff)
- **Target**: Python 3.11
- **Ruff rules**: E, W, F, I, B, C4, UP (ignores E501, B008)
- **Async**: pytest-asyncio with `asyncio_mode = "auto"`
