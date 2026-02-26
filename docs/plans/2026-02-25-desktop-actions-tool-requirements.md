# macOS Desktop Actions CLI Tool — Requirements

**Date:** 2026-02-25
**Purpose:** Standalone CLI tool providing all desktop automation capabilities needed by the automation agent, without requiring Hammerspoon.

---

## Overview

A Python CLI tool (`desktop-actions` or `da`) that exposes macOS desktop automation primitives as subcommands. It replaces the Hammerspoon actuator stack with PyAutoGUI + osascript + screencapture, eliminating the Hammerspoon dependency entirely.

**Design principle:** Each subcommand does one thing, prints JSON to stdout, exits. No daemon, no server, no state between calls.

---

## Dependencies

- `pyautogui` — click, type, keypress, screenshot
- `Pillow` — image handling (installed with pyautogui)
- No other external dependencies. Everything else uses macOS built-ins (`osascript`, `open`, `screencapture`).

---

## Capabilities

### 1. click

Click at absolute screen coordinates.

```
da click <x> <y>
da click 500 300
da click 500 300 --double        # double-click
da click 500 300 --right         # right-click
```

**Implementation:** `pyautogui.click(x, y)`

**Retina handling:** macOS reports logical coordinates but pyautogui operates in logical space by default — no scaling needed. Verify this on your display and add a `--scale <factor>` flag as escape hatch if needed.

**Output:**
```json
{"success": true, "x": 500, "y": 300}
```

**Error cases:**
- Coordinates out of screen bounds → error with screen dimensions in message

---

### 2. type

Type text at the current cursor position.

```
da type "hello world"
da type "hello world" --interval 0.05   # delay between keystrokes (seconds)
```

**Implementation:** `pyautogui.write(text, interval=interval)`

**Note:** `pyautogui.write()` only handles ASCII. For Unicode (emoji, accented characters), use `pyperclip` + Cmd+V as fallback:
```python
import pyperclip
pyperclip.copy(text)
pyautogui.hotkey('command', 'v')
```

**Output:**
```json
{"success": true, "text": "hello world", "length": 11}
```

---

### 3. key

Press a key combination (modifiers + key).

```
da key cmd c                   # Cmd+C
da key cmd shift s             # Cmd+Shift+S
da key return                  # Enter/Return
da key escape                  # Escape
da key tab
da key cmd l                   # Focus address bar in browsers
da key up up up                # Press up arrow 3 times
```

**Implementation:** `pyautogui.hotkey(*keys)` for combos, `pyautogui.press(key)` for single keys.

**Modifier names** (accept all common aliases):
| Input | Maps to |
|-------|---------|
| `cmd`, `command` | `command` |
| `ctrl`, `control` | `ctrl` |
| `alt`, `option` | `option` |
| `shift` | `shift` |
| `fn` | `fn` |

**Special key names:** `return`, `enter`, `tab`, `escape`, `space`, `delete`, `backspace`, `up`, `down`, `left`, `right`, `f1`–`f12`, `home`, `end`, `pageup`, `pagedown`

**Output:**
```json
{"success": true, "keys": ["cmd", "c"]}
```

---

### 4. activate

Bring an app to the foreground (launch if not running).

```
da activate Safari
da activate "Google Chrome"
da activate Calculator
```

**Implementation:**
```python
subprocess.run(["open", "-a", app_name])
```

**Wait behavior:** After `open -a`, poll until the app is frontmost (up to 5s timeout). This ensures the app is ready for input.

**Output:**
```json
{"success": true, "app": "Safari", "pid": 12345}
```

**Error cases:**
- App not found → error with suggestion to check app name
- App failed to launch within timeout → error

---

### 5. quit

Quit an application.

```
da quit Safari
da quit Safari --force          # force quit (SIGKILL)
```

**Implementation:**
```python
# Graceful
osascript -e 'tell application "Safari" to quit'

# Force
subprocess.run(["killall", app_name])
```

**Output:**
```json
{"success": true, "app": "Safari", "was_running": true}
```

If the app wasn't running:
```json
{"success": true, "app": "Safari", "was_running": false}
```

---

### 6. state

Get current desktop state: frontmost app, window title, window position/size, screen dimensions.

```
da state
```

**Implementation:** AppleScript via osascript:
```applescript
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    set appBundle to bundle identifier of frontApp
    tell frontApp
        set win to first window
        set winTitle to name of win
        set winPos to position of win
        set winSize to size of win
    end tell
end tell
```

Screen dimensions: `pyautogui.size()` returns `(width, height)` in logical pixels.

**Output:**
```json
{
  "app_name": "Safari",
  "app_bundle": "com.apple.Safari",
  "window_title": "Google - Safari",
  "window_x": 0,
  "window_y": 25,
  "window_w": 1440,
  "window_h": 875,
  "screen_w": 1440,
  "screen_h": 900
}
```

**Error cases:**
- No frontmost app (unlikely) → return empty strings and zeros
- No windows open → window fields are empty/zero, app fields still populated

---

### 7. screenshot

Capture a screenshot and save to file or print base64.

```
da screenshot                           # save to /tmp/screenshot.png, print path
da screenshot --output /path/to/file.png
da screenshot --base64                  # print base64-encoded PNG to stdout
da screenshot --region 100,200,500,400  # x,y,width,height crop
da screenshot --scale 0.5              # resize to 50% (useful for sending to vision APIs)
```

**Implementation:** `screencapture -x /tmp/screenshot.png` (silent, no shutter sound). The `-x` flag suppresses the capture sound.

For `--region`: `screencapture -x -R x,y,w,h /path/file.png`

For `--scale`: Use Pillow to resize after capture.

For `--base64`: Read file, encode, print.

**Output (file mode):**
```json
{"success": true, "path": "/tmp/screenshot.png", "width": 2880, "height": 1800}
```

**Output (base64 mode):**
```json
{"success": true, "base64": "iVBORw0KGgo...", "width": 2880, "height": 1800}
```

---

### 8. open-url

Open a URL in the default browser.

```
da open-url "https://www.amazon.com"
da open-url "https://google.com" --browser Safari
```

**Implementation:**
```python
# Default browser
subprocess.run(["open", url])

# Specific browser
subprocess.run(["open", "-a", browser, url])
```

**Output:**
```json
{"success": true, "url": "https://www.amazon.com"}
```

---

## Global Options

```
da --timeout <seconds>     # override default timeout for any command (default: 10)
da --json                  # force JSON output (default, for programmatic use)
da --quiet                 # suppress non-essential output
da --verbose               # include debug info in output
```

---

## Output Contract

Every subcommand prints a single JSON object to stdout and exits with code 0 on success, 1 on failure.

**Success:**
```json
{"success": true, ...}
```

**Failure:**
```json
{"success": false, "error": "description of what went wrong"}
```

No other output to stdout. Diagnostic/debug info goes to stderr only.

---

## Error Handling

- All subcommands catch exceptions and return JSON error objects (never crash with a traceback to stdout)
- Timeout for all operations defaults to 10s, overridable with `--timeout`
- If pyautogui failsafe triggers (mouse in corner), return error explaining how to disable it

---

## Accessibility Permissions

PyAutoGUI on macOS requires the **terminal app** (Terminal.app, iTerm2, VS Code, etc.) to have accessibility permissions. This is simpler than Hammerspoon because:
- Users already grant this to their terminal
- The prompt appears automatically on first use
- No separate app to configure

If accessibility is missing, the `click` and `key` commands should detect this and print a helpful error:
```json
{"success": false, "error": "Accessibility permission required. Grant it to your terminal app in System Settings > Privacy & Security > Accessibility"}
```

---

## Testing

Each subcommand should be testable in isolation:

```bash
# Quick smoke test
da state                                          # should show frontmost app
da activate Calculator && da key 5 && da key return && da state   # type in Calculator
da screenshot --output /tmp/test.png              # capture screen
da quit Calculator
```

---

## Integration with Automation Agent

The automation agent's `Actuator` protocol expects these methods:
- `click(x, y) → dict`
- `type_text(text) → dict`
- `press_key(keys) → dict`
- `activate_app(app_name) → dict`
- `open_url(url) → dict`
- `quit_app(app_name) → dict`
- `get_state() → dict`

A thin Python wrapper can call the CLI tool via subprocess, or the tool can be imported directly as a library. Either way, the JSON output contract matches what the actuator protocol expects.

---

## Non-Goals

- **No daemon/server mode.** Each invocation is stateless. The automation agent calls it per-action.
- **No vision/AI.** This tool does not find elements or describe screens. That's the coordinator's job using screenshots from this tool + Claude API.
- **No workflow orchestration.** This tool does atomic actions only. The orchestrator composes them.
- **No Linux/Windows support.** macOS only (osascript, screencapture, open are macOS-specific).
