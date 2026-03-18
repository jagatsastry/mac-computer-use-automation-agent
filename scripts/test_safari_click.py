#!/usr/bin/env python3
"""
End-to-end test: Find Safari icon and click it
Uses vision model to identify Safari, then clicks on it
"""
import asyncio
import sys
import re
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation_agent.llm.client import OllamaClient
from automation_agent.perception.capture import ScreenCapturer
from automation_agent.actions.simple import ClickAction
from automation_agent.config import AgentConfig


def parse_bbox(text: str, img_width: int, img_height: int):
    """
    Parse bounding box from model output.
    Expected formats:
    - <box>(x1,y1,x2,y2)</box> where coords are 0-1000 normalized integers
    - <box>(0.x1,0.y1,0.x2,0.y2)</box> where coords are 0-1 normalized decimals
    Returns: (x1, y1, x2, y2) in pixel coordinates, or None
    """
    # Try integer format first (0-1000)
    pattern_int = r'<box>\((\d+),(\d+),(\d+),(\d+)\)</box>'
    match = re.search(pattern_int, text)

    if match:
        x1, y1, x2, y2 = map(int, match.groups())
        # Normalize to 0-1
        x1, y1, x2, y2 = x1/1000.0, y1/1000.0, x2/1000.0, y2/1000.0
    else:
        # Try decimal format (0-1)
        pattern_dec = r'<box>\(([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)\)</box>'
        match = re.search(pattern_dec, text)
        if match:
            x1, y1, x2, y2 = map(float, match.groups())
        else:
            return None, None

    # Ensure coordinates are in correct order (x1 < x2, y1 < y2)
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1

    # Convert to pixel coordinates
    px1 = int(x1 * img_width)
    py1 = int(y1 * img_height)
    px2 = int(x2 * img_width)
    py2 = int(y2 * img_height)

    return (px1, py1, px2, py2), (x1, y1, x2, y2)


def draw_bbox(image: Image.Image, bbox, label: str, color: str = "red"):
    """Draw bounding box on image with label."""
    draw = ImageDraw.Draw(image)
    x1, y1, x2, y2 = bbox

    # Draw rectangle
    draw.rectangle([x1, y1, x2, y2], outline=color, width=4)

    # Draw crosshair at center
    center_x = (x1 + x2) // 2
    center_y = (y1 + y2) // 2
    crosshair_size = 10
    draw.line(
        [(center_x - crosshair_size, center_y), (center_x + crosshair_size, center_y)],
        fill=color, width=3
    )
    draw.line(
        [(center_x, center_y - crosshair_size), (center_x, center_y + crosshair_size)],
        fill=color, width=3
    )

    # Draw label background
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except:
        font = ImageFont.load_default()

    text_bbox = draw.textbbox((x1, y1), label, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]

    draw.rectangle(
        [x1, y1 - text_height - 8, x1 + text_width + 8, y1],
        fill=color
    )
    draw.text((x1 + 4, y1 - text_height - 4), label, fill="white", font=font)

    return image


async def find_and_click_safari(dry_run=False):
    """Find Safari icon using vision model and click it."""
    print("=" * 70)
    print("TEST: Find and Click Safari")
    print("=" * 70)

    config = AgentConfig()
    client = OllamaClient(host=config.ollama_host, timeout=config.ollama_timeout)
    capturer = ScreenCapturer(config)

    output_dir = Path(__file__).parent / "test_components" / "output"
    output_dir.mkdir(exist_ok=True, parents=True)

    # Check model availability
    print("\n[1] Checking vision model...")
    has_model = await client.check_model_available(config.vision_model)
    if not has_model:
        print(f"  ❌ Model {config.vision_model} not available")
        print(f"  Run: ollama pull {config.vision_model}")
        return False
    print(f"  ✅ Model available")

    # Capture screenshot
    print("\n[2] Capturing current screen...")
    screenshot = capturer.capture_screen()
    img_width, img_height = screenshot.size
    print(f"  Screen: {img_width}x{img_height}")

    # Save original
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    original_path = output_dir / f"safari_original_{timestamp}.png"
    screenshot.save(original_path)
    print(f"  Saved: {original_path.name}")

    # Get base64 for model
    img_b64 = capturer.capture_screen_b64()

    # Find Safari icon
    print("\n[3] Finding Safari icon with vision model...")
    prompt = """Look at the bottom of this macOS screenshot where the Dock is located.
Find the Safari browser icon - it's a blue and white compass icon.
Return ONLY the bounding box in this exact format: <box>(x1,y1,x2,y2)</box>
No explanation, just the coordinates."""

    print(f"  Asking model to locate Safari...")
    try:
        response = await client.generate_vision(
            model=config.vision_model,
            prompt=prompt,
            image_b64=img_b64
        )
        print(f"\n  Model response:")
        print(f"  '{response}'")

        # Parse bounding box
        pixel_bbox, norm_bbox = parse_bbox(response, img_width, img_height)

        if not pixel_bbox:
            print(f"\n  ❌ Could not find bounding box in response")
            print(f"  The model may not support bbox format or Safari not visible")
            return False

        print(f"\n  ✅ Safari icon found!")
        x1, y1, x2, y2 = pixel_bbox
        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2

        print(f"    Normalized coords: {norm_bbox}")
        print(f"    Pixel coords: {pixel_bbox}")
        print(f"    Size: {x2-x1}x{y2-y1} pixels")
        print(f"    Click point (center): ({center_x}, {center_y})")

        # Draw on screenshot
        annotated = screenshot.copy()
        annotated = draw_bbox(
            annotated,
            pixel_bbox,
            f"Safari (click: {center_x}, {center_y})",
            color="red"
        )

        # Save annotated
        annotated_path = output_dir / f"safari_annotated_{timestamp}.png"
        annotated.save(annotated_path)
        print(f"\n  Saved annotated: {annotated_path.name}")

        # Click Safari (if not dry run)
        print(f"\n[4] Clicking Safari icon...")
        if dry_run:
            print(f"  ⏭️  DRY RUN - Would click at ({center_x}, {center_y})")
            print(f"  To actually click, run with: --execute")
        else:
            print(f"  🖱️  Clicking at ({center_x}, {center_y})...")
            click_action = ClickAction(x=center_x, y=center_y)

            # Validate
            is_valid = await click_action.validate()
            if not is_valid:
                print(f"  ❌ Click coordinates out of bounds!")
                return False

            # Execute click
            result = await click_action.execute()
            if result.success:
                print(f"  ✅ Click executed successfully!")
                print(f"  Safari should now be opening...")
            else:
                print(f"  ❌ Click failed: {result.error}")
                return False

        print("\n" + "=" * 70)
        print("SUCCESS")
        print("=" * 70)
        print(f"  Vision model: ✅ Found Safari")
        print(f"  Coordinates: ✅ Parsed correctly")
        print(f"  Visualization: ✅ {annotated_path.name}")
        if not dry_run:
            print(f"  Click: ✅ Executed")
        print("=" * 70)

        return True

    except Exception as e:
        print(f"\n  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # Check for --execute flag
    execute = "--execute" in sys.argv
    dry_run = not execute

    if dry_run:
        print("Running in DRY RUN mode (no actual clicking)")
        print("To actually click Safari, run with: --execute")
        print()

    result = asyncio.run(find_and_click_safari(dry_run=dry_run))
    sys.exit(0 if result else 1)
