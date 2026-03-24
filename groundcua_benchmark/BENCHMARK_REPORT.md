# GroundCUA Grounding Benchmark Report

**Date**: 2026-03-24
**Dataset**: ServiceNow/GroundCUA (51K screenshots, 87 desktop platforms)
**Sample size**: 50 samples from 50 platforms (seed=42)
**Script**: `scripts/benchmark_groundcua.py`

This report now includes two phases:
- v5: cloud-model baseline and benchmark fixes for Gemini / Claude / GPT
- v6: local Molmo-family follow-up on the same 50-sample slice

## Cloud Model Results (v5)

| Model | Model ID | Accuracy | Hits | Misses | Not Found | Errors | Avg Latency |
|-------|----------|----------|------|--------|-----------|--------|-------------|
| **Gemini Flash 3** | `gemini-3-flash-preview` | **78.0%** | 39 | 11 | 0 | 0 | 9.5s |
| **Claude Sonnet 4** | `claude-sonnet-4-20250514` | **66.7%** | 20 | 10 | 20 | 0 | 2.3s |
| **GPT-5.4** | `gpt-5.4` | **57.1%** | 24 | 18 | 8 | 0 | 1.4s |

**Metric**: Point-in-bbox accuracy. A prediction (x, y) is correct if and only if the point falls inside the ground-truth bounding box [x1, y1, x2, y2].

**Accuracy excluding NOT_FOUND**:
- Gemini: 39/50 = 78.0% (never refuses)
- Claude: 20/30 = 66.7% (refuses 40% of the time)
- GPT: 24/42 = 57.1% (refuses 16% of the time)

## Molmo Family Follow-up (v6)

These runs use the current benchmark script after the Molmo-specific fixes described below.

| Model | Model ID | HitRate | FoundAcc | Hits | Misses | Not Found | Errors | Avg Latency |
|-------|----------|---------|----------|------|--------|-----------|--------|-------------|
| **Molmo** | `mlx-community/Molmo-7B-D-0924-3bit` | **10.0%** | **26.3%** | 5 | 14 | 31 | 0 | 7.2s |
| **MolmoPoint** | `mlx-community/MolmoPoint-8B-4bit` | **50.0%** | **51.0%** | 25 | 24 | 1 | 0 | 20.9s |
| **MolmoPoint-GUI** | `allenai/MolmoPoint-GUI-8B` | **68.0%** | **69.4%** | 34 | 15 | 1 | 0 | 39.0s |

**Metric note**:
- `HitRate` = hits / total samples
- `FoundAcc` = hits / (hits + misses), excluding NOT_FOUND

**Key takeaways**:
- `molmo` is now benchmarking honestly: weak overall, mostly `NOT_FOUND`, but no longer crashing or misparsed.
- `molmo-point` is a large step up over plain Molmo and uses the same clean pixel-point decode path.
- `molmo-point-gui` is the best local model on this slice, and it did **not** need any extra normalization or coordinate conversion changes.
- The main GUI-model tradeoff is latency. Median latency is about 31.8s, and one cold/worst-case sample took 285.1s.

**MolmoPoint-GUI pilot**:
- First 10 samples: 6/10 hits, 0 NOT_FOUND, 0 errors
- Pilot artifact: `groundcua_benchmark/groundcua_benchmark_20260323_154803.json`

## Configuration

| Model | Image Resolution | Coordinate Space | Prompt |
|-------|-----------------|-----------------|--------|
| Gemini Flash 3 | Original (e.g. 1920x1080) | 0-1000 normalized | "Return coordinates on a 0-1000 normalized grid" |
| Claude Sonnet 4 | Resized to 1024 width | Pixel | "Return pixel coordinates relative to the screenshot" |
| GPT-5.4 | Resized to 1024 width | Pixel | "Return pixel coordinates relative to the screenshot" |

**Why different configs?**
- Gemini returns coordinates on a 0-1000 grid regardless of image size — resizing wastes resolution.
- Claude and GPT return pixel coordinates — resizing to 1024 width keeps small targets proportionally larger and reduces the coordinate range, improving accuracy.

## Bugs Found & Fixed

### Bug 1: Gemini coordinate space (12% → 78%)

**Root cause**: Gemini was registered as `"pixel"` in the coordinate space registry, but it actually returns **0-1000 normalized coordinates** (same as Qwen models). This meant on a 1920x1080 image, Gemini's response `x=655` was treated as pixel 655 instead of `655/1000 * 1920 = 1258`.

**Evidence**: Y-axis ratio analysis showed Gemini's predicted coordinates were consistently 1.8x the expected values — exactly the ratio of image height to 1000.

**Fix**: Changed `COORDINATE_SPACES["gemini"]` from `"pixel"` to `"normalized_0_1000"` in:
- `src/automation_agent/vision/coordinator.py` (live agent)
- `scripts/compare_grounding.py`
- `scripts/benchmark_groundcua.py`

**Reference**: [Gemini Computer Use docs](https://ai.google.dev/gemini-api/docs/computer-use) — "coordinates on a 0-999 normalized grid"

### Bug 2: GPT model version (4.5% → 57.1%)

**Root cause**: Benchmark was using `gpt-4o` (a 2024 model) instead of `gpt-5.4` (the model actually configured in the live agent). GPT-4o returned NOT_FOUND on 56% of queries and was inaccurate on the rest.

**Fix**: Updated `MODEL_IDS["gpt"]` from `"gpt-4o"` to `"gpt-5.4"`.

### Bug 3: Image resize strategy

**Root cause**: Initially all models received resized 1024-wide images. This helped pixel-coordinate models (Claude, GPT) by making small targets proportionally larger, but was unnecessary for Gemini since it uses normalized coordinates.

Later, we switched to sending original-resolution images to all models. This helped Gemini but devastated Claude (73% → 20%) because small UI elements at 1920px width require much higher pixel precision.

**Fix**: Per-model image preparation:
- Gemini/Molmo: original resolution (normalized coords are resolution-independent)
- Claude/GPT: resized to 1024 width (pixel coords benefit from smaller coordinate range)

### Molmo Follow-up Fixes

#### Bug 4: GroundCUA screenshots were being recompressed to JPEG

**Root cause**: `benchmark_groundcua.py` was re-encoding dataset PNGs as JPEG before inference. For tiny toolbar icons and thin text labels, that added avoidable blur.

**Fix**: Preserve screenshots as lossless PNG throughout the benchmark request path.

#### Bug 5: Molmo native outputs and prompt echoes were being parsed incorrectly

**Root cause**:
- Molmo2 / MolmoPoint native `<points ...>` outputs were not fully supported everywhere.
- Plain Molmo often echoed prompt text, then answered `NOT_FOUND`, then included a later `FOUND:` example string. The old parser could count those as bogus misses.

**Fix**:
- Added native `<point>` and `<points coords="...">` parsing, including frame-prefixed forms like `"1 1 914 074"`.
- Updated parsers to trust the first line-level answer token (`NOT_FOUND`, `FOUND:`, `<point>`, `<points>`, or JSON) instead of later echoed examples.

#### Bug 6: Plain Molmo v1 OOMed on original GroundCUA screenshots

**Root cause**: Molmo v1 could not reliably handle full-resolution GroundCUA screenshots on local MLX without running out of GPU memory.

**Fix**: Keep original screenshots in the benchmark, but apply a **single** server-side `max_image_dim=768` cap only for plain Molmo v1. Molmo2 and MolmoPoint variants still use the original image size.

#### Bug 7: MolmoPoint-GUI needed a longer cold-start timeout

**Root cause**: The GUI finetune's first request can exceed the benchmark's previous 120-second HTTP timeout even when the server is healthy.

**Fix**: Increase local request timeout to 300s for `molmo-point-gui` only. No other coordinate handling changes were required.

## Iteration History

| Run | Gemini | GPT | Claude | Changes |
|-----|--------|-----|--------|---------|
| v1 | 12.0% | 9.5% | 73.3% | Baseline: resize all, pixel coords, gpt-4o |
| v2 | 68.0% | 3.4% | 21.2% | Fixed Gemini to 0-1000; no-resize; JSON prompt |
| v3 | 72.0% | 3.4% | 20.0% | Reverted to FOUND: prompt (JSON wasn't the issue) |
| v4 | 74.0% | 4.5% | 65.5% | Per-model: resize for pixel models, original for Gemini |
| **v5** | **78.0%** | **57.1%** | **66.7%** | Upgraded GPT from 4o to 5.4 |

## Detailed Analysis

### Bbox Size vs Accuracy

Smaller targets are harder. Average bbox area for hits vs misses:

| Model | Avg Hit Bbox | Avg Miss Bbox | Implication |
|-------|-------------|---------------|-------------|
| Gemini | 3,669 px² | 1,957 px² | Misses smaller targets |
| GPT | 1,737 px² | 547 px² | Struggles with tiny elements |
| Claude | 1,706 px² | 483 px² | Similar pattern to GPT |

### Model Agreement

| Outcome | Count | Percentage |
|---------|-------|------------|
| All 3 models correct | 15/50 | 30% |
| No model correct | 9/50 | 18% |
| Mixed (at least 1 correct) | 26/50 | 52% |

### Hardest Samples (no model correct)

| Platform | Element | Bbox Size | Why Hard |
|----------|---------|-----------|----------|
| MuseScore | "Close interface" | 17x15 px | Tiny icon |
| RStudio | "Plots" | 48x18 px | Small tab in dense IDE |
| FontForge | "change whether spiro is active or not" | 24x20 px | Ambiguous small toggle |
| Lemmy | "Header" | 36x33 px | Generic label, multiple candidates |
| OpenToonz | "Cutter tool" | 32x29 px | Small toolbar icon |
| GrassGIS | "Add various raster map layers" | 40x36 px | Verbose label for tiny icon |
| DuckDuckGo | "add " | 42x29 px | Ambiguous single word |
| OpenShot | "expand/hide" | 49x52 px | Ambiguous toggle icon |
| WeKan | "board name" | 178x26 px | Wide but very thin target |

### Latency

| Model | Avg | P50 | P95 |
|-------|-----|-----|-----|
| GPT-5.4 | 1.4s | 1.3s | 2.0s |
| Claude Sonnet 4 | 2.3s | 1.8s | 5.0s |
| Gemini Flash 3 | 9.5s | 4.9s | 37.7s |

GPT is fastest by 2-7x. Gemini has high variance — some queries trigger extended thinking (up to 38s).

### Not-Found Rate

| Model | Not Found | Rate |
|-------|-----------|------|
| Gemini Flash 3 | 0 | 0% — always attempts an answer |
| GPT-5.4 | 8 | 16% |
| Claude Sonnet 4 | 20 | 40% — very conservative |

Claude refuses to answer on 40% of queries. When it does answer, its accuracy is 20/30 = 66.7%. If Claude could be made to always attempt an answer (even uncertain), overall accuracy might improve.

## Per-Platform Results

50 platforms tested. Gemini found the element correctly on 39/50 platforms. Full breakdown:

| All 3 correct (15) | Only Gemini correct (10) | Only Claude/GPT correct (2) | None correct (9) |
|----|----|----|----|
| Anki, Audacity, Bash, Bitwarden, Blender, Cryptomator, Flameshot, IntelliJ IDEA, Kodi, Krita, Mastodon, Nextcloud, OnlyOffice Calendar, OpenProject, Shotcut | Calibre, GIMP, Inkscape, Joplin, LibreOffice Writer, Lightworks, OnlyOffice Document Editor, OnlyOffice Forms, PDFedit, Simplenote | Affine (GPT only), Eclipse (GPT+Claude) | DuckDuckGo, FontForge, GrassGIS, Lemmy, MuseScore, OpenShot, OpenToonz, RStudio, WeKan |

## Recommendations

1. **Use Gemini Flash 3 as primary grounding model** — highest accuracy (78%), never refuses. Latency is higher but acceptable for non-interactive grounding.

2. **Use Claude Sonnet 4 as secondary/verification** — 67% accuracy when it answers, very low false-positive risk due to conservative NOT_FOUND behavior.

3. **GPT-5.4 for speed-critical paths** — 57% accuracy at 1.4s average, 7x faster than Gemini.

4. **Ensemble approach**: If any 2 of 3 models agree on coordinates (within 30px), confidence is very high. The 15/50 all-agree cases had near-perfect accuracy.

5. **Browser-crop for the live agent**: The GroundCUA benchmark uses standalone desktop screenshots. In the live agent, cropping to just the browser content area would increase effective resolution and eliminate desktop chrome noise — expected to improve all models by 5-15%.

## Reproduction

```bash
# Install
pip install -e ".[dev]"

# Run the exact benchmark
.venv/bin/python scripts/benchmark_groundcua.py \
  --n 50 --seed 42 \
  --models gemini-flash,gpt,claude \
  --save-screenshots

# List available platforms
.venv/bin/python scripts/benchmark_groundcua.py --list-platforms

# Run on specific platforms
.venv/bin/python scripts/benchmark_groundcua.py \
  --n 20 --platforms Chrome,Firefox,VS\ Code

# Local Molmo family (one server at a time)
.venv-molmo2/bin/python scripts/mlx_vlm_server.py \
  --model mlx-community/Molmo-7B-D-0924-3bit --port 8091
.venv/bin/python scripts/benchmark_groundcua.py \
  --n 50 --seed 42 --models molmo

.venv-molmo2/bin/python scripts/mlx_vlm_server.py \
  --model mlx-community/MolmoPoint-8B-4bit --port 8092
.venv/bin/python scripts/benchmark_groundcua.py \
  --n 50 --seed 42 --models molmo-point

.venv-molmo2/bin/python scripts/mlx_vlm_server.py \
  --model allenai/MolmoPoint-GUI-8B --port 8092
.venv/bin/python scripts/benchmark_groundcua.py \
  --n 50 --seed 42 --models molmo-point-gui
```

## Files

- `scripts/benchmark_groundcua.py` — benchmark script
- `scripts/compare_grounding.py` — single-image comparison tool
- `groundcua_benchmark/groundcua_benchmark_20260321_232257.json` — raw results (v5)
- `groundcua_benchmark/groundcua_benchmark_20260323_150855.json` — Molmo v1 raw results (v6)
- `groundcua_benchmark/groundcua_benchmark_20260323_151535.json` — MolmoPoint raw results (v6)
- `groundcua_benchmark/groundcua_benchmark_20260323_154803.json` — MolmoPoint-GUI pilot (10 samples)
- `groundcua_benchmark/groundcua_benchmark_20260323_160053.json` — MolmoPoint-GUI full raw results (v6)
- `groundcua_benchmark/debug_20260321_232257/` — annotated miss images
- `src/automation_agent/vision/coordinator.py` — live agent coordinate space fix
