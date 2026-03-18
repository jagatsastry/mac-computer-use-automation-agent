#!/usr/bin/env python3
"""
Visual coordinate test: Vision model element detection with visualization
Tests vision model's ability to identify UI elements and draws bounding boxes
"""
import asyncio
import sys
import re
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from automation_agent.llm.client import OllamaClient
from automation_agent.perception.capture import ScreenCapturer
from automation_agent.config import AgentConfig


def parse_bbox(text: str, img_width: int, img_height: int):
    """
    Parse bounding box from model output.
    Expected format: <box>(x1,y1,x2,y2)</box> where coords are 0-1000 normalized
    Returns: (x1, y1, x2, y2) in pixel coordinates, or None
    """
    pattern = r'<box>\((\d+),(\d+),(\d+),(\d+)\)</box>'
    match = re.search(pattern, text)

    if match:
        x1, y1, x2, y2 = map(int, match.groups())
        # Convert from 0-1000 normalized to pixel coordinates
        px1 = int((x1 / 1000.0) * img_width)
        py1 = int((y1 / 1000.0) * img_height)
        px2 = int((x2 / 1000.0) * img_width)
        py2 = int((y2 / 1000.0) * img_height)
        return (px1, py1, px2, py2), (x1, y1, x2, y2)

    return None, None


def draw_bbox(image: Image.Image, bbox, label: str, color: str = "red"):
    """Draw bounding box on image with label."""
    draw = ImageDraw.Draw(image)
    x1, y1, x2, y2 = bbox

    # Draw rectangle
    draw.rectangle([x1, y1, x2, y2], outline=color, width=4)

    # Draw label background
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except:
        font = ImageFont.load_default()

    # Get text bounding box
    text_bbox = draw.textbbox((x1, y1), label, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]

    # Draw label background
    draw.rectangle(
        [x1, y1 - text_height - 8, x1 + text_width + 8, y1],
        fill=color
    )

    # Draw label text
    draw.text((x1 + 4, y1 - text_height - 4), label, fill="white", font=font)

    return image


async def test_vision_coordinates():
    """Test vision model coordinate detection with visualization."""
    print("=" * 70)
    print("VISUAL TEST: Vision Model Coordinate Detection")
    print("=" * 70)

    config = AgentConfig()
    client = OllamaClient(host=config.ollama_host, timeout=config.ollama_timeout)
    capturer = ScreenCapturer(config)

    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)

    # Check model availability
    print("\n[Setup] Checking vision model availability...")
    has_model = await client.check_model_available(config.vision_model)
    if not has_model:
        print(f"  ❌ Model {config.vision_model} not available")
        print(f"  Action: Run 'ollama pull {config.vision_model}'")
        return False
    print(f"  ✅ Model {config.vision_model} available")

    # Capture screenshot
    print("\n[Setup] Capturing current screen...")
    screenshot = capturer.capture_screen()
    img_width, img_height = screenshot.size
    print(f"  Screenshot size: {img_width}x{img_height}")

    # Save original screenshot
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    original_path = output_dir / f"original_{timestamp}.png"
    screenshot.save(original_path)
    print(f"  Saved original: {original_path}")

    # Get base64 for model
    img_b64 = capturer.capture_screen_b64()

    # Create annotated image (will draw boxes on this)
    annotated = screenshot.copy()

    # Test 1: Find Safari icon
    print("\n" + "=" * 70)
    print("[Test 1] Finding Safari icon")
    print("=" * 70)

    prompt1 = """Look at this macOS screenshot. Find the Safari browser icon (the blue compass icon).
Return the bounding box coordinates in this exact format: <box>(x1,y1,x2,y2)</box>
where coordinates are normalized from 0 to 1000.
Only return the bounding box, nothing else."""

    print(f"  Prompt: Find Safari icon...")
    try:
        response1 = await client.generate_vision(
            model=config.vision_model,
            prompt=prompt1,
            image_b64=img_b64
        )
        print(f"\n  Model response:")
        print(f"  {response1}")

        # Parse and draw bounding box
        pixel_bbox, norm_bbox = parse_bbox(response1, img_width, img_height)

        if pixel_bbox:
            print(f"\n  ✅ Bounding box found!")
            print(f"    Normalized (0-1000): {norm_bbox}")
            print(f"    Pixels: {pixel_bbox}")
            x1, y1, x2, y2 = pixel_bbox
            width = x2 - x1
            height = y2 - y1
            print(f"    Size: {width}x{height} pixels")
            print(f"    Center: ({(x1+x2)//2}, {(y1+y2)//2})")

            # Draw on annotated image
            annotated = draw_bbox(annotated, pixel_bbox, "Safari Icon", color="red")
        else:
            print(f"  ⚠️  No bounding box found in response")
            print(f"  Note: Model may not support bbox format or Safari not visible")

    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False

    # Test 2: Find URL bar (if Safari is visible)
    print("\n" + "=" * 70)
    print("[Test 2] Finding Safari URL/address bar")
    print("=" * 70)

    prompt2 = """Look at this macOS screenshot. Find the Safari browser's URL address bar (the text input field where you type web addresses).
Return the bounding box coordinates in this exact format: <box>(x1,y1,x2,y2)</box>
where coordinates are normalized from 0 to 1000.
Only return the bounding box, nothing else."""

    print(f"  Prompt: Find Safari URL bar...")
    try:
        response2 = await client.generate_vision(
            model=config.vision_model,
            prompt=prompt2,
            image_b64=img_b64
        )
        print(f"\n  Model response:")
        print(f"  {response2}")

        # Parse and draw bounding box
        pixel_bbox, norm_bbox = parse_bbox(response2, img_width, img_height)

        if pixel_bbox:
            print(f"\n  ✅ Bounding box found!")
            print(f"    Normalized (0-1000): {norm_bbox}")
            print(f"    Pixels: {pixel_bbox}")
            x1, y1, x2, y2 = pixel_bbox
            width = x2 - x1
            height = y2 - y1
            print(f"    Size: {width}x{height} pixels")
            print(f"    Center: ({(x1+x2)//2}, {(y1+y2)//2})")

            # Draw on annotated image
            annotated = draw_bbox(annotated, pixel_bbox, "URL Bar", color="blue")
        else:
            print(f"  ⚠️  No bounding box found in response")
            print(f"  Note: Safari may not be open or URL bar not visible")

    except Exception as e:
        print(f"  ❌ Error: {e}")

    # Test 3: Find all clickable elements
    print("\n" + "=" * 70)
    print("[Test 3] Finding all visible dock icons")
    print("=" * 70)

    prompt3 = """Look at this macOS screenshot. Find the Dock at the bottom of the screen and identify ALL application icons in it.
For EACH icon, return its bounding box in this exact format: <box>(x1,y1,x2,y2)</box>
List each icon with its name and bounding box.
Coordinates should be normalized from 0 to 1000."""

    print(f"  Prompt: Find all dock icons...")
    try:
        response3 = await client.generate_vision(
            model=config.vision_model,
            prompt=prompt3,
            image_b64=img_b64
        )
        print(f"\n  Model response:")
        print(f"  {response3}")

        # Parse all bounding boxes
        pattern = r'<box>\((\d+),(\d+),(\d+),(\d+)\)</box>'
        matches = re.findall(pattern, response3)

        if matches:
            print(f"\n  ✅ Found {len(matches)} bounding boxes!")

            for i, match in enumerate(matches, 1):
                x1, y1, x2, y2 = map(int, match)
                # Convert to pixels
                px1 = int((x1 / 1000.0) * img_width)
                py1 = int((y1 / 1000.0) * img_height)
                px2 = int((x2 / 1000.0) * img_width)
                py2 = int((y2 / 1000.0) * img_height)

                pixel_bbox = (px1, py1, px2, py2)
                print(f"    Icon {i}: Normalized {match} -> Pixels {pixel_bbox}")

                # Draw on annotated image
                annotated = draw_bbox(annotated, pixel_bbox, f"Icon {i}", color="green")
        else:
            print(f"  ⚠️  No bounding boxes found in response")

    except Exception as e:
        print(f"  ❌ Error: {e}")

    # Test 4: General scene understanding
    print("\n" + "=" * 70)
    print("[Test 4] General scene understanding")
    print("=" * 70)

    prompt4 = """Describe what you see in this macOS screenshot.
Include:
1. What applications or windows are visible
2. What's in the Dock
3. The overall layout
Keep it concise (3-4 sentences)."""

    print(f"  Prompt: Describe the scene...")
    try:
        response4 = await client.generate_vision(
            model=config.vision_model,
            prompt=prompt4,
            image_b64=img_b64
        )
        print(f"\n  Model response:")
        print(f"  {response4}")
        print(f"\n  ✅ Scene description generated")

    except Exception as e:
        print(f"  ❌ Error: {e}")

    # Save annotated image
    print("\n" + "=" * 70)
    print("[Output] Saving annotated image")
    print("=" * 70)

    annotated_path = output_dir / f"annotated_{timestamp}.png"
    annotated.save(annotated_path)
    print(f"  ✅ Saved annotated image: {annotated_path}")
    print(f"  ✅ Saved original image: {original_path}")

    # Create side-by-side comparison
    print(f"\n  Creating side-by-side comparison...")
    comparison = Image.new('RGB', (img_width * 2, img_height))
    comparison.paste(screenshot, (0, 0))
    comparison.paste(annotated, (img_width, 0))

    comparison_path = output_dir / f"comparison_{timestamp}.png"
    comparison.save(comparison_path)
    print(f"  ✅ Saved comparison: {comparison_path}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Screen size: {img_width}x{img_height}")
    print(f"  Output directory: {output_dir}")
    print(f"\n  Files created:")
    print(f"    1. {original_path.name} - Original screenshot")
    print(f"    2. {annotated_path.name} - With bounding boxes")
    print(f"    3. {comparison_path.name} - Side-by-side view")
    print(f"\n  Open the annotated image to see detected coordinates!")
    print("=" * 70)

    return True


if __name__ == "__main__":
    result = asyncio.run(test_vision_coordinates())
    sys.exit(0 if result else 1)
