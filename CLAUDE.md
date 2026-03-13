# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

macOS desktop automation agent that uses vision AI to understand screens and execute multi-step automation tasks from natural language prompts. Runs fully local via any OpenAI-compatible vision server such as llama.cpp (Qwen2-VL for vision, Gemma2 for text) or optionally via Claude API.

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
  → SkillRegistry.match()          # Three-stage: embedding → LLM re-rank → keyword fallback
  → ContextMonitor.update_cheap()  # Evolving world-state document
  → ScreenCoordinator.describe()   # Get current screen context
  → ActionPlanner.plan()           # Generate action steps via LLM
  → For each ActionStep:
      → Lookahead prediction       # Destructive steps only (opt-in via config)
      → Confirmation gate          # Destructive action user confirmation
      → Actuator.execute()         # Perform the action
      → StepVerifier.verify()      # 3-tier verification
      → Infeasibility detection    # FrustrationScore → planner advisory check
      → ContextMonitor.record_step_outcome()  # Track milestones/obstacles
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

**Capability-based protocol extension** (`protocols.py`): Optional coordinator methods (`find_element_dual`, `predict_action_outcome`) are advertised via `capabilities() -> FrozenSet[CoordinatorCapability]`. The orchestrator checks capabilities before calling optional methods using the `_has_explicit_method()` guard pattern.

**Destructive action classification** (`orchestrator/agent.py`): `_is_destructive_step()` returns a `DestructiveClassification` dataclass (not a tuple). Classification paths: `planner_flag`, `keyword_match`, `type_text_verify`. The `NOT_DESTRUCTIVE` class constant avoids tuple unpacking errors.

**Infeasibility detection** (`orchestrator/agent.py`): `FrustrationScore` is created FRESH per `execute()` call — never stored on `self`. Tracks same-state count, identical action retries, replan count, and advisory checks used.

**Confirmation handler injection** (`orchestrator/confirmation.py`): `ConsoleConfirmationHandler` is the default; tests inject `AutoDenyConfirmationHandler`. All display values are sanitized against ANSI escape sequences and Unicode directional overrides.

## Configuration

Settings are loaded from environment variables prefixed with `AGENT_` (see `.env.example`). Key settings:
- `AGENT_VISION_SERVER_URL` — Vision server endpoint (default `localhost:8080`; any OpenAI-compatible server)
- `AGENT_VISION_MODEL` / `AGENT_TEXT_MODEL` — model names
- `AGENT_LOG_DIR` — structured JSONL event logs with per-run directories

Safety and feature gate settings (all off by default):
- `AGENT_SOM_ENABLED` — Set-of-Mark numbered label overlay on screenshots
- `AGENT_DUAL_RESOLUTION_GROUNDING` — Send full + crop to VLM for grounding
- `AGENT_DUAL_RES_THRESHOLD` — Screenshot width (px) to trigger dual-res (default 1440)
- `AGENT_SKILL_EMBEDDING_ENABLED` — Embedding-based skill retrieval
- `AGENT_LOOKAHEAD_ENABLED` — Pre-action lookahead for destructive steps
- `AGENT_CONFIRM_DESTRUCTIVE` — Confirmation mode: `always`, `smart` (default), `never`
- `AGENT_INFEASIBILITY_SAME_STATE_LIMIT` — Same-state threshold (default 3)
- `AGENT_INFEASIBILITY_REPLAN_LIMIT` — Replan threshold (default 2)
- `AGENT_INFEASIBILITY_MAX_ADVISORY_CHECKS` — Hard abort after N advisories (default 2)

Config is in `src/automation_agent/config.py` using Pydantic Settings.

## LLM Backends

Located in `src/automation_agent/llm/`:
- **OllamaClient** — local Qwen2-VL / Gemma2 (planner text generation)
- **AnthropicClient** — Claude API (optional `pip install -e ".[anthropic]"`)
- **MolmoVisionClient** — Molmo via OpenRouter or local HuggingFace

## Install Extras

- `pip install -e ".[dev]"` — development (pytest, ruff, black, mypy)
- `pip install -e ".[anthropic]"` — Claude API backend
- `pip install -e ".[embeddings]"` — embedding-based skill retrieval (fastembed)
- `pip install -e ".[dev,anthropic,embeddings]"` — everything

## Code Style

- **Line length**: 100 (Black + Ruff)
- **Target**: Python 3.11
- **Ruff rules**: E, W, F, I, B, C4, UP (ignores E501, B008)
- **Async**: pytest-asyncio with `asyncio_mode = "auto"`
