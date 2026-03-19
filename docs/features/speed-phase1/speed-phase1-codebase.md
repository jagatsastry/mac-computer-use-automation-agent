# Speed Phase 1 — Codebase Analysis

Deep analysis of every file, function, and integration point relevant to the three engineering workstreams:
1. **Engineer 1**: JS injection layer + type_text verification
2. **Engineer 2**: AX confidence calibration
3. **Engineer 3**: JS-enriched page state for click verification

---

## 1. AppleScript Actuator (`src/automation_agent/actuator/applescript_actuator.py`)

### 1.1 Class Structure

- **`AppleScriptActuator.__init__(self, config)`** (line 23): Stores `AgentConfig` instance on `self.config`. Config may be `None`.
- **`TIMEOUT_SECONDS = 10`** (line 21): Default subprocess timeout. JS helpers use shorter timeouts (2-3s).

### 1.2 JS Injection Infrastructure (EXISTING)

**`_get_browser_js(self, app_name, js) -> Optional[str]`** (line 410-442):
- Core JS execution helper. Routes to Safari (`do JavaScript`) or Chrome (`execute ... javascript`).
- Returns `None` for non-browser apps, timeouts, or non-zero exit codes.
- Timeout: **2 seconds** (line 436).
- Catches `TimeoutExpired` and `OSError`.
- **Pattern to follow for any new JS injection**: Construct JS expression string, pass to this method with the app_name from `get_state()`.

**`get_scroll_position(self, axis) -> Optional[int]`** (line 364-408):
- Follows a different pattern: calls `get_state()` first to get `app_name`, then builds its own AppleScript (NOT using `_get_browser_js`). It uses `subprocess.run` directly with a 3s timeout.
- Duplicative approach. New code should prefer `_get_browser_js()`.

**`get_active_element_value(self) -> Optional[str]`** (line 444-456):
- Uses `_get_browser_js` correctly. JS: `"document.activeElement.value || document.activeElement.textContent || ''"`.
- Calls `get_state()` internally (an extra subprocess call).

**`get_selected_text(self) -> Optional[str]`** (line 458-470):
- Same pattern as above. JS: `"window.getSelection().toString()"`.
- Also calls `get_state()` internally.

### 1.3 `get_state()` Method (line 497-567)

This is the **primary integration point for Engineer 1 and Engineer 3**.

**Current dict keys returned:**
```python
{
    "app_name": str,
    "app_bundle": str,
    "window_title": str,
    "browser_url": str,       # Only populated for browsers
    "window_x": int,
    "window_y": int,
    "window_w": int,
    "window_h": int,
    # --- JS-injected fields (browser only, gated on js_verification_enabled) ---
    "focused_value": Optional[str],   # document.activeElement.value || .textContent
    "selected_text": Optional[str],   # window.getSelection().toString()
}
```

**Execution flow:**
1. Runs a single AppleScript block to get app_name, bundle, window_title, position, size (lines 508-536).
2. Parses pipe-separated output (line 536: `split("|", 6)`).
3. If browser (line 549): calls `_get_browser_url()` — another subprocess.
4. If browser AND `js_verification_enabled` (lines 552-564): calls `_get_browser_js()` **twice** — one for `focused_value`, one for `selected_text`.

**Total subprocess calls for a browser get_state():** 4 (state + URL + focused_value + selected_text).

**Constraint: Each `_get_browser_js` call = 1 subprocess.run = ~20-50ms real, up to 2s timeout.**
Adding `page_title` and `page_heading` would add 2 more subprocess calls = 6 total. Consider batching JS expressions into a single call.

**JS gating mechanism (line 552-554):**
```python
js_enabled = True
if self.config is not None:
    js_enabled = getattr(self.config, "js_verification_enabled", True)
```
Uses `getattr` with default `True` — defensive against missing config fields. Any new gating should follow this pattern.

### 1.4 Browser Detection

**`_is_browser(app_name) -> bool`** (line 569-572): Static method. Checks lowered app_name against 7 browser strings: safari, chrome, firefox, arc, edge, brave, opera.

**`_get_browser_url(self, app_name) -> str`** (line 472-495): Supports Safari, Chrome, Arc. Firefox returns empty string. Uses 2s timeout.

### 1.5 String Escaping

**`_escape_for_applescript(text) -> str`** (line 348-362): Escapes backslashes, double quotes, strips newlines/CR/tabs. Static method. Any JS expression embedded in AppleScript must be sanitized through this or handled carefully with inner quoting.

**LANDMINE**: JS expressions passed to `_get_browser_js` are embedded in double-quoted AppleScript strings. Complex JS with quotes will break. Current usage only passes simple expressions. New expressions (e.g., `document.querySelector('h1')?.textContent`) would need single quotes inside double quotes — works in AppleScript, but any expression containing `"` would break.

---

## 2. StepVerifier (`src/automation_agent/orchestrator/verifier.py`)

### 2.1 Class Structure

**`StepVerifier.__init__`** (line 37-47): Takes `actuator`, `coordinator`, `logger`, `accessibility`. All optional.

### 2.2 Tier Architecture

**`verify()` method** (line 49-193): The main entry point. Execution order:
1. Empty verify check (lines 67-85): `done`/`wait_for_user` allowed; others rejected.
2. **Tier 0** (lines 90-105): Accessibility API. Returns `(bool, str)` or `None`.
3. **Tier 1** (lines 108-130): Actuator state. Returns `(bool, str)` or `None`.
4. **Tier 2** (lines 140-183): Vision screenshot. Returns `((bool, str), screenshot_b64)` or `(None, screenshot_b64)`.
5. Fallback (lines 186-193): Uses actuator result success field.

### 2.3 `_verify_tier0()` (lines 263-334) — AX Verification

Handles:
- `activate_app` + `expected_app` (lines 279-284)
- App keywords in verify text (lines 286-292)
- `type_text` with `text` param (lines 294-317): Checks `focused.value`, `focused.title`, `focused.description`.
- `click` action (lines 320-332): Checks if focused element role is in `TEXT_INPUT_AX_ROLES`.

**No page_title/page_heading check exists in Tier 0.**

### 2.4 `_verify_tier1()` (lines 336-588) — Actuator State Verification

**Critical section for all three engineers.**

**Current matchers in order:**
1. `activate_app` action (lines 352-367)
2. App keyword match in verify text (lines 370-384)
3. `open_url` action (lines 386-502): Complex URL matching with domain verification, login redirect detection, host+path matching, token fallback, window title fallback.
4. `type_text` action (lines 504-516): Checks `focused_value`, `focused_text`, `selected_text` from state dict.
5. `scroll` action (lines 518-585): Three sub-tiers (JS delta, pixel diff, actuator success).

**Line 587-588**: `return None` — anything not matched above is inconclusive.

**WHERE NEW MATCHERS GO (Engineer 3):** Between line 586 and 588. After scroll verification, before the final `return None`. New page_heading/page_title matchers would add a new conditional block here.

**Type_text matcher detail (lines 504-516):**
```python
if step.action == "type_text" and step.params.get("text"):
    expected_text = step.params["text"]
    any_populated = False
    for key in ("focused_value", "focused_text", "selected_text"):
        actual_value = state.get(key)
        if actual_value is None:
            continue
        any_populated = True
        if expected_text in str(actual_value):
            return (True, f"Actuator state {key} contains '{expected_text}'")
    if any_populated:
        return (False, f"No actuator text field contains '{expected_text}'")
```
- Iterates three keys. Returns True on first match, False only if at least one field was populated but none matched. Returns None (inconclusive) if all are None.
- **Gotcha**: The field `focused_text` is checked but never populated by `get_state()`. It's a dead field in current code. Only `focused_value` and `selected_text` are populated.

### 2.5 `_verify_tier2()` (lines 600-702) — Vision Verification

Five "sites" checked in order:
1. Click region crop (lines 622-639)
2. type_text focused field (lines 642-658)
3. open_url destination (lines 661-677)
4. Generic conditions from step (lines 680-695)
5. Fallback: screenshot without crop

Returns `(None, screenshot_b64)` when all conditions are UNCLEAR (inconclusive).

### 2.6 Helper Methods

- `_is_browser_app(app_name)` (line 225-231): Same browser list as actuator.
- `_url_tokens(url)` (line 234-246): Extracts host+path tokens for fuzzy matching.
- `_extract_base_domain(url)` (line 249-253): Strips www. prefix.
- `_build_url_condition(url)` (line 255-261): Builds vision condition string for URL verification.
- `_crop_click_region()` (line 704-731): 200px half-size square crop.
- `_tier2_conditions()` (line 591-598): Deduplicates expected_observation and verify.

---

## 3. GroundingRouter (`src/automation_agent/orchestrator/grounding_router.py`)

### 3.1 Class Structure

**`GroundingRouter.__init__`** (lines 67-75): Takes `accessibility`, `vision_coordinator`, `config`.

### 3.2 `_ground_accessibility_match()` (lines 385-404) — Hardcoded Confidence

```python
def _ground_accessibility_match(self, elem: Any) -> Optional[GroundingResult]:
    center = getattr(elem, "center", None)
    if elem is None or not center:
        return None
    return GroundingResult(
        x=center[0],
        y=center[1],
        strategy_used=GroundingStrategy.ACCESSIBILITY,
        confidence=0.95,  # <-- HARDCODED
        element_info={...},
    )
```

**THIS IS THE KEY TARGET FOR ENGINEER 2.** The hardcoded `0.95` means every AX match gets the same confidence regardless of match quality. The `_element_match_score()` from `AccessibilityBridge` is used only for **sorting** candidates (line 346 in `_get_accessibility_matches`), never for setting the grounding confidence.

### 3.3 `_get_accessibility_matches()` (lines 301-355)

Complex method that:
1. Detects mock vs real accessibility (lines 307-323) — **elaborate mock detection for testing**.
2. Calls `_extract_match_target(description)` to get `(role, text_hint)`.
3. Calls `find_elements(role=..., title_contains=...)`.
4. Falls back with `role=None` for AXStaticText.
5. Sorts by `_element_match_score()` if callable (line 345-346).
6. Truncates to `_max_candidates()` (default 12).

**The score_fn is retrieved but only used for sorting, not for confidence.**

### 3.4 `find_element()` (lines 109-194)

**Flow:**
1. Collects accessibility matches.
2. If AX-first preferred AND single clean match AND no LLM tiebreak needed: return immediately with `confidence=0.95`.
3. Optionally reorder with LLM (`_maybe_reorder_with_llm`).
4. Try strategies in order.
5. Final vision fallback.

### 3.5 `_ground_vision()` (lines 415-436)

Returns vision results with `confidence = location.get("confidence", 0.75)` — defaults to 0.75.

### 3.6 Missing: `_compute_match_score()`

The `_element_match_score()` lives on `AccessibilityBridge` (perception layer). There is no bridge method or wrapper in `GroundingRouter` that converts the score to a confidence for the `GroundingResult`. **Engineer 2 needs to add this plumbing.**

---

## 4. AccessibilityBridge (`src/automation_agent/perception/accessibility.py`)

### 4.1 `_element_match_score()` (lines 380-414)

The scoring function that Engineer 2 will use for confidence calibration:

```python
@classmethod
def _element_match_score(cls, elem: AXElement, text_hint: Optional[str]) -> float:
```

**Scoring rules:**
| Condition | Score |
|-----------|-------|
| No text_hint, element enabled | 1.0 |
| No text_hint, element disabled | 0.8 |
| Exact match (field == query) | 1.0 |
| Substring match (query in field) | 0.9 |
| Token overlap | 0.45 + 0.4 * overlap_ratio |
| Focused bonus | +0.05 |
| Enabled bonus | +0.02 |

Capped at 1.0. Fields checked: `title`, `value`, `description`.

### 4.2 `_extract_match_target()` (lines 345-378)

Parses natural language descriptions:
- Special prefixes: `"text that says X"`, `"label showing X"`, `"heading X"` -> `("AXStaticText", X)`
- Keyword matching: `"button"` -> `"AXButton"`, etc. (from `_NL_ROLE_MAP`)
- Fallback: `(None, normalized_description)`

### 4.3 `find_element_by_description()` (lines 416-458)

Public API used by `GroundingRouter._ground_accessibility()`. Returns best-scoring `AXElement` or `None`.

### 4.4 `AXElement` dataclass (lines 91-114)

Fields: `role`, `title`, `value`, `description`, `position`, `size`, `enabled`, `focused`, `children`, `raw_ref`. Property `center` computes midpoint from position+size.

---

## 5. Config (`src/automation_agent/config.py`)

### 5.1 Existing Feature Flags Relevant to Speed Phase 1

| Flag | Type | Default | Line |
|------|------|---------|------|
| `js_verification_enabled` | `bool` | `True` | 467-470 |
| `grounding_llm_routing_enabled` | `bool` | `True` | 96-99 |
| `grounding_llm_max_candidates` | `int` | `12` | 100-104 |
| `use_accessibility` | `bool` | `False` | 167-170 |
| `dual_resolution_grounding` | `bool` | `False` | 396-399 |

### 5.2 Where to Add New Flags

Follow the pattern at lines 466-470:
```python
js_verification_enabled: bool = Field(
    default=True,
    description="Enable JS injection for browser state verification (type_text, page state)",
)
```

New flags needed:
- `js_page_state_enabled` (or extend `js_verification_enabled` to cover both)
- Any AX confidence calibration toggle

**LANDMINE**: `.env` file leaks `AGENT_MODEL_PROVIDER=anthropic` into pydantic-settings during tests. All test `_make_config()` helpers must pin `model_provider="local"` to avoid API key validation errors.

---

## 6. Orchestrator Agent Integration Points

### 6.1 `AutomationAgent.__init__()` (agent.py:201-240)

Creates `StepVerifier` at line 220-225:
```python
self.verifier = StepVerifier(
    actuator=actuator,
    coordinator=coordinator,
    logger=self.logger,
    accessibility=getattr(coordinator, "accessibility", None),
)
```

Stores `self.grounding_router = grounding_router` at line 228.

### 6.2 `_find_element()` (agent.py:2522-2679)

If `self.grounding_router is not None`: delegates to `grounding_router.find_element()`, converts `GroundingResult` to `FindElementResult` (lines 2535-2556).

Otherwise: uses coordinator path with optional dual-res and crop.

### 6.3 Confidence Gating (agent.py lines 2297-2312)

After finding an element:
```python
confidence = location.confidence
threshold = self._get_confidence_threshold(step)
if confidence > 0.0 and confidence < threshold:
    # Reject low-confidence match
```

**`_get_confidence_threshold()` (line 3011-3033)**: Returns 0.9 for critical keywords (submit, pay, confirm, delete...) and 0.5 for everything else. Safe-navigation phrases exempt from high threshold.

**Implication for Engineer 2**: If AX confidence is calibrated using `_element_match_score()`, the score must be mapped to a scale where 0.5 is the baseline threshold and 0.9 is the critical threshold. A raw score of 0.45 from token overlap would be rejected for all actions.

### 6.4 Pre-click Validation (agent.py:3255-3306)

`_validate_candidate()`: Crops 128px and 512px squares, asks vision model to verify. Skipped when:
- Source is "accessibility" (agent.py line ~2314-2317)
- Confidence >= 0.9

---

## 7. Shared Models (`src/automation_agent/shared_models.py`)

### 7.1 `FindElementResult` (lines 88-111)

```python
@dataclass
class FindElementResult:
    x: int
    y: int
    confidence: float = 0.0
    source: str = ""
    raw_response: str = ""
    screen_x: Optional[int] = None
    screen_y: Optional[int] = None
    image_width: int = 0
    image_height: int = 0
```

Has `__getitem__` and `get()` for dict-like compatibility.

### 7.2 `ActionStep` (lines 114-185)

Valid actions: click, type_text, press_key, open_url, activate_app, quit_app, scroll, observe, wait_for_user, done.

### 7.3 `TEXT_INPUT_AX_ROLES` (line 17)

```python
TEXT_INPUT_AX_ROLES: frozenset[str] = frozenset({
    "AXTextField", "AXTextArea", "AXSearchField", "AXComboBox",
})
```

Used by Tier 0 click verification.

---

## 8. Test Infrastructure

### 8.1 Global Fixtures (`tests/conftest.py`)

Key fixtures:
- `mock_actuator`: Returns state with `app_name=Safari`, `window_title=Google`.
- `mock_coordinator`: AsyncMock with `find_element`, `describe_screen`, `verify_condition`, `capture_screenshot`.
- `mock_planner`: With `plan`, `replan`, `check_infeasibility`.
- `tmp_log_dir`: Temp directory for event logs.
- `tmp_skill_dir`: Temp directory for skill files.

### 8.2 `_make_config()` Pattern

Every test file that creates an `AgentConfig` uses this pattern:
```python
def _make_config(**overrides) -> AgentConfig:
    defaults = dict(model_provider="local")
    defaults.update(overrides)
    return AgentConfig(**defaults)
```

**CRITICAL**: Must always include `model_provider="local"` to prevent `.env` leaking `anthropic` provider and triggering API key validation.

### 8.3 Verifier Test Patterns (`tests/unit/test_verifier.py`)

- **Fixtures**: `mock_act` (MagicMock actuator), `mock_coord` (AsyncMock coordinator), `mock_accessibility` (MagicMock bridge).
- **Helper**: `_make_jpeg_b64()` creates real JPEG base64 for screenshot tests.
- **Test structure**: Nested classes by tier/feature. Each test creates `StepVerifier`, calls `verify()`, asserts on `result.success`, `result.verification_method`, `result.evidence`.
- **Direct tier testing**: Tests call `verifier._verify_tier1(step, mock_act)` directly (returns tuple or None).

### 8.4 Grounding Router Test Patterns (`tests/orchestrator/test_grounding_router.py`)

- **Fixtures**: `mock_ax_element` (AXElement), `mock_accessibility` (MagicMock bridge), `mock_vision` (AsyncMock coordinator), `router` (GroundingRouter with both).
- **Strategy**: Tests classify(), then find_element() cascades.
- **Agent integration tests**: Create `AutomationAgent` inline with MagicMock config.
- **Confidence assertion**: `assert result.confidence == 0.95` for AX, `0.75` for vision.

### 8.5 JS Verification Test Patterns (`tests/unit/test_js_verification.py`)

- Patches `subprocess.run` at the actuator module level.
- Uses `_make_subprocess_result()` helper for mock subprocess results.
- Tests `_get_browser_js`, `get_state` JS fields, and Tier 1 resolution.
- Tests config flag gating (`js_verification_enabled=False`).

### 8.6 Test Config (`pyproject.toml`)

```toml
asyncio_mode = "auto"
markers = ["unit", "integration", "e2e", "manual", "legacy"]
addopts = ["-v", "--strict-markers"]
```

### 8.7 Test Count

Current test files touching relevant code:
- `tests/unit/test_verifier.py` — 22 test methods
- `tests/orchestrator/test_grounding_router.py` — 25 test methods
- `tests/unit/test_js_verification.py` — 11 test methods
- `tests/unit/test_vision_arch_improvements.py` — 79 tests (Rec 1-5)
- `tests/unit/test_grounding_adversarial.py` — adversarial grounding tests
- `tests/unit/test_grounding_model.py` — grounding model tests
- `tests/unit/test_grounding_pipeline.py` — pipeline tests

---

## 9. Patterns to Follow

### 9.1 JS Injection Pattern (for Engineer 1 and Engineer 3)

Follow `get_state()` lines 556-564:
```python
state["focused_value"] = self._get_browser_js(
    app_name,
    "document.activeElement.value || document.activeElement.textContent || ''",
)
```

For `page_title`: `"document.title || ''"`
For `page_heading`: `"(document.querySelector('h1') || {}).textContent || ''"`

### 9.2 Config Flag Pattern

Follow lines 466-470 of `config.py`:
```python
my_flag: bool = Field(
    default=True,
    description="Description here",
)
```

### 9.3 Tier 1 Matcher Pattern (for Engineer 3)

Follow the `type_text` block at verifier.py lines 504-516. A new page heading/title matcher would:
1. Check a regex pattern in `verify_lower` (e.g., `"page title"`, `"heading"`, `"h1"`).
2. Read the field from state dict.
3. Return `(True, evidence)` or `(False, evidence)` or `None`.

### 9.4 Test Pattern for Actuator

Follow `test_js_verification.py`:
- Patch `subprocess.run` at module level.
- Use `_make_subprocess_result()` helper.
- Test each browser separately.
- Test timeout/error paths.
- Test config gating.

### 9.5 Test Pattern for Verifier

Follow `test_verifier.py`:
- Create `StepVerifier` with mocked actuator.
- Create `ActionStep` with action, params, verify.
- Call `_verify_tier1(step, mock_act)` directly for unit tests.
- Call `verify(step, actuator_result)` for integration tests.
- Assert on `(bool, str)` tuple for tier tests, `StepResult` for verify tests.

### 9.6 Test Pattern for GroundingRouter

Follow `test_grounding_router.py`:
- Mock `accessibility` as MagicMock with explicit methods.
- For `_element_match_score` access, note the elaborate mock detection in `_get_accessibility_matches()` (lines 307-323).
- Set `ax._element_match_score = MagicMock(return_value=0.9)` as instance attribute.

---

## 10. Patterns to AVOID

### 10.1 Duplicate `get_state()` calls

`get_active_element_value()` and `get_selected_text()` each call `get_state()` internally. Never call these standalone methods when `get_state()` already has the data — it would mean 2-3 extra subprocess calls.

### 10.2 Bare `subprocess.run` for JS

Don't copy the `get_scroll_position()` pattern (direct subprocess). Use `_get_browser_js()` instead.

### 10.3 Non-gated JS injection

Always gate behind `js_verification_enabled` (or a new flag). Never inject JS unconditionally.

### 10.4 Single-key state dict checks

Don't check `state.get("focused_value")` without handling `None`. The existing type_text matcher correctly handles `None` vs empty string vs populated.

### 10.5 Hard-coded confidence without match quality signal

Don't replicate the `confidence=0.95` pattern. The whole point of Engineer 2 is to calibrate this.

### 10.6 Tests without `model_provider="local"`

Every `_make_config()` MUST include `model_provider="local"` or tests break when `.env` has `AGENT_MODEL_PROVIDER=anthropic`.

---

## 11. Integration Points Summary

### Engineer 1: JS Injection Layer

| What | Where | How |
|------|-------|-----|
| Add `page_title` to `get_state()` | `applescript_actuator.py:556-564` | Add `_get_browser_js(app_name, "document.title || ''")` |
| Add `page_heading` to `get_state()` | `applescript_actuator.py:556-564` | Add `_get_browser_js(app_name, "...h1...")` |
| Consider batching JS calls | `applescript_actuator.py:410-442` | Create `_get_browser_js_batch()` or multi-expression JS |
| Gate behind config flag | `config.py:466-470` | Reuse `js_verification_enabled` or add new flag |
| Test | Follow `test_js_verification.py` | Patch subprocess, test per-browser, test gating |

### Engineer 2: AX Confidence Calibration

| What | Where | How |
|------|-------|-----|
| Use `_element_match_score()` result | `grounding_router.py:385-404` | Replace hardcoded 0.95 with score-based confidence |
| Add `_compute_match_score()` | `grounding_router.py` (new method) | Bridge between `_element_match_score` and `GroundingResult.confidence` |
| Pass score through `_get_accessibility_matches` | `grounding_router.py:301-355` | Return `(element, score)` tuples or attach score to element |
| Map score to confidence scale | New logic | Must work with thresholds: 0.5 default, 0.9 critical |
| Test | Follow `test_grounding_router.py` | Assert `result.confidence` varies by match quality |

### Engineer 3: JS-Enriched Click Verification

| What | Where | How |
|------|-------|-----|
| Add page_heading/page_title Tier 1 matcher | `verifier.py:586-588` | New conditional block before final `return None` |
| Pattern-match verify text | `verifier.py` | Regex for "page title", "heading", "h1 says", etc. |
| Read from state dict | `verifier.py:348` | `state.get("page_title")`, `state.get("page_heading")` |
| Test | Follow `test_verifier.py` | Mock actuator state with page_title/page_heading fields |

---

## 12. Constraints and Landmines

### 12.1 Subprocess Cost

Each `_get_browser_js()` call is a separate `subprocess.run`. Adding 2 more fields (page_title, page_heading) to `get_state()` means 6 subprocess calls per `get_state()` for browsers. The verifier calls `get_state()` once per tier-1 check, and the orchestrator calls it in the execute loop. Consider:
- Batching JS calls into a single subprocess (e.g., JSON.stringify({title: document.title, heading: ...}))
- Making new fields lazy (only fetched when needed)

### 12.2 AppleScript String Quoting

JS expressions embedded in AppleScript must avoid double quotes. Use single quotes inside JS: `"(document.querySelector('h1') || {}).textContent || ''"`. If the JS itself needs double quotes, it must be escaped with `_escape_for_applescript()`.

### 12.3 `focused_text` Ghost Field

The verifier checks `state.get("focused_text")` at line 507, but `get_state()` never populates `focused_text`. It only populates `focused_value` and `selected_text`. This is a dormant inconsistency that won't break anything but is confusing.

### 12.4 `get_state()` Called Multiple Times

`_verify_tier1()` calls `actuator.get_state()` once (line 348). But `get_active_element_value()` and `get_selected_text()` each call it again internally. The `get_state()` method already returns these fields, so standalone helpers are redundant for callers that have the state dict.

### 12.5 Mock Detection in GroundingRouter

`_get_accessibility_matches()` has elaborate MagicMock detection (lines 307-323) to support both real and mocked accessibility backends. New code that accesses `_element_match_score` must follow this pattern or tests will fail.

### 12.6 Confidence Threshold Interaction

The orchestrator has two thresholds: 0.5 (default) and 0.9 (critical). If AX confidence is calibrated from `_element_match_score()`:
- Score 1.0 (exact match) → confidence should be ~0.95-1.0
- Score 0.9 (substring match) → confidence ~0.85-0.90
- Score 0.45+overlap → confidence ~0.5-0.7
- Token overlap only → confidence may drop below 0.5 → rejected for all actions

The mapping function must ensure that "good enough for clicking" matches land above 0.5.

### 12.7 Verifier Returns Tuple or None

All tier methods return `Optional[Tuple[bool, str]]`. The tuple is `(success, evidence_string)`. `None` means inconclusive. New matchers must follow this exact contract.

---

## 13. RECOMMENDATIONS

### 13.1 Engineer 1: JS Injection Layer

**Batch JS calls.** Instead of 4+ separate subprocess calls in `get_state()`, create a single `_get_browser_js_batch()` that runs a combined JS expression and returns a JSON object:
```javascript
JSON.stringify({
    focused_value: document.activeElement.value || document.activeElement.textContent || '',
    selected_text: window.getSelection().toString(),
    page_title: document.title || '',
    page_heading: (document.querySelector('h1') || {}).textContent || ''
})
```
This reduces 4 subprocess calls to 1. Parse the JSON result in Python and populate all fields at once.

**Keep backward compatibility.** The existing `get_active_element_value()` and `get_selected_text()` standalone methods should remain but can delegate to `get_state()` to avoid duplication.

**Gate new fields.** Use `js_verification_enabled` for all JS fields (don't create a separate flag per field). The existing gating pattern at lines 552-554 already handles this.

### 13.2 Engineer 2: AX Confidence Calibration

**Add `_compute_match_score()` to GroundingRouter.** This method should:
1. Accept the element and the original description.
2. Call `_extract_match_target(description)` to get `text_hint`.
3. Call `_element_match_score(elem, text_hint)` to get the raw score.
4. Map the raw score to a grounding confidence using a simple linear or piecewise mapping.

**Suggested mapping:**
```python
confidence = 0.4 + 0.55 * raw_score  # 0.0 -> 0.4, 0.5 -> 0.675, 0.9 -> 0.895, 1.0 -> 0.95
```
This ensures:
- Perfect matches (1.0) get ~0.95 (same as current hardcoded value)
- Good substring matches (0.9) get ~0.90 (passes critical threshold)
- Weak matches (0.5) get ~0.68 (passes default threshold but not critical)
- Very weak matches (0.2) get ~0.51 (barely passes default threshold)

**Preserve the mock detection pattern** in `_get_accessibility_matches()` — the same pattern is needed for accessing `_element_match_score`.

### 13.3 Engineer 3: JS-Enriched Click Verification

**Add two new matchers to `_verify_tier1()`.** Place them after the scroll block (line 586) and before `return None` (line 588):

1. **Page title matcher**: Trigger on `"page title"` or `"title is"` or `"title contains"` in verify text. Check `state.get("page_title")`.
2. **Page heading matcher**: Trigger on `"heading"`, `"h1"`, `"page heading"` in verify text. Check `state.get("page_heading")`.

**Follow the type_text matcher pattern**: Return `(True, evidence)` on match, `None` on no data. Do NOT return `(False, ...)` when the field is `None` — that should be inconclusive (escalate to Tier 2).

### 13.4 Test Strategy

Each engineer should create a dedicated test file:
- `tests/unit/test_js_batch_injection.py` (Engineer 1)
- `tests/unit/test_ax_confidence_calibration.py` (Engineer 2)
- `tests/unit/test_page_state_verification.py` (Engineer 3)

Follow the existing `_make_config(model_provider="local")` pattern. Mock subprocess for actuator tests. Use MagicMock for actuator in verifier tests. Use real `AXElement` dataclass in grounding router tests.

### 13.5 Integration Order

1. **Engineer 1 first**: JS injection layer changes `get_state()` output. Both Engineer 2 and Engineer 3 need stable state dict.
2. **Engineer 2 and 3 can proceed in parallel** after Engineer 1's `get_state()` changes land.
3. **Verify no regressions**: Run `pytest tests/unit/` after each change. Current count should be ~522 tests passing.
