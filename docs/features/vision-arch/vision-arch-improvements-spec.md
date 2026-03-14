# Vision Architecture Improvements Spec

This spec describes four surgical additions to the existing vision-based automation pipeline. Each improvement is backward-compatible: all existing callers continue to work unchanged with no new third-party dependencies.

Reference: `VISION_AUTOMATION_RESEARCH.md` recommendations 1-4.

---

## Recommendation 1: Parser-Assisted Grounding via macOS Accessibility API

### Problem

`find_element()` in `coordinator.py` relies entirely on a vision model to locate UI elements. This is slow (2-5s per call) and fragile on small targets and dense UIs. macOS exposes a rich Accessibility API that returns structured element trees with labels, roles, and bounding boxes -- for free, in milliseconds.

### Design

Add a helper function `get_accessibility_elements()` in the actuator layer that queries the macOS Accessibility API via `osascript` (AppleScript/JXA). Pass the resulting candidate list into `find_element()` as an optional parameter. When candidates are available, the vision model receives a structured list of clickable targets alongside the screenshot, dramatically reducing search ambiguity.

### New Helper: `get_accessibility_elements()`

**File**: `src/automation_agent/actuator/applescript_actuator.py`

```python
def get_accessibility_elements(self, app_name: str = "") -> list[dict]:
    """Query macOS Accessibility API for visible, interactive UI elements.

    Uses JXA (JavaScript for Automation) via osascript to walk the
    accessibility tree of the frontmost application (or the named app).

    Args:
        app_name: Application to query. Empty string means frontmost app.

    Returns:
        List of dicts, each with keys:
            - "label": str  (AXTitle or AXDescription or AXValue)
            - "role": str   (AXButton, AXTextField, AXLink, etc.)
            - "x": int      (left edge in screen pixels)
            - "y": int      (top edge in screen pixels)
            - "width": int
            - "height": int
            - "center_x": int
            - "center_y": int
        Returns empty list on any failure (permission denied, no elements, timeout).
    """
```

**Implementation sketch** (JXA via `osascript -l JavaScript`):

```javascript
// Executed via: osascript -l JavaScript -e '<this script>' [appName]
function run(argv) {
    const app = argv[0]
        ? Application(argv[0])
        : Application.currentApplication();
    const sysEvents = Application("System Events");
    const proc = app.name
        ? sysEvents.processes.byName(app.name || sysEvents.processes.whose({frontmost: true})[0].name())
        : sysEvents.processes.whose({frontmost: true})[0];

    const elements = [];
    const interactiveRoles = [
        "AXButton", "AXLink", "AXTextField", "AXTextArea",
        "AXCheckBox", "AXRadioButton", "AXPopUpButton",
        "AXComboBox", "AXMenuItem", "AXTab", "AXIncrementor"
    ];

    function walk(elem, depth) {
        if (depth > 6) return;  // Cap recursion to avoid hangs
        try {
            const role = elem.role();
            if (interactiveRoles.includes(role)) {
                const pos = elem.position();
                const size = elem.size();
                if (pos && size && size[0] > 0 && size[1] > 0) {
                    elements.push({
                        label: elem.title() || elem.description() || elem.value() || "",
                        role: role,
                        x: pos[0], y: pos[1],
                        width: size[0], height: size[1],
                        center_x: pos[0] + Math.round(size[0] / 2),
                        center_y: pos[1] + Math.round(size[1] / 2)
                    });
                }
            }
            const children = elem.uiElements();
            for (let i = 0; i < children.length; i++) {
                walk(children[i], depth + 1);
            }
        } catch(e) { /* skip inaccessible elements */ }
    }

    walk(proc, 0);
    return JSON.stringify(elements);
}
```

The Python wrapper calls this via `subprocess.run(["osascript", "-l", "JavaScript", "-e", script, app_name], ...)`, parses JSON output, and returns `list[dict]`. On any exception (timeout, permission error, empty output), it returns `[]`.

**Timeout**: 3 seconds (accessibility tree walks can hang on complex apps).

### Changes to `find_element()` in `coordinator.py`

**File**: `src/automation_agent/vision/coordinator.py`

Update `find_element()` signature:

```python
async def find_element(
    self,
    description: str,
    screenshot_b64: Optional[str] = None,
    candidates: Optional[list[dict]] = None,  # NEW — accessibility candidates
) -> Optional[Dict[str, Any]]:
```

When `candidates` is not `None` and not empty, prepend a structured candidate list to the prompt sent to the vision model:

```
The following interactive UI elements are visible on screen:
1. "Save" (AXButton) at center (520, 340)
2. "Cancel" (AXLink) at center (620, 340)
3. "Search" (AXTextField) at center (400, 50)
...

Which element best matches: "{description}"?
If one of the numbered elements matches, respond: FOUND: x=<center_x>, y=<center_y>
If none match, use the screenshot to locate the element.
```

This gives the vision model a shortcut: if a candidate clearly matches, it can return the candidate's pixel center directly without parsing the screenshot. If no candidate matches, the model falls back to its normal screenshot analysis.

### Changes to `ScreenCoordinator` Protocol

**File**: `src/automation_agent/protocols.py`

```python
async def find_element(
    self,
    description: str,
    screenshot_b64: Optional[str] = None,
    candidates: Optional[list[dict]] = None,  # NEW
) -> Optional[Dict[str, Any]]:
```

The `candidates` parameter defaults to `None`, so all existing callers pass no candidates and behavior is identical to today.

### Changes to Orchestrator: Wire Candidates Through

**File**: `src/automation_agent/orchestrator/agent.py`

In `_find_element()`, before calling `coordinator.find_element()`, attempt to get accessibility candidates:

```python
async def _find_element(self, description: str):
    # ... existing grounding_router path unchanged ...

    candidates = None
    if hasattr(self.actuator, "get_accessibility_elements"):
        try:
            candidates = self.actuator.get_accessibility_elements()
        except Exception:
            pass  # Fallback: no candidates

    result = await self.coordinator.find_element(description, candidates=candidates)
    return result
```

### Fallback Behavior

- If `get_accessibility_elements()` raises, times out, or returns `[]`: `candidates` is `None`, and `find_element()` behaves exactly as it does today (pure vision).
- If the vision model ignores the candidate list and returns its own coordinates from the screenshot: that's fine, the candidate list is advisory.
- No coordinate space issues: accessibility elements return native screen pixel coordinates, which are the final coordinate space for clicks.

### Files Changed

| File | Change |
|------|--------|
| `src/automation_agent/actuator/applescript_actuator.py` | Add `get_accessibility_elements()` method |
| `src/automation_agent/vision/coordinator.py` | Add `candidates` param to `find_element()`, build enriched prompt |
| `src/automation_agent/protocols.py` | Add `candidates` param to `ScreenCoordinator.find_element()` |
| `src/automation_agent/orchestrator/agent.py` | Call `get_accessibility_elements()` in `_find_element()` |

### New Fields / Parameters

| Location | Name | Type | Default |
|----------|------|------|---------|
| `AppleScriptActuator` | `get_accessibility_elements(app_name)` | method returning `list[dict]` | N/A |
| `find_element()` | `candidates` | `Optional[list[dict]]` | `None` |

### Error Handling

- `get_accessibility_elements()` catches all exceptions internally, returns `[]`.
- `subprocess.TimeoutExpired` after 3 seconds returns `[]`.
- Invalid JSON output from JXA returns `[]`.
- If `osascript` returns non-zero exit code, returns `[]`.

### Test Contract

1. **Unit test**: Mock `subprocess.run` to return a known JSON list of elements. Call `get_accessibility_elements()`. Assert the returned list contains dicts with the expected keys (`label`, `role`, `x`, `y`, `width`, `height`, `center_x`, `center_y`).
2. **Unit test**: Mock `subprocess.run` to raise `subprocess.TimeoutExpired`. Assert `get_accessibility_elements()` returns `[]`.
3. **Unit test**: Call `find_element(description="Save button", candidates=[{"label": "Save", "role": "AXButton", "center_x": 500, "center_y": 300, ...}])`. Assert the prompt sent to the vision model contains the candidate list text.
4. **Unit test**: Call `find_element(description="Save button", candidates=None)`. Assert the prompt does NOT contain candidate list text (backward compatibility).

---

## Recommendation 2: Confidence-Gated Actions

### Problem

The agent executes click actions regardless of how confident the vision model is about the target location. Misclicks on the wrong element can be destructive (especially for "Submit", "Pay", "Confirm" actions) and trigger cascading failures.

### Design

Add a `confidence` field to the element-finding result. When confidence is below a threshold, skip execution and let the replan loop handle it. For critical actions (those with safety-sensitive keywords in the `verify` field), use a higher threshold.

### New Field: `confidence` on Find Element Result

**File**: `src/automation_agent/shared_models.py`

Add a new dataclass:

```python
@dataclass
class FindElementResult:
    """Result of locating a UI element on screen."""
    x: int
    y: int
    confidence: float = 0.0  # 0.0 = unknown/not reported, 1.0 = certain
    source: str = ""         # "accessibility", "vision", "grounding"
    raw_response: str = ""
```

### Confidence Extraction

**File**: `src/automation_agent/vision/coordinator.py`

Update `_parse_coordinates()` to also extract an optional confidence value:

```python
def _parse_coordinates(self, response: str) -> Optional[Tuple[float, float, float]]:
    """Parse coordinates and optional confidence from a vision model response.

    Expects either:
        FOUND: x=<number>, y=<number> [confidence=<0.0-1.0>]
    or:
        NOT_FOUND

    Returns:
        Tuple of (x, y, confidence), or None if not found.
        confidence defaults to 0.0 if not present in response.
    """
```

The regex expands to optionally capture `confidence=<float>`:

```python
match = re.search(
    r"FOUND:\s*x\s*=\s*([0-9]*\.?[0-9]+)\s*,\s*y\s*=\s*([0-9]*\.?[0-9]+)"
    r"(?:\s*,?\s*confidence\s*=\s*([0-9]*\.?[0-9]+))?",
    response,
    re.IGNORECASE,
)
if match:
    x, y = float(match.group(1)), float(match.group(2))
    conf = float(match.group(3)) if match.group(3) else 0.0
    return x, y, conf
```

Update `find_element()` return value to include `confidence`:

```python
return {
    "x": x, "y": y,
    "confidence": conf,  # NEW
    "source": "vision",
    "raw_response": response,
}
```

For accessibility hits, confidence is `1.0` (exact structural match):

```python
return {"x": cx, "y": cy, "confidence": 1.0, "source": "accessibility"}
```

### Confidence Gating in Orchestrator

**File**: `src/automation_agent/orchestrator/agent.py`

Add a class-level constant and a helper:

```python
# Confidence thresholds
_DEFAULT_CONFIDENCE_THRESHOLD = 0.5
_CRITICAL_ACTION_KEYWORDS = {"submit", "pay", "confirm", "reserve", "delete", "remove", "send"}
_CRITICAL_CONFIDENCE_THRESHOLD = 0.9
```

In `_dispatch_action()`, after finding an element for a `click` action, check confidence before executing:

```python
if action == "click" and "element" in params:
    location = await self._find_element(params["element"])
    if location is None:
        return {"success": False, "error": f"Element not found: {params['element']}"}

    confidence = location.get("confidence", 0.0)
    threshold = self._get_confidence_threshold(step)

    if confidence > 0.0 and confidence < threshold:
        slog.warning(
            "Low confidence element match",
            confidence=confidence,
            threshold=threshold,
            element=params["element"],
        )
        return {
            "success": False,
            "error": f"low_confidence:{confidence:.2f}",
        }

    result = self.actuator.click(location["x"], location["y"])
    result["x"] = location["x"]
    result["y"] = location["y"]
```

The `confidence > 0.0` check ensures that models which don't report confidence (returning the default 0.0) are NOT gated. Only models that explicitly report a low confidence are blocked.

Helper method:

```python
def _get_confidence_threshold(self, step: ActionStep) -> float:
    """Return the confidence threshold for a step.

    Steps whose verify text contains critical-action keywords
    (submit, pay, confirm, reserve, delete, remove, send)
    use a higher threshold of 0.9.
    """
    verify_lower = step.verify.lower() if step.verify else ""
    if any(kw in verify_lower for kw in self._CRITICAL_ACTION_KEYWORDS):
        return self._CRITICAL_CONFIDENCE_THRESHOLD
    return self._DEFAULT_CONFIDENCE_THRESHOLD
```

### Prompt Update for Confidence Reporting

**File**: New prompt template for `find_element` (currently loaded from `prompts/find_element.md`, which needs to be created).

Add to the find-element prompt:

```
If you can locate the element, respond with:
FOUND: x=<x>, y=<y>, confidence=<0.0-1.0>

where confidence indicates how certain you are (1.0 = absolutely sure, 0.5 = uncertain, 0.0 = guessing).
```

### Files Changed

| File | Change |
|------|--------|
| `src/automation_agent/shared_models.py` | Add `FindElementResult` dataclass |
| `src/automation_agent/vision/coordinator.py` | Update `_parse_coordinates()` to extract confidence; update `find_element()` return dicts to include `confidence` |
| `src/automation_agent/orchestrator/agent.py` | Add confidence threshold constants, `_get_confidence_threshold()` helper, and confidence gate check in `_dispatch_action()` |
| Vision prompt template (find_element) | Add confidence reporting instruction |

### New Fields / Parameters

| Location | Name | Type | Default |
|----------|------|------|---------|
| `FindElementResult` | `confidence` | `float` | `0.0` |
| `find_element()` return dict | `"confidence"` | `float` | `0.0` |
| `AutomationAgent` | `_DEFAULT_CONFIDENCE_THRESHOLD` | class constant `float` | `0.5` |
| `AutomationAgent` | `_CRITICAL_CONFIDENCE_THRESHOLD` | class constant `float` | `0.9` |

### Error Handling

- Models that don't report confidence: `confidence` stays `0.0`. The gate check `confidence > 0.0 and confidence < threshold` ensures these models are never blocked (backward compatible).
- A `low_confidence` error string is returned in the actuator result dict. The existing replan loop in `_handle_failure()` handles this naturally (it treats any `success=False` as a reason to retry or replan).
- Accessibility hits always have `confidence=1.0`, so they are never gated.

### Test Contract

1. **Unit test**: Parse a response `"FOUND: x=500, y=300, confidence=0.85"`. Assert returned tuple is `(500.0, 300.0, 0.85)`.
2. **Unit test**: Parse a response `"FOUND: x=500, y=300"` (no confidence). Assert returned tuple is `(500.0, 300.0, 0.0)`.
3. **Unit test**: In `_dispatch_action()`, mock `_find_element` to return `{"x": 100, "y": 200, "confidence": 0.3}` for a step with `verify="Button is clicked"`. Assert the result is `{"success": False, "error": "low_confidence:0.30"}` and `actuator.click` is NOT called.
4. **Unit test**: Same as above but with `confidence=0.7`. Assert `actuator.click` IS called.
5. **Unit test**: For a step with `verify="Payment is submitted"` (contains "submit"), mock confidence at `0.6`. Assert the result is `{"success": False, "error": "low_confidence:0.60"}` because the critical threshold is 0.9.

---

## Recommendation 3: Two-Pass Target Validation

### Problem

Even when `find_element()` returns a coordinate, the clicked element may not be the intended target. The agent only discovers this after clicking (post-click verification). By then, the wrong action may have already been executed (e.g., clicking "Delete" instead of "Edit").

### Design

Add a pre-click validation step: before executing a click, crop a small region around the candidate and ask the vision model "what is this element? does it match the target?" If the answer is NO, skip this candidate and either try the next one or replan. Post-click verification remains unchanged (the existing 3-tier verifier in `verifier.py` handles it).

### New Helper: `_validate_candidate()`

**File**: `src/automation_agent/orchestrator/agent.py`

```python
async def _validate_candidate(
    self,
    candidate_x: int,
    candidate_y: int,
    target_description: str,
    screenshot_b64: Optional[str] = None,
) -> bool:
    """Pre-click validation: crop region around candidate and ask vision model if it matches.

    Crops a 200x200 pixel region centered on (candidate_x, candidate_y) from a
    fresh screenshot (or provided screenshot), then asks the coordinator's
    verify_condition() with the question:
        "The element at the center of this image is: {target_description}"

    Args:
        candidate_x: Pixel x of the candidate element center.
        candidate_y: Pixel y of the candidate element center.
        target_description: What the element should be (e.g., "Save button").
        screenshot_b64: Optional pre-captured full screenshot. If None, captures one.

    Returns:
        True if the vision model confirms the match, False otherwise.
    """
```

**Implementation**:

```python
async def _validate_candidate(
    self,
    candidate_x: int,
    candidate_y: int,
    target_description: str,
    screenshot_b64: Optional[str] = None,
) -> bool:
    if screenshot_b64 is None:
        screenshot_b64 = await self.coordinator.capture_screenshot()

    # Decode screenshot, crop 200x200 around candidate, re-encode
    import base64
    import io
    from PIL import Image

    img_bytes = base64.b64decode(screenshot_b64)
    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size

    # Crop region: 200x200 centered on candidate, clamped to image bounds
    crop_size = 200
    half = crop_size // 2
    left = max(0, candidate_x - half)
    top = max(0, candidate_y - half)
    right = min(w, candidate_x + half)
    bottom = min(h, candidate_y + half)

    cropped = img.crop((left, top, right, bottom))
    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=85)
    cropped_b64 = base64.b64encode(buf.getvalue()).decode()

    # Ask the coordinator if the cropped region matches the target
    condition = f"The element at the center of this image is: {target_description}"
    return await self.coordinator.verify_condition(condition, screenshot_b64=cropped_b64)
```

Note: `PIL` (`Pillow`) is already a dependency -- used in `vision/capture.py`.

### Integration in `_dispatch_action()`

**File**: `src/automation_agent/orchestrator/agent.py`

After finding the element and passing the confidence gate, but BEFORE clicking:

```python
if action == "click" and "element" in params:
    location = await self._find_element(params["element"])
    if location is None:
        return {"success": False, "error": f"Element not found: {params['element']}"}

    # Confidence gate (Rec 2) ... existing check ...

    # Pre-click validation (Rec 3)
    is_valid = await self._validate_candidate(
        location["x"], location["y"], params["element"]
    )
    if not is_valid:
        slog.warning(
            "Pre-click validation failed",
            element=params["element"],
            x=location["x"],
            y=location["y"],
        )
        return {
            "success": False,
            "error": f"Pre-click validation failed: element at ({location['x']}, {location['y']}) "
                     f"does not appear to be '{params['element']}'",
        }

    result = self.actuator.click(location["x"], location["y"])
```

### Performance Consideration

`_validate_candidate()` adds one extra vision model call (~2-5s) per click. To mitigate:

- **Skip validation for accessibility hits**: When `location["source"] == "accessibility"`, the element was found via the structured API and its label was already matched. Skip validation.
- **Skip validation for high-confidence matches**: When `location.get("confidence", 0.0) >= 0.9`, the model is very confident. Skip validation.

```python
source = location.get("source", "")
confidence = location.get("confidence", 0.0)
skip_validation = (source == "accessibility") or (confidence >= 0.9)

if not skip_validation:
    is_valid = await self._validate_candidate(
        location["x"], location["y"], params["element"]
    )
    if not is_valid:
        return {"success": False, "error": "Pre-click validation failed: ..."}
```

### Post-Click Verification

No changes needed. The existing 3-tier verifier in `verifier.py` already handles post-click verification using the step's `verify` field. The wiring is:

1. `_execute_step()` calls `self.verifier.verify(step, actuator_result)` at line 315 of `agent.py`.
2. `StepVerifier.verify()` runs Tier 1 (actuator state) then Tier 2 (vision).
3. The step's `verify` text (mandatory for all non-terminal actions) is used as the condition.

This chain is already correct and does not need modification.

### Files Changed

| File | Change |
|------|--------|
| `src/automation_agent/orchestrator/agent.py` | Add `_validate_candidate()` method; add pre-click validation call in `_dispatch_action()` |

### New Fields / Parameters

| Location | Name | Type | Default |
|----------|------|------|---------|
| `AutomationAgent` | `_validate_candidate()` | async method | N/A |

### Error Handling

- If `_validate_candidate()` raises an exception (e.g., PIL import fails, coordinator call fails): catch and treat as validation-passed (fail-open). Log a warning. This ensures the system degrades to current behavior on error rather than blocking all clicks.

```python
try:
    is_valid = await self._validate_candidate(...)
except Exception:
    slog.warning("Pre-click validation error, proceeding with click", exc_info=True)
    is_valid = True  # Fail-open
```

- Pre-click validation failure returns `success=False` with a descriptive error. The existing retry/replan loop in `_handle_failure()` handles this automatically.

### Test Contract

1. **Unit test**: Mock `coordinator.verify_condition()` to return `True`. Call `_validate_candidate(100, 200, "Save button")`. Assert returns `True`.
2. **Unit test**: Mock `coordinator.verify_condition()` to return `False`. Call `_validate_candidate(100, 200, "Save button")`. Assert returns `False`.
3. **Unit test**: In `_dispatch_action()`, mock `_find_element` returning `{"x": 100, "y": 200, "confidence": 0.6, "source": "vision"}` and `_validate_candidate` returning `False`. Assert the click is NOT executed and the result contains `"Pre-click validation failed"`.
4. **Unit test**: Same setup but with `"source": "accessibility"`. Assert `_validate_candidate` is NOT called (skipped) and click IS executed.

---

## Recommendation 4: Resolution-Aware Candidate Narrowing

### Problem

On Retina/HiDPI displays, screenshots are 2x-3x the resolution that vision models were trained on. Small UI elements occupy few pixels relative to the full image, making them harder for models to locate. Sending a full 2880x1800 screenshot to a model expecting 1024x768 wastes detail on irrelevant regions.

### Design

Before sending a screenshot to `find_element()`, check if the image is high-resolution. If so, and if we have a previously successful click region from the current execution, crop a 512x512 region centered on that area, send just the crop to the model, and then add the crop's offset back to the returned coordinates. If there's no previous region, send the full screenshot (current behavior).

### New State: `last_successful_region`

**File**: `src/automation_agent/orchestrator/agent.py`

Add instance attribute in `__init__`:

```python
def __init__(self, ...):
    # ... existing init ...
    self.last_successful_region: Optional[Tuple[int, int, int, int]] = None
    # Stored as (left, top, right, bottom) in pixel coordinates of the
    # screenshot resolution (config.screenshot_resolution), NOT raw screen pixels.
```

### Recording Successful Regions

**File**: `src/automation_agent/orchestrator/agent.py`

After a successful click verification in `_execute_step()`, record the region:

```python
# After verification succeeds for a click action:
if step.action == "click" and verification.success:
    click_x = actuator_result.get("x", step.params.get("x", 0))
    click_y = actuator_result.get("y", step.params.get("y", 0))
    # Store a 512x512 region centered on the successful click
    half = 256
    self.last_successful_region = (
        max(0, click_x - half),
        max(0, click_y - half),
        click_x + half,
        click_y + half,
    )
```

### Cropping Logic in `_find_element()`

**File**: `src/automation_agent/orchestrator/agent.py`

Before calling `coordinator.find_element()`, optionally crop:

```python
async def _find_element(self, description: str):
    if self.grounding_router is not None:
        # ... existing grounding_router path unchanged ...
        pass

    candidates = None
    if hasattr(self.actuator, "get_accessibility_elements"):
        try:
            candidates = self.actuator.get_accessibility_elements()
        except Exception:
            pass

    # Resolution-aware cropping (Rec 4)
    screenshot_b64 = await self.coordinator.capture_screenshot()
    crop_offset = None

    if self.last_successful_region is not None:
        crop_result = self._maybe_crop_screenshot(screenshot_b64)
        if crop_result is not None:
            screenshot_b64, crop_offset = crop_result

    result = await self.coordinator.find_element(
        description, screenshot_b64=screenshot_b64, candidates=candidates
    )

    # Adjust coordinates back to full-image space if we cropped
    if result is not None and crop_offset is not None:
        result["x"] = result["x"] + crop_offset[0]
        result["y"] = result["y"] + crop_offset[1]

    return result
```

### New Helper: `_maybe_crop_screenshot()`

**File**: `src/automation_agent/orchestrator/agent.py`

```python
_CROP_WIDTH_THRESHOLD = 1440  # Only crop if image wider than this

def _maybe_crop_screenshot(
    self, screenshot_b64: str
) -> Optional[Tuple[str, Tuple[int, int]]]:
    """Crop screenshot to 512x512 around last_successful_region if image is hi-res.

    Args:
        screenshot_b64: Full screenshot as base64 JPEG.

    Returns:
        Tuple of (cropped_b64, (offset_x, offset_y)) if cropping was applied.
        None if image is below the width threshold or no previous region exists.
    """
    import base64
    import io
    from PIL import Image

    img_bytes = base64.b64decode(screenshot_b64)
    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size

    if w <= self._CROP_WIDTH_THRESHOLD:
        return None

    if self.last_successful_region is None:
        return None

    # Center of the last successful region
    region = self.last_successful_region
    center_x = (region[0] + region[2]) // 2
    center_y = (region[1] + region[3]) // 2

    # Crop 512x512, clamped to image bounds
    crop_half = 256
    left = max(0, min(center_x - crop_half, w - 512))
    top = max(0, min(center_y - crop_half, h - 512))
    right = min(w, left + 512)
    bottom = min(h, top + 512)

    cropped = img.crop((left, top, right, bottom))
    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=85)
    cropped_b64 = base64.b64encode(buf.getvalue()).decode()

    return cropped_b64, (left, top)
```

### Coordinate Space Safety

The crop/offset approach works because:

1. `ScreenCapture.capture()` already downscales to `config.screenshot_resolution` (default 1024x768). The crop happens on this downscaled image.
2. `_convert_coordinates()` in `coordinator.py` converts model coordinates to pixel coordinates within the screenshot resolution (e.g., 0-1000 -> 0-1024).
3. After `find_element()` returns pixel coordinates within the crop, we add the crop's offset (`left`, `top`) to get pixel coordinates within the full downscaled screenshot.
4. These are the same coordinate space the actuator uses for clicks.

The `_CROP_WIDTH_THRESHOLD = 1440` check means: only crop if the image sent to the model is larger than 1440px wide. Since the default `screenshot_resolution` is (1024, 768), cropping will NOT happen by default. It only activates when:
- `screenshot_resolution` is set to a larger value (e.g., (2560, 1440) for Retina)
- Or the user is running without downscaling

This ensures zero behavioral change for the default configuration.

### Reset Between Tasks

`last_successful_region` is set to `None` at the start of `execute()`:

```python
async def execute(self, goal: str) -> ExecutionResult:
    self.last_successful_region = None  # Reset for new task
    # ... rest of execute() ...
```

### Files Changed

| File | Change |
|------|--------|
| `src/automation_agent/orchestrator/agent.py` | Add `last_successful_region` attribute, `_maybe_crop_screenshot()` helper, crop/offset logic in `_find_element()`, region recording after successful click, reset in `execute()` |

### New Fields / Parameters

| Location | Name | Type | Default |
|----------|------|------|---------|
| `AutomationAgent` | `last_successful_region` | `Optional[Tuple[int, int, int, int]]` | `None` |
| `AutomationAgent` | `_CROP_WIDTH_THRESHOLD` | class constant `int` | `1440` |
| `AutomationAgent` | `_maybe_crop_screenshot()` | method | N/A |

### Error Handling

- If PIL operations fail during cropping: catch exception, log warning, return `None` from `_maybe_crop_screenshot()` (fall back to full screenshot).
- If `last_successful_region` produces an out-of-bounds crop: the `max(0, ...)` / `min(w, ...)` clamping ensures valid crop coordinates.
- If the crop is too small (e.g., image is 500px and region is at edge): `_maybe_crop_screenshot()` returns `None` because `w <= _CROP_WIDTH_THRESHOLD`.

### Test Contract

1. **Unit test**: Create a 2560x1440 test image. Set `last_successful_region = (500, 500, 1012, 1012)`. Call `_maybe_crop_screenshot()`. Assert the returned crop is 512x512 and offset is correct (centered on region center, clamped to bounds).
2. **Unit test**: Create a 1024x768 test image (below threshold). Call `_maybe_crop_screenshot()`. Assert returns `None` (no cropping).
3. **Unit test**: Set `last_successful_region = None`. Call `_maybe_crop_screenshot()` with a hi-res image. Assert returns `None`.
4. **Unit test**: In `_find_element()`, mock `coordinator.find_element()` to return `{"x": 100, "y": 150}` after cropping with offset `(400, 300)`. Assert final result is `{"x": 500, "y": 450}` (offset applied).
5. **Unit test**: Call `execute()` and assert `last_successful_region` is reset to `None` at the start.

---

## Summary of All Changes

### Files Modified (no new files except prompt template)

| File | Rec | Change Description |
|------|-----|--------------------|
| `src/automation_agent/actuator/applescript_actuator.py` | 1 | Add `get_accessibility_elements()` method |
| `src/automation_agent/vision/coordinator.py` | 1, 2 | Add `candidates` param to `find_element()`; update `_parse_coordinates()` for confidence |
| `src/automation_agent/protocols.py` | 1 | Add `candidates` param to `ScreenCoordinator.find_element()` |
| `src/automation_agent/shared_models.py` | 2 | Add `FindElementResult` dataclass |
| `src/automation_agent/orchestrator/agent.py` | 1, 2, 3, 4 | Wire accessibility candidates; add confidence gating; add `_validate_candidate()`; add `_maybe_crop_screenshot()` and region tracking |
| Vision prompt template | 2 | Add confidence reporting instruction |

### Backward Compatibility Guarantees

- All new parameters have defaults (`None`, `0.0`, etc.) so existing callers are unaffected.
- `confidence > 0.0` guard means models that don't report confidence are never gated.
- Cropping only activates above 1440px width threshold, which is above the default 1024px.
- All accessibility/cropping/validation failures degrade to current behavior (fail-open).
- No changes to coordinate space handling. The `COORDINATE_SPACES` registry is untouched.
- No new third-party dependencies. Uses only: `subprocess`, `json`, `base64`, `io`, `re`, `PIL` (already a dep).
