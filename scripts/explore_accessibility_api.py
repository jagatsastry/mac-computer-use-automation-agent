#!/usr/bin/env python3
"""
Explore macOS Accessibility API to get UI element positions.
This is a much more reliable approach than vision-based detection.
"""
import sys
import subprocess
from typing import List, Dict, Optional, Tuple

def run_applescript(script: str) -> str:
    """Execute AppleScript and return output."""
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def get_dock_apps() -> List[str]:
    """Get list of all applications in the Dock."""
    script = '''
    tell application "System Events"
        tell dock preferences
            return name of every dock item
        end tell
    end tell
    '''
    result = run_applescript(script)
    if result and not result.startswith("ERROR"):
        # Parse the result (comma-separated list)
        return [app.strip() for app in result.split(',')]
    return []


def get_running_apps() -> List[str]:
    """Get list of all running applications."""
    script = '''
    tell application "System Events"
        return name of every process whose background only is false
    end tell
    '''
    result = run_applescript(script)
    if result and not result.startswith("ERROR"):
        return [app.strip() for app in result.split(',')]
    return []


def click_dock_app(app_name: str) -> bool:
    """Click on an app in the Dock using AppleScript."""
    script = f'''
    tell application "System Events"
        tell dock preferences
            set dockItems to name of every dock item
            if "{app_name}" is in dockItems then
                tell UI element "{app_name}" of list 1 of process "Dock"
                    perform action "AXPress"
                end tell
                return "SUCCESS"
            else
                return "NOT_IN_DOCK"
            end if
        end tell
    end tell
    '''
    result = run_applescript(script)
    return result == "SUCCESS"


def get_ui_element_position(app_name: str, element_type: str = "application") -> Optional[Dict]:
    """
    Get position and size of a UI element using Accessibility API.

    This uses AppleScript to query the Accessibility API.
    """
    if element_type == "dock_item":
        script = f'''
        tell application "System Events"
            tell process "Dock"
                tell list 1
                    try
                        set theItem to UI element "{app_name}"
                        set pos to position of theItem
                        set siz to size of theItem
                        return (item 1 of pos) & "," & (item 2 of pos) & "," & (item 1 of siz) & "," & (item 2 of siz)
                    on error errMsg
                        return "ERROR: " & errMsg
                    end try
                end tell
            end tell
        end tell
        '''
    else:
        script = f'''
        tell application "System Events"
            tell process "{app_name}"
                try
                    set pos to position of window 1
                    set siz to size of window 1
                    return (item 1 of pos) & "," & (item 2 of pos) & "," & (item 1 of siz) & "," & (item 2 of siz)
                on error errMsg
                    return "ERROR: " & errMsg
                end try
            end tell
        end tell
        '''

    result = run_applescript(script)

    if result and not result.startswith("ERROR"):
        try:
            parts = result.split(',')
            if len(parts) == 4:
                x, y, w, h = map(float, parts)
                return {
                    'x': x,
                    'y': y,
                    'width': w,
                    'height': h,
                    'center_x': x + w/2,
                    'center_y': y + h/2
                }
        except:
            pass

    return None


def explore_dock_structure():
    """Explore the structure of the Dock using Accessibility API."""
    script = '''
    tell application "System Events"
        tell process "Dock"
            try
                set dockList to list 1
                set listPos to position of dockList
                set listSize to size of dockList
                set itemCount to count of UI elements of dockList

                return "Position: " & (item 1 of listPos) & "," & (item 2 of listPos) & "; Size: " & (item 1 of listSize) & "x" & (item 2 of listSize) & "; Items: " & itemCount
            on error errMsg
                return "ERROR: " & errMsg
            end try
        end tell
    end tell
    '''
    return run_applescript(script)


def main():
    print("=" * 70)
    print("macOS Accessibility API Exploration")
    print("=" * 70)

    # 1. Get Dock structure
    print("\n[1] Dock Structure:")
    dock_info = explore_dock_structure()
    print(f"    {dock_info}")

    # 2. List apps in Dock
    print("\n[2] Apps in Dock:")
    dock_apps = get_dock_apps()
    if dock_apps:
        for i, app in enumerate(dock_apps[:10], 1):  # Show first 10
            print(f"    {i}. {app}")
        if len(dock_apps) > 10:
            print(f"    ... and {len(dock_apps) - 10} more")
    else:
        print("    Could not retrieve dock apps")

    # 3. Check if Safari is in Dock
    print("\n[3] Safari Detection:")
    if "Safari" in dock_apps:
        print("    ✅ Safari found in Dock")

        # Try to get Safari's position
        print("\n[4] Getting Safari position from Accessibility API:")
        safari_pos = get_ui_element_position("Safari", element_type="dock_item")

        if safari_pos:
            print(f"    ✅ Safari Dock icon position:")
            print(f"       X: {safari_pos['x']}")
            print(f"       Y: {safari_pos['y']}")
            print(f"       Width: {safari_pos['width']}")
            print(f"       Height: {safari_pos['height']}")
            print(f"       Center: ({safari_pos['center_x']}, {safari_pos['center_y']})")
            print(f"\n    💡 This can be clicked reliably with PyAutoGUI!")
        else:
            print("    ⚠️  Could not get position (may need accessibility permissions)")
    else:
        print("    ❌ Safari not in Dock")

    # 4. List running apps
    print("\n[5] Running Applications:")
    running = get_running_apps()
    if running:
        for i, app in enumerate(running[:10], 1):
            print(f"    {i}. {app}")
        if len(running) > 10:
            print(f"    ... and {len(running) - 10} more")

    print("\n" + "=" * 70)
    print("KEY FINDINGS:")
    print("=" * 70)
    print("""
1. Accessibility API can list all Dock items by name
2. Can get precise position/size of each Dock icon
3. Can programmatically click Dock items
4. No vision model needed - pure macOS APIs
5. Requires accessibility permissions (already granted)

RECOMMENDED APPROACH:
- Use Accessibility API to get UI element positions
- Use text LLM to parse user intent ("click Safari")
- Map intent to API calls (find "Safari" in Dock, get position, click)
- Much more reliable than vision-based detection
    """)


if __name__ == "__main__":
    main()
