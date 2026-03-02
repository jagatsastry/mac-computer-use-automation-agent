# Implementation Status

**Project:** macOS Desktop Automation Agent
**Started:** 2026-02-02
**Last Updated:** 2026-03-01
**Status:** 5-Component Architecture Complete, E2E Working

---

## Architecture: 5-Component Design

All components implemented and wired together. See `docs/QUICKSTART.md` for run commands.

| Component | Status | Location | Tests |
|-----------|--------|----------|-------|
| **Planner** | ✅ Complete | `planner/` | Unit tests passing |
| **Vision Coordinator** | ✅ Complete | `vision/` | Unit tests passing |
| **Actuator** | ✅ Complete | `actuator/` | Unit tests passing |
| **Skill Registry** | ✅ Complete | `skills/` | Unit tests passing |
| **Orchestrator + Verifier** | ✅ Complete | `orchestrator/` | Unit tests passing |

## Test Results

### Unit Tests: 401 passed, 2 failed (coordinate test updates needed)

```
tests/unit/ — 401 passed, 2 failed in 23.83s
```

### E2E Tests Verified

| Prompt | Backend | Result | Time |
|--------|---------|--------|------|
| Calculator `3 * 18` | Claude API | ✅ PASS | ~30s |
| Calculator `7 * 8` | Claude API | ✅ PASS | ~71s |
| Amazon cheapest shirts | llama.cpp (Qwen2.5-VL) | ✅ PASS | ~26s |
| Amazon cheapest shirts | Claude API | ❌ FAIL (accessibility) | — |

## LLM Backends

| Backend | Purpose | Status |
|---------|---------|--------|
| **Claude API** (Anthropic) | Planning (always), Vision (optional) | ✅ Working |
| **llama.cpp** | Local vision via OpenAI-compatible API | ✅ Working, 3.8x faster than Ollama |
| **Ollama** | Local vision (legacy) | ✅ Working but slower |

### Vision Benchmark (Qwen2.5-VL 7B, Q4_K_M)

| Backend | Cold Start | Warm Avg | Speedup |
|---------|-----------|----------|---------|
| llama.cpp | 32.6s | **2.9s** | **3.8x** |
| Ollama | 36.3s | 11.0s | baseline |

## Actuator Backends

| Backend | Status | Notes |
|---------|--------|-------|
| **HammerspoonBridge** (HTTP) | ✅ Primary | `localhost:27741`, fastest |
| **Hammerspoon CLI** (`hs`) | ✅ Fallback | Direct CLI invocation |
| **AppleScript** (`osascript`) | ✅ Fallback | Last resort, limited keyboard support |

**Fallback chain:** Bridge HTTP → `hs` CLI → `osascript`

## Skills Library

5 skills in `src/automation_agent/skills/library/`:

| Skill | Trigger Keywords |
|-------|-----------------|
| `amazon_search.md` | amazon, buy, cheapest, shop, purchase, price |
| `google_search.md` | google, search, look up |
| `return_amazon_order.md` | return, send back, refund, amazon |
| `send_imessage.md` | imessage, text, message, send |
| `open_app_and_navigate.md` | open, launch, navigate |

## Coordinate Space Registry

Vision models map to known coordinate formats (case-insensitive prefix matching):

| Model | Coordinate Space |
|-------|-----------------|
| `molmo` | normalized 0-1 |
| `qwen3-vl`, `qwen2.5-vl`, `qwen2-vl` | normalized 0-1000 |
| `claude-sonnet-*` | pixel coordinates |

GGUF filenames handled automatically (e.g. `Qwen2.5-VL-7B-Instruct-q4_k_m.gguf` → `qwen2.5-vl`).

## Project Structure

```
src/automation_agent/
├── __main__.py              # CLI entry point
├── config.py                # Pydantic Settings config
├── cli.py                   # Argument parsing
├── protocols.py             # Protocol classes (interfaces)
├── shared_models.py         # ActionStep, ActionPlan, StepResult, ExecutionResult
├── planner/
│   ├── planner.py           # ActionPlannerImpl (Claude API)
│   └── prompts/             # plan_from_prompt.md, replan_from_state.md
├── vision/
│   ├── coordinator.py       # ScreenCoordinatorImpl (local or Claude vision)
│   ├── capture.py           # Screenshot capture + resize
│   └── prompts/             # find_element.md, describe_screen.md, verify_condition.md
├── actuator/
│   ├── actuator.py          # HammerspoonActuator (hs CLI + Lua templates)
│   ├── bridge_actuator.py   # HammerspoonBridgeActuator (HTTP)
│   ├── applescript_actuator.py  # AppleScript fallback
│   └── lua_templates/       # click.lua, type_text.lua, press_key.lua, etc.
├── skills/
│   ├── registry.py          # SkillRegistryImpl
│   ├── loader.py            # YAML frontmatter + Markdown parser
│   ├── matcher.py           # Keyword matching + param extraction
│   └── library/             # .md skill files
├── orchestrator/
│   ├── agent.py             # AutomationAgent (main orchestration loop)
│   ├── verifier.py          # StepVerifier (3-tier verification)
│   ├── context_monitor.py   # Desktop state tracking
│   └── screenshot_diff.py   # Before/after screenshot comparison
├── perception/
│   ├── accessibility.py     # macOS Accessibility API bridge
│   └── capture.py           # Screen capture utilities
├── logging/
│   ├── event_logger.py      # Per-run JSONL + trace.md + screenshots
│   └── structured.py        # structlog configuration
└── llm/
    ├── client.py            # OllamaClient
    └── molmo_client.py      # MolmoVisionClient
```

**58 Python source files, 5 skill templates, 401+ unit tests.**

## Configuration

Environment variables with `AGENT_` prefix. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_MODEL_PROVIDER` | `local` | `local` or `anthropic` |
| `AGENT_VISION_SERVER_URL` | `http://localhost:8080` | OpenAI-compatible vision endpoint |
| `AGENT_VISION_MODEL` | `qwen3-vl` | Vision model name or GGUF filename |
| `ANTHROPIC_API_KEY` | — | Required (planner always uses Claude) |
| `AGENT_LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR |

## Logs

```
logs/
├── automation_agent.log           # Rolling log file
└── runs/{run_id}/
    ├── trace.md                   # Human-readable step-by-step trace
    ├── events.jsonl               # Structured event log
    └── screenshots/               # Per-step screenshots
```

## Known Issues

- **Accessibility permissions**: `press_key` with modifiers (Cmd+L, Cmd+A) fails in Safari unless terminal has Accessibility permission in System Settings
- **2 failing unit tests**: Coordinate conversion tests need updating after `_resolve_coordinate_space` refactor
- **Molmo not available**: No GGUF format exists; not in Ollama registry. Use Qwen2.5-VL instead
- **Element finding**: Local vision models (Qwen2.5-VL) struggle with precise UI element grounding; Claude API is more accurate but slower

## Key Design Decisions

1. **Skills use `open_url` with query params** instead of multi-step UI interaction — 7 steps reduced to 3, much more reliable
2. **Planner always uses Claude API** — local models not reliable enough for structured JSON planning
3. **Vision is pluggable** — llama.cpp, Ollama, or Claude API via `AGENT_MODEL_PROVIDER`
4. **Mandatory postconditions** — every ActionStep must have a `verify` field
5. **Strategy-changing retries** — replan prompt includes history and demands different approaches

---

**Author:** Jagat Pudipeddi
