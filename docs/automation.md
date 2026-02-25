# macOS Automation Agent — Manual Setup Steps

## Prerequisites

### 1. Install Hammerspoon

Download from https://www.hammerspoon.org/ or:
```bash
brew install --cask hammerspoon
```

Verify installation:
```bash
ls /Applications/Hammerspoon.app
```

### 2. Grant Hammerspoon Accessibility Permissions

**CRITICAL**: Without this, all keyboard/mouse input injection is silently dropped.

1. Open **System Settings > Privacy & Security > Accessibility**
   ```bash
   open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
   ```
2. Click the lock icon and authenticate
3. If Hammerspoon is not in the list, click **+** and add `/Applications/Hammerspoon.app`
4. Toggle Hammerspoon **ON**
5. Restart Hammerspoon after granting permissions

**Verification**: After granting, check via the bridge:
```bash
curl -s http://localhost:27741/health | python3 -m json.tool
# Should show: "accessibility": true
```

### 3. Install hs.claude Extensions

Copy the Hammerspoon extensions from the `hammerspoon-ai` project:

```bash
# Source: hammerspoon-ai/extensions/claude/
# Destination: ~/.hammerspoon/hs/

mkdir -p ~/.hammerspoon/hs

# Copy all extension files
cp ~/workspace/hammerspoon-ai/extensions/claude/claude.lua        ~/.hammerspoon/hs/
cp ~/workspace/hammerspoon-ai/extensions/claude/claude_api.lua    ~/.hammerspoon/hs/
cp ~/workspace/hammerspoon-ai/extensions/claude/claude_vision.lua ~/.hammerspoon/hs/
cp ~/workspace/hammerspoon-ai/extensions/claude/claude_actions.lua ~/.hammerspoon/hs/
cp ~/workspace/hammerspoon-ai/extensions/claude/claude_parser.lua  ~/.hammerspoon/hs/
cp ~/workspace/hammerspoon-ai/extensions/claude/claude_agent.lua   ~/.hammerspoon/hs/
cp ~/workspace/hammerspoon-ai/extensions/claude/claude_prompts.lua ~/.hammerspoon/hs/
cp ~/workspace/hammerspoon-ai/extensions/claude/claude_coordinates.lua ~/.hammerspoon/hs/
cp ~/workspace/hammerspoon-ai/extensions/claude/claude_server.lua  ~/.hammerspoon/hs/
```

### 4. Configure Hammerspoon init.lua

Create or update `~/.hammerspoon/init.lua`:

```lua
-- Enable IPC (for hs CLI communication)
require("hs.ipc")

-- Log file for debugging
local logFile = io.open(os.getenv("HOME") .. "/.hammerspoon/debug.log", "w")
local function log(msg)
    local line = os.date("%H:%M:%S") .. " " .. msg
    print(line)
    if logFile then
        logFile:write(line .. "\n")
        logFile:flush()
    end
end

log("init.lua starting...")

-- hs.claude extension setup
local preload = function(m) return function() return require(m) end end
package.preload['hs.claude.api']         = preload 'hs.claude_api'
package.preload['hs.claude.vision']      = preload 'hs.claude_vision'
package.preload['hs.claude.actions']     = preload 'hs.claude_actions'
package.preload['hs.claude.parser']      = preload 'hs.claude_parser'
package.preload['hs.claude.agent']       = preload 'hs.claude_agent'
package.preload['hs.claude.prompts']     = preload 'hs.claude_prompts'
package.preload['hs.claude.coordinates'] = preload 'hs.claude_coordinates'
package.preload['hs.claude.server']      = preload 'hs.claude_server'

log("preloads registered")

-- Load hs.claude with error handling
local ok, err = pcall(function()
    hs.claude = require("hs.claude")
    log("hs.claude loaded")
end)
if not ok then
    log("ERROR loading hs.claude: " .. tostring(err))
    hs.alert.show("hs.claude load failed: " .. tostring(err))
    return
end

-- Configure Claude
-- GUI apps don't inherit shell env vars, so read from .zshrc if needed
ok, err = pcall(function()
    local apiKey = os.getenv("ANTHROPIC_API_KEY")
    if not apiKey then
        local f = io.open(os.getenv("HOME") .. "/.zshrc", "r")
        if f then
            for line in f:lines() do
                local key = line:match("^export%s+ANTHROPIC_API_KEY=(.+)$")
                if key then
                    apiKey = key:gsub("^[\"']", ""):gsub("[\"']$", "")
                    break
                end
            end
            f:close()
        end
    end
    log("API key present: " .. tostring(apiKey ~= nil and #apiKey > 0))
    hs.claude.configure({ apiKey = apiKey })
    log("hs.claude configured")
end)
if not ok then
    log("ERROR configuring hs.claude: " .. tostring(err))
end

-- Start HTTP server bridge
ok, err = pcall(function()
    local server = require("hs.claude.server")
    log("server module loaded")
    local started, url = server.start()
    if started then
        log("server started at " .. tostring(url))
        hs.alert.show("hs.claude server: " .. tostring(url))
    else
        log("server failed: " .. tostring(url))
        hs.alert.show("server failed: " .. tostring(url))
    end
end)
if not ok then
    log("ERROR starting server: " .. tostring(err))
    hs.alert.show("server error: " .. tostring(err))
end

log("init.lua complete")
```

### 5. Set ANTHROPIC_API_KEY

The API key must be available. Since Hammerspoon is a GUI app and doesn't inherit
shell environment variables, the key should be set in `~/.zshrc`:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

The init.lua above reads this file as a fallback.

### 6. Install Python Dependencies

```bash
pip3 install pydantic pydantic-settings httpx structlog anthropic
```

### 7. Start Hammerspoon

```bash
open -a Hammerspoon
```

Or click the Hammerspoon icon in the menu bar.

## Verification

### Quick Health Check
```bash
curl -s http://localhost:27741/health | python3 -m json.tool
```

Expected output:
```json
{
    "status": "ok",
    "configured": true,
    "accessibility": true,
    "version": "1.0.0"
}
```

**If `accessibility: false`**: Grant accessibility permissions (see step 2 above) and restart Hammerspoon.

**If `configured: false`**: Check that ANTHROPIC_API_KEY is set in `~/.zshrc`.

### Run E2E Bridge Test
```bash
cd /path/to/macos-automation-agent
python3 scripts/e2e_full_bridge_test.py
```

### Run Calculator E2E Test
```bash
python3 scripts/run_calculator_test.py
```

## Troubleshooting

### Hammerspoon IPC Timeouts
If `hs -c` commands timeout with "receive timeout" or "send timeout":
- Restart Hammerspoon: `killall Hammerspoon; sleep 1; open -a Hammerspoon`
- The HTTP bridge at port 27741 is more reliable than the `hs` CLI IPC

### Keystrokes Not Reaching Target App
Root cause: Hammerspoon lacks accessibility permissions.
- Check: `curl -s http://localhost:27741/health` — look for `"accessibility": true`
- Fix: System Settings > Privacy & Security > Accessibility > enable Hammerspoon
- **Workaround**: The bridge actuator automatically falls back to `osascript` (System Events
  AppleScript) for keyboard and mouse input when Hammerspoon lacks accessibility.
  This works because the terminal process typically has accessibility permissions.
  The fallback is transparent — no code changes needed.

### cliclick Steals Focus
`cliclick` (command-line click tool) brings the terminal to the foreground when executed
as a subprocess. This makes coordinate-based clicking unreliable. Prefer keyboard input
via `press_key` or `type_text` when possible. The orchestrator planner should be configured
to prefer keyboard shortcuts over vision-based clicking.

### Vision Operations Timeout
Vision operations (describe, findElement, check) call the Claude API and can take 5-30s.
- Ensure ANTHROPIC_API_KEY is valid and has API access
- Check the Hammerspoon console for API errors
- The poll timeout is 30 seconds by default

### Lua Module Changes Not Taking Effect
Hammerspoon caches Lua modules. A config reload (`hammerspoon://reload`) may not
reload all modules. Use a full restart instead:
```bash
killall Hammerspoon; sleep 1; open -a Hammerspoon
```

### Known Lua Fixes Applied
1. **`claude_prompts.lua`**: Fixed `]]]` long string ambiguity in Lua 5.1.
   Changed `[[...]]` to `[=[...]=]` (level-1 long strings).
2. **`claude_vision.lua`**: Fixed `encodeAsURLString` call — stock Hammerspoon
   takes 2 args `(scale, type)`, not 3 (no quality parameter).
3. **`claude_actions.lua`**: Fixed keyboard event targeting — `typeText` and
   `pressKey` now pass the frontmost app to `keyStrokes`/`keyStroke` to ensure
   events reach the correct window when called from HTTP server callbacks.
