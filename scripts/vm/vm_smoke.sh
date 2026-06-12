#!/bin/bash
# Capability smoke test inside the macOS test VM.
# Verifies the SSH-driven process chain can do UI automation:
#   1. screen capture        (kTCCServiceScreenCapture)
#   2. synthetic keystrokes  (kTCCServiceAccessibility/PostEvent + AppleEvents)
#   3. accessibility reads   (System Events process list)
#   4. agent venv import
# Prints PASS/FAIL per capability; exits 1 if any FAIL.
set -u
fails=0

check() {
    local name="$1"; shift
    if "$@" >/tmp/smoke_out.txt 2>&1; then
        echo "PASS  $name"
    else
        echo "FAIL  $name -> $(head -c 200 /tmp/smoke_out.txt)"
        fails=$((fails + 1))
    fi
}

# 1. Screen capture produces a real image (>40KB rules out a black/denied frame)
capture_test() {
    rm -f /tmp/smoke.png
    screencapture -x /tmp/smoke.png || return 1
    local size
    size=$(stat -f%z /tmp/smoke.png 2>/dev/null || echo 0)
    [ "$size" -gt 40960 ]
}
check "screencapture" capture_test

# 2. Synthetic keystroke via System Events (Accessibility + AppleEvents)
check "synthetic_keystroke" osascript -e 'tell application "System Events" to key code 49'

# 3. Accessibility read: list visible processes
check "accessibility_read" osascript -e \
    'tell application "System Events" to get name of first application process whose frontmost is true'

# 4. Agent venv imports and CLI loads
check "agent_import" "$HOME/agent/.venv/bin/python" -c "import automation_agent; from automation_agent.actuator import create_actuator; print('ok')"

# 5. pyautogui can post a mouse move (CGEvent)
check "cgevent_mouse" "$HOME/agent/.venv/bin/python" -c "
import pyautogui
pyautogui.FAILSAFE = False
pyautogui.moveTo(200, 200)
print(pyautogui.position())
"

echo "----"
if [ "$fails" -gt 0 ]; then
    echo "SMOKE: $fails capability check(s) FAILED"
    exit 1
fi
echo "SMOKE: all capabilities OK"
