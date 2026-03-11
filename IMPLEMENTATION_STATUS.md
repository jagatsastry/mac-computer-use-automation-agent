# Implementation Status

**Project:** macOS Desktop Automation Agent  
**Started:** 2026-02-02  
**Last Updated:** 2026-03-10  
**Status:** Generic runtime is live, docs are aligned to the code, non-E2E suite is green

---

## Verified Today

### Automated Validation

```bash
pytest -q -m 'not e2e'
1198 passed, 3 skipped, 5 deselected
```

This is the current verified baseline after removing the bespoke restaurant runtime path and updating the docs to match the code.

### E2E Status

E2E tests still exist, but they were not rerun during this documentation sweep because they perform real desktop actions on macOS.

## What Is Actually Wired

### Planner

- `ActionPlannerImpl` is live.
- Planning currently calls Anthropic Claude directly.
- The project is not fully local yet.

### Vision

- `ScreenCoordinatorImpl` is live.
- Vision can run against:
  - Anthropic vision
  - a local OpenAI-compatible server via `AGENT_VISION_SERVER_URL`
- Coordinate spaces are explicit, not inferred:
  - `molmo` -> `normalized_0_100`
  - `molmo2`, `qwen3-vl`, `qwen2.5-vl`, `qwen2-vl` -> `normalized_0_1000`
  - `claude-sonnet-4-20250514` -> pixel coordinates

### Actuator

- `create_actuator()` currently returns `AppleScriptActuator`.
- There is no active Hammerspoon backend in the runtime code.
- App control, typing, keypresses, URL opens, and state queries use `osascript`.
- Coordinate clicks use `pyautogui` from inside the AppleScript actuator.

### Skills

- The skill library contains 8 markdown skill templates.
- Skills are optional priors, not hardcoded workflows.
- Restaurant skills remain as templates only.

### Orchestrator

- `AutomationAgent` runs the generic execute / verify / replan loop.
- The bespoke restaurant runtime path has been removed.
- `wait_for_user` remains part of the generic plan vocabulary.

### Observability

- Per-run logs: `logs/runs/<run_id>/events.jsonl`, `trace.md`, `screenshots/`
- Grounding debug overlays: `logs/runs/<run_id>/debug/find_*.jpg`
- Live status UI: overlay mode is available via `AGENT_STATUS_UI=overlay` or `--status-ui overlay`

## What Is No Longer True

- Hammerspoon is not the current actuator stack.
- `--hammerspoon` is not a real CLI flag.
- There is no `--restaurant-only` mode.
- The project is not "fully local" in its current planner path.

## Main Risks Still Left

- Grounding reliability is the main bottleneck, especially for visually ambiguous pages.
- Local vision remains weaker than Claude for precise element selection.
- Browser shortcuts like `Cmd+L` depend on Accessibility permission being granted to the host terminal app.
- High-stakes multi-step flows still need narrower, reliability-first validation before they are trustworthy.

## Recommended Alpha Surface

If the goal is a real alpha soon, Safari-first remains the best wedge:

1. Open Safari or bring it frontmost
2. Open a URL
3. Focus the address bar or search box
4. Click a visible control or link
5. Verify navigation or page state

That scope fits the current architecture much better than general desktop autonomy.
