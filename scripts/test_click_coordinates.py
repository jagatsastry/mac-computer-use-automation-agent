#!/usr/bin/env python3
"""
Simple test to verify click coordinates are working correctly.
This test:
1. Opens OpenTable for Joey's restaurant
2. Waits for page to load
3. Takes a screenshot and asks Claude to find a time slot
4. Verifies the returned coordinates make sense
5. Clicks and verifies
"""

import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Load .env file
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

from automation_agent.config import AgentConfig, ModelProvider
from automation_agent.perception.capture import ScreenCapturer
from automation_agent.actions.applescript import OpenURLAction
from automation_agent.actions.simple import ClickAction
from PIL import Image, ImageDraw
import pyautogui


async def main():
    print("=" * 60)
    print("CLICK COORDINATE VERIFICATION TEST")
    print("=" * 60)

    config = AgentConfig()
    config.model_provider = ModelProvider.ANTHROPIC

    if not config.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        return

    # Create Anthropic client
    from automation_agent.llm.anthropic_client import AnthropicClient
    client = AnthropicClient(api_key=config.anthropic_api_key)
    capturer = ScreenCapturer(config)

    # Step 1: Open OpenTable
    print("\n1. Opening OpenTable...")
    url = "https://www.opentable.com/s?term=Joey+Valley+Fair&covers=2"
    action = OpenURLAction(url=url, browser="Safari")
    result = await action.execute()
    print(f"   URL opened: {result.success}")

    # Wait for page to load
    print("   Waiting 5 seconds for page to load...")
    await asyncio.sleep(5)

    # Step 2: Take screenshot
    print("\n2. Taking screenshot...")
    screenshot = capturer.capture_screen()
    screenshot_w, screenshot_h = screenshot.size
    print(f"   Screenshot size: {screenshot_w} x {screenshot_h}")

    logical_w, logical_h = pyautogui.size()
    print(f"   Logical screen: {logical_w} x {logical_h}")

    scale_factor = screenshot_w / logical_w
    print(f"   Scale factor: {scale_factor}")

    # Save screenshot
    os.makedirs("/tmp/agent_screenshots", exist_ok=True)
    screenshot.save("/tmp/agent_screenshots/click_test_before.png")
    print("   Saved: /tmp/agent_screenshots/click_test_before.png")

    # Step 3: Ask Claude to find a time slot
    print("\n3. Asking Claude to find time slot closest to 7:00 PM...")

    screenshot_b64 = capturer.capture_screen_b64()

    prompt = """Find the time slot button closest to 7:00 PM on this OpenTable page.

Return the bounding box in this EXACT format:
<box>(x1,y1,x2,y2)</box>

Where coordinates are normalized 0-1000 (top-left is 0,0 and bottom-right is 1000,1000).

If no time slots are visible, respond with:
<box>NOT_FOUND</box>

Look for buttons showing times like "5:00 PM", "7:30 PM", etc.
Return ONLY the box tag."""

    response = await client.generate_vision(
        model=config.anthropic_vision_model,
        prompt=prompt,
        image_b64=screenshot_b64,
    )

    print(f"   Claude's response: {response}")

    # Step 4: Parse coordinates
    import re
    match = re.search(r"<box>\s*\(?\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)?\s*</box>", response)

    if not match:
        if "NOT_FOUND" in response:
            print("   Claude could not find time slots on this page")
        else:
            print(f"   Could not parse coordinates from response")
        return

    x1_norm = int(match.group(1))
    y1_norm = int(match.group(2))
    x2_norm = int(match.group(3))
    y2_norm = int(match.group(4))

    print(f"\n4. Parsed normalized coordinates (0-1000 scale):")
    print(f"   Box: ({x1_norm}, {y1_norm}) to ({x2_norm}, {y2_norm})")

    # Convert to screenshot pixels
    x1_px = int(x1_norm * screenshot_w / 1000)
    y1_px = int(y1_norm * screenshot_h / 1000)
    x2_px = int(x2_norm * screenshot_w / 1000)
    y2_px = int(y2_norm * screenshot_h / 1000)

    center_x = (x1_px + x2_px) // 2
    center_y = (y1_px + y2_px) // 2

    print(f"\n5. Converted to screenshot pixels:")
    print(f"   Box: ({x1_px}, {y1_px}) to ({x2_px}, {y2_px})")
    print(f"   Center: ({center_x}, {center_y})")

    # Draw bounding box on screenshot
    draw = ImageDraw.Draw(screenshot)
    draw.rectangle([(x1_px, y1_px), (x2_px, y2_px)], outline='red', width=5)
    draw.ellipse([(center_x-10, center_y-10), (center_x+10, center_y+10)], fill='red')

    # Add text label
    draw.text((x1_px, y1_px - 30), f"Target: ({center_x}, {center_y})", fill='red')

    screenshot.save("/tmp/agent_screenshots/click_test_target.png")
    print("   Saved with bounding box: /tmp/agent_screenshots/click_test_target.png")

    # Step 6: Calculate click coordinates (what ClickAction will do)
    scaled_x = int(center_x / scale_factor)
    scaled_y = int(center_y / scale_factor)

    print(f"\n6. Click coordinates (scaled for Retina):")
    print(f"   Screenshot coords: ({center_x}, {center_y})")
    print(f"   Scaled for click:  ({scaled_x}, {scaled_y})")

    # Sanity check
    if scaled_x > logical_w or scaled_y > logical_h:
        print(f"   WARNING: Scaled coords out of bounds!")
        return

    if scaled_x < 0 or scaled_y < 0:
        print(f"   WARNING: Negative coordinates!")
        return

    print(f"   Coordinates look valid (within {logical_w}x{logical_h} screen)")

    # Step 7: Ask user to confirm before clicking
    print(f"\n7. Ready to click at ({scaled_x}, {scaled_y})")
    print("   Check /tmp/agent_screenshots/click_test_target.png to verify target")

    # Perform the click
    print("\n8. Clicking...")
    click_action = ClickAction(x=center_x, y=center_y)
    click_result = await click_action.execute()

    print(f"   Click success: {click_result.success}")
    if hasattr(click_result, 'metadata'):
        print(f"   Metadata: {click_result.metadata}")

    # Take screenshot after click
    await asyncio.sleep(1)
    after_screenshot = capturer.capture_screen()
    after_screenshot.save("/tmp/agent_screenshots/click_test_after.png")
    print("   Saved: /tmp/agent_screenshots/click_test_after.png")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("Compare click_test_before.png, click_test_target.png, and click_test_after.png")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
