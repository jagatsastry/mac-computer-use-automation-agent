# Spec Review: Vision Architecture Improvements

**Reviewer**: Adversary agent
**Spec file**: `docs/vision-arch-improvements-spec.md`
**Date**: 2026-03-03

## Verdict: APPROVED WITH NOTES

The spec is well-structured, covers all 4 recommendations, and is largely testable and backward-compatible. A builder could implement it as written. However, I found several issues that the builder must handle carefully — these are not blockers for proceeding, but they must be addressed during implementation to avoid subtle bugs.

---

## Issue Catalogue

### [Rec 1 — Accessibility API] ISSUE A: JXA `Application.currentApplication()` is wrong for frontmost app

**Section**: "Implementation sketch" (the JXA code block)

The script uses:
```javascript
const app = argv[0]
    ? Application(argv[0])
    : Application.currentApplication();
```

`Application.currentApplication()` refers to the JXA scripting process itself (the script runner), NOT the frontmost app. The correct pattern for the frontmost app is:
```javascript
sysEvents.processes.whose({frontmost: true})[0]
```
The script already does this a few lines later in the `proc` assignment — but the initial `app` variable is unused in that branch. This inconsistency in the sketch could confuse a builder. The actual proc lookup via `sysEvents.processes.whose({frontmost: true})` is correct. The builder should ensure the `argv[0]` branch also routes through `sysEvents.processes.byName(argv[0])`, NOT through `Application(argv[0])` which opens a different scripting bridge.

**Impact**: If the builder copies the sketch literally, the no-arg branch may not walk the right process tree.

---

### [Rec 1 — Accessibility API] ISSUE B: Timeout unit inconsistency

**Section**: "New Helper: `get_accessibility_elements()`" — "Timeout: 3 seconds"

The spec says the accessibility call should have a 3-second timeout. However, `AppleScriptActuator.TIMEOUT_SECONDS = 10` is the class-level constant used for all other osascript calls. The spec does not say whether the builder should add a new constant (e.g., `AX_TIMEOUT_SECONDS = 3`) or just pass `timeout=3` inline. A builder could reasonably use the existing 10s timeout, defeating the spec intent.

**Recommendation**: The spec should explicitly state "use `timeout=3` (not `TIMEOUT_SECONDS`)".

---

### [Rec 1 — Accessibility API] ISSUE C: `candidates` not filtered/clamped before passing to prompt

**Section**: "Changes to `find_element()` in `coordinator.py`"

The spec shows a fixed prompt format with candidate list, but does NOT specify:
1. What happens if `candidates` is a very large list (e.g., 500 elements in a complex app)? The prompt could overflow the model's context.
2. Should labels be truncated for very long strings?

This is a missing constraint. The builder could pass all 500 elements and produce a broken prompt. A reasonable cap (e.g., top 20 candidates by proximity or first-encountered) should be specified.

---

### [Rec 2 — Confidence Gating] ISSUE D: Boundary condition at exactly threshold is ambiguous

**Section**: Confidence gate check: `if confidence > 0.0 and confidence < threshold`

The condition is `confidence < threshold` (strictly less than). This means:
- `confidence == 0.5` with `_DEFAULT_CONFIDENCE_THRESHOLD = 0.5` → gate triggers (blocked)
- `confidence == 0.5` with a strict `>= threshold` check → would pass

This is a deliberate design choice, but the spec doesn't call it out explicitly. The test contract in the spec tests `confidence=0.3` (below) and `confidence=0.7` (above), but **does NOT test the boundary value where `confidence == threshold`**.

**Recommendation**: Builder tests must cover `confidence == 0.5` explicitly to confirm it blocks. The spec's test contract (item 3 and 4) should add this as a test case.

---

### [Rec 2 — Confidence Gating] ISSUE E: `_CRITICAL_ACTION_KEYWORDS` checked against `step.verify`, not `params["element"]`

**Section**: `_get_confidence_threshold()` helper

```python
verify_lower = step.verify.lower() if step.verify else ""
if any(kw in verify_lower for kw in self._CRITICAL_ACTION_KEYWORDS):
    return self._CRITICAL_CONFIDENCE_THRESHOLD
```

The keyword list includes `"send"`. The word "send" appears frequently in non-critical verify text (e.g., "send keys", "keyboard shortcut is sent"). This could spuriously elevate the threshold for non-critical actions.

Similarly, `"delete"` and `"remove"` appear in many innocuous UI contexts ("removed from list", "deleted old text"). The spec doesn't discuss this false-positive risk.

**Recommendation**: The builder should be aware of this and tests should cover the case where "send" appears in a non-critical context to document the behavior.

---

### [Rec 3 — Two-Pass Validation] ISSUE F: `PIL` import is inside the method body but spec says it's "already a dep"

**Section**: `_validate_candidate()` implementation sketch

The import `from PIL import Image` is inside the async method body. This is fine at runtime, but the spec says "Note: PIL (Pillow) is already a dependency — used in `vision/capture.py`". I could not confirm `vision/capture.py` uses PIL — the file was not visible in the spec. The builder should verify this before relying on it.

If PIL is NOT already a dependency, importing it inside a method body that gracefully catches exceptions (`is_valid = True # Fail-open`) will silently swallow the ImportError and the pre-click validation will always appear to pass. This is a hidden footgun.

---

### [Rec 3 — Two-Pass Validation] ISSUE G: Crop region can be SMALLER than 200x200 for edge elements

**Section**: `_validate_candidate()` crop math

```python
left = max(0, candidate_x - half)
top = max(0, candidate_y - half)
right = min(w, candidate_x + half)
bottom = min(h, candidate_y + half)
```

If `candidate_x = 50` and `half = 100`, then `left = 0`, `right = 150`, yielding a 150-wide crop. The spec does NOT document that the cropped image may be smaller than 200x200. The vision model sees a smaller crop with the element NOT centered — which could degrade validation accuracy. The spec test contract does not include this edge case.

**Recommendation**: The test contract should include a test where `candidate_x < 100` or `candidate_y < 100` to verify the crop still works (or is padded).

---

### [Rec 3 — Two-Pass Validation] ISSUE H: Double vision call cost not mentioned for non-accessibility, non-high-confidence hits

**Section**: "Performance Consideration"

The spec correctly documents skipping validation for accessibility hits and high-confidence matches. But it doesn't address the case where BOTH Rec 2 and Rec 3 are active for the same click:
- Rec 2 may block the click (low confidence)
- Rec 3 validation runs only if confidence passes the gate

This sequencing is implicit. The spec pseudocode shows the confidence gate first, then validation — which is correct. But it's not called out explicitly, and a builder could accidentally run validation before the confidence gate, wasting a vision call on a click that will be blocked anyway.

---

### [Rec 4 — Resolution-Aware Cropping] ISSUE I: `_CROP_WIDTH_THRESHOLD = 1440` contradicts the default screenshot resolution

**Section**: "Coordinate Space Safety" and threshold explanation

The spec states:

> The `_CROP_WIDTH_THRESHOLD = 1440` check means: only crop if the image sent to the model is larger than 1440px wide. Since the default `screenshot_resolution` is (1024, 768), cropping will NOT happen by default.

This is correct and the backward compatibility argument is sound. However, the spec does NOT address what happens when `screenshot_resolution` is e.g., `(1920, 1080)`: the image IS wider than 1440px, but `last_successful_region` may contain coordinates from the 1920-wide space, while the NEXT call's crop operates in the same space. This is consistent (no bug), but worth explicit statement.

More importantly: the spec does NOT clarify whether `last_successful_region` stores coordinates in the `screenshot_resolution` space or in raw screen pixel space. The spec says:

> Stored as (left, top, right, bottom) in pixel coordinates of the screenshot resolution (config.screenshot_resolution), NOT raw screen pixels.

This is clear — but the test contract doesn't verify that region is stored in screenshot-resolution coordinates, only that coordinates are offset correctly. A builder might store raw screen coordinates if `actuator_result` returns them in raw pixels.

---

### [Rec 4 — Resolution-Aware Cropping] ISSUE J: `last_successful_region` is never reset on FAILED clicks, only on `execute()` start

**Section**: "Recording Successful Regions"

The spec records a region only after a SUCCESSFUL click:

```python
if step.action == "click" and verification.success:
    ...
    self.last_successful_region = ...
```

But `last_successful_region` is only reset at the start of `execute()`. If a task has multiple sequential clicks where click #1 succeeds (region recorded), click #2 fails, click #3 uses a stale `last_successful_region` from click #1. This could cause the crop to be centered on the wrong area for click #3. This is a silent, hard-to-debug behavior not documented in the spec.

**Recommendation**: Consider clearing `last_successful_region` when the step is NOT a click, or documenting this staleness as intentional (e.g., "the last successful region is used as a hint, not a guarantee").

---

### [General] ISSUE K: `FindElementResult` dataclass (Rec 2) is defined but never used as return type

**Section**: Rec 2, "New Field: `confidence` on Find Element Result"

The spec adds a `FindElementResult` dataclass to `shared_models.py` but all the code in `find_element()` continues to return `Optional[Dict[str, Any]]` (a plain dict), not `FindElementResult`. The dataclass is added but never wired as the actual return type of `find_element()`. The `ScreenCoordinator` protocol signature also remains `Optional[Dict[str, Any]]`.

This is an inconsistency: the spec introduces a typed result but then doesn't use it, leaving `FindElementResult` as dead code. Either:
1. The protocol should be updated to return `Optional[FindElementResult]`, OR
2. The spec should state explicitly that `FindElementResult` is for future use and the dict-based return is kept for backward compatibility.

As written, a builder will implement `FindElementResult` and wonder why it's never used.

---

### [General] ISSUE L: Missing test cases for Unicode in element labels (Accessibility API)

**Section**: All test contracts

None of the test contracts in the spec test element labels with non-ASCII characters (e.g., `"Sauvegarder"`, `"保存"`, Chinese labels, emoji in button text). The JXA `elem.title()` call can return Unicode strings. The JSON parsing in Python handles this correctly (`json.loads` in Python 3), but the spec's test coverage ignores this.

**Recommendation**: Add a test case: `get_accessibility_elements()` where the JSON output contains Unicode labels. Assert they're preserved correctly.

---

### [General] ISSUE M: No test for when Accessibility API permission is NOT granted

**Section**: Rec 1, Error Handling

The spec says "Returns empty list on any failure (permission denied, no elements, timeout)." When macOS Accessibility permission is not granted, `osascript` running JXA to access System Events will raise a permission error (non-zero exit code). The error handling correctly handles this by returning `[]`.

However, the test contract tests `TimeoutExpired` but NOT the permission-denied case (non-zero exit code from subprocess). These are different code paths in the implementation.

**Recommendation**: Add test: mock `subprocess.run` to return `returncode=1` (permission denied). Assert returns `[]`.

---

## Summary of Issues by Severity

| ID | Severity | Rec | Description |
|----|----------|-----|-------------|
| A  | Medium   | 1   | JXA `Application.currentApplication()` is wrong for frontmost |
| B  | Low      | 1   | Timeout unit inconsistency (3s vs TIMEOUT_SECONDS=10) |
| C  | Medium   | 1   | No cap on number of candidates in prompt |
| D  | Low      | 2   | Boundary value `confidence == threshold` not tested |
| E  | Low      | 2   | `"send"` in keywords causes false positive threshold elevation |
| F  | Medium   | 3   | PIL dependency may not exist; fail-open masks ImportError |
| G  | Low      | 3   | Crop can be smaller than 200x200 for edge elements (not documented) |
| H  | Low      | 3   | Double vision call sequencing not explicitly documented |
| I  | Low      | 4   | Coordinate space of `last_successful_region` not verified in tests |
| J  | Low      | 4   | `last_successful_region` staleness across failed clicks not documented |
| K  | Medium   | 2   | `FindElementResult` dataclass never used as actual return type |
| L  | Low      | 1   | No Unicode test for accessibility element labels |
| M  | Low      | 1   | No test for permission-denied (non-zero exit code) from osascript |

## What the Builder Can Proceed With

Despite the issues above, the spec is sufficiently detailed for implementation to begin. The core design is sound:

- All 4 recommendations have concrete function signatures.
- Backward compatibility is preserved via `None` defaults and `confidence > 0.0` guard.
- Error handling philosophy (fail-open) is consistent throughout.
- File-by-file change tables are accurate.

**The builder must pay attention to**:
- Issue A (JXA frontmost app detection)
- Issue C (candidate list size cap)
- Issue F (PIL dependency verification)
- Issue K (decide if `FindElementResult` is wired or stays as future dead code)

The adversary will write tests specifically targeting Issues D, G, I, L, and M (boundary/edge cases missing from the spec's test contract).
