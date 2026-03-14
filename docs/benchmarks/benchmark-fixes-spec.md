# Benchmark Fixes Spec

Spec for 4 fixes to `scripts/benchmark_grounding.py` discovered during 20-sample ScreenSpot run.

## Root Cause Analysis

qwen3-vl (via Ollama) is a **thinking model**: it emits internal reasoning into a
`reasoning` field before producing `content`. With `max_tokens: 256` (current setting),
the model exhausts all tokens on reasoning and returns `content: ""`. The parser then
returns `(None, None)` for 18/20 samples.

**Proof from live probes:**

| max_tokens | content               | reasoning len | finish_reason |
|------------|-----------------------|---------------|---------------|
| 256        | `""`                  | 958 chars     | `length`      |
| 4096       | `"FOUND: x=10, y=10"` | ~1200 chars   | `stop`        |
| 256 + native API `think: false` | `"FOUND: x=10, y=10"` | present but separate | `stop` |

qwen3-vl **does** follow the `FOUND: x=N, y=N` format when given enough tokens.
The coordinate space is confirmed as 0-1000 (already registered correctly in
`COORDINATE_SPACES`).

---

## Fix 1: qwen3-vl thinking model support

### Problem
`call_openai_compat_backend()` uses `max_tokens: 256`. qwen3-vl's thinking consumes
all 256 tokens, leaving `content` empty.

### Solution: Two-pronged approach

**1a. Switch qwen3-vl to native Ollama API with `think: false`**

The Ollama native API (`/api/chat`) supports `"think": false` which disables the
thinking trace and lets the model produce content directly within a small token budget.
The OpenAI-compat endpoint (`/v1/chat/completions`) does NOT support this parameter.

Add a new function `call_ollama_native_backend()` that uses `/api/chat` instead of
`/v1/chat/completions`:

```python
def call_ollama_native_backend(
    base_url: str, model: str, image_b64: str, prompt: str,
) -> Tuple[str, float]:
    """Call Ollama's native /api/chat endpoint with thinking disabled."""
    # base_url = "http://localhost:11434" (strip /v1/chat/completions)
    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
        "stream": False,
        "think": False,
    }
    # ... standard urllib request, return (content, latency)
```

**1b. Update BACKENDS dict to mark qwen3-vl as type `ollama_native`**

```python
"qwen3-vl-ollama": {
    "type": "ollama_native",
    "url": "http://localhost:11434",  # base URL, not /v1/chat/completions
    "model": "qwen3-vl:latest",
},
```

**1c. Update `run_sample()` to dispatch to `call_ollama_native_backend`**

Add an `elif cfg["type"] == "ollama_native":` branch in `run_sample()` (around line 699).

**1d. Update `check_backend()` and availability check**

Add `elif cfg["type"] == "ollama_native":` to `check_backend()` that checks
`http://localhost:11434/api/tags` (Ollama health check).

### Why NOT just increase max_tokens?
Increasing `max_tokens` to 4096 works but wastes ~1500 tokens of reasoning per sample,
making the benchmark ~6x slower for qwen3-vl. With `think: false` via native API, the
model responds in ~286 tokens total vs ~1768 tokens.

### Parser changes needed: None
The existing `_parse_coordinates()` regex already matches `FOUND: x=10, y=10`.
No changes needed to the parser.

### Prompt changes needed: None
The existing `PROMPT_TEMPLATE` works correctly with qwen3-vl when thinking is disabled.

---

## Fix 2: Claude API guard

### Problem
When `--backends` is not specified, the script defaults to `list(BACKENDS.keys())`
which includes `claude-sonnet`. This means running the benchmark without flags will
make paid Claude API calls if `ANTHROPIC_API_KEY` is set.

### Solution

Change the default backends in `main()` (line 963) from:

```python
backends_requested = args.backends if args.backends else list(BACKENDS.keys())
```

to:

```python
# Claude API: only runs when explicitly requested via --backends
DEFAULT_BACKENDS = ["molmo-mlx", "qwen3-vl-ollama"]
backends_requested = args.backends if args.backends else DEFAULT_BACKENDS
```

Also update `argparse` help text for `--backends` to say:
`"Backend names to run (default: molmo-mlx, qwen3-vl-ollama; claude-sonnet only when explicitly requested)"`

---

## Fix 3: Molmo warmup

### Problem
First molmo-mlx inference sample took 57s due to cold-start (model loading).
This skews the latency metrics for sample 0.

### Solution

Add a warmup step in `main()` after backend availability check (after line 980),
before the benchmark loop:

```python
# Warmup: send a tiny image to molmo-mlx to trigger model loading
if "molmo-mlx" in backends_available:
    print("Warming up molmo-mlx...", file=sys.stderr)
    warmup_image = base64.b64encode(_create_tiny_black_png()).decode()
    warmup_start = time.perf_counter()
    try:
        call_openai_compat_backend(
            BACKENDS["molmo-mlx"]["url"],
            BACKENDS["molmo-mlx"]["model"],
            warmup_image,
            "test",
        )
        warmup_elapsed = time.perf_counter() - warmup_start
        print(f"  Warmup complete ({warmup_elapsed:.1f}s)", file=sys.stderr)
    except Exception as e:
        print(f"  Warmup failed: {e}", file=sys.stderr)
```

If molmo-mlx is not in the requested backends, skip warmup entirely.

Also add warmup for qwen3-vl-ollama if it's in the backends list, using the
`call_ollama_native_backend()` function to ensure the model is loaded.

---

## Fix 4: Unit test updates

### Tests that need updating in `tests/unit/test_benchmark_grounding.py`

**4a. `TestBackendsDict.test_backends_has_expected_keys` (line 653)**
Currently checks for `"qwen2.5-vl-ollama"`. Needs to also check for `"qwen3-vl-ollama"`
and `"molmo-mlx"`. The `qwen2.5-vl-ollama` key no longer exists in BACKENDS (it was
replaced by `qwen3-vl-ollama`), so update the assertion.

```python
def test_backends_has_expected_keys(self, bg):
    assert "claude-sonnet" in bg.BACKENDS
    assert "qwen3-vl-ollama" in bg.BACKENDS
    assert "molmo-mlx" in bg.BACKENDS
    assert "qwen2.5-vl-llamacpp" in bg.BACKENDS
```

**4b. `TestBackendsDict.test_openai_compat_backends` (line 664)**
Currently checks `bg.BACKENDS["qwen2.5-vl-ollama"]` which no longer exists.
Replace with check for `qwen3-vl-ollama` with `type == "ollama_native"`.

```python
def test_ollama_native_backend(self, bg):
    qwen3 = bg.BACKENDS["qwen3-vl-ollama"]
    assert qwen3["type"] == "ollama_native"
    assert "11434" in qwen3["url"]
```

**4c. New test: `test_parse_coordinates_qwen3vl_format`**
Add test cases for the response format qwen3-vl produces. Since it follows `FOUND: x=N, y=N`
already, the existing tests cover this, but add an explicit named test:

```python
def test_qwen3_vl_found_response(self, bg):
    """qwen3-vl with thinking disabled returns standard FOUND format."""
    result = bg._parse_coordinates("FOUND: x=10, y=10")
    assert result == (10.0, 10.0)
```

**4d. New test: `test_default_backends_exclude_claude`**
Verify that the default backend list does not include claude-sonnet:

```python
def test_default_backends_exclude_claude(self, bg):
    """Default backends should not include claude-sonnet."""
    # DEFAULT_BACKENDS should be the new module-level constant
    assert "claude-sonnet" not in bg.DEFAULT_BACKENDS
    assert "molmo-mlx" in bg.DEFAULT_BACKENDS
    assert "qwen3-vl-ollama" in bg.DEFAULT_BACKENDS
```

**4e. `TestPricing.test_has_expected_backends` (line 706)**
Add `"qwen3-vl-ollama"` pricing entry (local, free) and test for it.

**4f. New test: `test_call_ollama_native_backend_exists`**
Verify the new function exists:

```python
def test_call_ollama_native_backend_exists(self, bg):
    """call_ollama_native_backend function should exist."""
    assert hasattr(bg, "call_ollama_native_backend")
    assert callable(bg.call_ollama_native_backend)
```

---

## Implementation Order

1. Fix 1 (qwen3-vl) -- highest impact, fixes 18/20 failures
2. Fix 2 (Claude guard) -- safety, prevents accidental API spend
3. Fix 3 (warmup) -- quality of life, fixes latency skew
4. Fix 4 (unit tests) -- last, after code changes are stable

## Verification

After all fixes, run:
```bash
python scripts/benchmark_grounding.py --n 20 --backends molmo-mlx qwen3-vl-ollama
```

Expected: both backends produce actual coordinates (not `(None, None)`).
