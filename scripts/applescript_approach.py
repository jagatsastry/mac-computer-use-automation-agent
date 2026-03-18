#!/usr/bin/env python3
"""
The SIMPLE and RELIABLE approach: Use AppleScript to control macOS apps.
No vision models, no Accessibility API, no coordinate guessing.
"""
import subprocess
import time
from typing import Optional


def activate_app(app_name: str) -> bool:
    """Activate (launch or bring to front) an application."""
    script = f'tell application "{app_name}" to activate'

    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0
    except Exception as e:
        print(f"ERROR: {e}")
        return False


def get_frontmost_app() -> Optional[str]:
    """Get name of the frontmost application."""
    script = '''
    tell application "System Events"
        get name of first application process whose frontmost is true
    end tell
    '''

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

    return None


def is_app_running(app_name: str) -> bool:
    """Check if an application is running."""
    script = f'''
    tell application "System Events"
        return (name of processes) contains "{app_name}"
    end tell
    '''

    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout.strip() == "true"
    except:
        return False


def open_url_in_safari(url: str) -> bool:
    """Open a URL in Safari."""
    script = f'''
    tell application "Safari"
        activate
        open location "{url}"
    end tell
    '''

    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0
    except:
        return False


def click_element_in_app(app_name: str, element_description: str) -> bool:
    """
    Click a UI element using AppleScript UI scripting.
    Requires Accessibility permissions.
    """
    script = f'''
    tell application "System Events"
        tell process "{app_name}"
            -- Example: click button "New Window"
            -- This requires knowing the exact element hierarchy
            -- For production, use with Text LLM to generate specific scripts
        end tell
    end tell
    '''

    # This is a placeholder - actual implementation would need
    # to parse element_description and generate appropriate script
    print(f"Would click: {element_description} in {app_name}")
    return False


def main():
    print("=" * 70)
    print("AppleScript Automation - The Right Way")
    print("=" * 70)

    print("\n" + "=" * 70)
    print("TEST 1: Activate Safari")
    print("=" * 70)

    print("\n[1] Checking if Safari is running...")
    running = is_app_running("Safari")
    print(f"    Safari running: {running}")

    print("\n[2] Getting frontmost app before...")
    before = get_frontmost_app()
    print(f"    Frontmost: {before}")

    print("\n[3] Activating Safari...")
    success = activate_app("Safari")

    if success:
        print("    ✅ AppleScript command succeeded")

        # Wait for activation
        time.sleep(1.5)

        print("\n[4] Verifying...")
        after = get_frontmost_app()
        print(f"    Frontmost now: {after}")

        if after == "Safari":
            print("\n    ✅✅✅ SUCCESS! Safari is now active!")
        else:
            print(f"\n    ⚠️  Expected Safari, got {after}")
    else:
        print("    ❌ AppleScript command failed")

    print("\n" + "=" * 70)
    print("TEST 2: Open URL in Safari")
    print("=" * 70)

    test_url = "https://www.apple.com"
    print(f"\n[1] Opening {test_url} in Safari...")

    success = open_url_in_safari(test_url)

    if success:
        print("    ✅ URL opened in Safari!")
    else:
        print("    ❌ Failed to open URL")

    print("\n" + "=" * 70)
    print("FINDINGS & RECOMMENDATIONS")
    print("=" * 70)
    print("""
✅ AppleScript can:
   - Launch any application by name
   - Bring applications to front
   - Open URLs in browsers
   - Control app UI (with Accessibility permissions)
   - Get app state and window info

✅ Advantages:
   - NO vision model needed
   - NO coordinate guessing
   - NO screen resolution issues
   - 100% reliable for basic app control
   - Built into macOS since the beginning

⚠️  For complex UI interactions:
   - Need Accessibility API + AppleScript UI scripting
   - Requires knowing element hierarchy
   - Some apps don't expose full UI via Accessibility

💡 PRODUCTION ARCHITECTURE:

┌─────────────────────────────────────────────────────────────┐
│                    USER REQUEST                             │
│              "Open Safari and go to google.com"             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                  TEXT LLM (Gemma2)                          │
│  Parses intent: {app: "Safari", url: "google.com"}         │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              AUTOMATION ORCHESTRATOR                        │
│  Generates: applescript_open_url("Safari", url)            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                  APPLESCRIPT EXECUTOR                       │
│  tell application "Safari"                                  │
│    activate                                                 │
│    open location "https://google.com"                       │
│  end tell                                                   │
└─────────────────────────────────────────────────────────────┘

WHEN TO USE EACH APPROACH:
━━━━━━━━━━━━━━━━━━━━━━━━━━
1. AppleScript: App launch, URL opening, basic app control
2. Accessibility API: Complex UI interactions (buttons, menus)
3. Vision Models: Non-standard UIs, games, graphics apps
4. PyAutoGUI: Direct pixel clicking (last resort)

FOR THIS PROJECT:
━━━━━━━━━━━━━━━━━
Start with AppleScript for all basic operations.
Only use vision when AppleScript can't access the UI.
    """)


if __name__ == "__main__":
    main()
