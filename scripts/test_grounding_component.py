#!/usr/bin/env python3
"""Component-level manual test for the grounding pipeline.

Takes a screenshot of the current screen, sends it to the grounding model,
parses the response, converts coordinates, and displays results. Optionally
draws the predicted point on the screenshot for visual verification.

Usage:
    # Basic: find an element on the current screen
    .venv/bin/python scripts/test_grounding_component.py "Sort by dropdown"

    # Save debug image with predicted point drawn
    .venv/bin/python scripts/test_grounding_component.py "Add to cart button" --save

    # Use a specific screenshot file instead of capturing
    .venv/bin/python scripts/test_grounding_component.py "Search field" --image screenshot.png

    # Run a battery of test elements
    .venv/bin/python scripts/test_grounding_component.py --battery

    # Test with raw Molmo responses (no screenshot needed)
    .venv/bin/python scripts/test_grounding_component.py --parse-test

Standalone script — imports only from automation_agent for config and coordinator.
"""

import argparse
import asyncio
import base64
import io
import json
import os
import re
import sys
import time
from pathlib import Path

# Load .env
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from automation_agent.config import AgentConfig
from automation_agent.vision.coordinator import (
    COORDINATE_SPACES,
    ScreenCoordinatorImpl,
)


def _take_screenshot(resolution: tuple = (1024, 768)) -> bytes:
    """Capture the current screen via screencapture."""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        tmp_path = f.name
    try:
        subprocess.run(
            [
                "screencapture",
                "-x",  # no sound
                "-t", "jpg",
                "-R", f"0,0,{resolution[0] * 2},{resolution[1] * 2}",  # Retina
                tmp_path,
            ],
            check=True,
            capture_output=True,
        )
        with open(tmp_path, "rb") as f:
            raw = f.read()
    finally:
        os.unlink(tmp_path)

    # Resize to target resolution
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        img = img.resize(resolution, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except ImportError:
        # No PIL — return raw (may be wrong resolution)
        return raw


def _load_image(path: str) -> bytes:
    """Load and resize an image file."""
    try:
        from PIL import Image
        img = Image.open(path)
        img = img.resize((1024, 768), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except ImportError:
        with open(path, "rb") as f:
            return f.read()


def _draw_crosshair(image_bytes: bytes, x: int, y: int, label: str) -> bytes:
    """Draw a crosshair with label on the image."""
    try:
        from PIL import Image, ImageDraw
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img)

        r = 30
        color = (255, 0, 0)
        outline = (0, 0, 0)
        w = 5

        for c, off in [(outline, 2), (color, 0)]:
            draw.line([(x - r, y), (x + r, y)], fill=c, width=w + off)
            draw.line([(x, y - r), (x, y + r)], fill=c, width=w + off)
            draw.ellipse([(x - r, y - r), (x + r, y + r)], outline=c, width=w + off)

        lx, ly = x + r + 6, y - 12
        bbox = draw.textbbox((lx, ly), label)
        draw.rectangle(
            [bbox[0] - 2, bbox[1] - 2, bbox[2] + 2, bbox[3] + 2],
            fill=(0, 0, 0),
        )
        draw.text((lx, ly), label, fill=(255, 255, 0))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except ImportError:
        return image_bytes


def test_parse_coordinates():
    """Test _parse_coordinates with known Molmo response formats."""
    config = AgentConfig(
        vision_server_url="http://localhost:8091",
        vision_model="molmo",
        grounding_model="",
        model_provider="local",
    )
    coord = ScreenCoordinatorImpl(config)

    test_cases = [
        # (description, raw_response, expected_coords_or_none)
        (
            "Standard FOUND format",
            'FOUND: x=50.5, y=32.1, confidence=0.9',
            (50.5, 32.1, 0.9),
        ),
        (
            "FOUND without confidence",
            'FOUND: x=14.1, y=25.0',
            (14.1, 25.0, 0.0),
        ),
        (
            "Molmo2 space-separated",
            'FOUND: x=14.1 250',
            (14.1, 250.0, 0.0),
        ),
        (
            "NOT_FOUND",
            'NOT_FOUND',
            None,
        ),
        (
            "<point> tag format",
            '<point x="50" y="32" confidence="0.8" />',
            (50.0, 32.0, 0.0),  # confidence parsing limited in current regex
        ),
        (
            "<points coords> format",
            '<points coords="0 500 250" />',
            (500.0, 250.0, 0.0),
        ),
        (
            "<points x1/y1> bbox format (Molmo native)",
            '<points x1="14.1" y1="250" x2="14.1" y2="251" alt="Sort">Sort</points>',
            (14.1, 250.5, 0.0),  # center of bbox
        ),
        (
            "<points x1/y1> no x2/y2",
            '<points x1="50.0" y1="32.0" alt="Button">Button</points>',
            (50.0, 32.0, 0.0),
        ),
        (
            "Molmo with markdown wrapper",
            ' - `<points x1="14.1" y1="250" x2="14.1" y2="251" alt="Sort by dropdown">'
            'Sort by dropdown</points>',
            (14.1, 250.5, 0.0),
        ),
        (
            "JSON format",
            '{"x": 50.5, "y": 32.1, "confidence": 0.85}',
            (50.5, 32.1, 0.85),
        ),
    ]

    print("=" * 70)
    print("PARSE COORDINATE TESTS")
    print("=" * 70)
    passed = 0
    failed = 0
    for desc, response, expected in test_cases:
        result = coord._parse_coordinates(response)
        if expected is None:
            ok = result is None
        elif result is None:
            ok = False
        else:
            ok = (
                abs(result[0] - expected[0]) < 0.01
                and abs(result[1] - expected[1]) < 0.01
                and abs(result[2] - expected[2]) < 0.01
            )
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] {desc}")
        if not ok:
            print(f"         Input:    {response[:80]}")
            print(f"         Expected: {expected}")
            print(f"         Got:      {result}")
    print(f"\n  {passed}/{passed + failed} passed")
    print()
    return failed == 0


def test_convert_coordinates():
    """Test _convert_coordinates with out-of-range detection."""
    config = AgentConfig(
        vision_server_url="http://localhost:8091",
        vision_model="molmo",
        grounding_model="",
        model_provider="local",
    )
    coord = ScreenCoordinatorImpl(config)

    test_cases = [
        # (desc, raw_x, raw_y, model, w, h, expected_x, expected_y)
        (
            "Normal 0-100 coords",
            50.0, 50.0, "molmo", 1024, 768,
            512, 384,
        ),
        (
            "0-100 coords at edge",
            100.0, 100.0, "molmo", 1024, 768,
            1023, 767,  # clamped to w-1, h-1
        ),
        (
            "Out-of-range y=250 → auto-escalate to 0-1000",
            14.1, 250.0, "molmo", 1024, 768,
            14, 192,  # 14.1/1000*1024=14, 250/1000*768=192
        ),
        (
            "Out-of-range both → auto-escalate to 0-1000",
            141.0, 250.0, "molmo", 1024, 768,
            144, 192,
        ),
        (
            "Normal 0-1000 coords (molmo2)",
            500.0, 500.0, "molmo2", 1024, 768,
            512, 384,
        ),
    ]

    print("=" * 70)
    print("COORDINATE CONVERSION TESTS (with out-of-range detection)")
    print("=" * 70)
    passed = 0
    failed = 0
    for desc, raw_x, raw_y, model, w, h, exp_x, exp_y in test_cases:
        x, y = coord._convert_coordinates(raw_x, raw_y, model, w, h)
        ok = abs(x - exp_x) <= 1 and abs(y - exp_y) <= 1
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] {desc}")
        if not ok:
            print(f"         raw=({raw_x}, {raw_y}) model={model} screen={w}x{h}")
            print(f"         Expected: ({exp_x}, {exp_y})")
            print(f"         Got:      ({x}, {y})")
    print(f"\n  {passed}/{passed + failed} passed")
    print()
    return failed == 0


async def test_live_grounding(
    element: str,
    image_bytes: bytes = None,
    save: bool = False,
    output_dir: str = "logs/grounding_tests",
):
    """Run a live grounding test against the Molmo server."""
    config = AgentConfig()
    coord = ScreenCoordinatorImpl(config)

    if image_bytes is None:
        print(f"📸 Capturing screenshot at {config.screenshot_resolution}...")
        image_bytes = _take_screenshot(config.screenshot_resolution)

    screenshot_b64 = base64.b64encode(image_bytes).decode()
    w, h = config.screenshot_resolution

    print(f"\n🔍 Finding: \"{element}\"")
    print(f"   Model: {config.grounding_model or config.vision_model}")
    print(f"   Server: {config.grounding_server_url or config.vision_server_url}")
    print(f"   Resolution: {w}x{h}")

    # Call grounding model directly
    if config.grounding_model and (config.grounding_server_url or config.vision_server_url):
        prompt_path = Path(__file__).resolve().parent.parent / "src" / "automation_agent" / "vision" / "prompts" / "find_element.md"
        prompt = prompt_path.read_text().replace("{{element_description}}", element)

        start = time.monotonic()
        try:
            raw_response = await coord._call_grounding_model(prompt, screenshot_b64)
        except Exception as e:
            print(f"   ❌ Grounding model error: {e}")
            return
        latency = time.monotonic() - start

        print(f"\n   Raw response ({latency:.1f}s):")
        print(f"   {raw_response[:300]}")

        # Parse
        parsed = coord._parse_coordinates(raw_response)
        if parsed is None:
            print(f"\n   ❌ Parser returned None — response format not recognized")
            return

        raw_x, raw_y, conf = parsed
        print(f"\n   Parsed: raw_x={raw_x}, raw_y={raw_y}, conf={conf}")

        # Determine coordinate space
        model = config.grounding_model
        space = coord._resolve_coordinate_space(model)
        print(f"   Coordinate space: {model} → {space}")

        # Check for out-of-range
        if space == "normalized_0_100" and (raw_x > 100 or raw_y > 100):
            print(f"   ⚠️  OUT OF RANGE for {space}! Will auto-escalate to 0-1000")

        # Convert
        px, py = coord._convert_coordinates(raw_x, raw_y, model, w, h)
        print(f"   Pixel coordinates: ({px}, {py})")

        # Sanity check
        in_bounds = 0 <= px < w and 0 <= py < h
        edge = px < 20 or px > w - 20 or py < 20 or py > h - 20
        print(f"   In bounds: {in_bounds}")
        if edge:
            print(f"   ⚠️  Near edge — possible misgrounding")

        if save:
            os.makedirs(output_dir, exist_ok=True)
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", element)[:40]
            ts = int(time.time())

            # Save annotated image
            annotated = _draw_crosshair(image_bytes, px, py, element[:30])
            out_path = f"{output_dir}/{ts}_{slug}.jpg"
            with open(out_path, "wb") as f:
                f.write(annotated)
            print(f"\n   💾 Saved: {out_path}")

            # Save raw response
            meta_path = f"{output_dir}/{ts}_{slug}_meta.json"
            with open(meta_path, "w") as f:
                json.dump({
                    "element": element,
                    "raw_response": raw_response,
                    "parsed": {"x": raw_x, "y": raw_y, "conf": conf},
                    "pixel": {"x": px, "y": py},
                    "model": model,
                    "space": space,
                    "resolution": [w, h],
                    "latency_s": round(latency, 2),
                }, f, indent=2)
            print(f"   💾 Saved: {meta_path}")
    else:
        print("   ⚠️  No grounding model configured. Set AGENT_GROUNDING_MODEL in .env")


async def test_battery(save: bool = False):
    """Run a battery of grounding tests on the current screen."""
    elements = [
        "Sort by dropdown",
        "Search field",
        "Close button",
        "First product listing",
        "Add to cart button",
        "Price: Low to High",
        "Navigation menu",
        "Back button",
    ]
    print("📸 Capturing screenshot for battery test...")
    image_bytes = _take_screenshot()

    for element in elements:
        print("\n" + "-" * 60)
        await test_live_grounding(element, image_bytes=image_bytes, save=save)


def main():
    parser = argparse.ArgumentParser(
        description="Component-level grounding pipeline test"
    )
    parser.add_argument(
        "element",
        nargs="?",
        help="Element description to find on screen",
    )
    parser.add_argument(
        "--image",
        help="Path to screenshot image (instead of capturing)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save annotated screenshot and metadata",
    )
    parser.add_argument(
        "--parse-test",
        action="store_true",
        help="Run parse/convert unit tests (no server needed)",
    )
    parser.add_argument(
        "--battery",
        action="store_true",
        help="Run a battery of test elements on current screen",
    )
    parser.add_argument(
        "--output-dir",
        default="logs/grounding_tests",
        help="Directory for saved output",
    )
    args = parser.parse_args()

    if args.parse_test:
        ok1 = test_parse_coordinates()
        ok2 = test_convert_coordinates()
        sys.exit(0 if (ok1 and ok2) else 1)

    if args.battery:
        asyncio.run(test_battery(save=args.save))
        return

    if not args.element:
        parser.error("Provide an element description or use --parse-test / --battery")

    image_bytes = None
    if args.image:
        image_bytes = _load_image(args.image)

    asyncio.run(
        test_live_grounding(
            args.element,
            image_bytes=image_bytes,
            save=args.save,
            output_dir=args.output_dir,
        )
    )


if __name__ == "__main__":
    main()
