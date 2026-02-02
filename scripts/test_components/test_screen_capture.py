#!/usr/bin/env python3
"""
Real component test: Screen capture
Tests actual screenshot capture and saves real images
"""
import sys
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from automation_agent.perception.capture import ScreenCapturer
from automation_agent.config import AgentConfig


def test_screen_capture():
    """Test real screen capture"""
    print("=" * 60)
    print("COMPONENT TEST: Screen Capture")
    print("=" * 60)

    config = AgentConfig()
    capturer = ScreenCapturer(config)

    # Test 1: Get screen size
    print("\n[Test 1] Getting screen size...")
    width, height = capturer.get_screen_size()
    print(f"  Screen size: {width}x{height}")
    print(f"  Status: ✅ Screen size detected")

    # Test 2: Capture full screenshot
    print("\n[Test 2] Capturing full screenshot...")
    try:
        screenshot = capturer.capture_screen()
        print(f"  Image size: {screenshot.size}")
        print(f"  Image mode: {screenshot.mode}")
        print(f"  Status: ✅ Screenshot captured")

        # Save screenshot
        output_dir = Path(__file__).parent / "output"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"screenshot_full_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        screenshot.save(output_path)
        print(f"  Saved to: {output_path}")

    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        print(f"  Note: May require Screen Recording permission in System Settings")
        return False

    # Test 3: Capture region
    print("\n[Test 3] Capturing screen region (top-left 400x300)...")
    try:
        region_screenshot = capturer.capture_region(0, 0, 400, 300)
        print(f"  Image size: {region_screenshot.size}")
        print(f"  Status: ✅ Region captured")

        # Save region screenshot
        output_path = output_dir / f"screenshot_region_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        region_screenshot.save(output_path)
        print(f"  Saved to: {output_path}")

    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 4: Get base64 encoding
    print("\n[Test 4] Testing base64 encoding...")
    try:
        b64_data = capturer.capture_screen_b64()
        print(f"  Base64 length: {len(b64_data)} characters")
        print(f"  First 50 chars: {b64_data[:50]}...")
        print(f"  Status: ✅ Base64 encoding working")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    print("\n" + "=" * 60)
    print("RESULT: ✅ All tests passed")
    print(f"OUTPUT: Check {output_dir} for captured images")
    print("=" * 60)
    return True


if __name__ == "__main__":
    result = test_screen_capture()
    sys.exit(0 if result else 1)
