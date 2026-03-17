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
  → extract_site_entity()          # Deterministic site pre-filter (e.g., "on target" → site=target)
  → SkillRegistry.match()          # Three-stage: embedding → LLM re-rank → keyword fallback (site-filtered)
  → ContextMonitor.update_cheap()  # Evolving world-state document
  → ScreenCoordinator.describe()   # Get current screen context
  → ActionPlanner.plan()           # Generate action steps via LLM
  → _is_truncated_plan()           # Detect shallow plans; fall back to skill template if truncated
  → _inject_domain_verification()  # Append "AND browser domain is X" to open_url verify fields
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
1. Tier 0: Accessibility API state — structured element checks
2. Tier 1: Actuator state query (~50ms) — URL match, domain verification, scroll verification, type_text field matching, page content token matching
3. Tier 2: Vision screenshot verification (2-5s) — visual confirmation via coordinator
Falls through tiers; returns first conclusive result.

**JS-injected browser state** (`actuator/applescript_actuator.py`): `_get_browser_js_batch()` executes a single `JSON.stringify()` call via AppleScript to extract `focused_value`, `selected_text`, `page_title`, and `page_heading` from the frontmost browser tab (Safari/Chrome). Populates `get_state()` dict, enabling Tier 1 verification for type_text and click actions without vision calls. Gated by `js_verification_enabled` config (default True). **Safari prerequisite**: Requires "Allow JavaScript from Apple Events" enabled (Safari > Develop > Developer Settings). The agent auto-detects when this is disabled, logs a warning once, and disables Safari JS for the rest of the session (Chrome JS is unaffected). No focus-stealing dialogs.

**Calibrated AX confidence** (`orchestrator/grounding_router.py`): `_ground_accessibility_match()` uses `confidence = 0.6 + 0.35 * min(match_score, 1.0)` instead of hardcoded 0.95. Exact AX matches (score=1.0) get 0.95 (skip pre-click validation); partial matches get lower confidence (trigger crop validation). The pre-click skip gate in `agent.py` checks confidence, not source strategy.

**Actuator fallback chain** (`actuator/__init__.py` → `create_actuator()`):
AppleScript (`osascript`) is the primary backend. Includes `get_scroll_position()` for JS-based scroll verification and `_escape_for_applescript()` for safe string embedding.

**Coordinate space explicitness** (`vision/coordinator.py`): Each vision model maps to a known coordinate format (`molmo` → normalized 0-1, `qwen3-vl` → 0-1000, `claude-sonnet-*` → pixels). Unknown models raise `ValueError` — no guessing.

**Action aliasing** (`shared_models.py`): `ActionStep.from_dict()` auto-corrects common LLM misspellings (e.g., `key_press` → `press_key`).

**Skill file format** (`skills/library/*.md`): YAML frontmatter (name, trigger-keywords, parameters, OS requirements) + Markdown steps with `{{param}}` placeholders and `verify` conditions. Skills may include a `site` metadata field (e.g., `site: target`) for site-entity routing and a `required-keywords` field to gate keyword-fallback matching.

**Skill matching pipeline** (`skills/registry.py`, `skills/router.py`): Three-stage matching with match types — `DIRECT` (exact), `ANALOGICAL` (similar workflow), `GENERIC` (fallback). `extract_site_entity()` in `router.py` uses seed ecommerce sites (amazon, target, walmart, bestbuy, etc.) plus skill `site:` metadata for deterministic pre-filtering. The LLM router (`route_skill.md` prompt) scores all candidates and returns top-k with confidence.

**Adaptive skill learning** (`skills/distiller.py`, `skills/librarian.py`, `skills/experience.py`): Post-run learning loop: `SkillDistiller` extracts observations (alternative_path, checkpoint, user_gate, anti_pattern) from execution traces → `SkillExperienceStore` persists them as JSONL sidecars at `logs/skill_learning/{skill_name}.jsonl` → `SkillLibrarian` evaluates accumulated observations for promotion when thresholds are met. Two promotion paths: `patch_parent` (append `## Learned Tips` to existing skill `.md`) or `create_sibling` (generate new `.md` with `parent-skill-id` link, `trusted: false`). Promotion history tracked at `logs/skill_learning/promotions/history.jsonl`. Guard: `_maybe_learn_skill_run()` at `agent.py` only fires when a skill was matched (`if not skill_name: return []`).

**Derived skill sessions** (`skills/derived_skill.py`): `DerivedSkillSession` is an in-memory session created when a skill matches, seeded from the parent skill's steps. Tracks adaptations during execution (label replacements, discovered landmarks, failed assumptions). Updated by `ReplanPatch` during replanning. Serialized for planner context injection. All lists capped at 20 items.

**Capability-based protocol extension** (`protocols.py`): Optional coordinator methods (`find_element_dual`, `predict_action_outcome`) are advertised via `capabilities() -> FrozenSet[CoordinatorCapability]`. The orchestrator checks capabilities before calling optional methods using the `_has_explicit_method()` guard pattern.

**Destructive action classification** (`orchestrator/agent.py`): `_is_destructive_step()` returns a `DestructiveClassification` dataclass (not a tuple). Classification paths: `planner_flag`, `keyword_match`, `type_text_verify`. The `NOT_DESTRUCTIVE` class constant avoids tuple unpacking errors.

**Infeasibility detection** (`orchestrator/agent.py`): `FrustrationScore` is created FRESH per `execute()` call — never stored on `self`. Tracks same-state count, identical action retries, replan count, and advisory checks used.

**Confirmation handler injection** (`orchestrator/confirmation.py`): `ConsoleConfirmationHandler` is the default; tests inject `AutoDenyConfirmationHandler`. All display values are sanitized against ANSI escape sequences and Unicode directional overrides.

## Configuration

Settings are loaded from environment variables prefixed with `AGENT_` (see `.env.example`). Key settings:
- `AGENT_VISION_SERVER_URL` — Vision server endpoint (default `localhost:8080`; any OpenAI-compatible server)
- `AGENT_VISION_MODEL` / `AGENT_TEXT_MODEL` — model names
- `AGENT_LOG_DIR` — structured JSONL event logs with per-run directories

Speed optimization settings (on by default):
- `AGENT_JS_VERIFICATION_ENABLED` — JS injection for browser state verification: focused_value, selected_text, page_title, page_heading (default True)

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

Skill learning settings:
- `AGENT_SKILL_LEARNING_ENABLED` — Enable post-run observation extraction (default True)
- `AGENT_SKILL_LIBRARIAN_ENABLED` — Enable observation promotion to skills (default True)
- `AGENT_SKILL_LIBRARIAN_MIN_OBSERVATIONS` — Minimum observations before promotion (default 3)
- `AGENT_SKILL_LIBRARIAN_MIN_RUNS` — Minimum distinct runs before promotion (default 2)
- `AGENT_SKILL_LIBRARIAN_MIN_CONFIDENCE` — Bayesian confidence threshold (default 0.55)
- `AGENT_SKILL_LIBRARIAN_MAX_TIPS` — Max learned tips per skill (default 10)

Config is in `src/automation_agent/config.py` using Pydantic Settings.

## LLM Backends

Located in `src/automation_agent/llm/`:
- **OllamaClient** — local Qwen2-VL / Gemma2 (planner text generation)
- **AnthropicClient** — Claude API (optional `pip install -e ".[anthropic]"`)
- **GeminiClient** — Google Gemini API (`pip install -e ".[gemini]"`; config: `AGENT_MODEL_PROVIDER=gemini`, `AGENT_GEMINI_API_KEY`, `AGENT_GEMINI_MODEL=gemini-2.5-flash`)
- **MolmoVisionClient** — Molmo via OpenRouter or local HuggingFace

## Install Extras

- `pip install -e ".[dev]"` — development (pytest, ruff, black, mypy)
- `pip install -e ".[anthropic]"` — Claude API backend
- `pip install -e ".[embeddings]"` — embedding-based skill retrieval (fastembed)
- `pip install -e ".[gemini]"` — Google Gemini API backend
- `pip install -e ".[dev,anthropic,gemini,embeddings]"` — everything

## Code Style

- **Line length**: 100 (Black + Ruff)
- **Target**: Python 3.11
- **Ruff rules**: E, W, F, I, B, C4, UP (ignores E501, B008)
- **Async**: pytest-asyncio with `asyncio_mode = "auto"`
- **Test gotcha**: `.env` may set `AGENT_MODEL_PROVIDER=anthropic` (or `gemini`) which leaks into pydantic-settings during tests. Pin `model_provider="local"` in `_make_config()` helpers in test files to avoid API key validation errors.
