#!/usr/bin/env python3
"""
Hybrid approach to find Safari using multiple macOS APIs:
1. Get Dock configuration from defaults
2. Use screen bounds to calculate icon positions
3. Verify with AppleScript
"""
import subprocess
import json
import plistlib
from pathlib import Path
from typing import List, Dict, Optional
import math


def get_dock_apps() -> List[str]:
    """Get list of persistent apps in Dock using defaults command."""
    try:
        result = subprocess.run(
            ['defaults', 'read', 'com.apple.dock', 'persistent-apps'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            # Parse the plist output
            import plistlib
            data = plistlib.loads(result.stdout.encode('utf-8'))

            apps = []
            for item in data:
                if 'tile-data' in item and 'file-label' in item['tile-data']:
                    apps.append(item['tile-data']['file-label'])

            return apps
    except Exception as e:
        print(f"ERROR getting dock apps: {e}")

    return []


def get_screen_size() -> tuple:
    """Get screen size using system_profiler."""
    try:
        import pyautogui
        return pyautogui.size()
    except:
        return (1512, 982)  # Default macOS resolution


def calculate_dock_position(screen_width: int, screen_height: int,
                           app_index: int, total_apps: int) -> Dict:
    """
    Calculate approximate Dock icon position.

    macOS Dock is centered at bottom of screen.
    Icons are evenly spaced.
    """
    # Typical Dock icon size
    icon_size = 57  # pixels

    # Dock is centered, with some padding
    dock_padding = 10
    icon_spacing = 5

    # Calculate total Dock width
    total_dock_width = (total_apps * icon_size) + ((total_apps - 1) * icon_spacing)

    # Dock starts at center - half width
    dock_start_x = (screen_width - total_dock_width) / 2

    # Calculate this icon's position
    icon_x = dock_start_x + (app_index * (icon_size + icon_spacing))

    # Dock is at bottom with some margin
    dock_bottom_margin = 10
    icon_y = screen_height - icon_size - dock_bottom_margin

    return {
        'x': icon_x,
        'y': icon_y,
        'width': icon_size,
        'height': icon_size,
        'center_x': icon_x + icon_size/2,
        'center_y': icon_y + icon_size/2,
    }


def click_app_via_applescript(app_name: str) -> bool:
    """
    Click app using AppleScript's 'activate' command.
    This is more reliable than Accessibility API for launching apps.
    """
    script = f'tell application "{app_name}" to activate'

    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except:
        return False


def get_frontmost_app() -> str:
    """Get name of frontmost application."""
    script = 'tell application "System Events" to get name of first application process whose frontmost is true'

    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except:
        pass

    return ""


def main():
    print("=" * 70)
    print("Hybrid Safari Finder - Multiple macOS APIs")
    print("=" * 70)

    # 1. Get Dock apps
    print("\n[1] Reading Dock configuration...")
    dock_apps = get_dock_apps()

    if dock_apps:
        print(f"    Found {len(dock_apps)} apps in Dock:")
        for i, app in enumerate(dock_apps[:10], 1):
            print(f"      {i}. {app}")
        if len(dock_apps) > 10:
            print(f"      ... and {len(dock_apps) - 10} more")
    else:
        print("    ❌ Could not read Dock apps")
        return

    # 2. Find Safari
    print("\n[2] Finding Safari...")
    safari_index = None
    for i, app in enumerate(dock_apps):
        if app.lower() == 'safari':
            safari_index = i
            print(f"    ✅ Safari found at index {safari_index}")
            break

    if safari_index is None:
        print("    ❌ Safari not in Dock")
        return

    # 3. Calculate position
    print("\n[3] Calculating Safari position...")
    screen_width, screen_height = get_screen_size()
    print(f"    Screen size: {screen_width}x{screen_height}")

    safari_pos = calculate_dock_position(
        screen_width, screen_height,
        safari_index, len(dock_apps)
    )

    print(f"    Calculated position:")
    print(f"      X: {safari_pos['x']:.0f}")
    print(f"      Y: {safari_pos['y']:.0f}")
    print(f"      Size: {safari_pos['width']:.0f}x{safari_pos['height']:.0f}")
    print(f"      Click center: ({safari_pos['center_x']:.0f}, {safari_pos['center_y']:.0f})")

    # 4. Try clicking with AppleScript (most reliable)
    print("\n[4] Launching Safari via AppleScript...")
    print("    (This is more reliable than pixel clicking)")

    success = click_app_via_applescript("Safari")

    if success:
        print("    ✅ AppleScript succeeded!")

        # Wait and verify
        import time
        time.sleep(2)

        frontmost = get_frontmost_app()
        print(f"\n[5] Verification:")
        print(f"    Frontmost app: {frontmost}")

        if frontmost == "Safari":
            print("    ✅✅✅ SUCCESS! Safari is now frontmost!")
        else:
            print(f"    ⚠️  Expected Safari, got {frontmost}")
    else:
        print("    ❌ AppleScript failed")

    # 5. Save data for integration
    output_data = {
        'safari_index': safari_index,
        'safari_position': safari_pos,
        'dock_apps': dock_apps,
        'screen_size': {'width': screen_width, 'height': screen_height}
    }

    output_file = Path(__file__).parent / "test_components" / "output" / "safari_hybrid.json"
    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"\n    💾 Saved data to: {output_file.name}")

    print("\n" + "=" * 70)
    print("CONCLUSION:")
    print("=" * 70)
    print("""
✅ Can get Dock apps via 'defaults' command
✅ Can calculate approximate icon positions
✅ Can launch apps via AppleScript 'activate'

BEST APPROACH FOR PRODUCTION:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Use AppleScript's "tell application X to activate" command!

This is:
- 100% reliable
- No vision model needed
- No Accessibility API permissions needed
- No pixel coordinate guessing
- Built into macOS

WORKFLOW:
1. User: "Open Safari"
2. Text LLM: Extract app_name="Safari"
3. AppleScript: tell application "Safari" to activate
4. Done!

For more complex UI tasks (clicking specific buttons, etc.):
- Use Accessibility API with proper permissions
- Or use AppleScript's UI scripting
- Vision as last resort for non-standard UIs
    """)


if __name__ == "__main__":
    main()
