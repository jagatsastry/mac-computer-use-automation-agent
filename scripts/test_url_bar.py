#!/usr/bin/env python3
"""
Find and click Safari URL bar using vision model
"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import re

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation_agent.llm.client import OllamaClient
from automation_agent.perception.capture import ScreenCapturer
from automation_agent.actions.simple import ClickAction
from automation_agent.config import AgentConfig


def parse_coordinates(text: str):
    """Parse any coordinate format from response."""
    # Try to find any numbers that look like coordinates
    patterns = [
        r'<box>\(([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)\)</box>',
        r'\(([0-9.]+),\s*([0-9.]+),\s*([0-9.]+),\s*([0-9.]+)\)',
        r'x1[=:]?\s*([0-9.]+).*?y1[=:]?\s*([0-9.]+).*?x2[=:]?\s*([0-9.]+).*?y2[=:]?\s*([0-9.]+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            coords = [float(x) for x in match.groups()]
            return coords

    return None


def draw_box(image, x1, y1, x2, y2, label, color="red"):
    """Draw box on image."""
    draw = ImageDraw.Draw(image)
    draw.rectangle([x1, y1, x2, y2], outline=color, width=4)

    # Crosshair at center
    cx, cy = (x1+x2)//2, (y1+y2)//2
    draw.line([(cx-15, cy), (cx+15, cy)], fill=color, width=3)
    draw.line([(cx, cy-15), (cx, cy+15)], fill=color, width=3)

    # Label
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
    except:
        font = ImageFont.load_default()

    draw.text((x1+5, y1-25), label, fill=color, font=font)


async def find_url_bar():
    """Find URL bar using vision model."""
    print("=" * 70)
    print("Finding Safari URL Bar")
    print("=" * 70)

    config = AgentConfig()
    client = OllamaClient(host=config.ollama_host, timeout=config.ollama_timeout)
    capturer = ScreenCapturer(config)

    # Capture screen
    print("\n[1] Capturing screen...")
    screenshot = capturer.capture_screen()
    img_width, img_height = screenshot.size
    print(f"  Screen: {img_width}x{img_height}")

    img_b64 = capturer.capture_screen_b64()

    # Ask model about URL bar
    print("\n[2] Asking vision model to find URL bar...")

    # Try a simpler approach - ask for description first
    prompt1 = "Describe the Safari window you see. Where is the address/URL bar located? Be specific about its position (top, middle, bottom) and approximate location."

    print("  Getting general description...")
    response1 = await client.generate_vision(
        model=config.vision_model,
        prompt=prompt1,
        image_b64=img_b64
    )
    print(f"\n  Description:\n  {response1}\n")

    # Now ask for approximate coordinates
    prompt2 = f"""Based on this Safari window, estimate the URL/address bar location.
Give me approximate coordinates as percentages of the screen.
For example: "The URL bar is at approximately x=10-90%, y=5-10% of the screen"
Be specific with numbers."""

    print("  Getting coordinates...")
    response2 = await client.generate_vision(
        model=config.vision_model,
        prompt=prompt2,
        image_b64=img_b64
    )
    print(f"\n  Coordinates:\n  {response2}\n")

    # Parse any numbers from response
    import re
    numbers = re.findall(r'(\d+(?:\.\d+)?)\s*%', response2)
    if len(numbers) >= 4:
        x1_pct, x2_pct, y1_pct, y2_pct = map(float, numbers[:4])

        # Convert to pixels
        x1 = int(img_width * x1_pct / 100)
        x2 = int(img_width * x2_pct / 100)
        y1 = int(img_height * y1_pct / 100)
        y2 = int(img_height * y2_pct / 100)

        # Ensure correct order
        if x1 > x2: x1, x2 = x2, x1
        if y1 > y2: y1, y2 = y2, y1

        print(f"  ✅ Parsed coordinates: ({x1}, {y1}, {x2}, {y2})")
        print(f"  Size: {x2-x1}x{y2-y1} pixels")

        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2
        print(f"  Click point: ({center_x}, {center_y})")

        # Visualize
        output_dir = Path(__file__).parent / "test_components" / "output"
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        annotated = screenshot.copy()
        draw_box(annotated, x1, y1, x2, y2, f"URL Bar - Click: ({center_x},{center_y})", "blue")

        annotated_path = output_dir / f"url_bar_annotated_{timestamp}.png"
        annotated.save(annotated_path)
        print(f"\n  📸 Saved: {annotated_path.name}")

        # Click
        print(f"\n[3] Clicking URL bar at ({center_x}, {center_y})...")
        click_action = ClickAction(x=center_x, y=center_y)

        if await click_action.validate():
            result = await click_action.execute()
            if result.success:
                print(f"  ✅ Clicked!")
                print(f"\n  URL bar should now be focused and ready for input")
                return True
            else:
                print(f"  ❌ Click failed: {result.error}")
        else:
            print(f"  ❌ Invalid coordinates")
    else:
        # Fallback: Use typical Safari URL bar position
        print("  ⚠️  Could not parse coordinates from response")
        print("  Using typical Safari URL bar position...")

        # URL bar is typically at top, ~10% from top, spans most of width
        x1 = int(img_width * 0.15)
        x2 = int(img_width * 0.85)
        y1 = int(img_height * 0.05)
        y2 = int(img_height * 0.09)

        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2

        print(f"  Estimated position: ({center_x}, {center_y})")

        # Visualize
        output_dir = Path(__file__).parent / "test_components" / "output"
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        annotated = screenshot.copy()
        draw_box(annotated, x1, y1, x2, y2, f"URL Bar (estimated) - Click: ({center_x},{center_y})", "orange")

        annotated_path = output_dir / f"url_bar_annotated_{timestamp}.png"
        annotated.save(annotated_path)
        print(f"\n  📸 Saved: {annotated_path.name}")

        # Click
        print(f"\n[3] Clicking URL bar at ({center_x}, {center_y})...")
        click_action = ClickAction(x=center_x, y=center_y)

        if await click_action.validate():
            result = await click_action.execute()
            if result.success:
                print(f"  ✅ Clicked!")
                print(f"\n  URL bar should now be focused")
                return True

    return False


if __name__ == "__main__":
    result = asyncio.run(find_url_bar())
    sys.exit(0 if result else 1)
