#!/usr/bin/env python3
"""
Debug vision detection - verify what the model actually sees
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
from automation_agent.config import AgentConfig


def parse_bbox(text: str, img_width: int, img_height: int):
    """Parse bounding box from response."""
    pattern_int = r'<box>\((\d+),(\d+),(\d+),(\d+)\)</box>'
    match = re.search(pattern_int, text)

    if match:
        x1, y1, x2, y2 = map(int, match.groups())
        x1, y1, x2, y2 = x1/1000.0, y1/1000.0, x2/1000.0, y2/1000.0
    else:
        pattern_dec = r'<box>\(([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)\)</box>'
        match = re.search(pattern_dec, text)
        if match:
            x1, y1, x2, y2 = map(float, match.groups())
        else:
            return None, None

    if x1 > x2: x1, x2 = x2, x1
    if y1 > y2: y1, y2 = y2, y1

    px1 = int(x1 * img_width)
    py1 = int(y1 * img_height)
    px2 = int(x2 * img_width)
    py2 = int(y2 * img_height)

    return (px1, py1, px2, py2), (x1, y1, x2, y2)


async def debug_safari_detection():
    """Debug Safari icon detection."""
    print("=" * 70)
    print("DEBUG: Safari Icon Detection")
    print("=" * 70)

    config = AgentConfig()
    client = OllamaClient(host=config.ollama_host, timeout=config.ollama_timeout)
    capturer = ScreenCapturer(config)

    output_dir = Path(__file__).parent / "test_components" / "output"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # Step 1: Capture screenshot
    print("\n[1] Capturing screenshot...")
    screenshot = capturer.capture_screen()
    img_width, img_height = screenshot.size
    print(f"  Screen: {img_width}x{img_height}")

    screenshot_path = output_dir / f"debug_full_{timestamp}.png"
    screenshot.save(screenshot_path)
    print(f"  Saved: {screenshot_path.name}")

    img_b64 = capturer.capture_screen_b64()

    # Step 2: Ask model to find Safari with detailed description
    print("\n[2] Asking model to find Safari icon...")
    print("  Prompt: Find Safari (blue compass with red/white needle)")

    prompt1 = """Find the Safari browser icon in this macOS screenshot.
Safari icon looks like: a blue and white compass with a red and white needle pointing northwest.
It should be in the Dock at the bottom of the screen.
Return ONLY the bounding box: <box>(x1,y1,x2,y2)</box>"""

    response1 = await client.generate_vision(
        model=config.vision_model,
        prompt=prompt1,
        image_b64=img_b64
    )

    print(f"\n  Model response: {response1}")

    pixel_bbox, norm_bbox = parse_bbox(response1, img_width, img_height)

    if not pixel_bbox:
        print("\n  ❌ No bounding box found")
        return False

    x1, y1, x2, y2 = pixel_bbox
    print(f"\n  ✅ Bounding box detected:")
    print(f"    Pixels: ({x1}, {y1}, {x2}, {y2})")
    print(f"    Size: {x2-x1}x{y2-y1} pixels")

    # Step 3: Extract the detected region
    print("\n[3] Extracting detected region...")

    # Add some padding to see context
    padding = 20
    crop_x1 = max(0, x1 - padding)
    crop_y1 = max(0, y1 - padding)
    crop_x2 = min(img_width, x2 + padding)
    crop_y2 = min(img_height, y2 + padding)

    detected_region = screenshot.crop((crop_x1, crop_y1, crop_x2, crop_y2))
    region_path = output_dir / f"debug_detected_region_{timestamp}.png"
    detected_region.save(region_path)
    print(f"  Saved: {region_path.name}")
    print(f"  Region size: {detected_region.size}")

    # Convert to base64
    import io
    import base64
    buffered = io.BytesIO()
    detected_region.save(buffered, format="PNG")
    region_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

    # Step 4: Ask model to describe what it detected
    print("\n[4] Asking model to describe the detected region...")

    prompt2 = """Describe this icon in detail.
What application does this icon represent?
What colors do you see?
What shapes or symbols are visible?"""

    response2 = await client.generate_vision(
        model=config.vision_model,
        prompt=prompt2,
        image_b64=region_b64
    )

    print(f"\n  Model description:")
    print(f"  {response2}")

    # Step 5: Verify if it matches Safari
    print("\n[5] Verification...")

    safari_keywords = ['safari', 'compass', 'blue', 'needle', 'browser']
    response_lower = response2.lower()

    matches = [kw for kw in safari_keywords if kw in response_lower]

    if matches:
        print(f"  ✅ Matches Safari keywords: {matches}")
    else:
        print(f"  ❌ Does NOT match Safari keywords")
        print(f"  Detected keywords suggest different app")

    # Step 6: Try alternative approach - find ALL dock icons
    print("\n[6] Alternative: Finding ALL dock icons...")

    prompt3 = """Look at the Dock at the bottom of this macOS screenshot.
List ALL visible application icons from left to right.
For each icon, provide:
1. App name
2. Brief description
3. Bounding box <box>(x1,y1,x2,y2)</box>

Format: AppName: description <box>(x1,y1,x2,y2)</box>"""

    response3 = await client.generate_vision(
        model=config.vision_model,
        prompt=prompt3,
        image_b64=img_b64
    )

    print(f"\n  All dock icons:")
    print(f"  {response3}")

    # Parse all bounding boxes
    all_boxes = re.findall(r'<box>\(([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)\)</box>', response3)
    print(f"\n  Found {len(all_boxes)} bounding boxes")

    # Step 7: Create visualization with all detected regions
    print("\n[7] Creating comprehensive visualization...")

    vis = screenshot.copy()
    draw = ImageDraw.Draw(vis)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except:
        font = ImageFont.load_default()

    # Draw the originally detected box in RED
    draw.rectangle([x1, y1, x2, y2], outline="red", width=4)
    draw.text((x1, y1-20), "DETECTED", fill="red", font=font)

    # Draw all found boxes in GREEN
    for i, box in enumerate(all_boxes, 1):
        bx1, by1, bx2, by2 = map(float, box)
        if bx1 > bx2: bx1, bx2 = bx2, bx1
        if by1 > by2: by1, by2 = by2, by1

        px1 = int(bx1 * img_width)
        py1 = int(by1 * img_height)
        px2 = int(bx2 * img_width)
        py2 = int(by2 * img_height)

        draw.rectangle([px1, py1, px2, py2], outline="green", width=2)
        draw.text((px1, py1-15), f"#{i}", fill="green", font=font)

    vis_path = output_dir / f"debug_all_detections_{timestamp}.png"
    vis.save(vis_path)
    print(f"  Saved: {vis_path.name}")

    print("\n" + "=" * 70)
    print("DEBUG COMPLETE")
    print("=" * 70)
    print(f"\nFiles created:")
    print(f"  1. {screenshot_path.name} - Full screenshot")
    print(f"  2. {region_path.name} - What model detected")
    print(f"  3. {vis_path.name} - All detections visualized")
    print(f"\nOpen these files to see what the model is detecting!")

    return True


if __name__ == "__main__":
    result = asyncio.run(debug_safari_detection())
    sys.exit(0 if result else 1)
