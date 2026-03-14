# Grounding Benchmark Specification

Script: `scripts/benchmark_grounding.py`

Measures accuracy and speed of visual grounding (element finding) across different vision model backends using the ScreenSpot dataset.

---

## A. Data Shapes

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional

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
```

---

## B. Dataset Loading

### Source

HuggingFace dataset: `rootsautomation/ScreenSpot` (1,272 samples total, test split only).

Parquet files (datasets-server API):

```
https://huggingface.co/datasets/rootsautomation/ScreenSpot/resolve/refs%2Fconvert%2Fparquet/default/test/0000.parquet
https://huggingface.co/datasets/rootsautomation/ScreenSpot/resolve/refs%2Fconvert%2Fparquet/default/test/0001.parquet
https://huggingface.co/datasets/rootsautomation/ScreenSpot/resolve/refs%2Fconvert%2Fparquet/default/test/0002.parquet
```

### Cache directory

`~/.cache/automation_agent/screenspot/`

Download each Parquet file into the cache directory on first run. Skip download if the file already exists. The `--no-cache` flag deletes cached files and re-downloads.

### Column schema (from the Parquet files)

| Column        | Type              | Description                                      |
|---------------|-------------------|--------------------------------------------------|
| `file_name`   | string            | Image filename, e.g. `pc_ede36f9b-....png`       |
| `bbox`        | list[float64] x4  | `[left, top, right, bottom]` normalized 0.0-1.0  |
| `instruction` | string            | Natural language instruction, e.g. `"close"`     |
| `data_type`   | string            | `"icon"` or `"text"`                             |
| `data_source` | string            | One of 8 values (see below)                      |
| `image`       | dict              | `{"bytes": <bytes>, "path": <str or null>}`      |

**data_source values** (with counts):

| data_source | count |
|-------------|-------|
| iOS         | 255   |
| Android     | 247   |
| macOS       | 172   |
| windows     | 162   |
| Tool        | 139   |
| Shop        | 119   |
| GitLab      | 89    |
| Forum       | 89    |

**data_type values**: `"icon"` (575), `"text"` (697).

### Image loading from Parquet

When loaded via pyarrow, the `image` column contains a struct with `bytes` (raw image bytes) and `path` (nullable string). Extract the `bytes` field. The image dimensions (`image_width`, `image_height`) must be read from the image bytes using PIL:

```python
from PIL import Image
import io

img = Image.open(io.BytesIO(row["image"]["bytes"]))
width, height = img.size
```

When loaded via the datasets-server API (for the first-rows preview), the `image` column instead contains `{"src": "<url>", "width": <int>, "height": <int>}`. The script uses Parquet loading, NOT the API preview.

### Filtering

- Default: load all samples, randomly sample `--n` (default 50) with a fixed seed (`random.seed(42)`)
- `--category` flag: filter by `data_source` value before sampling. Accepted values: `macOS`, `windows`, `iOS`, `Android`, `Tool`, `Shop`, `GitLab`, `Forum` (case-sensitive match against the dataset values)

### Fallback: curated local JSON

If `pyarrow` is not installed (ImportError), fall back to a curated 30-sample local JSON file at `scripts/screenspot_curated.json`. This file is NOT generated by the benchmark script; it is a static file checked into the repo. Its schema:

```json
[
  {
    "file_name": "pc_ede36f9b-....png",
    "instruction": "close",
    "bbox": [0.9479, 0.1444, 0.9938, 0.2074],
    "data_type": "icon",
    "data_source": "macOS",
    "image_url": "https://...",
    "image_width": 960,
    "image_height": 540
  }
]
```

When using the fallback, download images from `image_url` into the cache directory.

---

## C. Coordinate Pipeline

The benchmark sends each sample's image + instruction to a vision backend and parses the response. The model is expected to respond in `FOUND: x=N, y=N` or `NOT_FOUND` format (same as the existing coordinator).

### Step 1: Parse coordinates from model response

Use the `_parse_coordinates` function (copied from `coordinator.py:355-382`):

```python
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
        r"FOUND:\s*x\s*=\s*([0-9]*\.?[0-9]+)\s*,\s*y\s*=\s*([0-9]*\.?[0-9]+)",
        response,
        re.IGNORECASE,
    )
    if match:
        return float(match.group(1)), float(match.group(2))

    return None
```

### Step 2: Normalize to 0-1

After parsing raw `(x, y)` from the model, normalize to 0-1 range based on the model's coordinate space. Use the `COORDINATE_SPACES` dict and `_convert_coordinates` logic copied from `coordinator.py`.

```python
# Copied from src/automation_agent/vision/coordinator.py
COORDINATE_SPACES: Dict[str, str] = {
    "molmo": "normalized_0_1",
    "qwen3-vl": "normalized_0_1000",
    "qwen2.5-vl": "normalized_0_1000",
    "qwen2-vl": "normalized_0_1000",
    "claude-sonnet-4-20250514": "pixel",
}
```

Normalization logic (inverse of `_convert_coordinates` -- this converts TO 0-1 instead of FROM 0-1):

```python
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
        # Already 0-1
        return (
            min(max(raw_x, 0.0), 1.0),
            min(max(raw_y, 0.0), 1.0),
        )
    elif space == "normalized_0_1000":
        # Divide by 1000
        return (
            min(max(raw_x / 1000.0, 0.0), 1.0),
            min(max(raw_y / 1000.0, 0.0), 1.0),
        )
    elif space == "pixel":
        # Divide by image dimensions
        return (
            min(max(raw_x / image_width, 0.0), 1.0),
            min(max(raw_y / image_height, 0.0), 1.0),
        )
    else:
        raise ValueError(f"Unknown coordinate space type: {space}")
```

Also copy `_resolve_coordinate_space` from `coordinator.py:98-115`:

```python
def _resolve_coordinate_space(model: str) -> Optional[str]:
    """Resolve coordinate space for a model via case-insensitive prefix matching."""
    model_lower = model.lower()
    if model_lower in COORDINATE_SPACES:
        return COORDINATE_SPACES[model_lower]
    for key, space in COORDINATE_SPACES.items():
        if model_lower.startswith(key):
            return space
    return None
```

### Step 3: Evaluate

Compare the normalized predicted `(x, y)` against the ground-truth `bbox = [left, top, right, bottom]` (also 0-1 normalized). See Metrics section below.

---

## D. Backend Definitions

```python
BACKENDS: Dict[str, Dict[str, str]] = {
    "claude-sonnet": {
        "type": "anthropic",
        "model": "claude-sonnet-4-20250514",
    },
    "qwen2.5-vl-ollama": {
        "type": "openai_compat",
        "url": "http://localhost:11434/v1/chat/completions",
        "model": "qwen2.5-vl:7b",
    },
    "qwen2.5-vl-llamacpp": {
        "type": "openai_compat",
        "url": "http://localhost:8090/v1/chat/completions",
        "model": "Qwen2.5-VL-7B-Instruct",
    },
}
```

### Backend dispatch

- **`type: "anthropic"`**: Use the `anthropic` Python SDK. Requires `ANTHROPIC_API_KEY` env var. Sends image as base64 with `type: "image"` and `source.type: "base64"`. See `coordinator.py:245-304` for the exact message format.
- **`type: "openai_compat"`**: Use `urllib.request` (stdlib, no extra deps). Sends image as base64 data URL in OpenAI chat completions format. See `benchmark_vision.py:52-101` for the exact message format.

### Prompt template

Use this exact prompt for all backends:

```
Find the UI element described below and return its location.

Element: {instruction}

If you can find the element, respond with exactly:
FOUND: x=<number>, y=<number>

If you cannot find it, respond with exactly:
NOT_FOUND
```

Where `{instruction}` is the `instruction` field from the `BenchmarkSample`.

### Backend availability check

Before running samples through a backend:
- **anthropic**: Check that `ANTHROPIC_API_KEY` env var is set and the `anthropic` package is importable. If either fails, skip with a warning.
- **openai_compat**: HTTP GET to the base URL (strip `/v1/chat/completions`, try `/v1/models`) with a 5-second timeout. If unreachable, skip with a warning.

Use the same `check_backend` pattern from `benchmark_vision.py:104-121`.

---

## E. Metrics

### `point_in_bbox(x, y, bbox) -> bool`

```python
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
```

### `compute_accuracy(results) -> float`

```python
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
```

### `compute_mean_distance(results) -> float`

```python
import math

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
```

The `distance_px` field on `BackendResult` is computed at evaluation time as follows:

```python
def compute_distance_px(
    pred_x: Optional[float],   # normalized 0-1, or None
    pred_y: Optional[float],   # normalized 0-1, or None
    bbox: List[float],         # [left, top, right, bottom] normalized 0-1
    image_width: int,
    image_height: int,
) -> float:
    """Euclidean distance in pixels from prediction to bbox center.

    If prediction is None, returns the full image diagonal (maximum penalty).
    """
    # Bbox center in normalized coords
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0

    if pred_x is None or pred_y is None:
        # Maximum penalty: full diagonal
        return math.sqrt(image_width ** 2 + image_height ** 2)

    # Convert to pixel space for distance
    dx = (pred_x - cx) * image_width
    dy = (pred_y - cy) * image_height
    return math.sqrt(dx ** 2 + dy ** 2)
```

### `estimate_cost(backend_name, n_samples, prompt_tokens_per_sample) -> float`

```python
# Pricing per 1M tokens (USD), as of 2025-05
PRICING = {
    "claude-sonnet": {"input": 3.00, "output": 15.00},
    "qwen2.5-vl-ollama": {"input": 0.0, "output": 0.0},      # local, free
    "qwen2.5-vl-llamacpp": {"input": 0.0, "output": 0.0},     # local, free
}

def estimate_cost(
    backend_name: str,
    n_samples: int,
    prompt_tokens_per_sample: int = 1600,  # ~1 image + short text
    output_tokens_per_sample: int = 30,    # "FOUND: x=N, y=N"
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
```

---

## F. CLI (argparse)

```
usage: benchmark_grounding.py [-h] [--backends BACKENDS [BACKENDS ...]]
                               [--n N] [--category CATEGORY]
                               [--save-screenshots] [--no-cache]
```

| Flag                | Type       | Default           | Description                                           |
|---------------------|------------|-------------------|-------------------------------------------------------|
| `--backends`        | list[str]  | all reachable     | Backend names from BACKENDS dict to run               |
| `--n`               | int        | 50                | Number of samples to evaluate per backend             |
| `--category`        | str        | None (all)        | Filter by `data_source` value (case-sensitive)        |
| `--save-screenshots`| flag       | False             | Save annotated images (with predicted + GT overlay) to `logs/` |
| `--no-cache`        | flag       | False             | Delete cached Parquet files and re-download           |

### Behavior

1. Parse CLI args
2. Load dataset (Parquet or fallback JSON)
3. Filter by `--category` if specified
4. Random sample `--n` items (seed=42)
5. For each backend in `--backends`:
   a. Check availability (skip unavailable with warning)
   b. Print estimated cost via `estimate_cost()`
   c. For each sample:
      - Send image + instruction prompt to backend
      - Parse response, normalize coordinates
      - Compute hit + distance
      - Store `BackendResult`
   d. Print per-backend progress: `[HIT/MISS] instruction... -> (x, y) (latency)`
6. Print results table
7. Save JSON to `logs/`
8. Print winner line

---

## G. Output

### Console table

Print a formatted table (similar to `benchmark_skill_router.py:211-222`):

```
================================================================
GROUNDING BENCHMARK RESULTS
================================================================
Backend              | Accuracy | Avg Dist (px) | Avg Latency | Cost (est.)
---------------------+----------+---------------+-------------+------------
claude-sonnet        |   72.0%  |     45.3      |    1.23s    |   $0.2520
qwen2.5-vl-ollama    |   58.0%  |     78.1      |    3.45s    |   $0.0000
qwen2.5-vl-llamacpp  |   60.0%  |     72.4      |    2.10s    |   $0.0000
================================================================
```

### Winner line

```
Winner: claude-sonnet (72.0% accuracy, 1.23s avg latency, $0.25 est. cost)
```

The winner is determined by highest accuracy. Ties broken by lowest avg latency.

### JSON output

Save to `logs/benchmark_grounding_{timestamp}.json` where `timestamp` is `YYYYMMDD_HHMMSS` format.

```json
{
  "timestamp": "2025-06-15T10:30:00Z",
  "n_samples": 50,
  "category": null,
  "backends_requested": ["claude-sonnet", "qwen2.5-vl-ollama", "qwen2.5-vl-llamacpp"],
  "backends_skipped": ["qwen2.5-vl-ollama"],
  "results": {
    "claude-sonnet": {
      "accuracy": 0.72,
      "mean_distance_px": 45.3,
      "avg_latency_s": 1.23,
      "estimated_cost_usd": 0.252,
      "n_samples": 50,
      "n_hits": 36,
      "n_misses": 14,
      "samples": [
        {
          "file_name": "pc_ede36f9b-....png",
          "instruction": "close",
          "predicted_x": 0.97,
          "predicted_y": 0.17,
          "ground_truth_bbox": [0.9479, 0.1444, 0.9938, 0.2074],
          "hit": true,
          "distance_px": 5.2,
          "latency_s": 1.10,
          "raw_response": "FOUND: x=930, y=95",
          "error": null
        }
      ]
    }
  },
  "winner": "claude-sonnet"
}
```

### Logs directory

Create `logs/` directory if it does not exist (same as other benchmark scripts write to project root `logs/`).

---

## H. Standalone Constraint

The script MUST NOT `import` from the `automation_agent` package. It must be fully self-contained.

Copy the following functions/constants verbatim from coordinator.py, with a comment at the top of each copied block:

```python
# Copied from src/automation_agent/vision/coordinator.py
COORDINATE_SPACES: Dict[str, str] = { ... }

# Copied from src/automation_agent/vision/coordinator.py
def _resolve_coordinate_space(model: str) -> Optional[str]:
    ...

# Copied from src/automation_agent/vision/coordinator.py
def _parse_coordinates(response: str) -> Optional[Tuple[float, float]]:
    ...
```

The `normalize_prediction` function is new (not copied) -- it is the inverse of `_convert_coordinates`. The `_convert_coordinates` function itself is NOT needed because the benchmark works in normalized 0-1 space, not pixel space. Instead, the benchmark normalizes model outputs TO 0-1 via `normalize_prediction`.

### Dependencies (stdlib + pip)

Required:
- `pyarrow` (for Parquet loading) -- optional, with fallback
- `Pillow` (for reading image dimensions from bytes)
- `anthropic` (for claude-sonnet backend) -- optional, skip backend if missing

Stdlib only:
- `urllib.request` (for openai_compat API calls)
- `argparse`, `json`, `time`, `math`, `re`, `os`, `random`, `base64`, `io`, `pathlib`, `dataclasses`, `datetime`

Do NOT use `httpx`, `requests`, or any other third-party HTTP library.

---

## I. Error Handling

| Scenario                        | Behavior                                                  |
|---------------------------------|-----------------------------------------------------------|
| Backend timeout (120s)          | Return `BackendResult` with `predicted_x=None`, `predicted_y=None`, `error="timeout"`. Counts as a miss. Distance = full diagonal. |
| Network/HTTP error              | Log warning to stderr, return `BackendResult` with `predicted_x=None`, `predicted_y=None`, `error=<message>`. Counts as a miss. |
| Model returns unparseable text  | `predicted_x=None`, `predicted_y=None`, `error="parse_failed"`. Counts as a miss. |
| `pyarrow` ImportError           | Print warning, fall back to curated JSON at `scripts/screenspot_curated.json`. |
| `anthropic` ImportError         | Skip the `claude-sonnet` backend with a warning message.  |
| Missing `ANTHROPIC_API_KEY`     | Skip the `claude-sonnet` backend with a warning message.  |
| `--category` value not in data  | Print error and exit with code 1.                         |
| `--n` exceeds available samples | Clamp to available count, print warning.                  |
| Parquet download fails          | Print error to stderr and exit with code 1 (unless fallback JSON exists). |

---

## J. Test Requirements

The adversary should write tests at `tests/unit/test_benchmark_grounding.py`.

### What MUST be testable without network

1. **`point_in_bbox`**: Test with point inside, outside, on boundary, and edge cases (point on left/right/top/bottom edge).
2. **`compute_accuracy`**: Test with all hits, all misses, mixed, and empty list.
3. **`compute_mean_distance`**: Test with known distances, None predictions (should use diagonal), and empty list.
4. **`estimate_cost`**: Test with local (free) and anthropic backends, various sample counts.
5. **`normalize_prediction`**: Test each coordinate space type:
   - `normalized_0_1` (molmo): values pass through clamped to [0, 1]
   - `normalized_0_1000` (qwen2.5-vl): 500 -> 0.5, 1000 -> 1.0, 0 -> 0.0
   - `pixel` (claude-sonnet): 350 with image_width=960 -> 350/960
   - Unknown model -> `ValueError`
6. **`_parse_coordinates`**: Test `FOUND: x=100, y=200`, `NOT_FOUND`, garbage input, whitespace variants.
7. **`_resolve_coordinate_space`**: Exact match, prefix match (`qwen2.5-vl-7b` matches `qwen2.5-vl`), unknown model returns None.

### What MUST be mockable

8. **HTTP calls**: All `urllib.request.urlopen` calls must be mockable via `unittest.mock.patch("urllib.request.urlopen", ...)`.
9. **Anthropic SDK calls**: Mock `anthropic.Anthropic` (or `anthropic.Client`) to return a fake response.
10. **Parquet loading**: Mock `pyarrow.parquet.read_table` to return a fake table, or test the ImportError fallback by patching `builtins.__import__`.

### JSON output schema stability

The JSON field names documented in section G are the contract. Tests should verify:
- Top-level keys: `timestamp`, `n_samples`, `category`, `backends_requested`, `backends_skipped`, `results`, `winner`
- Per-backend keys: `accuracy`, `mean_distance_px`, `avg_latency_s`, `estimated_cost_usd`, `n_samples`, `n_hits`, `n_misses`, `samples`
- Per-sample keys: `file_name`, `instruction`, `predicted_x`, `predicted_y`, `ground_truth_bbox`, `hit`, `distance_px`, `latency_s`, `raw_response`, `error`
