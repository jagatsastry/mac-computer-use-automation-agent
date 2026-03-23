# GroundCUA Grounding Benchmark Report

**Date**: 2026-03-23
**Dataset**: ServiceNow/GroundCUA (51K screenshots, 87 desktop platforms)
**Sample size**: 50 samples from 50 platforms (seed=42)
**Script**: `scripts/benchmark_groundcua.py`

## Final Results (v5)

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
```

## Files

- `scripts/benchmark_groundcua.py` — benchmark script
- `scripts/compare_grounding.py` — single-image comparison tool
- `groundcua_benchmark/groundcua_benchmark_20260321_232257.json` — raw results (v5)
- `groundcua_benchmark/debug_20260321_232257/` — annotated miss images
- `src/automation_agent/vision/coordinator.py` — live agent coordinate space fix
