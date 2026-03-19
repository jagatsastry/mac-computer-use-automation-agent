# GPT Grounding Offset Fix — Spec

## Problem

GPT (OpenAI) grounding sometimes produces coordinates with a fixed offset that shifts
click targets by a consistent amount, while other screenshots work fine.

## Root Causes (confirmed)

### 1. Missing `detail: "original"` on screenshot submission

**Primary cause.** The OpenAI computer-use guide (as of March 2026) specifies that
`detail: "original"` preserves coordinate accuracy. Without it, OpenAI may downscale
the image internally and return coordinates in the downscaled space.

| Path | File | Line | Has `detail: "original"`? |
|------|------|------|---------------------------|
| Benchmark script | `scripts/benchmark_grounding.py` | 695 | YES |
| Overlay app | `providers.ts` | 596 | YES |
| Live agent `find_element` | `src/automation_agent/llm/openai_client.py` | 239-248 | **NO** |
| Live agent `generate_vision` | `src/automation_agent/llm/openai_client.py` | 370-378 | **NO** |

The benchmark and overlay work correctly because they send `detail: "original"`. The
live agent does not.

### 2. Screenshot letterboxing creates constant padding

`capture.py` (line 75-98) always captures into a 1024x768 target with letterboxing.
On a 1440x900 screen, this creates ~43px of black top/bottom padding. When GPT grounds
on the letterboxed image and returns pixel coordinates, those coordinates are in the
1024x768 image space — which includes the padding offset.

The geometry helpers in `geometry.py` (`image_to_screen_coords`) handle this correctly,
but only when they are used. The `_convert_coordinates` path in the coordinator treats
GPT as `"pixel"` space and divides by image width/height, which does NOT account for
the letterbox offset.

### 3. `image_width`/`image_height` received but unused in `find_element`

`OpenAIClient.find_element()` receives `image_width` and `image_height` parameters
(lines 195-196) but never uses them in the API payload. Unlike Anthropic's computer-use
API which accepts `display_width_px`/`display_height_px`, the OpenAI computer tool
does not have explicit display size parameters — but `detail: "original"` achieves the
same result by telling the API not to downscale.

## Why some screenshots work fine

- Screenshots that go through the benchmark or overlay path are correct because they
  preserve `detail: "original"` and don't introduce an extra coordinate frame.
- Screenshots that go through the live agent path are more likely to drift because
  they're first resized into a letterboxed 1024x768 JPEG, and the GPT client does not
  request `detail: "original"`.
- When the aspect ratio happens to match (no letterboxing) or when GPT correctly infers
  the content bounds from visual cues, the offset is minimal/zero.

## Fixes

### Fix 1: Add `detail: "original"` to both GPT paths

**File:** `src/automation_agent/llm/openai_client.py`

**a) `find_element` method (line 239-248) — computer-use path:**

```python
# Current (broken):
"output": {
    "type": "computer_screenshot",
    "image_url": _to_data_url(screenshot_b64),
}

# Fixed:
"output": {
    "type": "computer_screenshot",
    "image_url": _to_data_url(screenshot_b64),
    "detail": "original",
}
```

**b) `generate_vision` method (line 370-378) — generic vision path:**

```python
# Current (broken):
{
    "type": "input_image",
    "image_url": _to_data_url(img),
}

# Fixed:
{
    "type": "input_image",
    "image_url": _to_data_url(img),
    "detail": "high",
}
```

Note: `generate_vision` uses the standard vision API (not computer-use), where the
equivalent parameter is `detail: "high"` (not `"original"`). This prevents OpenAI
from downscaling the image and misaligning coordinates.

### Fix 2: Add debug logging of letterbox ContentRect

**File:** `src/automation_agent/vision/coordinator.py`

When grounding via GPT (provider is `openai`), log the letterbox ContentRect so we can
immediately diagnose whether a bad result is a scale/offset problem:

```python
from automation_agent.vision.geometry import fit_screen_into_image

rect = fit_screen_into_image(screen_size, self.config.screenshot_resolution)
logger.debug(
    "gpt_grounding_letterbox",
    content_left=rect.left,
    content_top=rect.top,
    content_width=rect.width,
    content_height=rect.height,
    scale=rect.scale,
    screenshot_resolution=self.config.screenshot_resolution,
)
```

### Fix 3: Log screenshot dimensions sent to GPT

**File:** `src/automation_agent/llm/openai_client.py`

Add structlog debug logging in both `find_element` and `generate_vision` showing:
- Screenshot dimensions (from base64 decode or passed params)
- Whether `detail: "original"/"high"` was set

## Non-changes

- **Molmo/Qwen/Gemini/Anthropic paths**: Not affected. They use normalized coordinates
  (0-1000 or 0-1) or explicit `display_width_px`/`display_height_px`.
- **`geometry.py`**: Already correct. No changes needed.
- **`capture.py`**: Letterboxing is intentional and correct for other providers. No change.
- **Coordinator `_convert_coordinates`**: The `"pixel"` space handler for GPT models
  divides by image width/height, which is correct when `detail: "original"` is set
  (GPT returns coordinates in the original image's pixel space).

## Acceptance Criteria

1. `detail: "original"` present in `find_element`'s `computer_screenshot` output
2. `detail: "high"` present in `generate_vision`'s `input_image`
3. Debug log emitted for every GPT grounding call showing letterbox rect
4. Unit tests covering the `detail` field presence in both paths
5. All existing tests still pass (1604+)
6. Customer test: run 3 grounding scenarios with `AGENT_GROUNDING_MODEL_PROVIDER=openai`
   and verify no systematic offset in debug images
