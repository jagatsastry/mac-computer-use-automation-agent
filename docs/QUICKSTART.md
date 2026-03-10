# Quick Start

This is the shortest path to a working current setup.

## 1. Install

```bash
cd /Users/jagatp/workspace/macos-automation-agent
pip install -e ".[dev,anthropic]"
cp .env.example .env
```

## 2. Grant macOS Permissions

The terminal or IDE that runs the agent needs:

- Accessibility
- Screen Recording

Without Accessibility, keypresses and clicks may fail.

Without Screen Recording, screenshot-based perception will fail.

## 3. Set the Anthropic Key

Planning still depends on Anthropic today, even if you use local vision.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## 4. Run the Simplest Supported Path

Use Anthropic for both planning and vision:

```bash
automation-agent --provider anthropic "Open Safari and go to news.ycombinator.com"
```

Dry run:

```bash
automation-agent --dry-run "Open Safari and search Google for weather"
```

## 5. Optional: Use Local Vision

If you already have a local OpenAI-compatible vision server, point the agent at it:

```bash
AGENT_MODEL_PROVIDER=local \
AGENT_VISION_SERVER_URL=http://localhost:8091 \
AGENT_VISION_MODEL=mlx-community/Molmo-7B-D-0924-4bit \
automation-agent "Open Safari and search for coffee near me"
```

`AGENT_MODEL_PROVIDER=local` changes the vision backend. It does not make planning local.

Example local server from this repo:

```bash
python scripts/mlx_vlm_server.py \
  --model mlx-community/Molmo-7B-D-0924-4bit \
  --port 8091
```

## 6. Optional: Enable Accessibility-First Grounding

```bash
export AGENT_USE_ACCESSIBILITY=true
```

This can improve text-heavy grounding and verification when macOS Accessibility access is available.

## 7. Useful Run Modes

```bash
# Verbose logs
automation-agent --verbose "Open Safari"

# Live overlay
automation-agent --status-ui overlay "Open Safari"

# Direct actuator smoke tests
python -m automation_agent.actuator status
python -m automation_agent.actuator state
```

## Logs

```text
logs/
  automation_agent.log
  runs/<run_id>/
    events.jsonl
    trace.md
    screenshots/
    debug/
```

The most useful files after a run are:

- `logs/runs/<run_id>/trace.md`
- `logs/runs/<run_id>/events.jsonl`
- `logs/runs/<run_id>/debug/find_*.jpg`

## Tests

```bash
pytest -q -m "not e2e"
```

Validated on March 10, 2026:

```text
1198 passed, 3 skipped, 5 deselected
```
