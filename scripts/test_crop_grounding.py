#!/usr/bin/env python3
"""Standalone test: compare grounding accuracy with full-desktop vs browser-crop.

Takes a desktop screenshot, detects the browser window bounds via AppleScript,
crops to just the browser content area, sends BOTH versions to vision models,
and compares which produces more accurate grounding.

This tests the hypothesis that models ground better on cropped browser content
than full desktop screenshots (less visual noise, higher effective resolution).

Usage:
    .venv/bin/python scripts/test_crop_grounding.py "View order details"
    .venv/bin/python scripts/test_crop_grounding.py "Search Orders button" --models gemini-flash,gpt
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from PIL import Image, ImageDraw, ImageFont

# Load .env
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip().strip("'\""))


# ---------------------------------------------------------------------------
# Prompt & model config (same as compare_grounding.py)
# ---------------------------------------------------------------------------

FIND_ELEMENT_PROMPT = """\
Look at this screenshot of a macOS desktop. Find this element: {{element_description}}

Return pixel coordinates relative to the provided screenshot image (origin at top-left corner of the image).

If you CANNOT find it, respond: NOT_FOUND
If you CAN find it, respond: FOUND: x=<number>, y=<number>, confidence=<0.0-1.0>

Only respond with one of these formats, nothing else."""

# For the cropped version, adjust the prompt — it's browser content, not full desktop
FIND_ELEMENT_PROMPT_CROPPED = """\
Look at this screenshot of a browser page. Find this element: {{element_description}}

Return pixel coordinates relative to the provided screenshot image (origin at top-left corner of the image).

If you CANNOT find it, respond: NOT_FOUND
If you CAN find it, respond: FOUND: x=<number>, y=<number>, confidence=<0.0-1.0>

Only respond with one of these formats, nothing else."""

MODEL_IDS = {
    "gemini-flash": "gemini-3-flash-preview",
    "gemini-pro": "gemini-3-pro-preview",
    "gpt": "gpt-4o",
    "claude": "claude-sonnet-4-20250514",
    "molmo": "mlx-community/Molmo-7B-D-0924-3bit",
}

COORDINATE_SPACES = {
    "molmo": "normalized_0_100",
    "molmo-8b": "normalized_0_1000",
}

MOLMO_PORTS = {"molmo": 8091, "molmo-8b": 8092}

MODEL_COLORS = {
    "gemini-flash": (0, 100, 255),
    "gemini-pro": (0, 60, 180),
    "gpt": (0, 200, 0),
    "claude": (180, 0, 255),
    "molmo": (255, 140, 0),
}

_TARGET_WIDTH = 1024


# ---------------------------------------------------------------------------
# Browser window detection via AppleScript
# ---------------------------------------------------------------------------

def get_browser_content_bounds() -> Optional[Dict[str, Any]]:
    """Get the frontmost browser window's content area bounds.

    Returns dict with:
        app: str (e.g., "Google Chrome", "Safari")
        window_x, window_y: top-left of window (screen coords)
        window_w, window_h: window size
        content_x, content_y: estimated content area top-left (below toolbar)
        content_w, content_h: estimated content area size
    """
    script = '''
    tell application "System Events"
        set fp to first process whose frontmost is true
        set appName to name of fp
        set fw to front window of fp
        set {wx, wy} to position of fw
        set {ww, wh} to size of fw
    end tell
    return appName & "|" & wx & "|" & wy & "|" & ww & "|" & wh
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            print(f"  AppleScript error: {result.stderr.strip()}")
            return None

        parts = result.stdout.strip().split("|")
        if len(parts) != 5:
            return None

        app_name = parts[0]
        wx, wy, ww, wh = int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])

        # Estimate content area: skip toolbar/tab bar
        # Chrome: ~88px for tab bar + address bar + bookmarks bar
        # Safari: ~76px for similar
        # This is approximate — good enough for testing
        if "chrome" in app_name.lower():
            toolbar_h = 88
        elif "safari" in app_name.lower():
            toolbar_h = 76
        elif "firefox" in app_name.lower():
            toolbar_h = 82
        else:
            toolbar_h = 70  # generic

        return {
            "app": app_name,
            "window_x": wx, "window_y": wy,
            "window_w": ww, "window_h": wh,
            "content_x": wx, "content_y": wy + toolbar_h,
            "content_w": ww, "content_h": wh - toolbar_h,
        }
    except Exception as e:
        print(f"  Failed to get browser bounds: {e}")
        return None


# ---------------------------------------------------------------------------
# Screenshot capture and cropping
# ---------------------------------------------------------------------------

def capture_desktop() -> Image.Image:
    """Capture full desktop screenshot."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        subprocess.run(["screencapture", "-x", tmp], check=True, timeout=10)
        return Image.open(tmp).convert("RGB")
    finally:
        Path(tmp).unlink(missing_ok=True)


def crop_to_content(
    desktop_img: Image.Image,
    bounds: Dict[str, Any],
    screen_size: Tuple[int, int],
) -> Tuple[Image.Image, Tuple[int, int]]:
    """Crop desktop image to browser content area.

    Args:
        desktop_img: Full desktop PIL image (Retina resolution).
        bounds: From get_browser_content_bounds() — logical screen coords.
        screen_size: Logical screen size (e.g., 1512x982 for a 3024x1964 Retina).

    Returns:
        (cropped_image, (offset_x, offset_y)) where offset is in the
        desktop image's pixel space.
    """
    dw, dh = desktop_img.size
    sw, sh = screen_size

    # Scale from logical screen coords to image pixel coords
    scale_x = dw / sw
    scale_y = dh / sh

    cx = int(bounds["content_x"] * scale_x)
    cy = int(bounds["content_y"] * scale_y)
    cw = int(bounds["content_w"] * scale_x)
    ch = int(bounds["content_h"] * scale_y)

    # Clamp
    cx = max(0, min(cx, dw - 1))
    cy = max(0, min(cy, dh - 1))
    cw = min(cw, dw - cx)
    ch = min(ch, dh - cy)

    cropped = desktop_img.crop((cx, cy, cx + cw, cy + ch))
    return cropped, (cx, cy)


def resize_for_model(img: Image.Image) -> Image.Image:
    """Resize to _TARGET_WIDTH, preserving aspect ratio."""
    w, h = img.size
    if w <= _TARGET_WIDTH:
        return img
    scale = _TARGET_WIDTH / w
    return img.resize((_TARGET_WIDTH, int(h * scale)), Image.LANCZOS)


def img_to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# Model queries (same as compare_grounding.py)
# ---------------------------------------------------------------------------

import re


def parse_coordinates(response: str) -> Optional[Tuple[float, float]]:
    response = response.strip()
    if response.upper().startswith("NOT_FOUND"):
        return None
    patterns = [
        r'FOUND:\s*x\s*=\s*"?([0-9]*\.?[0-9]+)"?\s*,\s*y\s*=\s*"?([0-9]*\.?[0-9]+)"?',
        r'FOUND:\s*x\s*=\s*"?([0-9]*\.?[0-9]+)"?\s+y\s*=\s*"?([0-9]*\.?[0-9]+)"?',
        r'FOUND:\s*x\s*=\s*"?([0-9]*\.?[0-9]+)"?\s+"?([0-9]*\.?[0-9]+)"?',
    ]
    for pat in patterns:
        m = re.search(pat, response, re.IGNORECASE)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


def convert_coords(raw_x: float, raw_y: float, model: str, w: int, h: int) -> Tuple[int, int]:
    space = "pixel"
    for prefix, sp in COORDINATE_SPACES.items():
        if model.startswith(prefix):
            space = sp
            break
    if space == "normalized_0_100":
        if raw_x > 100 or raw_y > 100:
            space = "normalized_0_1000"
        else:
            return min(int(raw_x / 100 * w), w - 1), min(int(raw_y / 100 * h), h - 1)
    if space == "normalized_0_1000":
        return min(int(raw_x / 1000 * w), w - 1), min(int(raw_y / 1000 * h), h - 1)
    return int(raw_x), int(raw_y)


def query_model(model_name: str, prompt: str, image_b64: str) -> str:
    if model_name.startswith("gemini"):
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("AGENT_GEMINI_API_KEY")
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        img_bytes = base64.b64decode(image_b64)
        part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
        resp = client.models.generate_content(model=MODEL_IDS[model_name], contents=[part, prompt])
        return resp.text
    elif model_name == "claude":
        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AGENT_ANTHROPIC_API_KEY")
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=MODEL_IDS["claude"], max_tokens=256,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        return msg.content[0].text
    elif model_name == "gpt":
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("AGENT_OPENAI_API_KEY")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": MODEL_IDS["gpt"],
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{image_b64}", "detail": "high"},
            ]}],
        }
        resp = httpx.post("https://api.openai.com/v1/responses", json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return data.get("output_text", "")
    elif model_name.startswith("molmo"):
        port = MOLMO_PORTS[model_name]
        payload = {
            "model": MODEL_IDS[model_name],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]}],
            "max_tokens": 256, "temperature": 0,
        }
        resp = httpx.post(f"http://localhost:{port}/v1/chat/completions", json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    raise ValueError(f"Unknown model: {model_name}")


# ---------------------------------------------------------------------------
# Crosshair drawing
# ---------------------------------------------------------------------------

def draw_crosshair(draw, x, y, label, color=(255, 0, 0), r=20):
    for c, off in [((0, 0, 0), 2), (color, 0)]:
        draw.line([(x - r, y), (x + r, y)], fill=c, width=4 + off)
        draw.line([(x, y - r), (x, y + r)], fill=c, width=4 + off)
        draw.ellipse([(x - r, y - r), (x + r, y + r)], outline=c, width=4 + off)
    lx, ly = x + r + 4, y - 10
    bbox = draw.textbbox((lx, ly), label)
    draw.rectangle([bbox[0] - 2, bbox[1] - 2, bbox[2] + 2, bbox[3] + 2], fill=(0, 0, 0))
    draw.text((lx, ly), label, fill=(255, 255, 0))


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def run_one(
    model_name: str,
    label: str,
    full_b64: str,
    full_w: int,
    full_h: int,
    crop_b64: str,
    crop_w: int,
    crop_h: int,
    crop_offset: Tuple[int, int],
    desktop_size: Tuple[int, int],
) -> Dict[str, Any]:
    """Run one model on both full and cropped, return comparison."""
    result = {"model": model_name, "full": {}, "crop": {}}

    # --- Full desktop ---
    prompt_full = FIND_ELEMENT_PROMPT.replace("{{element_description}}", label)
    try:
        t0 = time.time()
        raw_full = query_model(model_name, prompt_full, full_b64)
        lat_full = time.time() - t0
        parsed_full = parse_coordinates(raw_full)
        if parsed_full:
            fx, fy = convert_coords(parsed_full[0], parsed_full[1], model_name, full_w, full_h)
            result["full"] = {"x": fx, "y": fy, "latency": round(lat_full, 1), "raw": raw_full[:120]}
        else:
            result["full"] = {"x": None, "y": None, "latency": round(lat_full, 1), "raw": raw_full[:120]}
    except Exception as e:
        result["full"] = {"x": None, "y": None, "latency": 0, "error": str(e)[:80]}

    # --- Cropped browser ---
    prompt_crop = FIND_ELEMENT_PROMPT_CROPPED.replace("{{element_description}}", label)
    try:
        t0 = time.time()
        raw_crop = query_model(model_name, prompt_crop, crop_b64)
        lat_crop = time.time() - t0
        parsed_crop = parse_coordinates(raw_crop)
        if parsed_crop:
            cx, cy = convert_coords(parsed_crop[0], parsed_crop[1], model_name, crop_w, crop_h)
            # Translate back to full-desktop image space
            # crop_offset is in the original desktop image pixel space
            # But we resized both images to _TARGET_WIDTH, so we need to scale the offset
            # crop was resized from (orig_crop_w, orig_crop_h) -> (crop_w, crop_h)
            # full was resized from (desktop_w, desktop_h) -> (full_w, full_h)
            # The offset needs to be in the resized full-desktop space
            scale_full = full_w / desktop_size[0]
            offset_x_scaled = int(crop_offset[0] * scale_full)
            offset_y_scaled = int(crop_offset[1] * scale_full)
            translated_x = cx + offset_x_scaled
            translated_y = cy + offset_y_scaled

            result["crop"] = {
                "x_in_crop": cx, "y_in_crop": cy,
                "x_translated": translated_x, "y_translated": translated_y,
                "offset": [offset_x_scaled, offset_y_scaled],
                "latency": round(lat_crop, 1),
                "raw": raw_crop[:120],
            }
        else:
            result["crop"] = {"x_translated": None, "y_translated": None, "latency": round(lat_crop, 1), "raw": raw_crop[:120]}
    except Exception as e:
        result["crop"] = {"x_translated": None, "y_translated": None, "latency": 0, "error": str(e)[:80]}

    return result


def main():
    parser = argparse.ArgumentParser(description="Test full-desktop vs browser-crop grounding")
    parser.add_argument("label", help="Element to find")
    parser.add_argument("--models", default="gemini-flash,gpt,claude", help="Models to test")
    parser.add_argument("--output-dir", default="./crop_grounding_test/")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_names = [m.strip() for m in args.models.split(",")]
    run_tag = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 1. Detect browser window
    print("Detecting browser window bounds...")
    bounds = get_browser_content_bounds()
    if not bounds:
        print("Error: Could not detect frontmost browser window.")
        sys.exit(1)
    print(f"  App: {bounds['app']}")
    print(f"  Window: ({bounds['window_x']},{bounds['window_y']}) {bounds['window_w']}x{bounds['window_h']}")
    print(f"  Content: ({bounds['content_x']},{bounds['content_y']}) {bounds['content_w']}x{bounds['content_h']}")

    # 2. Capture desktop
    print("Capturing desktop screenshot...")
    desktop = capture_desktop()
    desktop_w, desktop_h = desktop.size
    print(f"  Desktop: {desktop_w}x{desktop_h}")

    # 3. Get logical screen size
    try:
        import pyautogui
        screen_size = pyautogui.size()
        screen_w, screen_h = int(screen_size.width), int(screen_size.height)
    except Exception:
        # Assume Retina 2x
        screen_w, screen_h = desktop_w // 2, desktop_h // 2
    print(f"  Logical screen: {screen_w}x{screen_h}")

    # 4. Crop to browser content
    cropped, crop_offset = crop_to_content(desktop, bounds, (screen_w, screen_h))
    print(f"  Cropped: {cropped.size[0]}x{cropped.size[1]} at offset ({crop_offset[0]},{crop_offset[1]})")

    # 5. Resize both for model
    full_resized = resize_for_model(desktop)
    crop_resized = resize_for_model(cropped)
    full_w, full_h = full_resized.size
    crop_w, crop_h = crop_resized.size
    print(f"  Full resized: {full_w}x{full_h}")
    print(f"  Crop resized: {crop_w}x{crop_h}")

    full_b64 = img_to_b64(full_resized)
    crop_b64 = img_to_b64(crop_resized)

    # Save both for inspection
    full_resized.save(output_dir / f"full_{run_tag}.jpg", quality=90)
    crop_resized.save(output_dir / f"crop_{run_tag}.jpg", quality=90)

    print(f"\nLabel: \"{args.label}\"")
    print(f"Models: {model_names}")
    print("=" * 70)

    # 6. Run each model
    all_results = {}
    for model_name in model_names:
        print(f"\n[{model_name}]")
        r = run_one(
            model_name, args.label,
            full_b64, full_w, full_h,
            crop_b64, crop_w, crop_h,
            crop_offset, (desktop_w, desktop_h),
        )
        all_results[model_name] = r

        f = r["full"]
        c = r["crop"]
        if f.get("x") is not None:
            print(f"  FULL:    ({f['x']},{f['y']}) {f['latency']}s")
        else:
            print(f"  FULL:    NOT_FOUND/ERROR {f.get('latency', 0)}s")

        if c.get("x_translated") is not None:
            print(f"  CROP:    ({c['x_in_crop']},{c['y_in_crop']}) in crop → ({c['x_translated']},{c['y_translated']}) translated  {c['latency']}s")
        else:
            print(f"  CROP:    NOT_FOUND/ERROR {c.get('latency', 0)}s")

    # 7. Draw comparison image: full with both crosshairs per model
    for model_name in model_names:
        r = all_results[model_name]
        img = full_resized.copy()
        draw = ImageDraw.Draw(img)
        color = MODEL_COLORS.get(model_name, (255, 0, 0))

        if r["full"].get("x") is not None:
            draw_crosshair(draw, r["full"]["x"], r["full"]["y"], f"FULL", color=color)

        if r["crop"].get("x_translated") is not None:
            # Draw translated crop result in a contrasting color (shifted hue)
            crop_color = (min(color[0] + 80, 255), max(color[1] - 40, 0), min(color[2] + 80, 255))
            draw_crosshair(draw, r["crop"]["x_translated"], r["crop"]["y_translated"], f"CROP", color=crop_color)

        img.save(output_dir / f"{model_name}_comparison_{run_tag}.jpg", quality=90)

    # 8. Save JSON
    report = {
        "label": args.label,
        "run_tag": run_tag,
        "bounds": bounds,
        "desktop_size": [desktop_w, desktop_h],
        "screen_size": [screen_w, screen_h],
        "crop_offset": list(crop_offset),
        "full_size": [full_w, full_h],
        "crop_size": [crop_w, crop_h],
        "results": all_results,
    }
    json_path = output_dir / f"results_{run_tag}.json"
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)

    # 9. Summary
    print(f"\n{'='*70}")
    print(f"{'Model':<15} {'Full (x,y)':<18} {'Crop→Trans (x,y)':<22} {'Δ pixels':>10}")
    print(f"{'-'*70}")
    for model_name in model_names:
        r = all_results[model_name]
        fx = r["full"].get("x")
        fy = r["full"].get("y")
        tx = r["crop"].get("x_translated")
        ty = r["crop"].get("y_translated")

        full_str = f"({fx},{fy})" if fx is not None else "N/F"
        crop_str = f"({tx},{ty})" if tx is not None else "N/F"

        if fx is not None and tx is not None:
            delta = ((fx - tx) ** 2 + (fy - ty) ** 2) ** 0.5
            delta_str = f"{delta:.0f}"
        else:
            delta_str = "—"

        print(f"{model_name:<15} {full_str:<18} {crop_str:<22} {delta_str:>10}")

    print(f"\nOutputs: {output_dir}")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    main()
