#!/usr/bin/env python3
"""Standalone grounding comparison script.

Sends a screenshot + element label to multiple vision models (Gemini, GPT,
Claude, Molmo) using the same prompt template as the live agent, draws
color-coded crosshairs on the image, and logs structured results.

Usage:
    .venv/bin/python scripts/compare_grounding.py screenshot.png "View order details"
    .venv/bin/python scripts/compare_grounding.py screenshot.png "View order details" --models gemini,gpt
    .venv/bin/python scripts/compare_grounding.py screenshot.png "View order details" --output-dir /tmp/results
    .venv/bin/python scripts/compare_grounding.py --most-recent-screenshot-on-desktop "View order details"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Prompt template (same as src/automation_agent/vision/prompts/find_element.md)
# ---------------------------------------------------------------------------

FIND_ELEMENT_PROMPT = """\
Look at this screenshot of a macOS desktop. Find this element: {{element_description}}

Return pixel coordinates relative to the provided screenshot image (origin at top-left corner of the image).

If you CANNOT find it, respond: NOT_FOUND
If you CAN find it, respond: FOUND: x=<number>, y=<number>, confidence=<0.0-1.0>

Only respond with one of these formats, nothing else."""

# ---------------------------------------------------------------------------
# Coordinate spaces (mirrors coordinator.py COORDINATE_SPACES)
# ---------------------------------------------------------------------------

COORDINATE_SPACES: Dict[str, str] = {
    "molmo": "normalized_0_100",
    "molmo2": "normalized_0_1000",
    "qwen3-vl": "normalized_0_1000",
    "qwen2.5-vl": "normalized_0_1000",
    "qwen2-vl": "normalized_0_1000",
    "claude-sonnet-4-20250514": "pixel",
    "gpt-4.1": "pixel",
    "gpt-4o": "pixel",
    "gpt-5.4": "pixel",
    "gemini-2.5-flash": "normalized_0_1000",
    "gemini-3.0-flash": "normalized_0_1000",
    "gemini-3.0-pro": "normalized_0_1000",
    "gemini-3-flash-preview": "normalized_0_1000",
    "gemini-3-pro-preview": "normalized_0_1000",
    "gemini-3.1": "normalized_0_1000",
}

# Model display colors (RGB)
MODEL_COLORS: Dict[str, Tuple[int, int, int]] = {
    "gemini-flash": (0, 100, 255),     # blue
    "gemini-pro": (0, 60, 180),        # dark blue
    "gpt": (0, 200, 0),               # green
    "claude": (180, 0, 255),           # purple
    "molmo": (255, 140, 0),            # orange
    "molmo-8b": (200, 100, 0),         # dark orange
}

# Model IDs per backend
# NOTE: molmo-gui (MolmoPoint-GUI-8B) uses special token pointing, not text
# coordinates. It requires a custom inference pipeline and cannot be used with
# the standard text-parsing approach in this script.
MODEL_IDS: Dict[str, str] = {
    "gemini-flash": "gemini-3-flash-preview",
    "gemini-pro": "gemini-3-pro-preview",
    "gpt": "gpt-5.4",
    "claude": "claude-sonnet-4-20250514",
    "molmo": "mlx-community/Molmo-7B-D-0924-3bit",
    "molmo-8b": "mlx-community/Molmo2-8B-5bit",
}

# Local Molmo server ports
MOLMO_PORTS: Dict[str, int] = {
    "molmo": 8091,
    "molmo-8b": 8092,
}


# ---------------------------------------------------------------------------
# Coordinate parsing (7 regex patterns, mirrors coordinator.py _parse_coordinates)
# ---------------------------------------------------------------------------


def parse_coordinates(response: str) -> Optional[Tuple[float, float, float]]:
    """Parse coordinates and optional confidence from a vision model response.

    Returns (x, y, confidence) or None if NOT_FOUND.
    """
    response = response.strip()
    if response.upper().startswith("NOT_FOUND"):
        return None

    # Pattern group 1: FOUND: x=N, y=N (comma separated)
    # Pattern group 2: FOUND: x=N y=N (space separated, with y= label)
    # Pattern group 3: FOUND: x=N N (space separated, no y= label — Molmo2 fallback)
    patterns = [
        r'FOUND:\s*x\s*=\s*"?([0-9]*\.?[0-9]+)"?\s*,\s*y\s*=\s*"?([0-9]*\.?[0-9]+)"?'
        r'(?:\s*,?\s*confidence\s*=\s*"?([0-9]*\.?[0-9]+)"?)?',
        r'FOUND:\s*x\s*=\s*"?([0-9]*\.?[0-9]+)"?\s+y\s*=\s*"?([0-9]*\.?[0-9]+)"?'
        r'(?:\s*,?\s*confidence\s*=\s*"?([0-9]*\.?[0-9]+)"?)?',
        r'FOUND:\s*x\s*=\s*"?([0-9]*\.?[0-9]+)"?\s+"?([0-9]*\.?[0-9]+)"?'
        r'(?:\s*,?\s*confidence\s*=\s*"?([0-9]*\.?[0-9]+)"?)?',
    ]
    for pattern in patterns:
        match = re.search(pattern, response, re.IGNORECASE)
        if match:
            x = float(match.group(1))
            y = float(match.group(2))
            conf = float(match.group(3)) if match.lastindex and match.group(3) else 0.0
            return x, y, conf

    # <point x="N" y="N" confidence="N" />
    point_match = re.search(
        r'<point\b[^>]*\bx="([0-9]*\.?[0-9]+)"[^>]*\by="([0-9]*\.?[0-9]+)"[^>]*'
        r'(?:\bconfidence="([0-9]*\.?[0-9]+)")?[^>]*/?>',
        response,
        re.IGNORECASE,
    )
    if point_match:
        conf = float(point_match.group(3)) if point_match.group(3) else 0.0
        return float(point_match.group(1)), float(point_match.group(2)), conf

    # <points coords="..."> (triplet format)
    points_match = re.search(
        r'<points\b[^>]*\bcoords="([^"]+)"[^>]*/?>',
        response,
        re.IGNORECASE,
    )
    if points_match:
        coords_str = points_match.group(1)
        triplet = re.search(
            r'([0-9]+)\s+([0-9]*\.?[0-9]+)\s+([0-9]*\.?[0-9]+)', coords_str
        )
        if triplet:
            return float(triplet.group(2)), float(triplet.group(3)), 0.0

    # <points x1="N" y1="N" x2="N" y2="N"> bounding box → center
    points_xy_match = re.search(
        r'<points\b[^>]*\bx1="([0-9]*\.?[0-9]+)"[^>]*\by1="([0-9]*\.?[0-9]+)"'
        r'(?:[^>]*\bx2="([0-9]*\.?[0-9]+)"[^>]*\by2="([0-9]*\.?[0-9]+)")?',
        response,
        re.IGNORECASE,
    )
    if points_xy_match:
        x1 = float(points_xy_match.group(1))
        y1 = float(points_xy_match.group(2))
        if points_xy_match.group(3) and points_xy_match.group(4):
            x2 = float(points_xy_match.group(3))
            y2 = float(points_xy_match.group(4))
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
        else:
            cx, cy = x1, y1
        return cx, cy, 0.0

    # JSON fallback: {"x": N, "y": N} or {"point": {"x": N, "y": N}}
    json_match = re.search(r"\{.*\}", response, re.DOTALL)
    if json_match:
        try:
            payload = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            point = payload.get("point") or payload.get("target") or payload
            if isinstance(point, dict) and "x" in point and "y" in point:
                return (
                    float(point["x"]),
                    float(point["y"]),
                    float(point.get("confidence", 0.0) or 0.0),
                )

    return None


# ---------------------------------------------------------------------------
# Coordinate conversion (mirrors coordinator.py _convert_coordinates)
# ---------------------------------------------------------------------------


def resolve_coordinate_space(model: str) -> str:
    """Resolve coordinate space for a model via case-insensitive prefix matching."""
    model_lower = model.lower()
    if model_lower in COORDINATE_SPACES:
        return COORDINATE_SPACES[model_lower]
    for key, space in COORDINATE_SPACES.items():
        if model_lower.startswith(key):
            return space
    if "/" in model_lower:
        basename = model_lower.split("/", 1)[1]
        if basename in COORDINATE_SPACES:
            return COORDINATE_SPACES[basename]
        for key, space in COORDINATE_SPACES.items():
            if basename.startswith(key):
                return space
    return "pixel"  # default for cloud models


def convert_coordinates(
    raw_x: float,
    raw_y: float,
    space: str,
    img_width: int,
    img_height: int,
) -> Tuple[int, int]:
    """Convert model-specific coordinates to pixel coordinates."""
    # Out-of-range escalation
    if space == "normalized_0_100" and (raw_x > 100 or raw_y > 100):
        print(f"  [warn] Coordinates exceed 0-100 range, escalating to 0-1000")
        space = "normalized_0_1000"

    if space == "normalized_0_1":
        return (
            min(int(raw_x * img_width), img_width - 1),
            min(int(raw_y * img_height), img_height - 1),
        )
    elif space == "normalized_0_100":
        return (
            min(int(raw_x / 100.0 * img_width), img_width - 1),
            min(int(raw_y / 100.0 * img_height), img_height - 1),
        )
    elif space == "normalized_0_1000":
        return (
            min(int(raw_x / 1000 * img_width), img_width - 1),
            min(int(raw_y / 1000 * img_height), img_height - 1),
        )
    elif space == "pixel":
        return int(raw_x), int(raw_y)
    else:
        raise ValueError(f"Unknown coordinate space: {space}")


# ---------------------------------------------------------------------------
# Crosshair drawing (mirrors agent.py _draw_crosshair)
# ---------------------------------------------------------------------------


def draw_crosshair(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    color: Tuple[int, int, int] = (255, 0, 0),
    r: int = 30,
) -> None:
    """Draw a crosshair with label on an ImageDraw canvas."""
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


# ---------------------------------------------------------------------------
# Backend implementations (self-contained, no automation_agent imports)
# ---------------------------------------------------------------------------


# Default width the agent sends to vision models (matches ScreenCapture default)
_TARGET_WIDTH = 1024


def _load_and_resize(path: str) -> Tuple[Image.Image, Tuple[int, int]]:
    """Load image, resize to _TARGET_WIDTH, return (PIL image, (w,h))."""
    img = Image.open(path)
    if img.mode == "RGBA":
        img = img.convert("RGB")
    orig_w, orig_h = img.size
    if orig_w > _TARGET_WIDTH:
        scale = _TARGET_WIDTH / orig_w
        new_h = int(orig_h * scale)
        img = img.resize((_TARGET_WIDTH, new_h), Image.LANCZOS)
        print(f"Resized: {orig_w}x{orig_h} → {_TARGET_WIDTH}x{new_h}")
    return img, img.size


def _image_to_b64(img: Image.Image) -> str:
    """Encode a PIL image as base64 JPEG."""
    import io

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def query_molmo(prompt: str, image_b64: str, model_name: str = "molmo") -> str:
    """Query local Molmo via OpenAI-compatible API."""
    port = MOLMO_PORTS[model_name]
    url = f"http://localhost:{port}/v1/chat/completions"
    payload = {
        "model": MODEL_IDS[model_name],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        },
                    },
                ],
            }
        ],
        "max_tokens": 256,
        "temperature": 0,
    }
    resp = httpx.post(url, json=payload, timeout=120.0)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def query_gemini(prompt: str, image_b64: str, model_name: str = "gemini-flash") -> str:
    """Query Gemini API via google.genai."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("AGENT_GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY or AGENT_GEMINI_API_KEY not set")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    image_bytes = base64.b64decode(image_b64)
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    response = client.models.generate_content(
        model=MODEL_IDS[model_name],
        contents=[image_part, prompt],
    )
    return response.text


def query_claude(prompt: str, image_b64: str) -> str:
    """Query Claude API via anthropic SDK."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AGENT_ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY or AGENT_ANTHROPIC_API_KEY not set")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=MODEL_IDS["claude"],
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return message.content[0].text


def query_gpt(prompt: str, image_b64: str) -> str:
    """Query GPT via OpenAI Responses API."""
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("AGENT_OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY or AGENT_OPENAI_API_KEY not set")

    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL_IDS["gpt"],
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{image_b64}",
                        "detail": "high",
                    },
                ],
            }
        ],
    }
    resp = httpx.post(url, json=payload, headers=headers, timeout=120.0)
    resp.raise_for_status()
    data = resp.json()

    # Extract text from Responses API format
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = data.get("output", [])
    parts: List[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("text"), str):
            parts.append(item["text"])
        content = item.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
    return "\n".join(parts) if parts else json.dumps(data.get("output", ""))


ALL_MODELS = list(MODEL_IDS.keys())


def _query_model(model_name: str, prompt: str, image_b64: str) -> str:
    """Dispatch a query to the right backend based on model name."""
    if model_name.startswith("molmo"):
        return query_molmo(prompt, image_b64, model_name=model_name)
    elif model_name.startswith("gemini"):
        return query_gemini(prompt, image_b64, model_name=model_name)
    elif model_name == "claude":
        return query_claude(prompt, image_b64)
    elif model_name == "gpt":
        return query_gpt(prompt, image_b64)
    else:
        raise ValueError(f"Unknown model: {model_name}")


# ---------------------------------------------------------------------------
# Main comparison logic
# ---------------------------------------------------------------------------


def run_model(
    model_name: str,
    prompt: str,
    label: str,
    image_b64: str,
    img_width: int,
    img_height: int,
    output_dir: Path,
    original_image_path: str,
    run_tag: str = "",
) -> Dict[str, Any]:
    """Run a single model and return structured result."""
    result: Dict[str, Any] = {
        "model": MODEL_IDS[model_name],
        "raw_response": None,
        "parsed_x": None,
        "parsed_y": None,
        "raw_x": None,
        "raw_y": None,
        "confidence": None,
        "coordinate_space": None,
        "latency_s": None,
        "error": None,
    }

    color = MODEL_COLORS[model_name]
    kb_size = len(image_b64) * 3 // 4 // 1024

    print(f"[{model_name}] Sending {img_width}x{img_height} JPEG ({kb_size}KB), label: \"{label}\"")
    print(f"[{model_name}] Prompt: \"{prompt[:80]}...\"")

    try:
        t0 = time.time()
        raw_response = _query_model(model_name, prompt, image_b64)
        latency = time.time() - t0
    except Exception as e:
        result["error"] = str(e)
        print(f"[{model_name}] ERROR: {e}")
        return result

    result["raw_response"] = raw_response
    result["latency_s"] = round(latency, 2)
    print(f"[{model_name}] Response ({latency:.1f}s): \"{raw_response[:120]}\"")

    parsed = parse_coordinates(raw_response)
    if parsed is None:
        print(f"[{model_name}] Result: NOT_FOUND or unparseable")
        return result

    raw_x, raw_y, conf = parsed
    result["raw_x"] = raw_x
    result["raw_y"] = raw_y
    result["confidence"] = conf

    space = resolve_coordinate_space(MODEL_IDS[model_name])
    result["coordinate_space"] = space

    px_x, px_y = convert_coordinates(raw_x, raw_y, space, img_width, img_height)
    result["parsed_x"] = px_x
    result["parsed_y"] = px_y

    print(
        f"[{model_name}] Parsed: ({raw_x}, {raw_y}) conf={conf} space={space}"
        f" → pixel ({px_x}, {px_y})"
    )

    # Draw crosshair on a copy of the image
    img = Image.open(original_image_path)
    if img.mode == "RGBA":
        img = img.convert("RGB")
    draw = ImageDraw.Draw(img)
    label = f"{model_name} ({px_x},{px_y})"
    draw_crosshair(draw, px_x, px_y, label, color=color)

    suffix = f"_{run_tag}" if run_tag else ""
    out_path = output_dir / f"{model_name}_result{suffix}.jpg"
    img.save(out_path, quality=90)
    result["image_path"] = str(out_path)
    print(f"[{model_name}] Saved: {out_path}")

    return result


def make_comparison_grid(
    model_names: List[str],
    results: Dict[str, Dict[str, Any]],
    original_image_path: str,
    output_dir: Path,
    run_tag: str = "",
) -> None:
    """Create a dynamic grid image from per-model results."""
    import math

    original = Image.open(original_image_path)
    if original.mode == "RGBA":
        original = original.convert("RGB")
    cell_w, cell_h = original.size

    suffix = f"_{run_tag}" if run_tag else ""

    # Use all registered models as slots (shows SKIPPED for non-selected ones)
    all_models = ALL_MODELS
    n = len(all_models)
    cols = min(3, n)
    rows = math.ceil(n / cols)
    grid = Image.new("RGB", (cell_w * cols, cell_h * rows), (40, 40, 40))

    for idx, model_name in enumerate(all_models):
        col = idx % cols
        row = idx // cols
        x_off = col * cell_w
        y_off = row * cell_h

        if model_name in results and results[model_name].get("parsed_x") is not None:
            cell_path = output_dir / f"{model_name}_result{suffix}.jpg"
            if cell_path.exists():
                cell_img = Image.open(cell_path)
                grid.paste(cell_img, (x_off, y_off))
                continue

        # Model was skipped or had no result — paste original with SKIPPED label
        cell_img = original.copy()
        draw = ImageDraw.Draw(cell_img)
        if model_name not in model_names:
            label = f"{model_name}: SKIPPED"
        elif results.get(model_name, {}).get("error"):
            label = f"{model_name}: ERROR"
        else:
            label = f"{model_name}: NOT_FOUND"

        # Draw label centered
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
        except (OSError, IOError):
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (cell_w - tw) // 2
        ty = (cell_h - th) // 2
        draw.rectangle(
            [tx - 10, ty - 10, tx + tw + 10, ty + th + 10],
            fill=(0, 0, 0, 180),
        )
        draw.text((tx, ty), label, fill=(255, 255, 0), font=font)
        grid.paste(cell_img, (x_off, y_off))

    grid_path = output_dir / f"comparison_grid{suffix}.jpg"
    grid.save(grid_path, quality=90)
    print(f"\nGrid saved: {grid_path}")


def _load_dotenv() -> None:
    """Load .env file if present (no dependency on python-dotenv)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def main() -> None:
    _load_dotenv()

    parser = argparse.ArgumentParser(
        description="Compare grounding accuracy across vision models"
    )
    parser.add_argument(
        "screenshot", nargs="?", help="Path to screenshot image (PNG/JPEG)"
    )
    parser.add_argument("label", help="Element description to find")
    parser.add_argument(
        "--output-dir",
        default="./grounding_comparison/",
        help="Where to save results (default: ./grounding_comparison/)",
    )
    all_default = ",".join(ALL_MODELS)
    parser.add_argument(
        "--models",
        default=all_default,
        help=f"Comma-separated model subset (default: {all_default})",
    )
    parser.add_argument(
        "--most-recent-screenshot-on-desktop",
        action="store_true",
        help="Use the most recent screenshot file from ~/Desktop",
    )
    args = parser.parse_args()

    if args.most_recent_screenshot_on_desktop:
        desktop = Path.home() / "Desktop"
        screenshots = sorted(
            (
                p
                for p in desktop.iterdir()
                if p.suffix.lower() in (".png", ".jpg", ".jpeg")
                and p.stem.lower().startswith("screenshot")
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not screenshots:
            print(f"Error: no screenshot files found on {desktop}")
            sys.exit(1)
        screenshot_path = str(screenshots[0])
        print(f"Using most recent desktop screenshot: {screenshot_path}")
    elif args.screenshot:
        screenshot_path = args.screenshot
    else:
        print("Error: provide a screenshot path or use --most-recent-screenshot-on-desktop")
        sys.exit(1)

    if not os.path.exists(screenshot_path):
        print(f"Error: screenshot not found: {screenshot_path}")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    model_names = [m.strip() for m in args.models.split(",")]
    valid_models = set(ALL_MODELS)
    for m in model_names:
        if m not in valid_models:
            print(f"Error: unknown model '{m}'. Valid: {sorted(valid_models)}")
            sys.exit(1)

    resized_img, (img_width, img_height) = _load_and_resize(screenshot_path)
    image_b64 = _image_to_b64(resized_img)

    # Save the resized image so crosshairs are drawn at the correct scale
    resized_path = output_dir / "_resized_input.jpg"
    resized_img.save(resized_path, quality=90)

    prompt = FIND_ELEMENT_PROMPT.replace("{{element_description}}", args.label)
    run_tag = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"Screenshot: {screenshot_path} → {img_width}x{img_height}")
    print(f"Label: \"{args.label}\"")
    print(f"Models: {model_names}")
    print(f"Output: {output_dir}  (tag: {run_tag})")
    print("=" * 60)

    results: Dict[str, Dict[str, Any]] = {}
    for model_name in model_names:
        print()
        result = run_model(
            model_name=model_name,
            prompt=prompt,
            label=args.label,
            image_b64=image_b64,
            img_width=img_width,
            img_height=img_height,
            output_dir=output_dir,
            original_image_path=str(resized_path),
            run_tag=run_tag,
        )
        results[model_name] = result

    # Save results.json
    output_json = {
        "screenshot": str(Path(screenshot_path).resolve()),
        "label": args.label,
        "image_size": [img_width, img_height],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "run_tag": run_tag,
        "results": results,
    }
    results_path = output_dir / f"results_{run_tag}.json"
    with open(results_path, "w") as f:
        json.dump(output_json, f, indent=2)
    print(f"\nResults: {results_path}")

    # Generate comparison grid
    make_comparison_grid(model_names, results, str(resized_path), output_dir, run_tag)

    # Summary table
    print("\n" + "=" * 60)
    print(f"{'Model':<10} {'Pixel (x,y)':<16} {'Conf':<8} {'Latency':<10} {'Status'}")
    print("-" * 60)
    for model_name in model_names:
        r = results[model_name]
        if r.get("error"):
            print(f"{model_name:<10} {'—':<16} {'—':<8} {'—':<10} ERROR: {r['error'][:40]}")
        elif r.get("parsed_x") is not None:
            coord = f"({r['parsed_x']}, {r['parsed_y']})"
            conf = f"{r['confidence']:.2f}" if r["confidence"] is not None else "—"
            lat = f"{r['latency_s']:.1f}s"
            print(f"{model_name:<10} {coord:<16} {conf:<8} {lat:<10} OK")
        else:
            lat = f"{r['latency_s']:.1f}s" if r["latency_s"] else "—"
            print(f"{model_name:<10} {'—':<16} {'—':<8} {lat:<10} NOT_FOUND")


if __name__ == "__main__":
    main()
