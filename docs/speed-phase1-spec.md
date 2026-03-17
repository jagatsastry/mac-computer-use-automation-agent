# Speed Phase 1 — Architecture Specification

**Date**: 2026-03-16
**Author**: Tech Lead (Claude Opus 4.6)
**Status**: Draft
**Inputs**: `docs/speed-phase1-sota.md`, `docs/speed-phase1-codebase.md`, `docs/speed-phase1-prd.md`
**PRD ACs**: AC-1 through AC-15

---

## 1. System Design

### 1.1 Data Flow Overview

Three changes, three data flows. All are additive — no existing flow is modified, only extended.

```
                          ┌──────────────────────────────────┐
                          │  AppleScriptActuator.get_state() │
                          │  (applescript_actuator.py)       │
                          └──────────┬───────────────────────┘
                                     │
                          ┌──────────▼───────────────────────┐
                          │  _is_browser(app_name)?          │
                          │  AND js_verification_enabled?    │
                          └──────────┬───────────────────────┘
                                     │ yes
                          ┌──────────▼───────────────────────┐
                          │  SINGLE _get_browser_js() call   │
                          │  JSON.stringify({                 │
                          │    focused_value: ...,            │
                          │    selected_text: ...,            │
                          │    page_title: ...,               │
                          │    page_heading: ...              │
                          │  })                               │
                          └──────────┬───────────────────────┘
                                     │
                          ┌──────────▼───────────────────────┐
                          │  State dict populated:           │
                          │  {focused_value, selected_text,  │
                          │   page_title, page_heading}      │
                          └──────────┬───────────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
            ┌──────────┐   ┌────────────────┐  ┌──────────────┐
            │ Tier 1:  │   │ Tier 1:        │  │ Grounding    │
            │ type_text│   │ page_heading / │  │ Router:      │
            │ matcher  │   │ page_title     │  │ calibrated   │
            │ (exists) │   │ token matcher  │  │ confidence   │
            │          │   │ (NEW)          │  │ (NEW)        │
            └──────────┘   └────────────────┘  └──────────────┘
              Change 1        Change 3            Change 2
```

### 1.2 JS Injection Batching Decision

The codebase doc (Section 12.1, 13.1) recommends batching JS calls into a single `JSON.stringify()` call. The SOTA doc (Section 1) confirms this reduces 4 subprocess calls to 1.

**Decision: Batch.** The current `get_state()` makes 2 separate `_get_browser_js()` calls (lines 556-564 of `applescript_actuator.py`). Adding `page_title` and `page_heading` would be 4 calls. Batching into a single `JSON.stringify()` call saves 3 subprocess round-trips (~60-150ms saved). The complexity cost is minimal: one `json.loads()` call to parse the result, with a try/except fallback to `None` for all fields.

The JS expression is static (no user input), so `_escape_for_applescript()` is not needed for the JS itself. The expression uses only single quotes internally, which is safe inside AppleScript's double-quoted string.

### 1.3 How JS Injection Feeds Tier 1 Verification

1. `StepVerifier._verify_tier1()` calls `actuator.get_state()` once (verifier.py line 348).
2. `get_state()` returns the enriched dict with `focused_value`, `selected_text`, `page_title`, `page_heading`.
3. Existing type_text matcher (verifier.py lines 504-516) checks `focused_value` and `selected_text` — unchanged.
4. NEW page content matcher (after line 586) checks `page_title` and `page_heading` against verify tokens.
5. If any matcher returns `(True/False, evidence)`, Tier 1 is conclusive. Otherwise falls through to Tier 2 vision.

### 1.4 How AX Confidence Calibration Feeds the Grounding Pipeline

1. `GroundingRouter._get_accessibility_matches()` already retrieves `score_fn` (line 319-323) and uses it for sorting (line 345-346).
2. NEW: `_compute_match_score()` calls the same `score_fn` to get a raw score.
3. NEW: `_ground_accessibility_match()` accepts `match_score` parameter, computes `confidence = 0.6 + 0.35 * min(match_score, 1.0)`.
4. `find_element()` passes the score from step 2 into step 3.
5. Downstream: `AutomationAgent._get_confidence_threshold()` (agent.py line 3011-3033) compares calibrated confidence against 0.5/0.9 thresholds. Partial matches (confidence < 0.9) now trigger `_validate_candidate()` pre-click validation.

**CRITICAL FIX (from DE/Short-Seller review):** The pre-click validation skip gate at `agent.py:2315-2318` currently reads:
```python
skip_validation = (
    location.source in ("accessibility", "grounding", "vision")
    or confidence >= 0.9
)
```
This unconditionally skips validation for ALL accessibility sources, making confidence calibration useless. **Engineer 2 MUST also modify `agent.py:2316`** to remove `"accessibility"` from the skip list:
```python
skip_validation = (
    location.source in ("grounding", "vision")
    or confidence >= 0.9
)
```
This way, exact AX matches (confidence=0.95 >= 0.9) still skip validation via the confidence check, but partial matches (confidence=0.845 < 0.9) go through pre-click crop validation as intended by AC-8.

---

## 2. Domain Slice Decomposition

### 2.1 Engineer 1: JS Injection Layer + type_text/page state (Changes 1+3)

**Scope**: Batch all browser JS queries into a single subprocess call in `get_state()`. Add `page_title` and `page_heading` to the state dict. Add a Tier 1 page content token matcher in the verifier.

**Files modified**:

#### `src/automation_agent/actuator/applescript_actuator.py`

**Change A: Add `_get_browser_js_batch()` method** (new method, after `_get_browser_js` at line 442):

```python
def _get_browser_js_batch(self, app_name: str) -> dict:
    """Run a batched JS expression returning multiple DOM values as JSON.

    Returns a dict with keys: focused_value, selected_text, page_title,
    page_heading. All values are Optional[str]. Returns empty dict on
    any failure (non-browser, timeout, parse error).
    """
```

This method calls `_get_browser_js()` with a single `JSON.stringify({...})` expression and parses the result with `json.loads()`. On any failure, returns `{}`.

The JS expression:

```javascript
JSON.stringify({focused_value: (document.activeElement ? (document.activeElement.value || document.activeElement.textContent || '') : ''), selected_text: (window.getSelection ? window.getSelection().toString() : ''), page_title: document.title || '', page_heading: (document.querySelector('h1') ? document.querySelector('h1').textContent : '') || ''})
```

Note: All property accesses use single quotes or no quotes — safe inside AppleScript's double-quoted string.

**Change B: Modify `get_state()` JS section** (lines 551-564):

Replace the two separate `_get_browser_js()` calls with a single `_get_browser_js_batch()` call. Populate `focused_value`, `selected_text`, `page_title`, and `page_heading` from the batch result.

Before (lines 555-564):
```python
if js_enabled:
    state["focused_value"] = self._get_browser_js(
        app_name,
        "document.activeElement.value"
        " || document.activeElement.textContent || ''",
    )
    state["selected_text"] = self._get_browser_js(
        app_name,
        "window.getSelection().toString()",
    )
```

After:
```python
if js_enabled:
    batch = self._get_browser_js_batch(app_name)
    state["focused_value"] = batch.get("focused_value") or None
    state["selected_text"] = batch.get("selected_text") or None
    state["page_title"] = batch.get("page_title") or None
    state["page_heading"] = batch.get("page_heading") or None
```

The `or None` converts empty strings to `None`, preserving the existing contract where `None` means "data unavailable."

**Change C: Add standalone methods** (after `get_selected_text` at line 470):

```python
def get_page_title(self) -> Optional[str]:
    """Get document.title from frontmost browser tab. Returns None for non-browser or on error."""

def get_page_heading(self) -> Optional[str]:
    """Get document.querySelector('h1')?.textContent from frontmost browser tab. Returns None for non-browser or on error."""
```

These delegate to `get_state()` to avoid separate subprocess calls (following the pattern note in codebase doc Section 10.1).

#### `src/automation_agent/orchestrator/verifier.py`

**Change D: Add page content token matcher** (after line 586, before `return None` at line 588):

New block between the scroll matcher and the final `return None`:

```python
# Tier 1: Page content token match (page_title, page_heading)
page_title = state.get("page_title")
page_heading = state.get("page_heading")
if page_title is not None or page_heading is not None:
    verify_tokens = set(re.findall(r'\b\w{3,}\b', verify_lower))
    if verify_tokens:
        for field_name, field_value in [("page_title", page_title), ("page_heading", page_heading)]:
            if field_value is None:
                continue
            field_tokens = set(re.findall(r'\b\w{3,}\b', field_value.lower()))
            overlap = verify_tokens & field_tokens
            # Require 2+ matching tokens to avoid false positives
            if len(overlap) >= 2:
                return (
                    True,
                    f"Tier 1 page {field_name} tokens match verify: {overlap}",
                )
            # Single token match only if it's >= 5 chars (distinctive)
            if len(overlap) == 1 and all(len(t) >= 5 for t in overlap):
                return (
                    True,
                    f"Tier 1 page {field_name} token match: {overlap}",
                )
# No page content match — fall through to Tier 2
```

**Design decisions for the matcher**:
- Tokens are words of 3+ characters, extracted with `\b\w{3,}\b` regex. This filters out "is", "a", "the" which cause false positives.
- Requires 2+ token overlap OR 1 token of 5+ characters. This prevents "cart" matching "Carter" (codebase doc concern).
- Returns `None` (not `(False, ...)`) when fields are `None` — preserving Tier 2 fallback per AC-12.
- Returns `None` when fields are populated but no tokens match — also preserves Tier 2 fallback.

#### `src/automation_agent/config.py`

**Change E: No new config flags needed.** The existing `js_verification_enabled` flag (line 467-470) already gates all JS injection in `get_state()`. The new `page_title` and `page_heading` fields are populated inside the same `if js_enabled:` block (Change B), so they inherit the existing gate. Adding a separate flag would over-segment the feature gate without benefit.

**Test file**: `tests/unit/test_js_verification.py` (~16 tests)

---

### 2.2 Engineer 2: AX Confidence Calibration (Change 2)

**Scope**: Replace the hardcoded `confidence=0.95` in `_ground_accessibility_match()` with a calibrated value derived from `_element_match_score()`.

**Files modified**:

#### `src/automation_agent/orchestrator/grounding_router.py`

**Change F: Add `_compute_match_score()` method** (new method, after `_ground_accessibility_match` at line 404):

```python
def _compute_match_score(self, description: str, elem: Any) -> float:
    """Compute match score using AccessibilityBridge._element_match_score().

    Returns 1.0 if scoring is unavailable (mock, no accessibility, no score_fn).
    This preserves the current behavior where all AX matches get high confidence
    when the scoring infrastructure is absent.
    """
```

Implementation follows the MagicMock detection pattern from `_get_accessibility_matches()` (lines 307-323):

```python
def _compute_match_score(self, description: str, elem: Any) -> float:
    if not self.accessibility:
        return 1.0
    try:
        is_mock = "MagicMock" in type(self.accessibility).__name__
        explicit_attrs = vars(self.accessibility) if is_mock else {}
        extract_target = (
            explicit_attrs.get("_extract_match_target")
            if is_mock
            else getattr(type(self.accessibility), "_extract_match_target", None)
        )
        score_fn = (
            explicit_attrs.get("_element_match_score")
            if is_mock
            else getattr(type(self.accessibility), "_element_match_score", None)
        )
        if not callable(extract_target) or not callable(score_fn):
            return 1.0
        _, text_hint = extract_target(description)
        return score_fn(elem, text_hint)
    except Exception:
        return 1.0
```

**Change G: Modify `_ground_accessibility_match()` signature** (line 385):

Before (line 385):
```python
def _ground_accessibility_match(self, elem: Any) -> Optional[GroundingResult]:
```

After:
```python
def _ground_accessibility_match(self, elem: Any, match_score: float = 1.0) -> Optional[GroundingResult]:
```

Replace the hardcoded `confidence=0.95` at line 394 with:
```python
confidence=0.6 + 0.35 * min(match_score, 1.0),
```

This formula (from PRD AC-9) maps:
| Raw score | Confidence | Passes default (0.5)? | Passes critical (0.9)? |
|-----------|-----------|----------------------|----------------------|
| 1.0 (exact) | 0.95 | Yes | Yes |
| 0.9 (substring) | 0.915 | Yes | Yes |
| 0.7 (good overlap) | 0.845 | Yes | No — triggers pre-click validation |
| 0.3 (weak overlap) | 0.705 | Yes | No |
| 0.0 (role-only match) | 0.6 | Yes | No |

**Change H: Update `find_element()` call sites** (lines 115-118 and 159):

At line 115-118, where `accessibility_result` is constructed:
```python
# Before:
accessibility_result = (
    self._ground_accessibility_match(accessibility_matches[0])
    if accessibility_matches
    else None
)

# After:
if accessibility_matches:
    _score = self._compute_match_score(description, accessibility_matches[0])
    accessibility_result = self._ground_accessibility_match(accessibility_matches[0], match_score=_score)
else:
    accessibility_result = None
```

The `_ground_accessibility(description)` method at line 412 also calls `_ground_accessibility_match()`. Update it:

```python
async def _ground_accessibility(self, description: str) -> Optional[GroundingResult]:
    if not self.accessibility:
        return None
    elem = self.accessibility.find_element_by_description(description)
    if elem is None:
        return None
    score = self._compute_match_score(description, elem)
    return self._ground_accessibility_match(elem, match_score=score)
```

**Test file**: `tests/unit/test_ax_confidence.py` (~8 tests)

---

### 2.3 Engineer 3: Integration Testing

**Scope**: End-to-end integration tests that validate the three changes work together. Tests exercise the full path from `get_state()` through `StepVerifier.verify()` and `GroundingRouter.find_element()`, using mocked subprocess and AX backends.

**Test file**: `tests/integration/test_speed_phase1.py` (~12 tests)

Tests validate:
- Batched JS injection populates all 4 fields in `get_state()`
- Tier 1 resolves type_text conclusively with `focused_value`
- Tier 1 resolves page content with `page_heading` token match
- Tier 1 resolves page content with `page_title` token match
- Tier 1 falls through to Tier 2 when JS fields are `None`
- AX confidence varies by match quality (exact vs. partial vs. weak)
- Partial AX match (confidence < 0.9) does NOT skip pre-click validation
- Exact AX match (confidence >= 0.9) skips pre-click validation
- Config flag `js_verification_enabled=False` suppresses all JS fields
- Full verify() call resolves at Tier 1 for browser type_text (no vision call)
- Full verify() call resolves at Tier 1 for page heading match (no vision call)
- Graceful degradation when subprocess times out

---

## 3. Exact Interfaces

### 3.1 Engineer 1 Interfaces

#### `AppleScriptActuator._get_browser_js_batch()`

```python
def _get_browser_js_batch(self, app_name: str) -> dict:
    """Run a batched JS expression returning multiple DOM values as JSON.

    Executes a single JSON.stringify() call in the frontmost browser tab
    containing focused_value, selected_text, page_title, and page_heading.

    Args:
        app_name: The application name (e.g., "Safari", "Google Chrome").

    Returns:
        Dict with string keys: "focused_value", "selected_text",
        "page_title", "page_heading". Values are str or empty str.
        Returns empty dict {} on any failure: non-browser, timeout,
        JSON parse error, subprocess error.
    """
```

#### `AppleScriptActuator.get_page_title()`

```python
def get_page_title(self) -> Optional[str]:
    """Get document.title from frontmost browser tab.

    Returns:
        Page title string, or None for non-browser apps, timeout, or error.
    """
```

#### `AppleScriptActuator.get_page_heading()`

```python
def get_page_heading(self) -> Optional[str]:
    """Get document.querySelector('h1')?.textContent from frontmost browser tab.

    Returns:
        First h1 element text, or None for non-browser apps, no h1, timeout, or error.
    """
```

#### `get_state()` return dict (updated)

```python
{
    "app_name": str,
    "app_bundle": str,
    "window_title": str,
    "browser_url": str,
    "window_x": int,
    "window_y": int,
    "window_w": int,
    "window_h": int,
    # --- JS-injected fields (browser only, gated on js_verification_enabled) ---
    "focused_value": Optional[str],   # document.activeElement.value || .textContent
    "selected_text": Optional[str],   # window.getSelection().toString()
    "page_title": Optional[str],      # document.title                        [NEW]
    "page_heading": Optional[str],    # document.querySelector('h1').textContent [NEW]
}
```

### 3.2 Engineer 2 Interfaces

#### `GroundingRouter._ground_accessibility_match()`

```python
def _ground_accessibility_match(
    self, elem: Any, match_score: float = 1.0
) -> Optional[GroundingResult]:
    """Convert AX element to GroundingResult with calibrated confidence.

    Args:
        elem: An AXElement (or mock) with .center, .role, .title, etc.
        match_score: Raw score from _element_match_score(), range [0.0, 1.0+].
            Default 1.0 preserves backward compat for callers that don't
            compute score.

    Returns:
        GroundingResult with confidence = 0.6 + 0.35 * min(match_score, 1.0),
        or None if elem is None or has no center.
    """
```

#### `GroundingRouter._compute_match_score()`

```python
def _compute_match_score(self, description: str, elem: Any) -> float:
    """Compute match score using AccessibilityBridge._element_match_score().

    Follows the MagicMock detection pattern from _get_accessibility_matches()
    to support both real and mocked accessibility backends.

    Args:
        description: The natural language element description.
        elem: The AXElement candidate.

    Returns:
        Float score in range [0.0, 1.0+]. Returns 1.0 if scoring is
        unavailable (no accessibility backend, no score_fn, exception).
    """
```

### 3.3 Verifier Page Content Matcher (Engineer 1)

No new public method. The matcher is internal logic within `_verify_tier1()`. Contract:

- **Input**: `verify_lower` (str), `state.get("page_title")` (Optional[str]), `state.get("page_heading")` (Optional[str])
- **Output**: `Optional[Tuple[bool, str]]`
  - `(True, evidence)` — page content tokens match verify condition
  - `None` — fields are `None`, or no token overlap, or verify text has no extractable tokens

Note: This matcher never returns `(False, ...)`. Page content mismatch is inconclusive, not a failure — the page might have content that vision can verify but tokens can't.

---

## 4. Config Additions

### No new config fields required.

The existing `js_verification_enabled` flag covers all JS injection paths:

| Field | Type | Default | Env var | Line | Status |
|-------|------|---------|---------|------|--------|
| `js_verification_enabled` | `bool` | `True` | `AGENT_JS_VERIFICATION_ENABLED` | 467-470 | **Existing** — gates `focused_value`, `selected_text`, and now also `page_title`, `page_heading` |

**Rationale**: The PRD (AC-6) codifies that this flag defaults to `True`. Adding separate flags for `page_title`/`page_heading` would create a config proliferation problem without addressing a real use case. If JS injection causes problems, the user disables one flag and all JS fields are suppressed together.

The AX confidence calibration (Change 2) has no feature gate. The formula `0.6 + 0.35 * score` with default `match_score=1.0` produces `0.95` — identical to the current hardcoded value. The calibration activates only when `_element_match_score()` is available, which it always is in production. No flag needed because the default behavior is unchanged.

---

## 5. Testing Strategy

### 5.1 Engineer 1: `tests/unit/test_js_verification.py` (~16 tests)

This file already exists with 11 tests. Engineer 1 adds ~5 new tests and may modify existing ones.

| # | Test | AC | Description |
|---|------|----|-------------|
| 1 | `test_get_browser_js_batch_safari` | AC-1,2,10 | Patch subprocess; verify JSON parsed for Safari |
| 2 | `test_get_browser_js_batch_chrome` | AC-1,2,10 | Same for Chrome |
| 3 | `test_get_browser_js_batch_non_browser` | AC-15 | Non-browser returns `{}` |
| 4 | `test_get_browser_js_batch_timeout` | AC-5,15 | TimeoutExpired returns `{}` |
| 5 | `test_get_browser_js_batch_invalid_json` | AC-15 | Malformed JSON returns `{}` |
| 6 | `test_get_state_populates_page_title` | AC-10 | State dict has `page_title` for Safari |
| 7 | `test_get_state_populates_page_heading` | AC-10 | State dict has `page_heading` for Chrome |
| 8 | `test_get_state_js_disabled` | AC-6 | `js_verification_enabled=False` suppresses all 4 fields |
| 9 | `test_get_state_batch_populates_all_four` | AC-3,10 | Single subprocess call populates focused_value + selected_text + page_title + page_heading |
| 10 | `test_get_state_empty_string_becomes_none` | AC-15 | Empty JS values become `None` in state dict |
| 11 | `test_get_page_title_delegates_to_get_state` | AC-10 | Standalone method returns page_title |
| 12 | `test_get_page_heading_delegates_to_get_state` | AC-10 | Standalone method returns page_heading |
| 13 | `test_tier1_page_heading_token_match` | AC-11 | Verifier returns `(True, ...)` when heading tokens overlap verify |
| 14 | `test_tier1_page_title_token_match` | AC-11 | Same for page_title |
| 15 | `test_tier1_page_none_returns_none` | AC-12 | Both fields None -> returns None (inconclusive) |
| 16 | `test_tier1_page_no_token_overlap_returns_none` | AC-11 | Fields populated but no overlap -> None |

### 5.2 Engineer 2: `tests/unit/test_ax_confidence.py` (~8 tests)

New file.

| # | Test | AC | Description |
|---|------|----|-------------|
| 1 | `test_exact_match_confidence` | AC-7,9 | score=1.0 -> confidence=0.95 |
| 2 | `test_substring_match_confidence` | AC-7,9 | score=0.9 -> confidence=0.915 |
| 3 | `test_token_overlap_confidence` | AC-7,9 | score=0.7 -> confidence=0.845 |
| 4 | `test_zero_score_confidence` | AC-9 | score=0.0 -> confidence=0.6 |
| 5 | `test_capped_score_confidence` | AC-9 | score=1.07 (focused+enabled bonus) -> confidence=0.95 |
| 6 | `test_compute_match_score_no_accessibility` | AC-7 | No accessibility backend -> returns 1.0 |
| 7 | `test_compute_match_score_mock_detection` | AC-7 | MagicMock accessibility with explicit score_fn works |
| 8 | `test_find_element_uses_calibrated_confidence` | AC-8 | `find_element()` returns varying confidence based on match quality |

### 5.3 Engineer 3: `tests/integration/test_speed_phase1.py` (~12 tests)

New file. Integration tests using mocked subprocess and AX but real `StepVerifier` and `GroundingRouter`.

| # | Test | AC | Description |
|---|------|----|-------------|
| 1 | `test_type_text_tier1_resolves_no_vision` | AC-4 | Full verify() for type_text with populated focused_value -> Tier 1, vision never called |
| 2 | `test_type_text_empty_falls_to_tier2` | AC-4 | focused_value=None -> Tier 2 vision called |
| 3 | `test_page_heading_tier1_resolves_no_vision` | AC-11 | Full verify() for click with page_heading match -> Tier 1 |
| 4 | `test_page_title_tier1_resolves_no_vision` | AC-11 | Same for page_title |
| 5 | `test_page_fields_none_falls_to_tier2` | AC-12 | page_title=None, page_heading=None -> Tier 2 |
| 6 | `test_ax_exact_match_high_confidence` | AC-7 | GroundingRouter returns confidence=0.95 for exact match |
| 7 | `test_ax_partial_match_lower_confidence` | AC-8 | GroundingRouter returns confidence<0.9 for partial match |
| 8 | `test_js_disabled_suppresses_all_fields` | AC-6 | Config flag False -> no JS fields in state |
| 9 | `test_batch_js_single_subprocess_call` | AC-5 | Verify only 1 subprocess.run call for all 4 JS fields |
| 10 | `test_subprocess_timeout_graceful` | AC-15 | TimeoutExpired -> all fields None, no exception |
| 11 | `test_page_token_match_needs_two_tokens` | AC-11 | Single short token (3-4 chars) does NOT match |
| 12 | `test_page_token_match_single_long_token` | AC-11 | Single 5+ char token DOES match |

### 5.4 Test Config Pattern

All test files must use:

```python
def _make_config(**overrides) -> AgentConfig:
    defaults = dict(model_provider="local")
    defaults.update(overrides)
    return AgentConfig(**defaults)
```

Per codebase doc Section 10.6: `.env` leaks `AGENT_MODEL_PROVIDER=anthropic` into pydantic-settings. Pinning `model_provider="local"` prevents API key validation errors.

---

## 6. Feature Gates

| Gate | Controls | Default | Fallback when off |
|------|----------|---------|-------------------|
| `js_verification_enabled` | All JS fields in `get_state()`: `focused_value`, `selected_text`, `page_title`, `page_heading` | `True` | Fields absent from state dict. Tier 1 type_text matcher returns None (inconclusive). Tier 1 page content matcher returns None. Both escalate to Tier 2 vision. |
| (none) | AX confidence calibration | Always on | `_compute_match_score()` returns `1.0` when `_element_match_score` is unavailable -> `confidence = 0.6 + 0.35 * 1.0 = 0.95` (identical to current hardcoded value). |

---

## 7. Backward Compatibility

All changes are additive. Existing behavior is preserved by default.

| Change | Backward compatibility mechanism |
|--------|----------------------------------|
| **Batched JS injection** | `_get_browser_js_batch()` is a new method. The old `get_active_element_value()` and `get_selected_text()` standalone methods remain but delegate to `get_state()` to avoid duplication. Callers of these standalone methods see identical return values. |
| **New state dict keys** | `page_title` and `page_heading` are new keys. Code that uses `state.get("page_title")` on old state dicts gets `None` — no crash. The verifier matcher checks `is not None` before using these fields. |
| **AX confidence calibration** | Default `match_score=1.0` parameter in `_ground_accessibility_match()` means any caller that doesn't pass a score gets `confidence = 0.6 + 0.35 * 1.0 = 0.95` — identical to the current hardcoded value. The `_ground_accessibility()` method and `find_element()` are the only callers, and both are updated to pass the computed score. |
| **Page content matcher** | New conditional block inserted before `return None`. Existing matchers (activate_app, open_url, type_text, scroll) execute first. The new matcher only fires when none of the existing matchers returned a result. |
| **Existing tests** | AC-13 requires all ~1456 tests pass. The batched JS changes `get_state()` output format (dict values come from JSON batch instead of individual calls), but the keys and types are identical. Test mocks that patch `subprocess.run` may need adjustment if they expect a specific number of subprocess calls — Engineer 1 must verify. |

---

## 8. Codebase Research Integration

### 8.1 Batching JS Calls (Codebase Doc Section 13.1)

**Decision: Batch.** The codebase doc recommends batching JS calls into a single `JSON.stringify()` call. Evaluation:

- **Saves 3 subprocess calls**: Current `get_state()` makes 2 JS calls (focused_value, selected_text). Adding page_title and page_heading would be 4 calls total. Batching reduces to 1.
- **Complexity cost**: One `json.loads()` call with try/except. The JS expression is straightforward — no dynamic content, no user input, no escaping concerns.
- **Risk**: If `JSON.stringify()` returns `missing value` from AppleScript (e.g., when JS execution fails silently), the entire batch fails and all 4 fields are `None`. This is acceptable — the current individual calls also return `None` on failure.
- **Verdict**: The 3-subprocess savings (~60-150ms) justifies the minimal complexity. Batch.

### 8.2 MagicMock Detection in `_compute_match_score()` (Codebase Doc Section 12.5)

The `_get_accessibility_matches()` method has an elaborate MagicMock detection pattern (lines 307-323) that distinguishes between real `AccessibilityBridge` instances and MagicMock test doubles. This is necessary because MagicMock auto-generates methods that would bypass `getattr(type(...), ...)` checks.

`_compute_match_score()` must follow the same pattern:

```python
is_mock = "MagicMock" in type(self.accessibility).__name__
explicit_attrs = vars(self.accessibility) if is_mock else {}
extract_target = (
    explicit_attrs.get("_extract_match_target")
    if is_mock
    else getattr(type(self.accessibility), "_extract_match_target", None)
)
score_fn = (
    explicit_attrs.get("_element_match_score")
    if is_mock
    else getattr(type(self.accessibility), "_element_match_score", None)
)
```

This is a direct copy of the pattern from `_get_accessibility_matches()`. The alternative (accessing `self.accessibility._element_match_score` directly) would silently succeed on MagicMock but return a MagicMock callable that returns another MagicMock, not a float — causing downstream confidence calculation to fail with a TypeError.

### 8.3 Static JS Strings and `_escape_for_applescript()` (Codebase Doc Section 12.2)

The codebase doc notes that `_escape_for_applescript()` exists but the JS strings we inject are static. Evaluation:

- The batched JS expression uses only single quotes internally: `document.querySelector('h1')`. AppleScript's double-quoted strings accept single quotes without escaping.
- No dynamic user input is embedded in the JS string.
- The `_get_browser_js()` method embeds the JS in an AppleScript double-quoted string via f-string: `f'"{js}"'`. Our JS contains no double quotes.
- **Verdict**: No escaping needed for the JS expression itself. The `_get_browser_js()` method handles AppleScript quoting.

### 8.4 `focused_text` Ghost Field (Codebase Doc Section 12.3)

The verifier's type_text matcher at line 507 checks `("focused_value", "focused_text", "selected_text")`. The `focused_text` key is never populated by `get_state()`. This is a dormant inconsistency. Engineer 1 should NOT fix this — it's out of scope, harmless (the loop skips `None` values), and fixing it risks test breakage.

---

## 9. Observability

Each new code path emits a structlog event for debugging and metrics.

### Engineer 1 Events

| Event | Logger | Fields | When |
|-------|--------|--------|------|
| `browser_js_batch_result` | `slog` (applescript_actuator) | `app_name`, `keys_populated: list[str]`, `latency_ms: float` | After successful `_get_browser_js_batch()` |
| `browser_js_batch_error` | `slog` (applescript_actuator) | `app_name`, `error: str`, `latency_ms: float` | On timeout, parse error, or subprocess failure |
| `tier1_page_content_match` | `slog` (verifier) | `field: str` ("page_title" or "page_heading"), `overlap_tokens: set`, `verify_text: str` | When page content matcher returns `(True, ...)` |

### Engineer 2 Events

| Event | Logger | Fields | When |
|-------|--------|--------|------|
| `ax_confidence_calibrated` | `logger` (grounding_router) | `description: str`, `raw_score: float`, `confidence: float`, `elem_role: str`, `elem_title: str` | After `_ground_accessibility_match()` computes calibrated confidence |
| `ax_score_unavailable` | `logger` (grounding_router) | `description: str`, `reason: str` | When `_compute_match_score()` falls back to 1.0 |

### Engineer 3 Events

Integration tests don't add production events. They verify the events above are emitted correctly.

---

## 10. Implementation Sequencing

```
Week 1, Day 1-2:  Engineer 1 ships Changes A-E (JS batch + page content matcher)
Week 1, Day 1-2:  Engineer 2 ships Changes F-H (AX calibration) — parallel with E1
Week 1, Day 3:    Engineer 3 writes integration tests against merged E1+E2
Week 1, Day 3:    Full pytest suite (AC-13: all ~1456 tests pass)
```

Engineer 1 and Engineer 2 have zero code dependencies on each other:
- Engineer 1 modifies `applescript_actuator.py` and `verifier.py`
- Engineer 2 modifies `grounding_router.py`
- No shared files

Engineer 3 depends on both E1 and E2 being merged before integration tests can run against real code. However, Engineer 3 can write test scaffolding (mocks, fixtures, test skeletons) in parallel with E1/E2.

---

## 11. Acceptance Criteria Traceability

| AC | Change | Engineer | Validated by test(s) |
|----|--------|----------|---------------------|
| AC-1 | get_active_element_value via JS | E1 | test_get_browser_js_batch_safari, test_get_browser_js_batch_chrome |
| AC-2 | get_selected_text via JS | E1 | test_get_browser_js_batch_safari, test_get_browser_js_batch_chrome |
| AC-3 | get_state populates focused_value + selected_text | E1 | test_get_state_batch_populates_all_four |
| AC-4 | Tier 1 type_text resolves conclusively | E1, E3 | test_tier1_page_heading_token_match, test_type_text_tier1_resolves_no_vision |
| AC-5 | JS injection <200ms | E1, E3 | test_batch_js_single_subprocess_call (verifies single call) |
| AC-6 | js_verification_enabled defaults True | E1 | test_get_state_js_disabled |
| AC-7 | AX confidence calibrated from match_score | E2 | test_exact_match_confidence through test_zero_score_confidence |
| AC-8 | Only exact matches skip pre-click validation | E2, E3 | test_ax_partial_match_lower_confidence |
| AC-9 | Formula: 0.6 + 0.35 * min(score, 1.0) | E2 | test_exact_match_confidence, test_capped_score_confidence |
| AC-10 | page_title + page_heading in get_state | E1 | test_get_state_populates_page_title, test_get_state_populates_page_heading |
| AC-11 | Tier 1 checks page tokens against verify | E1, E3 | test_tier1_page_heading_token_match, test_tier1_page_title_token_match |
| AC-12 | None fields fall through to Tier 2 | E1, E3 | test_tier1_page_none_returns_none, test_page_fields_none_falls_to_tier2 |
| AC-13 | All existing tests pass | E3 | Full pytest suite |
| AC-14 | 8+ tests per change | E1, E2, E3 | Test counts: E1=16, E2=8, E3=12 |
| AC-15 | Graceful fallback on timeout/error | E1, E3 | test_get_browser_js_batch_timeout, test_subprocess_timeout_graceful |
