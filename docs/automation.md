# macOS Automation Setup

This document reflects the current runtime. The old Hammerspoon bridge path is no longer the active setup.

## What the Agent Uses Today

- `AppleScriptActuator` for app activation, URL opens, typing, keypresses, and state queries
- `pyautogui` for coordinate clicks and screenshots
- Optional macOS Accessibility bridge when `AGENT_USE_ACCESSIBILITY=true`

## 1. Install the Project

```bash
cd /Users/jagatp/workspace/macos-automation-agent
pip install -e ".[dev,anthropic]"
```

## 2. Grant Accessibility Permission

The terminal or IDE running the agent must be allowed under:

`System Settings -> Privacy & Security -> Accessibility`

Open the panel directly:

```bash
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
```

This permission is required for:

- keypress injection
- typing
- reliable desktop input

## 3. Grant Screen Recording Permission

The same terminal or IDE must also be allowed under:

`System Settings -> Privacy & Security -> Screen Recording`

Open the panel directly:

```bash
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
```

This permission is required for:

- screenshots
- screen description
- vision verification
- grounding debug images

## 4. Set the Anthropic Key

Planning still uses Anthropic in the current code.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## 5. Verify the Actuator

```bash
python -m automation_agent.actuator status
python -m automation_agent.actuator state
```

Expected:

- `status` reports `Actuator (AppleScript): available`
- `state` returns JSON for the current frontmost app and window

## 6. Optional: Enable Accessibility API Grounding

```bash
export AGENT_USE_ACCESSIBILITY=true
```

When enabled, the agent will try to initialize the macOS Accessibility bridge for faster structured UI lookup and verification. If initialization fails, the agent falls back to vision-only behavior.

## 7. Optional: Point at a Local Vision Server

Any OpenAI-compatible vision endpoint can be used:

```bash
export AGENT_MODEL_PROVIDER=local
export AGENT_VISION_SERVER_URL=http://localhost:8091
export AGENT_VISION_MODEL=mlx-community/Molmo-7B-D-0924-4bit
```

Example server from this repo:

```bash
python scripts/mlx_vlm_server.py \
  --model mlx-community/Molmo-7B-D-0924-4bit \
  --port 8091
```

Planning still goes through Anthropic in this mode.

## 8. Smoke Test

```bash
automation-agent --provider anthropic "Open Safari"
automation-agent --dry-run "Open Safari and search for weather"
```

## Troubleshooting

### Keystrokes or shortcuts do nothing

The terminal app likely does not have Accessibility permission.

### Screenshot capture fails

The terminal app likely does not have Screen Recording permission.

### Planning fails before any UI action happens

Check that `ANTHROPIC_API_KEY` is set and the `anthropic` package is installed.

### Local vision calls fail immediately

Check that the configured server actually exposes an OpenAI-compatible `POST /v1/chat/completions` endpoint and that `AGENT_VISION_MODEL` matches a model the server knows about.

### Accessibility mode does not turn on

`AGENT_USE_ACCESSIBILITY=true` only enables the attempt. The agent will still fall back if the bridge cannot initialize or macOS permissions are missing.
