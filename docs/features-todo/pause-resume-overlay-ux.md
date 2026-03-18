# Pause/Resume + Overlay UX Improvements

## Context

The agent runs autonomously and the user has no way to pause execution mid-run. If the plan is wrong, they must wait for it to fail or Ctrl-C. The overlay shows events but plan steps blend in with other text. The user wants:
1. Pause/Resume buttons in the agent's menu bar
2. Plan/replan steps shown in a distinct color (orange) to grab attention
3. A config flag (`AGENT_PAUSE_AFTER_PLAN`) to auto-pause after plan generation for review

## IPC Mechanism: File-Based Pause Signal

The overlay runs as a separate process reading `events.jsonl` (one-way). For reverse communication, use signal files in the run directory (same directory both processes already share):

```
{run_dir}/pause.signal   — overlay creates on Pause click, agent reads and deletes
{run_dir}/resume.signal  — overlay creates on Resume click, agent reads and deletes
```

Agent confirms state via events: `AGENT_PAUSED` and `AGENT_RESUMED` flow through events.jsonl back to the overlay, which toggles its menu state.

## Changes

### 1. Event types + Config
**`src/automation_agent/logging/models.py`**: Add `AGENT_PAUSED`, `AGENT_RESUMED`
**`src/automation_agent/config.py`**: Add `pause_after_plan: bool = Field(default=False)`

### 2. Agent pause logic
**`src/automation_agent/orchestrator/agent.py`**:
- Add `_check_pause()` — checks for `pause.signal`, if found: deletes it, logs `AGENT_PAUSED`, polls for `resume.signal` every 0.5s, logs `AGENT_RESUMED` on resume
- Call `await self._check_pause()` before each step in the main loop and replan loop
- After `PLAN_COMPLETE` and `REPLAN_COMPLETE`: if `pause_after_plan=True`, create `pause.signal` and call `_check_pause()` (self-initiated pause)

### 3. Status snapshot extensions
**`src/automation_agent/status.py`**:
- Add `is_plan: bool = False` and `pause_state: str = ""` to `StatusSnapshot`
- Set `is_plan=True` for `plan_complete`/`replan_complete` events
- Set `pause_state="paused"/"resumed"` for the new event types
- Add titles: "PAUSED" and "Resumed"

### 4. Overlay UI
**`src/automation_agent/status_overlay.py`**:

**Colored plan display**: Switch `StatusOverlayWindow.apply_snapshot()` from plain `setString_()` to `NSMutableAttributedString`. Store lines as `list[tuple[str, bool]]` (text, is_plan). Plan lines render in `NSColor.systemOrangeColor()`, normal lines in `NSColor.labelColor()`.

**Menu bar items**: Add "Pause Agent" and "Resume Agent" items to `StatusMenuBarItem` using existing `_make_menu_target()` pattern. Pause creates `{run_dir}/pause.signal`, Resume creates `{run_dir}/resume.signal`. Menu state toggles when `agent_paused`/`agent_resumed` events arrive via `apply_snapshot()`.

**run_dir derivation**: Computed from `events_file.parent` (no new CLI arg needed).

### 5. Tests
**`tests/unit/test_pause_resume.py`** (~10 tests):
- `_check_pause` with no signal = noop
- `_check_pause` with signal = pauses, resumes on resume.signal, logs events
- Auto-pause after plan when config enabled
- No auto-pause when config disabled
- StatusSnapshot is_plan/pause_state set correctly
- Menu state toggles on pause/resume events

## Data Flow

```
Pause: User clicks → overlay creates pause.signal → agent detects → logs AGENT_PAUSED
       → overlay reads event → enables Resume, disables Pause, shows "PAUSED"
Resume: User clicks → overlay creates resume.signal → agent detects → logs AGENT_RESUMED
        → overlay reads event → enables Pause, disables Resume, restores title
Auto:   Plan generated → agent creates pause.signal → same flow as above
```

## Verification
1. Run tests: `.venv/bin/python -m pytest tests/unit/test_pause_resume.py -v`
2. Run full suite: `.venv/bin/python -m pytest tests/unit/ tests/integration/ -x -q`
3. Manual test: `AGENT_PAUSE_AFTER_PLAN=true .venv/bin/python -m automation_agent --status-ui overlay --verbose-overlay "search amazon for laptop"` — verify overlay shows plan in orange, agent pauses, Resume in menu works
4. Manual test: Click Pause mid-run, verify agent stops, click Resume, verify agent continues
