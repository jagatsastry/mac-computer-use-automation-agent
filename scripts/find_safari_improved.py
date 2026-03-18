#!/usr/bin/env python3
"""
Improved Safari detection using two-stage approach:
1. List all dock icons
2. Extract Safari's coordinates from the list
"""
import asyncio
import sys
import re
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation_agent.llm.client import OllamaClient
from automation_agent.perception.capture import ScreenCapturer
from automation_agent.actions.simple import ClickAction
from automation_agent.config import AgentConfig


def draw_box(image, x1, y1, x2, y2, label, color="red"):
    """Draw box with label."""
    draw = ImageDraw.Draw(image)
    draw.rectangle([x1, y1, x2, y2], outline=color, width=4)

    cx, cy = (x1+x2)//2, (y1+y2)//2
    draw.line([(cx-15, cy), (cx+15, cy)], fill=color, width=3)
    draw.line([(cx, cy-15), (cx, cy+15)], fill=color, width=3)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
    except:
        font = ImageFont.load_default()

    draw.text((x1+5, y1-25), label, fill=color, font=font)


async def find_safari_improved():
    """Find Safari using improved two-stage approach."""
    print("=" * 70)
    print("IMPROVED: Find Safari in Dock")
    print("=" * 70)

    config = AgentConfig()
    client = OllamaClient(host=config.ollama_host, timeout=config.ollama_timeout)
    capturer = ScreenCapturer(config)

    output_dir = Path(__file__).parent / "test_components" / "output"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # Capture screenshot
    print("\n[1] Capturing screenshot...")
    screenshot = capturer.capture_screen()
    img_width, img_height = screenshot.size
    print(f"  Screen: {img_width}x{img_height}")

    img_b64 = capturer.capture_screen_b64()

    # Two-stage approach: List all icons first
    print("\n[2] Listing ALL dock icons...")

    prompt = """Look at the Dock at the bottom of this macOS screenshot.
List ALL visible application icons from left to right.
For EACH icon, provide ONLY: AppName <box>(x1,y1,x2,y2)</box>

Example format:
Finder <box>(19,952,59,999)</box>
Calendar <box>(59,952,85,999)</box>

No descriptions, just: AppName <box>(coords)</box>"""

    response = await client.generate_vision(
        model=config.vision_model,
        prompt=prompt,
        image_b64=img_b64
    )

    print(f"\n  Model response:\n{response}\n")

    # Parse the response to find Safari
    print("\n[3] Extracting Safari coordinates...")

    # Look for Safari line
    safari_pattern = r'Safari\s*[:\-]?\s*<box>\(([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)\)</box>'
    safari_match = re.search(safari_pattern, response, re.IGNORECASE)

    if safari_match:
        x1, y1, x2, y2 = map(float, safari_match.groups())

        # Normalize if needed (0-1000 range)
        if x1 <= 1.0:  # Already normalized 0-1
            pass
        elif x1 <= 1000:  # Normalized 0-1000
            x1, y1, x2, y2 = x1/1000, y1/1000, x2/1000, y2/1000

        # Ensure correct order
        if x1 > x2: x1, x2 = x2, x1
        if y1 > y2: y1, y2 = y2, y1

        # Convert to physical pixels (for visualization)
        px1 = int(x1 * img_width)
        py1 = int(y1 * img_height)
        px2 = int(x2 * img_width)
        py2 = int(y2 * img_height)

        # Get logical screen size for clicking
        import pyautogui
        logical_width, logical_height = pyautogui.size()

        # Convert to logical pixels (for PyAutoGUI clicks)
        lx1 = int(x1 * logical_width)
        ly1 = int(y1 * logical_height)
        lx2 = int(x2 * logical_width)
        ly2 = int(y2 * logical_height)

        print(f"  ✅ Safari found!")
        print(f"    Normalized: ({x1:.3f}, {y1:.3f}, {x2:.3f}, {y2:.3f})")
        print(f"    Physical pixels: ({px1}, {py1}, {px2}, {py2})")
        print(f"    Logical pixels: ({lx1}, {ly1}, {lx2}, {ly2})")
        print(f"    Size: {px2-px1}x{py2-py1} physical, {lx2-lx1}x{ly2-ly1} logical")

        center_x = (lx1 + lx2) // 2
        center_y = (ly1 + ly2) // 2
        print(f"    Click point (logical): ({center_x}, {center_y})")

        # Verify by extracting and checking the region
        print("\n[4] Verifying detection...")

        padding = 10
        crop_x1 = max(0, px1 - padding)
        crop_y1 = max(0, py1 - padding)
        crop_x2 = min(img_width, px2 + padding)
        crop_y2 = min(img_height, py2 + padding)

        region = screenshot.crop((crop_x1, crop_y1, crop_x2, crop_y2))

        # Ask model to verify
        import io, base64
        buffered = io.BytesIO()
        region.save(buffered, format="PNG")
        region_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

        verify_prompt = "What application icon is this? Answer with just the app name."

        verify_response = await client.generate_vision(
            model=config.vision_model,
            prompt=verify_prompt,
            image_b64=region_b64
        )

        print(f"  Model says: {verify_response.strip()}")

        if 'safari' in verify_response.lower():
            print(f"  ✅ Verification: Correct! This is Safari")
        else:
            print(f"  ⚠️  Verification: Model identified as '{verify_response.strip()}'")
            print(f"  Proceeding anyway based on dock listing...")

        # Visualize
        print("\n[5] Creating visualization...")

        annotated = screenshot.copy()
        draw_box(annotated, px1, py1, px2, py2,
                f"Safari - Click: ({center_x},{center_y})", "green")

        annotated_path = output_dir / f"safari_improved_{timestamp}.png"
        annotated.save(annotated_path)
        print(f"  Saved: {annotated_path.name}")

        # Click Safari
        print(f"\n[6] Clicking Safari at ({center_x}, {center_y})...")

        click_action = ClickAction(x=center_x, y=center_y)

        if await click_action.validate():
            result = await click_action.execute()
            if result.success:
                print(f"  ✅ Clicked successfully!")
                print(f"\n  Safari should be opening...")
                return True
            else:
                print(f"  ❌ Click failed: {result.error}")
        else:
            print(f"  ❌ Invalid coordinates")

    else:
        print(f"  ❌ Could not find Safari in dock listing")
        print(f"  Response might need better parsing")

    return False


if __name__ == "__main__":
    result = asyncio.run(find_safari_improved())
    sys.exit(0 if result else 1)
