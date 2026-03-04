# Molmo Grounding Integration Spec

How to add Molmo as a visual grounding backend to `scripts/benchmark_grounding.py`.

---

## A. Environment Findings

| Resource           | Value                                    |
|--------------------|------------------------------------------|
| CPU                | Apple M2 Pro                             |
| GPU                | Integrated (M2 Pro, 19-core GPU)         |
| Unified RAM        | 32 GB                                    |
| Disk free          | 227 GB available on `/`                  |
| Python env         | `.venv` present; no torch, no transformers, no vllm |
| Ollama models      | qwen3-vl:latest, llava:latest, gemma2:9b, embeddinggemma:latest |

---

## B. Molmo Options Ranked by Feasibility

### Option 1: OpenRouter API (Molmo 2 8B)

| Field                | Detail |
|----------------------|--------|
| Available?           | YES -- `allenai/molmo-2-8b` on OpenRouter |
| Model ID             | `allenai/molmo-2-8b` (paid) or `allenai/molmo-2-8b:free` (rate-limited: 20 req/min, 200 req/day) |
| Older model          | `allenai/molmo-7b-d:free` also available (original Molmo, Qwen2-7B backbone) |
| Setup steps          | 1. Get OpenRouter API key at https://openrouter.ai/keys 2. `export OPENROUTER_API_KEY=sk-or-...` 3. No install needed -- uses stdlib `urllib.request` |
| Model size           | Runs remotely, no local resources needed |
| RAM requirements     | N/A (cloud) |
| Inference speed      | ~1-3s per image (network dependent) |
| Cost                 | Paid tier: $0.20 / 1M input tokens, $0.20 / 1M output tokens. Free tier: $0 but rate-limited. For 50 samples: ~$0.02 estimated |
| **Recommended**      | **YES** -- Fastest to set up, zero local dependencies, works immediately |

### Option 2: MLX-VLM Local Server (Molmo 7B, 4-bit)

| Field                | Detail |
|----------------------|--------|
| Available?           | YES -- `mlx-community/Molmo-7B-D-0924-4bit` on HuggingFace (5.3 GB) |
| Other quantizations  | 3-bit, 4-bit, 6-bit, 8-bit, bf16 available |
| Setup steps          | 1. `pip install -U mlx-vlm` 2. `python -m mlx_vlm.server --model mlx-community/Molmo-7B-D-0924-4bit --port 8090` (auto-downloads model on first run) |
| Model size           | 5.3 GB (4-bit); ~7.5 GB (8-bit); ~14 GB (bf16) |
| RAM requirements     | ~8-10 GB for 4-bit inference on M2 Pro unified memory |
| Inference speed      | ~3-8s per image estimated on M2 Pro (MLX Metal acceleration) |
| Cost                 | $0 (fully local) |
| API endpoint         | `http://localhost:8090/v1/chat/completions` (OpenAI-compatible) |
| **Recommended**      | **YES (secondary)** -- Good local option, but requires mlx-vlm install and first-run download |

### Option 3: Ollama (Molmo)

| Field                | Detail |
|----------------------|--------|
| Available?           | **NO** -- Molmo is NOT in the Ollama model library |
| Status               | GitHub issue #6958 open since Sep 2024, 77 thumbs-up, no GGUF/Ollama support merged |
| Workaround?          | No official GGUF exists for Molmo. AllenAI stated they are not releasing GGUF "anytime soon" |
| **Recommended**      | **NO** -- Not available, cannot use `ollama pull molmo` |

### Option 4: HuggingFace + vLLM

| Field                | Detail |
|----------------------|--------|
| Available?           | Partially -- `vllm-metal` plugin exists for Apple Silicon but requires MLX-format models |
| Setup complexity     | High -- need `vllm-metal` plugin, torch, and potentially model conversion |
| Molmo 2 support      | Molmo 2 8B (allenai/Molmo2-8B) available on HuggingFace as safetensors, but no MLX community conversion yet for Molmo 2 |
| Original Molmo       | MLX conversions exist (see Option 2), so vLLM-Metal could work indirectly |
| RAM requirements     | ~16 GB+ for 8B model in bf16 |
| **Recommended**      | **NO** -- Over-engineered for benchmark; mlx-vlm is simpler for the same hardware |

### Option 5: HuggingFace Transformers Direct

| Field                | Detail |
|----------------------|--------|
| Available?           | YES -- `allenai/Molmo2-8B` or `allenai/Molmo-7B-D-0924` via `transformers` |
| Setup steps          | `pip install transformers torch pillow einops` + ~16 GB download |
| Problem              | No torch in current `.venv`; installing torch is ~2 GB; no OpenAI-compatible server out of the box |
| **Recommended**      | **NO** -- Heavyweight, no API server, benchmark expects HTTP endpoint |

---

## C. Recommended Setup

**Primary: OpenRouter API** (zero setup, works now).
**Secondary: MLX-VLM local** (free, needs one `pip install`).

### OpenRouter Setup (recommended for benchmark)

```bash
# 1. Set API key
export OPENROUTER_API_KEY="sk-or-v1-..."

# 2. Verify connectivity
curl -s https://openrouter.ai/api/v1/models \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" | python3 -c "
import json, sys
models = json.load(sys.stdin).get('data', [])
molmo = [m for m in models if 'molmo' in m.get('id', '').lower()]
for m in molmo:
    print(f\"{m['id']}  context={m.get('context_length', '?')}\")
"

# 3. Test inference with an image
curl -s https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "allenai/molmo-2-8b",
    "messages": [{"role": "user", "content": "Say hello"}],
    "max_tokens": 32
  }' | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

### MLX-VLM Local Setup (alternative)

```bash
# 1. Install mlx-vlm (into project venv)
.venv/bin/pip install -U mlx-vlm

# 2. Start server (auto-downloads 5.3 GB model on first run)
.venv/bin/python -m mlx_vlm.server \
  --model mlx-community/Molmo-7B-D-0924-4bit \
  --port 8090

# 3. Verify
curl -s http://localhost:8090/v1/models | python3 -c "
import json, sys
for m in json.load(sys.stdin).get('data', []):
    print(m['id'])
"

# 4. Test inference
curl -s http://localhost:8090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/Molmo-7B-D-0924-4bit",
    "messages": [{"role": "user", "content": "Say hello"}],
    "max_tokens": 32
  }' | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

---

## D. Molmo Coordinate Format

### Native output format

Molmo outputs point coordinates as **XML-like tags** with coordinates **normalized to 0-100** (percentage of image dimensions):

**Single point:**
```
<point x="29.78" y="83.80" alt="the close button">close button</point>
```

**Multiple points:**
```
<points x1="10.0" y1="10.0" x2="20.0" y2="20.0" alt="buttons">two buttons</points>
```

### Coordinate space

| Property          | Value |
|-------------------|-------|
| Range             | 0.0 to 100.0 |
| Interpretation    | Percentage of image width (x) and height (y) |
| Origin            | Top-left corner |
| Conversion to 0-1 | Divide by 100.0 |

This means Molmo uses a **different coordinate space** from what the current `COORDINATE_SPACES` dict says. The existing entry `"molmo": "normalized_0_1"` is **incorrect** -- Molmo actually uses a 0-100 range, not 0-1.

### Parsing changes needed

The current `_parse_coordinates()` expects `FOUND: x=N, y=N` format. Molmo does NOT output this format natively. Two approaches:

**Approach A (recommended): Custom prompt + existing parser.**
Use the existing `PROMPT_TEMPLATE` which instructs the model to respond with `FOUND: x=N, y=N`. Molmo (via OpenRouter or mlx-vlm) receives this instruction and should comply. The values it returns will be in its native 0-100 space even when using the `FOUND:` format. The parser already handles this since it just extracts numbers.

**Approach B (fallback): Add Molmo-native parser.**
If Molmo ignores the prompt format and responds with its native `<point>` tags, add a fallback parser:

```python
def _parse_molmo_native(response: str) -> Optional[Tuple[float, float]]:
    """Parse Molmo's native <point x="N" y="N"> format."""
    match = re.search(
        r'<point\s+x="([0-9]*\.?[0-9]+)"\s+y="([0-9]*\.?[0-9]+)"',
        response,
    )
    if match:
        return float(match.group(1)), float(match.group(2))
    return None
```

The benchmark should try `_parse_coordinates()` first, then fall back to `_parse_molmo_native()` if parsing fails.

### COORDINATE_SPACES update

Change the Molmo entry from `"normalized_0_1"` to `"normalized_0_100"`:

```python
COORDINATE_SPACES: Dict[str, str] = {
    "molmo": "normalized_0_100",        # <-- CHANGED from "normalized_0_1"
    "molmo-2": "normalized_0_100",      # <-- NEW for Molmo 2
    "qwen3-vl": "normalized_0_1000",
    "qwen2.5-vl": "normalized_0_1000",
    "qwen2-vl": "normalized_0_1000",
    "claude-sonnet-4-20250514": "pixel",
}
```

Add normalization case in `normalize_prediction()`:

```python
elif space == "normalized_0_100":
    return (
        min(max(raw_x / 100.0, 0.0), 1.0),
        min(max(raw_y / 100.0, 0.0), 1.0),
    )
```

**Important**: The same change must be made in `src/automation_agent/vision/coordinator.py` (the source of truth) and the benchmark's copied constants.

---

## E. Changes Needed in benchmark_grounding.py

### E1. New BACKENDS entries

```python
BACKENDS: Dict[str, Dict[str, str]] = {
    # ... existing entries ...

    "molmo-openrouter": {
        "type": "openrouter",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "allenai/molmo-2-8b",
    },
    "molmo-mlx": {
        "type": "openai_compat",
        "url": "http://localhost:8090/v1/chat/completions",
        "model": "mlx-community/Molmo-7B-D-0924-4bit",
    },
}
```

### E2. New backend type: `openrouter`

The OpenRouter API is OpenAI-compatible but requires an `Authorization: Bearer` header. Add a new backend type `"openrouter"` that:
- Reuses `call_openai_compat_backend()` logic
- Adds `Authorization: Bearer $OPENROUTER_API_KEY` header
- Availability check: verify `OPENROUTER_API_KEY` env var is set

Alternatively, generalize `call_openai_compat_backend()` to accept optional headers, and pass the auth header for OpenRouter.

### E3. COORDINATE_SPACES update

```python
COORDINATE_SPACES: Dict[str, str] = {
    "molmo": "normalized_0_100",          # WAS: "normalized_0_1" -- WRONG
    "molmo-2": "normalized_0_100",        # NEW
    "qwen3-vl": "normalized_0_1000",
    "qwen2.5-vl": "normalized_0_1000",
    "qwen2-vl": "normalized_0_1000",
    "claude-sonnet-4-20250514": "pixel",
}
```

### E4. normalize_prediction() update

Add handling for the new `"normalized_0_100"` space:

```python
elif space == "normalized_0_100":
    return (
        min(max(raw_x / 100.0, 0.0), 1.0),
        min(max(raw_y / 100.0, 0.0), 1.0),
    )
```

### E5. Parser fallback for Molmo native format

Update the parsing logic so that when `_parse_coordinates()` returns `None`, attempt `_parse_molmo_native()` as a fallback (only for Molmo backends). This handles the case where Molmo responds with `<point x="..." y="...">` instead of `FOUND: x=N, y=N`.

### E6. JPEG/PNG media type bug fix

**Problem**: Both `call_openai_compat_backend()` (line 365) and `call_anthropic_backend()` (line 423) hardcode `image/png` for the media type. ScreenSpot dataset images may be JPEG or PNG. Claude's API returns HTTP 400 if the declared media type does not match the actual image bytes.

**Fix**: Detect the actual image format from the raw bytes and use the correct media type:

```python
def _detect_media_type(image_bytes: bytes) -> str:
    """Detect image media type from file header bytes."""
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    if image_bytes[:2] == b'\xff\xd8':
        return "image/jpeg"
    if image_bytes[:4] == b'GIF8':
        return "image/gif"
    if image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return "image/webp"
    # Default to PNG if unknown
    return "image/png"
```

Then pass the detected media type to both backend functions. In `call_openai_compat_backend()`:

```python
"image_url": {"url": f"data:{media_type};base64,{image_b64}"}
```

In `call_anthropic_backend()`:

```python
"media_type": media_type,
```

### E7. PRICING update

```python
PRICING = {
    # ... existing ...
    "molmo-openrouter": {"input": 0.20, "output": 0.20},
    "molmo-mlx": {"input": 0.0, "output": 0.0},
}
```

---

## F. Benchmark Plan

### Backends to run

| Backend              | Type          | Notes |
|----------------------|---------------|-------|
| `molmo-openrouter`   | openrouter    | Primary Molmo backend (Molmo 2 8B via OpenRouter) |
| `claude-sonnet`      | anthropic     | Reference baseline |
| `qwen3-vl-ollama`    | openai_compat | Already running locally |

Optional: `molmo-mlx` if the builder sets up the local MLX server.

### Sample count

`--n 20` for a meaningful but fast initial run. Each backend runs all 20 samples, so total calls = 60.

### Expected runtime

| Backend            | Est. latency/sample | Est. total (20 samples) |
|--------------------|---------------------|-------------------------|
| molmo-openrouter   | ~2s                 | ~40s                    |
| claude-sonnet      | ~1.5s               | ~30s                    |
| qwen3-vl-ollama    | ~5s                 | ~100s                   |

**Total estimated runtime: ~3 minutes.**

### Expected cost

| Backend            | Est. cost (20 samples) |
|--------------------|------------------------|
| molmo-openrouter   | ~$0.01                 |
| claude-sonnet      | ~$0.10                 |
| qwen3-vl-ollama    | $0.00                  |

### Run command

```bash
python scripts/benchmark_grounding.py \
  --backends molmo-openrouter claude-sonnet qwen3-vl-ollama \
  --n 20
```
