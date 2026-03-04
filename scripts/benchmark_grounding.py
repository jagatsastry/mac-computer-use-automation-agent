#!/usr/bin/env python3
"""Benchmark visual grounding accuracy across vision backends using ScreenSpot dataset.

Measures how accurately different vision models can locate UI elements on screen
given a natural language description. Uses the ScreenSpot dataset (1,272 samples)
with point-in-bounding-box accuracy as the primary metric.

Usage:
    python scripts/benchmark_grounding.py
    python scripts/benchmark_grounding.py --backends claude-sonnet qwen2.5-vl-ollama
    python scripts/benchmark_grounding.py --n 20 --category macOS
    python scripts/benchmark_grounding.py --save-screenshots --no-cache

Standalone script -- does NOT import from automation_agent package.
"""

import argparse
import base64
import io
import json
import math
import os
import random
import re
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkSample:
    """One sample from the ScreenSpot dataset."""
    file_name: str                      # e.g. "pc_ede36f9b-1154-4f76-b7f8-c15d7d3f9b6e.png"
    instruction: str                    # e.g. "close", "minimize this window"
    bbox: List[float]                   # [left, top, right, bottom] normalized 0-1
    data_type: str                      # "icon" or "text"
    data_source: str                    # "macOS", "windows", "iOS", "Android", etc.
    image_width: int                    # original image width in pixels
    image_height: int                   # original image height in pixels
    image_bytes: bytes                  # raw PNG/JPEG bytes of the screenshot image


@dataclass
class BackendResult:
    """Result of running one sample through one backend."""
    sample_file_name: str               # links back to BenchmarkSample.file_name
    instruction: str                    # the instruction sent to the model
    backend_name: str                   # e.g. "claude-sonnet", "qwen2.5-vl-ollama"
    predicted_x: Optional[float]        # normalized 0-1, or None if model failed/timed out
    predicted_y: Optional[float]        # normalized 0-1, or None if model failed/timed out
    ground_truth_bbox: List[float]      # [left, top, right, bottom] normalized 0-1
    hit: bool                           # True if predicted point is inside ground_truth_bbox
    distance_px: float                  # Euclidean distance in pixels (diagonal if None prediction)
    latency_s: float                    # seconds for this single inference call
    raw_response: str                   # raw text response from the model
    error: Optional[str]                # error message if the call failed, else None


# ---------------------------------------------------------------------------
# Coordinate pipeline (copied from coordinator.py + new normalize function)
# ---------------------------------------------------------------------------

# Copied from src/automation_agent/vision/coordinator.py
COORDINATE_SPACES: Dict[str, str] = {
    "molmo": "normalized_0_100",
    "qwen3-vl": "normalized_0_1000",
    "qwen2.5-vl": "normalized_0_1000",
    "qwen2-vl": "normalized_0_1000",
    "claude-sonnet-4-20250514": "pixel",
}


# Copied from src/automation_agent/vision/coordinator.py
def _resolve_coordinate_space(model: str) -> Optional[str]:
    """Resolve coordinate space for a model via case-insensitive prefix or substring matching."""
    model_lower = model.lower()
    if model_lower in COORDINATE_SPACES:
        return COORDINATE_SPACES[model_lower]
    for key, space in COORDINATE_SPACES.items():
        if model_lower.startswith(key):
            return space
    # Substring match for HuggingFace-style model IDs (e.g. "mlx-community/Molmo-...")
    for key, space in COORDINATE_SPACES.items():
        if key in model_lower:
            return space
    return None


# Copied from src/automation_agent/vision/coordinator.py
def _parse_coordinates(response: str) -> Optional[Tuple[float, float]]:
    """Parse coordinates from a vision model response.

    Expects either:
        FOUND: x=<number>, y=<number>
    or:
        NOT_FOUND
    """
    response = response.strip()
    if response.upper().startswith("NOT_FOUND"):
        return None

    match = re.search(
        r'FOUND:\s*x\s*=\s*"?([0-9]*\.?[0-9]+)"?\s*[,\s]\s*y\s*=\s*"?([0-9]*\.?[0-9]+)"?',
        response,
        re.IGNORECASE,
    )
    if match:
        return float(match.group(1)), float(match.group(2))

    # Try Molmo's native <point x="..." y="..."> format
    point_match = re.search(
        r'<point\s+x="([0-9]*\.?[0-9]+)"\s+y="([0-9]*\.?[0-9]+)"',
        response,
        re.IGNORECASE,
    )
    if point_match:
        return float(point_match.group(1)), float(point_match.group(2))

    return None


def normalize_prediction(
    raw_x: float,
    raw_y: float,
    model: str,
    image_width: int,
    image_height: int,
) -> Tuple[float, float]:
    """Normalize a model's raw coordinate output to 0-1 range.

    Args:
        raw_x, raw_y: Raw coordinates parsed from model response.
        model: Model name (used to look up coordinate space).
        image_width, image_height: Original image dimensions in pixels.

    Returns:
        (x, y) normalized to 0.0-1.0 range.
    """
    space = _resolve_coordinate_space(model)
    if space is None:
        raise ValueError(f"Unknown coordinate space for model '{model}'")

    if space == "normalized_0_1":
        return (
            min(max(raw_x, 0.0), 1.0),
            min(max(raw_y, 0.0), 1.0),
        )
    elif space == "normalized_0_100":
        return (
            min(max(raw_x / 100.0, 0.0), 1.0),
            min(max(raw_y / 100.0, 0.0), 1.0),
        )
    elif space == "normalized_0_1000":
        return (
            min(max(raw_x / 1000.0, 0.0), 1.0),
            min(max(raw_y / 1000.0, 0.0), 1.0),
        )
    elif space == "pixel":
        return (
            min(max(raw_x / image_width, 0.0), 1.0),
            min(max(raw_y / image_height, 0.0), 1.0),
        )
    else:
        raise ValueError(f"Unknown coordinate space type: {space}")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def point_in_bbox(x: float, y: float, bbox: List[float]) -> bool:
    """Check if a predicted (x, y) point falls inside the ground-truth bounding box.

    All values are normalized 0-1.

    Args:
        x: Predicted x coordinate (0-1).
        y: Predicted y coordinate (0-1).
        bbox: [left, top, right, bottom] (0-1).

    Returns:
        True if (x, y) is inside bbox (inclusive).
    """
    left, top, right, bottom = bbox
    return left <= x <= right and top <= y <= bottom


def compute_accuracy(results: List[BackendResult]) -> float:
    """Fraction of samples where the predicted point is inside the ground-truth bbox.

    Args:
        results: List of BackendResult for a single backend.

    Returns:
        Float between 0.0 and 1.0. Returns 0.0 if results is empty.
    """
    if not results:
        return 0.0
    hits = sum(1 for r in results if r.hit)
    return hits / len(results)


def compute_mean_distance(results: List[BackendResult]) -> float:
    """Mean Euclidean distance in pixels between prediction and bbox center.

    For None predictions (timeout/error), use full diagonal distance as penalty.

    Args:
        results: List of BackendResult for a single backend.

    Returns:
        Mean distance in pixels. Returns 0.0 if results is empty.
    """
    if not results:
        return 0.0
    return sum(r.distance_px for r in results) / len(results)


def compute_distance_px(
    pred_x: Optional[float],
    pred_y: Optional[float],
    bbox: List[float],
    image_width: int,
    image_height: int,
) -> float:
    """Euclidean distance in pixels from prediction to bbox center.

    If prediction is None, returns the full image diagonal (maximum penalty).
    """
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0

    if pred_x is None or pred_y is None:
        return math.sqrt(image_width ** 2 + image_height ** 2)

    dx = (pred_x - cx) * image_width
    dy = (pred_y - cy) * image_height
    return math.sqrt(dx ** 2 + dy ** 2)


# Pricing per 1M tokens (USD), as of 2025-05
PRICING = {
    "claude-sonnet": {"input": 3.00, "output": 15.00},
    "qwen2.5-vl-ollama": {"input": 0.0, "output": 0.0},
    "qwen2.5-vl-llamacpp": {"input": 0.0, "output": 0.0},
    "molmo-mlx": {"input": 0.0, "output": 0.0},  # local, free
}


def estimate_cost(
    backend_name: str,
    n_samples: int,
    prompt_tokens_per_sample: int = 1600,
    output_tokens_per_sample: int = 30,
) -> float:
    """Estimate USD cost for running n_samples through a backend.

    Returns 0.0 for local backends.
    """
    pricing = PRICING.get(backend_name)
    if not pricing:
        return 0.0
    input_cost = (n_samples * prompt_tokens_per_sample / 1_000_000) * pricing["input"]
    output_cost = (n_samples * output_tokens_per_sample / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 4)


# ---------------------------------------------------------------------------
# Backend definitions
# ---------------------------------------------------------------------------

BACKENDS: Dict[str, Dict[str, str]] = {
    "claude-sonnet": {
        "type": "anthropic",
        "model": "claude-sonnet-4-20250514",
    },
    "qwen3-vl-ollama": {
        "type": "openai_compat",
        "url": "http://localhost:11434/v1/chat/completions",
        "model": "qwen3-vl:latest",
    },
    "qwen2.5-vl-llamacpp": {
        "type": "openai_compat",
        "url": "http://localhost:8090/v1/chat/completions",
        "model": "Qwen2.5-VL-7B-Instruct",
    },
    "molmo-mlx": {
        "type": "openai_compat",
        "url": "http://localhost:8091/v1/chat/completions",
        "model": "mlx-community/Molmo-7B-D-0924-3bit",
    },
}

PROMPT_TEMPLATE = """Find the UI element described below and return its location.

Element: {instruction}

If you can find the element, respond with exactly:
FOUND: x=<number>, y=<number>

If you cannot find it, respond with exactly:
NOT_FOUND"""


def _load_prompt(instruction: str) -> str:
    """Build the grounding prompt for a given instruction."""
    return PROMPT_TEMPLATE.format(instruction=instruction)


# ---------------------------------------------------------------------------
# Backend availability checks
# ---------------------------------------------------------------------------

def check_openai_compat_backend(url: str) -> bool:
    """Check if an OpenAI-compatible backend is reachable."""
    health_url = url.rsplit("/v1/", 1)[0] + "/v1/models"
    try:
        req = urllib.request.Request(health_url)
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        base_url = url.rsplit("/v1/", 1)[0]
        try:
            req = urllib.request.Request(base_url)
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            return False


def check_anthropic_backend() -> bool:
    """Check if the Anthropic backend is available (API key + SDK)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def check_backend(name: str, cfg: Dict[str, str]) -> bool:
    """Check if a backend is available."""
    if cfg["type"] == "anthropic":
        return check_anthropic_backend()
    elif cfg["type"] == "openai_compat":
        return check_openai_compat_backend(cfg["url"])
    return False


# ---------------------------------------------------------------------------
# Backend call functions
# ---------------------------------------------------------------------------

def _detect_image_media_type(image_bytes: bytes) -> str:
    """Detect image media type from file header bytes."""
    if image_bytes[:3] == b'\xff\xd8\xff':
        return "image/jpeg"
    elif image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    elif image_bytes[:6] in (b'GIF87a', b'GIF89a'):
        return "image/gif"
    elif image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return "image/webp"
    return "image/png"  # fallback


def call_openai_compat_backend(
    url: str, model: str, image_b64: str, prompt: str,
    media_type: str = "image/png",
) -> Tuple[str, float]:
    """Call an OpenAI-compatible vision backend.

    Args:
        url: Chat completions endpoint URL.
        model: Model name.
        image_b64: Base64-encoded image.
        prompt: Text prompt.

    Returns:
        Tuple of (response_text, latency_seconds).

    Raises:
        Exception on network/HTTP errors.
    """
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 256,
        "stream": False,
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )

    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())
    elapsed = time.perf_counter() - start

    content = result["choices"][0]["message"]["content"]
    return content, elapsed


def call_anthropic_backend(
    model: str, image_b64: str, prompt: str,
    media_type: str = "image/png",
) -> Tuple[str, float]:
    """Call the Anthropic vision backend.

    Args:
        model: Anthropic model name.
        image_b64: Base64-encoded image.
        prompt: Text prompt.

    Returns:
        Tuple of (response_text, latency_seconds).

    Raises:
        Exception on API errors.
    """
    import anthropic

    client = anthropic.Anthropic()

    start = time.perf_counter()
    message = client.messages.create(
        model=model,
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
    )
    elapsed = time.perf_counter() - start

    content = message.content[0].text
    return content, elapsed


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

PARQUET_URLS = [
    "https://huggingface.co/datasets/rootsautomation/ScreenSpot/resolve/refs%2Fconvert%2Fparquet/default/test/0000.parquet",
    "https://huggingface.co/datasets/rootsautomation/ScreenSpot/resolve/refs%2Fconvert%2Fparquet/default/test/0001.parquet",
    "https://huggingface.co/datasets/rootsautomation/ScreenSpot/resolve/refs%2Fconvert%2Fparquet/default/test/0002.parquet",
]

CACHE_DIR = Path.home() / ".cache" / "automation_agent" / "screenspot"
FALLBACK_JSON = Path(__file__).parent / "screenspot_curated.json"


def _create_tiny_black_png() -> bytes:
    """Create a minimal 1x1 black PNG image as bytes."""
    try:
        from PIL import Image as PILImage
        buf = io.BytesIO()
        img = PILImage.new("RGB", (1, 1), (0, 0, 0))
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        # Hardcoded minimal 1x1 black PNG
        import struct
        import zlib

        def _chunk(chunk_type: bytes, data: bytes) -> bytes:
            c = chunk_type + data
            crc = zlib.crc32(c) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

        sig = b"\x89PNG\r\n\x1a\n"
        ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        ihdr = _chunk(b"IHDR", ihdr_data)
        raw = b"\x00\x00\x00\x00"  # filter byte + RGB
        idat_data = zlib.compress(raw)
        idat = _chunk(b"IDAT", idat_data)
        iend = _chunk(b"IEND", b"")
        return sig + ihdr + idat + iend


def _download_parquet_files(no_cache: bool = False) -> List[Path]:
    """Download parquet files to cache directory.

    Args:
        no_cache: If True, delete cached files and re-download.

    Returns:
        List of local file paths.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if no_cache:
        for f in CACHE_DIR.glob("*.parquet"):
            f.unlink()

    paths = []
    for url in PARQUET_URLS:
        filename = url.rsplit("/", 1)[-1]
        local_path = CACHE_DIR / filename
        if not local_path.exists():
            print(f"  Downloading {filename}...", file=sys.stderr)
            try:
                urllib.request.urlretrieve(url, str(local_path))
            except Exception as e:
                print(f"ERROR: Failed to download {url}: {e}", file=sys.stderr)
                # Clean up partial download
                if local_path.exists():
                    local_path.unlink()
                sys.exit(1)
        paths.append(local_path)
    return paths


def _load_from_parquet(no_cache: bool = False) -> List[BenchmarkSample]:
    """Load samples from Parquet files using pyarrow + PIL."""
    import pyarrow.parquet as pq
    from PIL import Image as PILImage

    parquet_paths = _download_parquet_files(no_cache)

    samples = []
    for path in parquet_paths:
        table = pq.read_table(str(path))
        for i in range(len(table)):
            row_file_name = table.column("file_name")[i].as_py()
            row_instruction = table.column("instruction")[i].as_py()
            row_bbox = table.column("bbox")[i].as_py()
            row_data_type = table.column("data_type")[i].as_py()
            row_data_source = table.column("data_source")[i].as_py()
            row_image = table.column("image")[i].as_py()

            image_bytes = row_image["bytes"]
            img = PILImage.open(io.BytesIO(image_bytes))
            width, height = img.size

            samples.append(BenchmarkSample(
                file_name=row_file_name,
                instruction=row_instruction,
                bbox=list(row_bbox),
                data_type=row_data_type,
                data_source=row_data_source,
                image_width=width,
                image_height=height,
                image_bytes=image_bytes,
            ))

    return samples


def _load_from_fallback_json() -> List[BenchmarkSample]:
    """Load samples from the curated fallback JSON file."""
    if not FALLBACK_JSON.exists():
        print(
            f"ERROR: Fallback JSON not found at {FALLBACK_JSON}",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(FALLBACK_JSON) as f:
        entries = json.load(f)

    samples = []
    for entry in entries:
        image_url = entry.get("image_url", "")
        image_width = entry.get("image_width", 960)
        image_height = entry.get("image_height", 540)

        if image_url:
            # Download image from URL
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path = CACHE_DIR / entry["file_name"]
            if not cache_path.exists():
                try:
                    urllib.request.urlretrieve(image_url, str(cache_path))
                except Exception as e:
                    print(
                        f"WARNING: Failed to download {image_url}: {e}",
                        file=sys.stderr,
                    )
                    image_bytes = _create_tiny_black_png()
                    samples.append(BenchmarkSample(
                        file_name=entry["file_name"],
                        instruction=entry["instruction"],
                        bbox=entry["bbox"],
                        data_type=entry["data_type"],
                        data_source=entry["data_source"],
                        image_width=image_width,
                        image_height=image_height,
                        image_bytes=image_bytes,
                    ))
                    continue
            image_bytes = cache_path.read_bytes()
            # Try to get actual dimensions from the image
            try:
                from PIL import Image as PILImage
                img = PILImage.open(io.BytesIO(image_bytes))
                image_width, image_height = img.size
            except ImportError:
                pass
        else:
            # No URL - create a tiny placeholder image
            image_bytes = _create_tiny_black_png()

        samples.append(BenchmarkSample(
            file_name=entry["file_name"],
            instruction=entry["instruction"],
            bbox=entry["bbox"],
            data_type=entry["data_type"],
            data_source=entry["data_source"],
            image_width=image_width,
            image_height=image_height,
            image_bytes=image_bytes,
        ))

    return samples


def load_dataset(no_cache: bool = False) -> List[BenchmarkSample]:
    """Load the ScreenSpot dataset, with pyarrow fallback to curated JSON.

    Args:
        no_cache: If True, delete cached files and re-download.

    Returns:
        List of BenchmarkSample objects.
    """
    try:
        return _load_from_parquet(no_cache)
    except ImportError:
        print(
            "WARNING: pyarrow not installed, falling back to curated JSON "
            f"at {FALLBACK_JSON}",
            file=sys.stderr,
        )
        return _load_from_fallback_json()


# ---------------------------------------------------------------------------
# Run one sample through one backend
# ---------------------------------------------------------------------------

def run_sample(
    sample: BenchmarkSample,
    backend_name: str,
    cfg: Dict[str, str],
) -> BackendResult:
    """Run a single sample through a backend and return the result."""
    prompt = _load_prompt(sample.instruction)
    image_b64 = base64.b64encode(sample.image_bytes).decode()
    media_type = _detect_image_media_type(sample.image_bytes)

    raw_response = ""
    error = None
    predicted_x = None
    predicted_y = None

    try:
        if cfg["type"] == "openai_compat":
            raw_response, latency = call_openai_compat_backend(
                cfg["url"], cfg["model"], image_b64, prompt,
                media_type=media_type,
            )
        elif cfg["type"] == "anthropic":
            raw_response, latency = call_anthropic_backend(
                cfg["model"], image_b64, prompt,
                media_type=media_type,
            )
        else:
            raise ValueError(f"Unknown backend type: {cfg['type']}")

        # Parse coordinates from response
        coords = _parse_coordinates(raw_response)
        if coords is not None:
            raw_x, raw_y = coords
            try:
                predicted_x, predicted_y = normalize_prediction(
                    raw_x, raw_y, cfg["model"],
                    sample.image_width, sample.image_height,
                )
            except ValueError as e:
                error = f"normalize_failed: {e}"
        else:
            error = "parse_failed"

    except urllib.error.URLError as e:
        latency = 0.0
        error = f"network_error: {e}"
        print(
            f"  WARNING: Network error for {backend_name}: {e}",
            file=sys.stderr,
        )
    except Exception as e:
        latency = 0.0
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            error = "timeout"
        else:
            error = str(e)
        print(
            f"  WARNING: Error for {backend_name}: {e}",
            file=sys.stderr,
        )

    # Compute hit and distance
    hit = False
    if predicted_x is not None and predicted_y is not None:
        hit = point_in_bbox(predicted_x, predicted_y, sample.bbox)

    distance_px = compute_distance_px(
        predicted_x, predicted_y, sample.bbox,
        sample.image_width, sample.image_height,
    )

    return BackendResult(
        sample_file_name=sample.file_name,
        instruction=sample.instruction,
        backend_name=backend_name,
        predicted_x=predicted_x,
        predicted_y=predicted_y,
        ground_truth_bbox=sample.bbox,
        hit=hit,
        distance_px=distance_px,
        latency_s=latency,
        raw_response=raw_response,
        error=error,
    )


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def print_results_table(all_results: Dict[str, List[BackendResult]]) -> None:
    """Print a formatted results table to stdout."""
    print("=" * 80)
    print("GROUNDING BENCHMARK RESULTS")
    print("=" * 80)
    print(
        f"{'Backend':<21s} | {'Accuracy':>8s} | {'Avg Dist (px)':>13s} | "
        f"{'Avg Latency':>11s} | {'Cost (est.)':>11s}"
    )
    print("-" * 21 + "+" + "-" * 10 + "+" + "-" * 15 + "+" + "-" * 13 + "+" + "-" * 12)

    for backend_name, results in all_results.items():
        accuracy = compute_accuracy(results)
        mean_dist = compute_mean_distance(results)
        avg_latency = sum(r.latency_s for r in results) / len(results) if results else 0.0
        cost = estimate_cost(backend_name, len(results))

        print(
            f"{backend_name:<21s} | {accuracy * 100:>6.1f}%  | {mean_dist:>11.1f}  "
            f"  | {avg_latency:>8.2f}s   | ${cost:>8.4f}"
        )

    print("=" * 80)


def determine_winner(all_results: Dict[str, List[BackendResult]]) -> Optional[str]:
    """Determine the winning backend by highest accuracy, ties broken by lowest latency."""
    if not all_results:
        return None

    best_name = None
    best_accuracy = -1.0
    best_latency = float("inf")

    for name, results in all_results.items():
        accuracy = compute_accuracy(results)
        avg_latency = sum(r.latency_s for r in results) / len(results) if results else 0.0

        if (accuracy > best_accuracy) or (
            accuracy == best_accuracy and avg_latency < best_latency
        ):
            best_name = name
            best_accuracy = accuracy
            best_latency = avg_latency

    return best_name


def save_json_results(
    all_results: Dict[str, List[BackendResult]],
    backends_requested: List[str],
    backends_skipped: List[str],
    n_samples: int,
    category: Optional[str],
    winner: Optional[str],
) -> Path:
    """Save results to JSON in the logs/ directory."""
    logs_dir = Path(__file__).parent.parent / "logs"
    logs_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = logs_dir / f"benchmark_grounding_{timestamp}.json"

    results_dict = {}
    for backend_name, results in all_results.items():
        accuracy = compute_accuracy(results)
        mean_dist = compute_mean_distance(results)
        avg_latency = sum(r.latency_s for r in results) / len(results) if results else 0.0
        cost = estimate_cost(backend_name, len(results))
        n_hits = sum(1 for r in results if r.hit)

        samples_list = []
        for r in results:
            samples_list.append({
                "file_name": r.sample_file_name,
                "instruction": r.instruction,
                "predicted_x": r.predicted_x,
                "predicted_y": r.predicted_y,
                "ground_truth_bbox": r.ground_truth_bbox,
                "hit": r.hit,
                "distance_px": round(r.distance_px, 2),
                "latency_s": round(r.latency_s, 3),
                "raw_response": r.raw_response,
                "error": r.error,
            })

        results_dict[backend_name] = {
            "accuracy": round(accuracy, 4),
            "mean_distance_px": round(mean_dist, 2),
            "avg_latency_s": round(avg_latency, 3),
            "estimated_cost_usd": cost,
            "n_samples": len(results),
            "n_hits": n_hits,
            "n_misses": len(results) - n_hits,
            "samples": samples_list,
        }

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "n_samples": n_samples,
        "category": category,
        "backends_requested": backends_requested,
        "backends_skipped": backends_skipped,
        "results": results_dict,
        "winner": winner,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark visual grounding accuracy across vision backends "
                    "using the ScreenSpot dataset."
    )
    parser.add_argument(
        "--backends",
        nargs="+",
        default=None,
        choices=list(BACKENDS.keys()),
        help="Backend names to run (default: all reachable)",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=50,
        help="Number of samples to evaluate per backend (default: 50)",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Filter by data_source value (case-sensitive). "
             "Options: macOS, windows, iOS, Android, Tool, Shop, GitLab, Forum",
    )
    parser.add_argument(
        "--save-screenshots",
        action="store_true",
        help="Save annotated images with predicted + ground-truth overlay to logs/",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Delete cached Parquet files and re-download",
    )
    args = parser.parse_args()

    # 1. Load dataset
    print("Loading ScreenSpot dataset...", file=sys.stderr)
    all_samples = load_dataset(no_cache=args.no_cache)
    print(f"  Loaded {len(all_samples)} total samples", file=sys.stderr)

    # 2. Filter by category
    if args.category:
        valid_categories = sorted(set(s.data_source for s in all_samples))
        if args.category not in valid_categories:
            print(
                f"ERROR: Category '{args.category}' not found in dataset. "
                f"Available categories: {valid_categories}",
                file=sys.stderr,
            )
            sys.exit(1)
        all_samples = [s for s in all_samples if s.data_source == args.category]
        print(
            f"  Filtered to {len(all_samples)} samples for category '{args.category}'",
            file=sys.stderr,
        )

    # 3. Sample --n items
    n = args.n
    if n > len(all_samples):
        print(
            f"WARNING: --n={n} exceeds available samples ({len(all_samples)}). "
            f"Clamping to {len(all_samples)}.",
            file=sys.stderr,
        )
        n = len(all_samples)

    random.seed(42)
    samples = random.sample(all_samples, n)
    print(f"  Selected {n} samples for benchmarking", file=sys.stderr)

    # 4. Determine backends to run
    backends_requested = args.backends if args.backends else list(BACKENDS.keys())
    backends_available = []
    backends_skipped = []

    for name in backends_requested:
        cfg = BACKENDS[name]
        if check_backend(name, cfg):
            backends_available.append(name)
        else:
            backends_skipped.append(name)
            print(
                f"WARNING: Backend '{name}' is not available, skipping.",
                file=sys.stderr,
            )

    if not backends_available:
        print("ERROR: No backends available. Exiting.", file=sys.stderr)
        sys.exit(1)

    # 5. Run benchmarks
    all_results: Dict[str, List[BackendResult]] = {}

    for backend_name in backends_available:
        cfg = BACKENDS[backend_name]
        cost_est = estimate_cost(backend_name, n)
        print(f"\n--- {backend_name} (est. cost: ${cost_est:.4f}) ---")

        results = []
        for i, sample in enumerate(samples):
            result = run_sample(sample, backend_name, cfg)
            results.append(result)

            status = "HIT " if result.hit else "MISS"
            instr_short = sample.instruction[:40]
            pred_str = (
                f"({result.predicted_x:.3f}, {result.predicted_y:.3f})"
                if result.predicted_x is not None
                else "(None, None)"
            )
            print(
                f"  [{status}] {instr_short:<40s} -> {pred_str} "
                f"({result.latency_s:.2f}s)"
            )

        all_results[backend_name] = results

    # 6. Print results table
    print()
    print_results_table(all_results)

    # 7. Determine winner
    winner = determine_winner(all_results)

    if winner:
        results = all_results[winner]
        accuracy = compute_accuracy(results)
        avg_latency = sum(r.latency_s for r in results) / len(results) if results else 0.0
        cost = estimate_cost(winner, len(results))
        print(
            f"\nWinner: {winner} ({accuracy * 100:.1f}% accuracy, "
            f"{avg_latency:.2f}s avg latency, ${cost:.2f} est. cost)"
        )

    # 8. Save JSON
    output_path = save_json_results(
        all_results=all_results,
        backends_requested=backends_requested,
        backends_skipped=backends_skipped,
        n_samples=n,
        category=args.category,
        winner=winner,
    )
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
