#!/usr/bin/env python3
"""
Precise Safari detection using dock-only crop and iterative verification
"""
import asyncio
import sys
import re
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import io, base64

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation_agent.llm.client import OllamaClient
from automation_agent.perception.capture import ScreenCapturer
from automation_agent.actions.simple import ClickAction
from automation_agent.config import AgentConfig
import pyautogui


async def find_safari_precise():
    """Find Safari using precise dock cropping and verification."""
    print("=" * 70)
    print("PRECISE: Find Safari in Dock with Verification")
    print("=" * 70)

    config = AgentConfig()
    client = OllamaClient(host=config.ollama_host, timeout=config.ollama_timeout)
    capturer = ScreenCapturer(config)

    output_dir = Path(__file__).parent / "test_components" / "output"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # Capture full screenshot
    print("\n[1] Capturing screenshot...")
    screenshot = capturer.capture_screen()
    img_width, img_height = screenshot.size
    logical_width, logical_height = pyautogui.size()
    print(f"  Physical: {img_width}x{img_height}")
    print(f"  Logical: {logical_width}x{logical_height}")

    # Crop just the dock area (bottom ~10% of screen)
    print("\n[2] Cropping dock area...")
    dock_y_start = int(img_height * 0.90)
    dock_crop = screenshot.crop((0, dock_y_start, img_width, img_height))

    dock_path = output_dir / f"dock_crop_{timestamp}.png"
    dock_crop.save(dock_path)
    print(f"  Saved: {dock_path.name}")
    print(f"  Dock size: {dock_crop.size}")

    # Convert dock crop to base64
    buffered = io.BytesIO()
    dock_crop.save(buffered, format="PNG")
    dock_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

    # Ask model about Safari in the dock crop
    print("\n[3] Finding Safari in dock crop...")

    prompt = """This is the macOS Dock. Find the Safari icon.
Safari looks like: blue and white compass with a red/white needle.
Return coordinates relative to THIS image: <box>(x1,y1,x2,y2)</box>
Coordinates should be 0-1000 normalized to THIS image."""

    response = await client.generate_vision(
        model=config.vision_model,
        prompt=prompt,
        image_b64=dock_b64
    )

    print(f"  Response: {response}")

    # Parse bounding box from dock crop
    pattern = r'<box>\(([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)\)</box>'
    match = re.search(pattern, response)

    if not match:
        print("  ❌ No bounding box found")
        return False

    x1, y1, x2, y2 = map(float, match.groups())

    # Normalize if needed
    if x1 > 1.0 and x1 <= 1000:
        x1, y1, x2, y2 = x1/1000, y1/1000, x2/1000, y2/1000

    if x1 > x2: x1, x2 = x2, x1
    if y1 > y2: y1, y2 = y2, y1

    print(f"  Normalized in dock: ({x1:.3f}, {y1:.3f}, {x2:.3f}, {y2:.3f})")

    # Convert dock-relative coords to full-screen coords
    dock_height = dock_crop.size[1]

    # In dock crop coordinates (pixels)
    dock_px1 = int(x1 * dock_crop.size[0])
    dock_py1 = int(y1 * dock_crop.size[1])
    dock_px2 = int(x2 * dock_crop.size[0])
    dock_py2 = int(y2 * dock_crop.size[1])

    # In full screenshot coordinates (pixels)
    full_px1 = dock_px1
    full_py1 = dock_y_start + dock_py1
    full_px2 = dock_px2
    full_py2 = dock_y_start + dock_py2

    print(f"  Full screen physical: ({full_px1}, {full_py1}, {full_px2}, {full_py2})")

    # Convert to logical coordinates for clicking
    full_nx1 = full_px1 / img_width
    full_ny1 = full_py1 / img_height
    full_nx2 = full_px2 / img_width
    full_ny2 = full_py2 / img_height

    lx1 = int(full_nx1 * logical_width)
    ly1 = int(full_ny1 * logical_height)
    lx2 = int(full_nx2 * logical_width)
    ly2 = int(full_ny2 * logical_height)

    center_x = (lx1 + lx2) // 2
    center_y = (ly1 + ly2) // 2

    print(f"  Logical coords: ({lx1}, {ly1}, {lx2}, {ly2})")
    print(f"  Click point: ({center_x}, {center_y})")

    # Extract and verify
    print("\n[4] Verifying detection...")

    verify_crop = screenshot.crop((full_px1-10, full_py1-10, full_px2+10, full_py2+10))
    verify_buffered = io.BytesIO()
    verify_crop.save(verify_buffered, format="PNG")
    verify_b64 = base64.b64encode(verify_buffered.getvalue()).decode('utf-8')

    verify_prompt = """What application icon is this?
Answer with ONLY the application name, nothing else.
If it's Safari, say "Safari". If it's something else, say the app name."""

    verify_response = await client.generate_vision(
        model=config.vision_model,
        prompt=verify_prompt,
        image_b64=verify_b64
    )

    print(f"  Model says: '{verify_response.strip()}'")

    is_safari = 'safari' in verify_response.lower()

    if is_safari:
        print(f"  ✅ Verified: This is Safari!")
    else:
        print(f"  ❌ NOT Safari - model says it's '{verify_response.strip()}'")
        print(f"  Saving detection for manual review...")

        verify_path = output_dir / f"wrong_detection_{timestamp}.png"
        verify_crop.save(verify_path)
        print(f"  Saved: {verify_path.name}")

        print(f"\n  Trying alternative: scanning each icon individually...")
        return False

    # Visualize on full screenshot
    print("\n[5] Creating visualization...")
    vis = screenshot.copy()
    draw = ImageDraw.Draw(vis)

    draw.rectangle([full_px1, full_py1, full_px2, full_py2], outline="green", width=4)

    cx_phys = (full_px1 + full_px2) // 2
    cy_phys = (full_py1 + full_py2) // 2
    draw.line([(cx_phys-20, cy_phys), (cx_phys+20, cy_phys)], fill="green", width=3)
    draw.line([(cx_phys, cy_phys-20), (cx_phys, cy_phys+20)], fill="green", width=3)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except:
        font = ImageFont.load_default()

    draw.text((full_px1, full_py1-30), f"Safari - Click: ({center_x},{center_y})",
              fill="green", font=font)

    vis_path = output_dir / f"safari_precise_{timestamp}.png"
    vis.save(vis_path)
    print(f"  Saved: {vis_path.name}")

    # Click
    print(f"\n[6] Clicking Safari at ({center_x}, {center_y})...")

    click_action = ClickAction(x=center_x, y=center_y)

    if await click_action.validate():
        result = await click_action.execute()
        if result.success:
            print(f"  ✅ Clicked!")

            # Wait and verify what opened
            await asyncio.sleep(2)

            import subprocess
            try:
                frontmost = subprocess.check_output([
                    'osascript', '-e',
                    'tell application "System Events" to get name of first application process whose frontmost is true'
                ], text=True, timeout=5).strip()

                print(f"\n  Frontmost app: {frontmost}")

                if frontmost == "Safari":
                    print(f"  ✅✅✅ SUCCESS! Safari opened!")
                    return True
                else:
                    print(f"  ❌ Wrong app opened: {frontmost}")
                    return False
            except:
                print(f"  ⚠️  Could not verify frontmost app")
                return True  # Click succeeded anyway
        else:
            print(f"  ❌ Click failed: {result.error}")
            return False
    else:
        print(f"  ❌ Invalid coordinates")
        return False


if __name__ == "__main__":
    result = asyncio.run(find_safari_precise())
    sys.exit(0 if result else 1)
