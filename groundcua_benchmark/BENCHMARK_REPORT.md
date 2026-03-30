# GroundCUA Grounding Benchmark Report

**Date**: 2026-03-24
**Dataset**: ServiceNow/GroundCUA (51K screenshots, 87 desktop platforms)
**Sample size**: 50 samples from 50 platforms (seed=42)
**Script**: `scripts/benchmark_groundcua.py`

This report covers the same 50-sample GroundCUA slice across six models: Gemini Flash 3, MolmoPoint-GUI, MolmoPoint, GPT-5.4, Claude Sonnet 4, and Molmo. The benchmark implementation evolved while we were debugging it, so every table below is normalized from the saved raw result rows rather than copied from mixed console summaries. In particular, the cloud artifact originally stored only found-only accuracy; this document recomputes both overall hit rate and found-only accuracy for all six models from the raw JSON files.

## Final Results

| Model | Model ID | HitRate | FoundAcc | Hits | Misses | Not Found | Errors | Avg Latency |
|-------|----------|---------|----------|------|--------|-----------|--------|-------------|
| **Gemini Flash 3** | `gemini-3-flash-preview` | **78.0%** | **78.0%** | 39 | 11 | 0 | 0 | 9.5s |
| **MolmoPoint-GUI** | `allenai/MolmoPoint-GUI-8B` | **68.0%** | **69.4%** | 34 | 15 | 1 | 0 | 39.0s |
| **MolmoPoint** | `mlx-community/MolmoPoint-8B-4bit` | **50.0%** | **51.0%** | 25 | 24 | 1 | 0 | 20.9s |
| **GPT-5.4** | `gpt-5.4` | **48.0%** | **57.1%** | 24 | 18 | 8 | 0 | 1.4s |
| **Claude Sonnet 4** | `claude-sonnet-4-20250514` | **40.0%** | **66.7%** | 20 | 10 | 20 | 0 | 2.3s |
| **Molmo** | `mlx-community/Molmo-7B-D-0924-3bit` | **10.0%** | **26.3%** | 5 | 14 | 31 | 0 | 7.2s |

**Metric note**:
- `HitRate` = hits / total samples
- `FoundAcc` = hits / (hits + misses), excluding `NOT_FOUND`
- Older cloud artifacts printed only the equivalent of `FoundAcc`; this report recomputes both metrics uniformly from the saved raw rows

**Key takeaways**:
- Gemini Flash 3 is still the strongest single model on this slice.
- MolmoPoint-GUI is the best fully local model and closes much of the gap to Gemini, but it is also the slowest model by a wide margin.
- MolmoPoint is the better local speed/accuracy tradeoff if MolmoPoint-GUI latency is too high.
- GPT-5.4 is the fastest strong baseline, while Claude Sonnet 4 is accurate when it answers but gives up too often.
- Plain Molmo now benchmarks honestly, but it is not competitive on GroundCUA-style tiny desktop targets.

## Configuration

| Model | Image Handling | Coordinate Space | Prompt Style |
|-------|----------------|------------------|--------------|
| Gemini Flash 3 | Original screenshot | 0-1000 normalized | Structured `FOUND: x=..., y=...` prompt |
| MolmoPoint-GUI | Original screenshot | Pixel, decoded server-side from point tokens | `Point to ...` |
| MolmoPoint | Original screenshot | Pixel, decoded server-side from point tokens | `Point to ...` |
| GPT-5.4 | Resized to 1024 width | Pixel | Structured `FOUND: x=..., y=...` prompt |
| Claude Sonnet 4 | Resized to 1024 width | Pixel | Structured `FOUND: x=..., y=...` prompt |
| Molmo | Original screenshot with server-side `max_image_dim=768` | 0-100 normalized | Structured `FOUND: x=..., y=...` prompt, plus native-output parse fallback |

**Why the configs differ**:
- Gemini, Molmo, MolmoPoint, and MolmoPoint-GUI do not use the cloud-model 1024-wide resize path used for GPT and Claude.
- GPT-5.4 and Claude Sonnet 4 return pixel coordinates directly, so shrinking the image width to 1024 keeps tiny UI elements proportionally larger and reduces the coordinate range they have to predict over.
- Molmo is the one exception inside the original-resolution path: it still receives the original screenshot logically, but the local MLX server caps `max_image_dim` at 768 to avoid out-of-memory failures.

## Bugs Found & Fixed

Across Gemini Flash 3, MolmoPoint-GUI, MolmoPoint, GPT-5.4, Claude Sonnet 4, and Molmo, we found seven benchmark-path bugs that materially changed the interpretation of the results.

### Bug 1: Gemini was decoded in the wrong coordinate space

**Root cause**: Gemini was registered as a pixel-space model even though it returns coordinates on a 0-1000 normalized grid. On a 1920x1080 screenshot, a response like `x=655` was being treated as pixel `655` instead of `655 / 1000 * 1920`.

**Fix**: Changed Gemini to `normalized_0_1000` in the benchmark and live parsing paths.

**Reference**: [Gemini Computer Use docs](https://ai.google.dev/gemini-api/docs/computer-use)

### Bug 2: The GPT benchmark was using the wrong model version

**Root cause**: The benchmark still targeted `gpt-4o` instead of `gpt-5.4`, even though the live agent had already moved to GPT-5.4.

**Fix**: Updated the benchmark model registry from `gpt-4o` to `gpt-5.4`.

### Bug 3: A single resize policy was hurting different model families in different ways

**Root cause**: We initially used the same image-prep strategy for every model. That hid the fact that normalized-coordinate models and pixel-coordinate models want different treatment. Sending everything resized helped GPT and Claude but wasted resolution for Gemini; later, sending everything at original size helped Gemini but sharply hurt the pixel-coordinate models.

**Fix**: Use original screenshots for Gemini and the Molmo family, and use the 1024-wide resize path only for GPT-5.4 and Claude Sonnet 4.

### Bug 4: GroundCUA screenshots were being recompressed to JPEG

**Root cause**: `benchmark_groundcua.py` was re-encoding dataset PNGs as JPEG before inference. That added blur to exactly the kind of tiny toolbar icons and narrow labels that dominate GroundCUA misses.

**Fix**: Preserve screenshots as lossless PNG all the way through the benchmark request path.

### Bug 5: Molmo-family native outputs and prompt echoes were parsed incorrectly

**Root cause**: Molmo2 and MolmoPoint-style native outputs such as `<point ...>` and `<points coords="...">` were not fully supported everywhere, and plain Molmo sometimes echoed the prompt, then answered `NOT_FOUND`, then included a later `FOUND:` example string that the old parser incorrectly treated as a real answer.

**Fix**: Added full native Molmo parsing, including frame-prefixed `<points coords="1 1 914 074">` forms, and changed the parsers to trust the first line-level answer token instead of later echoed examples.

### Bug 6: Plain Molmo v1 needed a bounded server-side image cap

**Root cause**: Plain Molmo could not reliably process full-resolution GroundCUA screenshots on local MLX without running out of memory.

**Fix**: Keep the original screenshot path in the benchmark, but apply a single server-side `max_image_dim=768` cap for plain Molmo only. MolmoPoint and MolmoPoint-GUI continue to run on the original image size.

### Bug 7: MolmoPoint-GUI needed a longer cold-start timeout

**Root cause**: MolmoPoint-GUI's first request can exceed the old 120-second local HTTP timeout even when the server is healthy.

**Fix**: Increase the local timeout to 300 seconds for `molmo-point-gui` only. No extra normalization or coordinate conversion changes were needed.

## Iteration History

Historical iteration tracking is most comparable in `FoundAcc`, because that is the metric recorded directly in the older cloud-only runs. Dashes mean that model was not part of that iteration. In `v6`, the cloud values are the unchanged `v5` results carried forward, and the Molmo-family values come from the final local runs on the same seed-42 sample slice.

| Run | Gemini | MolmoPoint-GUI | MolmoPoint | GPT | Claude | Molmo | Changes |
|-----|--------|----------------|------------|-----|--------|-------|---------|
| v1 | 12.0% | — | — | 9.5% | 73.3% | — | Baseline: resize all images, pixel decoding everywhere, GPT still on `gpt-4o` |
| v2 | 68.0% | — | — | 3.4% | 21.2% | — | Fixed Gemini to 0-1000; switched to no-resize and JSON prompt |
| v3 | 72.0% | — | — | 3.4% | 20.0% | — | Reverted to the structured `FOUND:` prompt |
| v4 | 74.0% | — | — | 4.5% | 65.5% | — | Split image prep by model family: original for Gemini, resized for GPT and Claude |
| v5 | 78.0% | — | — | 57.1% | 66.7% | — | Upgraded GPT from `gpt-4o` to `gpt-5.4` |
| v6 | 78.0% | 69.4% | 51.0% | 57.1% | 66.7% | 26.3% | Final parser, image, and timeout fixes; added Molmo, MolmoPoint, and MolmoPoint-GUI |

## Detailed Analysis

### Bbox Size vs Accuracy

Answered samples only. `NOT_FOUND` and error rows are excluded from this comparison.

| Model | Avg Hit Bbox | Avg Miss Bbox | Reading |
|-------|--------------|---------------|---------|
| Gemini Flash 3 | 3,669 px² | 1,957 px² | Strong overall, but the misses still skew smaller than the hits |
| MolmoPoint-GUI | 3,583 px² | 2,771 px² | Best local model, but it still drops on the smallest or most ambiguous controls |
| MolmoPoint | 4,350 px² | 2,287 px² | Similar pattern to MolmoPoint-GUI with a larger gap between confident hits and misses |
| GPT-5.4 | 1,737 px² | 547 px² | Very sensitive to tiny targets |
| Claude Sonnet 4 | 1,706 px² | 483 px² | Similar to GPT, plus many more refusals |
| Molmo | 14,495 px² | 2,193 px² | Mostly succeeds only when the target is unusually large |

### Cross-Model Agreement

| Correct Models on a Sample | Count | Percentage |
|----------------------------|-------|------------|
| 0 of 6 | 5 | 10% |
| 1 of 6 | 9 | 18% |
| 2 of 6 | 8 | 16% |
| 3 of 6 | 6 | 12% |
| 4 of 6 | 9 | 18% |
| 5 of 6 | 10 | 20% |
| 6 of 6 | 3 | 6% |

Only five samples defeated all six models. Three samples were solved by everyone: Blender (`slot 1`), Mastodon (`Quote - Honesty is Best Policy`), and Nextcloud (`edit widgets`).

### Hardest Samples

These are the five samples that none of the six models grounded correctly.

| Platform | Element | Bbox Size | Why Hard |
|----------|---------|-----------|----------|
| OpenToonz | `Cutter tool` | 32x30 px | Tiny toolbar icon |
| Lemmy | `Header` | 36x33 px | Generic label with multiple plausible candidates |
| WeKan | `board name` | 178x26 px | Wide but very thin text target |
| OpenShot | `expand/hide` | 49x52 px | Ambiguous toggle icon |
| GrassGIS | `Add various raster map layers` | 40x36 px | Verbose label for a tiny toolbar control |

### Latency

| Model | Avg | P50 | P95 |
|-------|-----|-----|-----|
| Gemini Flash 3 | 9.5s | 4.8s | 37.7s |
| MolmoPoint-GUI | 39.0s | 31.2s | 74.1s |
| MolmoPoint | 20.9s | 22.4s | 25.5s |
| GPT-5.4 | 1.4s | 1.3s | 2.0s |
| Claude Sonnet 4 | 2.3s | 1.8s | 5.0s |
| Molmo | 7.2s | 7.1s | 8.2s |

MolmoPoint-GUI also had one cold-start outlier at 285.1 seconds, which is why the 300-second timeout change mattered even though its steady-state median is much lower.

### Not-Found Rate

| Model | Not Found | Rate |
|-------|-----------|------|
| Gemini Flash 3 | 0/50 | 0.0% |
| MolmoPoint-GUI | 1/50 | 2.0% |
| MolmoPoint | 1/50 | 2.0% |
| GPT-5.4 | 8/50 | 16.0% |
| Claude Sonnet 4 | 20/50 | 40.0% |
| Molmo | 31/50 | 62.0% |

## Per-Platform Results

Because this slice contains one sample per platform, each model's hit count is also its number of correctly grounded platforms.

| Model | Correct Platforms | Unique Wins |
|-------|-------------------|-------------|
| Gemini Flash 3 | 39 | 5 |
| MolmoPoint-GUI | 34 | 2 |
| MolmoPoint | 25 | 1 |
| GPT-5.4 | 24 | 1 |
| Claude Sonnet 4 | 20 | 0 |
| Molmo | 5 | 0 |

- Platforms solved by all six models: Blender (`slot 1`), Mastodon (`Quote - Honesty is Best Policy`), and Nextcloud (`edit widgets`).
- Gemini-only wins: OnlyOffice Forms (`form settings`), Komodo Edit (`maximize`), OnlyOffice PDF Forms (`zoom in`), LibreOffice Draw (`Rectangle`), and Zulip (`Voice call`).
- GPT-5.4's only unique win was Affine (`Sticky notes`).
- MolmoPoint's only unique win was FontForge (`change whether spiro is active or not`).
- MolmoPoint-GUI's unique wins were DuckDuckGo (`add`) and RStudio (`Plots`).
- Claude Sonnet 4 and Molmo had no unique wins on this slice.

## Recommendations

1. **Use Gemini Flash 3 as the default grounding model** when raw accuracy matters most.
2. **Use MolmoPoint-GUI as the best local model** when you can tolerate 30-70 second latency and occasional cold-start spikes.
3. **Use MolmoPoint when you need a better local latency/accuracy tradeoff** than MolmoPoint-GUI.
4. **Use GPT-5.4 for speed-critical paths**. It is the fastest strong baseline by a wide margin.
5. **Use Claude Sonnet 4 as a conservative fallback or verifier**, not as the only grounding model. Its found-only accuracy is solid, but its refusal rate is too high for primary use.
6. **Do not use plain Molmo for GroundCUA-like desktop grounding**. The benchmark is now correct, and the corrected result is still poor.
7. **If you can afford a two-model ensemble, pair Gemini Flash 3 with MolmoPoint-GUI**. That pair covers 43/50 samples on this slice, which is the best two-model union in the saved results.

## Reproduction

```bash
# Install
pip install -e ".[dev]"

# Cloud models on the exact 50-sample slice
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
- `groundcua_benchmark/groundcua_benchmark_20260321_232257.json` — Gemini Flash 3, GPT-5.4, and Claude Sonnet 4 raw results
- `groundcua_benchmark/groundcua_benchmark_20260323_150855.json` — Molmo raw results
- `groundcua_benchmark/groundcua_benchmark_20260323_151535.json` — MolmoPoint raw results
- `groundcua_benchmark/groundcua_benchmark_20260323_154803.json` — MolmoPoint-GUI pilot raw results (10 samples)
- `groundcua_benchmark/groundcua_benchmark_20260323_160053.json` — MolmoPoint-GUI full raw results
- `groundcua_benchmark/debug_20260321_232257/` — annotated miss images from the cloud-model run
- `src/automation_agent/vision/coordinator.py` — live agent coordinate parsing and coordinate-space fixes
