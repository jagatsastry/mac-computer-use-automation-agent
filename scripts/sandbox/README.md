# Sandboxed E2E Testing

Test the automation agent end-to-end **without it touching your screen,
mouse, or keyboard**. The agent's planner, vision, and orchestrator run on
the host exactly as in production; only actuation and screen capture are
redirected into a disposable Docker Linux desktop (Xvfb + Openbox +
Chromium + a deterministic local test website).

```
┌─ host (macOS) ──────────────────────────┐   ┌─ docker: agent-sandbox ────────┐
│ Planner (Gemini/…)                      │   │ Xvfb :99  1024x768             │
│ Vision grounding (OpenAI/Gemini/molmo)  │   │ Openbox + Chromium + galculator│
│ Orchestrator + 3-tier verifier          │   │ test website on :8000          │
│   SandboxActuator ──── docker exec ─────┼──▶│ xdotool / scrot                │
│   SandboxScreenCapture ◀── scrot PNG ───┼───│                                │
│   CdpClient ◀── DevTools :19222 ────────┼───│ chromium CDP (socat :9223)     │
└─────────────────────────────────────────┘   └─ VNC :15900 to watch live ─────┘
```

## Run it

```bash
# Full suite (builds image + starts container automatically)
scripts/sandbox/run_e2e.sh

# Specific scenarios / list / stability loop / ad-hoc task
scripts/sandbox/run_e2e.sh --scenarios search_widgets,contact_form
scripts/sandbox/run_e2e.sh --list
scripts/sandbox/run_e2e.sh --runs 2
scripts/sandbox/run_e2e.sh --task "Open http://localhost:8000/shop.html and add the Red Mug to the cart"

# Watch the sandbox desktop live while tests run (optional)
open vnc://localhost:15900        # Screen Sharing, no password
```

Requirements: Docker running, `.env` with the planner/grounding API keys
(same ones the agent normally uses), `websocket-client` in the venv.

Exit code 0 ⇔ every scenario passed **independent ground truth** — final
browser URL / page heading / scroll position / focused app are read via
Chrome DevTools Protocol and X11, never trusted from the agent's own
self-report. Artifacts land in `logs/sandbox_e2e/<timestamp>/`:
`results.json`, per-scenario agent event logs, and `final_state.jpg`.

## Scenarios

| name             | exercises                                                |
|------------------|----------------------------------------------------------|
| `open_portal`    | open_url + Tier-1 URL verification                        |
| `search_widgets` | click-to-focus, type_text, vision grounding of buttons    |
| `contact_form`   | multi-field form fill, multi-step plan                    |
| `calculator_app` | activate_app (app alias map), app-frontmost verification  |
| `scroll_article` | scroll actuation + CDP scroll verification                |

Add scenarios in `run_e2e.py`: a prompt plus a `ground_truth(actuator, cdp)`
callable. Test pages live in `scripts/sandbox/image/testsite/` — static,
hermetic, no external network.

Every prompt is prefixed with an environment note ("minimal test desktop —
no Spotlight, no Dock…"). Without it the planner assumes macOS affordances:
it emits preconditions like "the macOS desktop is visible" (conclusively
denied by vision on the Linux desktop) and replans into Spotlight
(`cmd+space`) flows that cannot exist in the sandbox.

## Using the sandbox from the normal CLI

```bash
AGENT_ACTUATOR_BACKEND=sandbox automation-agent "Open http://localhost:8000/"
```

`create_actuator()` returns `SandboxActuator` and `__main__` injects
`SandboxScreenCapture` automatically when `actuator_backend=sandbox`.

## macOS-isms translated by SandboxActuator

| planner says            | sandbox does                              |
|-------------------------|-------------------------------------------|
| `cmd+l`, `cmd+a`, …     | `ctrl+l`, `ctrl+a` (xdotool)              |
| Safari / browser        | Chromium (reported as "Google Chrome (Chromium)") |
| Calculator              | galculator (reported as "Calculator (galculator)") |
| TextEdit / Notes        | mousepad (reported as "TextEdit (mousepad)") |
| macOS `delete`          | BackSpace                                  |

## Container lifecycle

`run_e2e.py` manages everything, but manually:

```bash
docker build -t agent-sandbox:latest scripts/sandbox/image/
docker run -d --name agent-sandbox -p 15900:5900 -p 19222:9223 -p 18000:8000 agent-sandbox:latest
docker exec agent-sandbox launch-browser "http://localhost:8000/"
docker rm -f agent-sandbox
```

The browser profile and all apps are reset between scenarios; rebuild with
`--rebuild` after editing the image or test pages.

## Full-fidelity macOS VM track

For testing the real AppleScript actuator / Safari / Accessibility API,
see `scripts/vm/` (Tart-based macOS VM; same don't-touch-my-desktop
guarantee). `scripts/vm/setup_vm.sh` provisions once; 
`scripts/vm/run_vm_e2e.sh "<task>"` runs a task in a disposable VM clone.
