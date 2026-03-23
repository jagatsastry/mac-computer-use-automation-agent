#!/usr/bin/env python3
"""Benchmark grounding accuracy using the GroundCUA dataset.

Evaluates vision models on real desktop UI screenshots from 87 platforms.
Uses point-in-bbox accuracy: the predicted (x,y) must fall inside the
ground-truth bounding box.

Usage:
    .venv/bin/python scripts/benchmark_groundcua.py --n 50
    .venv/bin/python scripts/benchmark_groundcua.py --n 100 --models gemini-flash,gpt,claude
    .venv/bin/python scripts/benchmark_groundcua.py --n 20 --platforms Chrome,Firefox
    .venv/bin/python scripts/benchmark_groundcua.py --n 50 --save-screenshots

Standalone script -- does NOT import from automation_agent package.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from PIL import Image

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
# Constants (reused from compare_grounding.py)
# ---------------------------------------------------------------------------

# Per-model prompts: Gemini uses 0-1000 normalized grid, others use pixel coords.
PROMPTS: Dict[str, str] = {
    "gemini": """\
Look at this screenshot. Find this UI element: {{element_description}}

Return coordinates on a 0-1000 normalized grid (0=top-left, 999=bottom-right).

If you CANNOT find it, respond: NOT_FOUND
If you CAN find it, respond: FOUND: x=<number>, y=<number>, confidence=<0.0-1.0>

Only respond with one of these formats, nothing else.""",

    "default": """\
Look at this screenshot of a macOS desktop. Find this element: {{element_description}}

Return pixel coordinates relative to the provided screenshot image (origin at top-left corner of the image).

If you CANNOT find it, respond: NOT_FOUND
If you CAN find it, respond: FOUND: x=<number>, y=<number>, confidence=<0.0-1.0>

Only respond with one of these formats, nothing else.""",
}


def get_prompt(model_name: str, label: str) -> str:
    key = "gemini" if model_name.startswith("gemini") else "default"
    return PROMPTS[key].replace("{{element_description}}", label)

MODEL_IDS: Dict[str, str] = {
    "gemini-flash": "gemini-3-flash-preview",
    "gemini-pro": "gemini-3-pro-preview",
    "gpt": "gpt-5.4",
    "claude": "claude-sonnet-4-20250514",
    "molmo": "mlx-community/Molmo-7B-D-0924-3bit",
    "molmo2": "mlx-community/Molmo2-8B-5bit",
    "molmo-point": "mlx-community/MolmoPoint-8B-4bit",
    "molmo-point-gui": "allenai/MolmoPoint-GUI-8B",
}

COORDINATE_SPACES: Dict[str, str] = {
    "molmo-point": "normalized_0_100",  # MolmoPoint: same as Molmo v1
    "molmo2": "normalized_0_1000",      # Molmo2: 0-1000
    "molmo": "normalized_0_100",        # Molmo v1: 0-100
    "gemini": "normalized_0_1000",
    "gpt": "pixel",
    "claude": "pixel",
}

# All local Molmo models use the same server, just swap which one is running.
# Only one can run at a time due to memory constraints.
MOLMO_PORTS: Dict[str, int] = {
    "molmo": 8091,
    "molmo2": 8092,
    "molmo-point": 8092,
    "molmo-point-gui": 8092,
}

_TARGET_WIDTH = 1024
CACHE_DIR = Path.home() / ".cache" / "automation_agent" / "groundcua"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class Sample:
    platform: str
    image_path: str  # relative, e.g. "Chrome/abc123.png"
    text: str        # element label
    bbox: List[float]  # [x1, y1, x2, y2] pixel coords in original image
    category: str
    element_id: str
    image_width: int = 0
    image_height: int = 0


@dataclass
class Result:
    sample_idx: int
    platform: str
    instruction: str
    model: str
    predicted_x: Optional[int]
    predicted_y: Optional[int]
    bbox: List[float]
    hit: bool
    latency_s: float
    raw_response: str
    error: Optional[str]


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

def _list_platforms() -> List[str]:
    """List available platforms from HuggingFace dataset tree."""
    url = "https://huggingface.co/api/datasets/ServiceNow/GroundCUA/tree/main/data"
    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    entries = resp.json()
    return sorted(e["path"].replace("data/", "") for e in entries if e["type"] == "directory")


def _list_jsons_for_platform(platform: str, limit: int = 50) -> List[str]:
    """List JSON annotation files for a platform."""
    url = f"https://huggingface.co/api/datasets/ServiceNow/GroundCUA/tree/main/data/{platform}"
    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    entries = resp.json()
    jsons = [e["path"] for e in entries if e["path"].endswith(".json")]
    return jsons[:limit]


def _download_file(path: str) -> bytes:
    """Download a file from the GroundCUA dataset, with local cache."""
    cache_path = CACHE_DIR / path
    if cache_path.exists():
        return cache_path.read_bytes()

    url = f"https://huggingface.co/datasets/ServiceNow/GroundCUA/resolve/main/{path}"
    resp = httpx.get(url, timeout=60, follow_redirects=True)
    resp.raise_for_status()

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.content)
    return resp.content


def load_samples(
    n: int = 50,
    platforms: Optional[List[str]] = None,
    seed: int = 42,
) -> List[Sample]:
    """Load n samples from the GroundCUA dataset.

    Picks random platforms and random elements within each screenshot.
    """
    rng = random.Random(seed)

    print("Fetching platform list...")
    all_platforms = _list_platforms()
    if platforms:
        all_platforms = [p for p in all_platforms if p in platforms]
        if not all_platforms:
            print(f"Error: none of {platforms} found. Available: {_list_platforms()[:10]}...")
            sys.exit(1)

    print(f"Found {len(all_platforms)} platforms, loading samples...")

    # Distribute n samples across platforms
    samples: List[Sample] = []
    # Shuffle platforms and round-robin
    rng.shuffle(all_platforms)
    platform_cycle = all_platforms * ((n // len(all_platforms)) + 2)

    attempts = 0
    max_attempts = n * 5
    pi = 0

    while len(samples) < n and attempts < max_attempts:
        platform = platform_cycle[pi % len(platform_cycle)]
        pi += 1
        attempts += 1

        try:
            jsons = _list_jsons_for_platform(platform, limit=100)
            if not jsons:
                continue
            json_path = rng.choice(jsons)
            data = json.loads(_download_file(json_path))
            if not data:
                continue

            # Pick a random element from this screenshot
            element = rng.choice(data)
            img_rel = element.get("image_path", "")
            text = element.get("text", "")
            bbox = element.get("bbox", [])
            if not img_rel or not text or len(bbox) != 4:
                continue

            # Download the image to get dimensions
            img_path = f"images/{img_rel}"
            try:
                img_bytes = _download_file(img_path)
            except httpx.HTTPStatusError:
                continue
            img = Image.open(io.BytesIO(img_bytes))
            w, h = img.size

            # Skip tiny bboxes (< 5px in either dimension)
            bw = bbox[2] - bbox[0]
            bh = bbox[3] - bbox[1]
            if bw < 5 or bh < 5:
                continue

            samples.append(Sample(
                platform=platform,
                image_path=img_rel,
                text=text,
                bbox=bbox,
                category=element.get("category", ""),
                element_id=element.get("id", ""),
                image_width=w,
                image_height=h,
            ))
            print(f"  [{len(samples)}/{n}] {platform}: \"{text[:50]}\" ({w}x{h})")

        except Exception as e:
            print(f"  [skip] {platform}: {e}")
            continue

    print(f"Loaded {len(samples)} samples from {len(set(s.platform for s in samples))} platforms")
    return samples


# ---------------------------------------------------------------------------
# Image preparation
# ---------------------------------------------------------------------------

def prepare_image(sample: Sample, resize: bool = True) -> Tuple[str, int, int]:
    """Load image, optionally resize to _TARGET_WIDTH, return (base64, w, h).

    Pixel-coord models (Claude, GPT) benefit from resize (smaller targets
    are proportionally larger). Gemini uses 0-1000 normalized coords so
    resolution doesn't affect coordinate accuracy — send original.
    """
    img_bytes = _download_file(f"images/{sample.image_path}")
    img = Image.open(io.BytesIO(img_bytes))
    if img.mode == "RGBA":
        img = img.convert("RGB")

    if resize:
        orig_w, orig_h = img.size
        if orig_w > _TARGET_WIDTH:
            scale = _TARGET_WIDTH / orig_w
            new_h = int(orig_h * scale)
            img = img.resize((_TARGET_WIDTH, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return b64, img.size[0], img.size[1]


# ---------------------------------------------------------------------------
# Coordinate parsing (from compare_grounding.py)
# ---------------------------------------------------------------------------

def parse_coordinates(response: str) -> Optional[Tuple[float, float]]:
    """Parse coordinates from model response. Returns (x, y) or None.

    Tries JSON first (preferred), then legacy FOUND: format, then XML.
    """
    response = response.strip()

    # 1. JSON — preferred format {"found": true, "x": N, "y": N}
    json_m = re.search(r"\{[^{}]*\}", response, re.DOTALL)
    if json_m:
        try:
            payload = json.loads(json_m.group(0))
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            # {"found": false} → not found
            if payload.get("found") is False:
                return None
            # {"found": true, "x": N, "y": N} or {"x": N, "y": N}
            if "x" in payload and "y" in payload:
                return float(payload["x"]), float(payload["y"])
            # {"point": {"x": N, "y": N}}
            pt = payload.get("point") or payload.get("target")
            if isinstance(pt, dict) and "x" in pt and "y" in pt:
                return float(pt["x"]), float(pt["y"])

    # 2. Legacy FOUND: format fallback
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


def convert_coordinates(raw_x: float, raw_y: float, model_name: str, w: int, h: int) -> Tuple[int, int]:
    """Convert model coordinates to pixel coords."""
    # Determine space from model name
    space = "pixel"
    for prefix, sp in COORDINATE_SPACES.items():
        if model_name.startswith(prefix):
            space = sp
            break

    if space == "normalized_0_100":
        if raw_x > 100 or raw_y > 100:
            space = "normalized_0_1000"
        else:
            return min(int(raw_x / 100.0 * w), w - 1), min(int(raw_y / 100.0 * h), h - 1)

    if space == "normalized_0_1000":
        return min(int(raw_x / 1000 * w), w - 1), min(int(raw_y / 1000 * h), h - 1)

    return int(raw_x), int(raw_y)


def point_in_bbox(x: int, y: int, bbox: List[float]) -> bool:
    """Check if (x,y) falls inside [x1, y1, x2, y2]."""
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


# ---------------------------------------------------------------------------
# Model query functions (from compare_grounding.py)
# ---------------------------------------------------------------------------

def query_gemini(prompt: str, image_b64: str, model_name: str = "gemini-flash") -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("AGENT_GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY not set")
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)
    image_bytes = base64.b64decode(image_b64)
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    response = client.models.generate_content(
        model=MODEL_IDS[model_name], contents=[image_part, prompt],
    )
    return response.text


def query_claude(prompt: str, image_b64: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AGENT_ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set")
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


def query_gpt(prompt: str, image_b64: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("AGENT_OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY not set")
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
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    parts = []
    for item in data.get("output", []):
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("text"), str):
            parts.append(item["text"])
        for block in (item.get("content") or []):
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
    return "\n".join(parts) if parts else ""


def query_molmo(prompt: str, image_b64: str, model_name: str = "molmo") -> str:
    port = MOLMO_PORTS[model_name]
    url = f"http://localhost:{port}/v1/chat/completions"
    payload = {
        "model": MODEL_IDS[model_name],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ]}],
        "max_tokens": 256, "temperature": 0,
    }
    resp = httpx.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def dispatch_query(model_name: str, prompt: str, image_b64: str) -> str:
    if model_name.startswith("gemini"):
        return query_gemini(prompt, image_b64, model_name=model_name)
    elif model_name.startswith("molmo"):
        return query_molmo(prompt, image_b64, model_name=model_name)
    elif model_name == "claude":
        return query_claude(prompt, image_b64)
    elif model_name == "gpt":
        return query_gpt(prompt, image_b64)
    raise ValueError(f"Unknown model: {model_name}")


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

def run_benchmark(
    samples: List[Sample],
    model_names: List[str],
    output_dir: Path,
    save_screenshots: bool = False,
) -> Dict[str, Any]:
    """Run all samples through all models and compute accuracy."""
    run_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    results: List[Result] = []
    model_stats: Dict[str, Dict[str, int]] = {
        m: {"hits": 0, "misses": 0, "errors": 0, "not_found": 0} for m in model_names
    }

    total = len(samples) * len(model_names)
    done = 0

    # Pre-compute per-model image variants: Gemini gets original, others get resized
    # Gemini uses 0-1000 normalized coords — resolution-independent, send original.
    # All others (including Molmo) benefit from resized images.
    _uses_original = lambda m: m.startswith("gemini")

    for si, sample in enumerate(samples):
        # Prepare both variants (cached per sample)
        try:
            img_orig_b64, orig_w, orig_h = prepare_image(sample, resize=False)
            img_resized_b64, resized_w, resized_h = prepare_image(sample, resize=True)
        except Exception as e:
            print(f"  [skip] Failed to load image {sample.image_path}: {e}")
            continue

        # Bbox scaling for resized images
        if orig_w != resized_w:
            sx, sy = resized_w / orig_w, resized_h / orig_h
            resized_bbox = [sample.bbox[0] * sx, sample.bbox[1] * sy,
                            sample.bbox[2] * sx, sample.bbox[3] * sy]
        else:
            resized_bbox = sample.bbox

        for model_name in model_names:
            done += 1
            prompt = get_prompt(model_name, sample.text)
            prefix = f"[{done}/{total}] [{model_name}]"

            # Gemini: original image + original bbox (0-1000 normalized coords)
            # Others: resized image + scaled bbox (pixel coords)
            if _uses_original(model_name):
                img_b64, img_w, img_h = img_orig_b64, orig_w, orig_h
                eval_bbox = sample.bbox
            else:
                img_b64, img_w, img_h = img_resized_b64, resized_w, resized_h
                eval_bbox = resized_bbox

            try:
                t0 = time.time()
                raw = dispatch_query(model_name, prompt, img_b64)
                latency = time.time() - t0
            except Exception as e:
                model_stats[model_name]["errors"] += 1
                results.append(Result(
                    sample_idx=si, platform=sample.platform,
                    instruction=sample.text, model=model_name,
                    predicted_x=None, predicted_y=None,
                    bbox=eval_bbox, hit=False, latency_s=0,
                    raw_response="", error=str(e),
                ))
                print(f"{prefix} ERROR: {str(e)[:60]}")
                continue

            parsed = parse_coordinates(raw)
            if parsed is None:
                model_stats[model_name]["not_found"] += 1
                results.append(Result(
                    sample_idx=si, platform=sample.platform,
                    instruction=sample.text, model=model_name,
                    predicted_x=None, predicted_y=None,
                    bbox=eval_bbox, hit=False, latency_s=latency,
                    raw_response=raw[:200], error=None,
                ))
                print(f"{prefix} NOT_FOUND ({latency:.1f}s) \"{sample.text[:40]}\"")
                continue

            px, py = convert_coordinates(parsed[0], parsed[1], model_name, img_w, img_h)
            hit = point_in_bbox(px, py, eval_bbox)

            if hit:
                model_stats[model_name]["hits"] += 1
            else:
                model_stats[model_name]["misses"] += 1

            results.append(Result(
                sample_idx=si, platform=sample.platform,
                instruction=sample.text, model=model_name,
                predicted_x=px, predicted_y=py,
                bbox=eval_bbox, hit=hit, latency_s=latency,
                raw_response=raw[:200], error=None,
            ))

            marker = "HIT" if hit else "MISS"
            print(
                f"{prefix} {marker} ({px},{py}) bbox={[int(b) for b in eval_bbox]}"
                f" ({latency:.1f}s) \"{sample.text[:40]}\""
            )

            if save_screenshots and not hit:
                _save_debug(output_dir, run_tag, si, model_name, img_b64, px, py, eval_bbox, sample.text)

    # Summary
    report = {
        "run_tag": run_tag,
        "n_samples": len(samples),
        "n_platforms": len(set(s.platform for s in samples)),
        "models": {},
        "results": [asdict(r) for r in results],
    }
    for m in model_names:
        s = model_stats[m]
        total_m = s["hits"] + s["misses"] + s["errors"] + s["not_found"]
        evaluated = s["hits"] + s["misses"]
        acc = s["hits"] / evaluated * 100 if evaluated > 0 else 0
        report["models"][m] = {
            "model_id": MODEL_IDS.get(m, m),
            "accuracy": round(acc, 1),
            "hits": s["hits"],
            "misses": s["misses"],
            "not_found": s["not_found"],
            "errors": s["errors"],
            "total": total_m,
        }

    out_path = output_dir / f"groundcua_benchmark_{run_tag}.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nResults saved: {out_path}")

    # Print summary table
    print(f"\n{'='*70}")
    print(f"GroundCUA Benchmark Results ({len(samples)} samples, {len(set(s.platform for s in samples))} platforms)")
    print(f"{'='*70}")
    print(f"{'Model':<15} {'Accuracy':>8} {'Hits':>6} {'Miss':>6} {'N/F':>5} {'Err':>5} {'Total':>6}")
    print(f"{'-'*70}")
    for m in model_names:
        d = report["models"][m]
        print(
            f"{m:<15} {d['accuracy']:>7.1f}% {d['hits']:>6} {d['misses']:>6}"
            f" {d['not_found']:>5} {d['errors']:>5} {d['total']:>6}"
        )
    print(f"{'='*70}")

    return report


def _save_debug(
    output_dir: Path, run_tag: str, idx: int, model: str,
    img_b64: str, px: int, py: int, bbox: List[float], text: str,
) -> None:
    """Save a debug image for missed predictions."""
    from PIL import ImageDraw
    img_bytes = base64.b64decode(img_b64)
    img = Image.open(io.BytesIO(img_bytes))
    draw = ImageDraw.Draw(img)

    # Draw bbox
    draw.rectangle([(bbox[0], bbox[1]), (bbox[2], bbox[3])], outline=(0, 255, 0), width=3)
    # Draw predicted point
    r = 15
    draw.line([(px - r, py), (px + r, py)], fill=(255, 0, 0), width=3)
    draw.line([(px, py - r), (px, py + r)], fill=(255, 0, 0), width=3)

    debug_dir = output_dir / f"debug_{run_tag}"
    debug_dir.mkdir(parents=True, exist_ok=True)
    img.save(debug_dir / f"{idx:04d}_{model}_miss.jpg", quality=85)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark grounding on GroundCUA dataset")
    parser.add_argument("--n", type=int, default=50, help="Number of samples (default: 50)")
    parser.add_argument(
        "--models", default="gemini-flash,gpt,claude",
        help="Comma-separated models (default: gemini-flash,gpt,claude)",
    )
    parser.add_argument("--platforms", default=None, help="Comma-separated platform filter")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument(
        "--output-dir", default="./groundcua_benchmark/",
        help="Output directory (default: ./groundcua_benchmark/)",
    )
    parser.add_argument("--save-screenshots", action="store_true", help="Save debug images for misses")
    parser.add_argument("--list-platforms", action="store_true", help="List available platforms and exit")
    args = parser.parse_args()

    if args.list_platforms:
        platforms = _list_platforms()
        print(f"{len(platforms)} platforms available:")
        for p in platforms:
            print(f"  {p}")
        return

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    model_names = [m.strip() for m in args.models.split(",")]
    valid = set(MODEL_IDS.keys())
    for m in model_names:
        if m not in valid:
            print(f"Error: unknown model '{m}'. Valid: {sorted(valid)}")
            sys.exit(1)

    platform_filter = [p.strip() for p in args.platforms.split(",")] if args.platforms else None

    samples = load_samples(n=args.n, platforms=platform_filter, seed=args.seed)
    if not samples:
        print("No samples loaded.")
        sys.exit(1)

    run_benchmark(samples, model_names, output_dir, save_screenshots=args.save_screenshots)


if __name__ == "__main__":
    main()
