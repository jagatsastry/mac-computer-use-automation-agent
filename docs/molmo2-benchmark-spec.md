# Molmo2-8B Benchmark Integration Spec

## Summary

Add **Molmo2-8B** (`allenai/Molmo2-8B`) to the grounding benchmark alongside the existing
Molmo-7B-D, Qwen3-VL, and Claude Sonnet backends. Molmo2 is based on Qwen3-8B + SigLIP 2
vision backbone and uses a **different coordinate format** from original Molmo.

---

## A. Recommended Serving Approach

### Option 1 (Recommended): MLX quantized via mlx-vlm -- port 8092

| Item | Value |
|------|-------|
| Model ID | `mlx-community/Molmo2-8B-4bit` |
| Disk size | ~6.4 GB |
| RAM at runtime | ~7-8 GB (4-bit quantized) |
| Required mlx-vlm | >= 0.3.10 (Molmo2 support added in v0.3.10) |
| Required Python | >= 3.10 (mlx-vlm 0.3.x requirement) |
| Server script | `scripts/mlx_vlm_server.py --model mlx-community/Molmo2-8B-4bit --port 8092` |

**Problem: Current venv is Python 3.9.6.** mlx-vlm 0.3.10+ requires Python >= 3.10.

**Solution:** Create a separate venv for the Molmo2 server using uv:
```bash
uv venv --python 3.11 .venv-molmo2
source .venv-molmo2/bin/activate
pip install mlx-vlm>=0.3.10 starlette uvicorn pillow
```

Then serve:
```bash
.venv-molmo2/bin/python scripts/mlx_vlm_server.py \
    --model mlx-community/Molmo2-8B-4bit --port 8092
```

Alternative quantizations (if 4-bit quality is insufficient):

| Model ID | Disk | RAM (est.) |
|----------|------|------------|
| `mlx-community/Molmo2-8B-4bit` | 6.4 GB | ~7-8 GB |
| `mlx-community/Molmo2-8B-5bit` | ~7.5 GB | ~8-9 GB |
| `mlx-community/Molmo2-8B-8bit` | 10.2 GB | ~12 GB |
| `mlx-community/Molmo2-8B-fp16` | ~16 GB | ~18 GB |

### Option 2 (Fallback): Custom transformers server

Only if MLX path fails. Requires `transformers==4.57.1`, `torch`, `molmo_utils`, etc.
Much heavier (~16 GB RAM for fp16). Not recommended for M2 Pro 32 GB alongside other models.

---

## B. Molmo2 Coordinate Format (Single Image)

### Critical Difference from Original Molmo

| | Original Molmo (v1) | Molmo2 |
|---|---|---|
| Output format | `<point x="29.78" y="83.80">` | `<points coords="..."/>` |
| Coordinate scale | 0-100 (percentage) | 0-1000 (scaled integer) |
| Coordinate key | `"molmo"` | `"molmo2"` (new) |
| Space type | `normalized_0_100` | `normalized_0_1000` |

### Molmo2 Native Output Format

For a pointing query like "Point to the close button", Molmo2 outputs:
```
<points coords="1 483 127"/>
```

The coords field contains space-separated triplets: `ID X Y` where:
- `ID` = integer object identifier (starts at 1)
- `X` = x-coordinate scaled 0-1000
- `Y` = y-coordinate scaled 0-1000

To convert to pixel coordinates:
```python
pixel_x = float(x) / 1000 * image_width
pixel_y = float(y) / 1000 * image_height
```

For multi-image/video, the format includes frame IDs, but for single-image grounding
the structure is the same.

### Parsing Regexes (from allenai/Molmo2-8B model card)

```python
COORD_REGEX = re.compile(r"<(?:points|tracks).*? coords=\"([0-9\t:;, .]+)\"/?>")
POINTS_REGEX = re.compile(r"([0-9]+) ([0-9]{3,4}) ([0-9]{3,4})")
```

### Will Molmo2 respond to `FOUND: x=N, y=N` prompting?

**Unknown / unlikely.** The model was trained to output `<points coords="..."/>` natively.
It may or may not comply with our `FOUND: x=<number>, y=<number>` prompt template.

The benchmark should try our standard `FOUND:` prompt first. If parse rate is very low,
add a Molmo2-specific parser as fallback (see below).

---

## C. Changes to `_parse_coordinates()` in `benchmark_grounding.py`

Add a fallback parser for Molmo2's native `<points coords="..."/>` format:

```python
# After the existing Molmo <point x="..." y="..."> parser:

# Try Molmo2's native <points coords="ID X Y"/> format
points_match = re.search(
    r'<points[^>]*coords="([^"]+)"',
    response,
    re.IGNORECASE,
)
if points_match:
    coords_str = points_match.group(1)
    # Extract first point triplet: ID X Y
    triplet = re.search(r'([0-9]+)\s+([0-9]{3,4})\s+([0-9]{3,4})', coords_str)
    if triplet:
        return float(triplet.group(2)), float(triplet.group(3))
```

This should also be added to `coordinator.py:_parse_coordinates()` for production use.

---

## D. New `COORDINATE_SPACES` Entry

```python
# In both coordinator.py and benchmark_grounding.py:
COORDINATE_SPACES: Dict[str, str] = {
    "molmo": "normalized_0_100",       # Original Molmo: 0-100
    "molmo2": "normalized_0_1000",     # Molmo2: 0-1000 (same as Qwen)
    "qwen3-vl": "normalized_0_1000",
    "qwen2.5-vl": "normalized_0_1000",
    "qwen2-vl": "normalized_0_1000",
    "claude-sonnet-4-20250514": "pixel",
}
```

The substring matching in `_resolve_coordinate_space()` will match
`mlx-community/Molmo2-8B-4bit` to key `"molmo2"` because `"molmo2"` is a substring.

**Important ordering note:** `"molmo2"` must be checked before `"molmo"` in substring
matching, or else `"molmo2"` would match the `"molmo"` key first (getting wrong scale
`normalized_0_100` instead of `normalized_0_1000`). The current dict-based matching
iterates keys, and since `"molmo"` is a substring of `"molmo2"`, we need `"molmo2"` to
appear first OR switch to longest-match-first logic.

---

## E. New Backend Entry in `benchmark_grounding.py`

```python
BACKENDS["molmo2-mlx"] = {
    "type": "openai_compat",
    "url": "http://localhost:8092/v1/chat/completions",
    "model": "mlx-community/Molmo2-8B-4bit",
}

PRICING["molmo2-mlx"] = {"input": 0.0, "output": 0.0}  # local, free
```

Add `"molmo2-mlx"` to `DEFAULT_BACKENDS` list and to warmup candidates.

---

## F. Hardware Feasibility

| Resource | Available | Molmo2 4-bit | Molmo1 3-bit (port 8091) | Both |
|----------|-----------|-------------|-------------------------|------|
| RAM | 32 GB | ~7-8 GB | ~4-5 GB | ~12-13 GB |
| Disk | 206 GB free | 6.4 GB | already present | OK |

**Verdict: Feasible.** 32 GB M2 Pro can comfortably run both Molmo-7B-D-3bit (port 8091)
and Molmo2-8B-4bit (port 8092) simultaneously, with ~18-20 GB free for OS + other models.

Running Molmo2-8B-8bit (10.2 GB on disk, ~12 GB RAM) alongside Molmo1 would be tighter
but still possible. Start with 4-bit.

---

## G. Implementation Checklist for Builder

1. Create `.venv-molmo2` with Python 3.11 via uv
2. Install `mlx-vlm>=0.3.10`, `starlette`, `uvicorn`, `pillow` in that venv
3. Download model: `mlx-community/Molmo2-8B-4bit` (will auto-download on first load)
4. Test server: `.venv-molmo2/bin/python scripts/mlx_vlm_server.py --model mlx-community/Molmo2-8B-4bit --port 8092`
5. Add `"molmo2"` to `COORDINATE_SPACES` in `benchmark_grounding.py` (and `coordinator.py`)
6. Add Molmo2 `<points coords>` parser to `_parse_coordinates()` in `benchmark_grounding.py`
7. Add `"molmo2-mlx"` backend entry in `benchmark_grounding.py`
8. Fix `_resolve_coordinate_space()` to prefer longest key match (so `molmo2` matches before `molmo`)
9. Test with: `python scripts/benchmark_grounding.py --backends molmo2-mlx --n 5`

---

## H. Risk: Coordinate Format Uncertainty

The main risk is that Molmo2 may not respond to our `FOUND: x=N, y=N` prompt template
and will instead output its native `<points coords="..."/>` format. The parser fallback
in section C handles this. If Molmo2 does comply with `FOUND:` prompting, the coordinates
it returns will be in 0-1000 scale (not 0-100 like original Molmo), which the new
`COORDINATE_SPACES["molmo2"]` entry handles correctly.

The adversary should test both scenarios in the benchmark run.
