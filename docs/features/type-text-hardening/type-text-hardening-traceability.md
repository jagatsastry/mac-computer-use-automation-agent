---
name: type-text-hardening-traceability
---

# Type-Text Hardening: Traceability Matrix (Slice 1)

| AC | Description | Test Class | Test Count | Production File | Status |
|----|-------------|-----------|------------|-----------------|--------|
| AC-1 | Direct search URL in buy_on_target.md | TestAC1DirectSearchURL | 4 | (read-only verification) | PASS |
| AC-2 | Compiler element extraction from "in the" clause | TestAC2CompilerElementExtraction | 8 | (read-only verification) | PASS |
| AC-3 | URL encoding via urllib.parse | TestAC3URLEncoding | 10 | `agent.py:1770-1798` | PASS |
| AC-4 | Word-boundary matching in required-keywords | TestAC4WordBoundaryMatching | 6 | `matcher.py:44-57` (logging) | PASS |
| AC-5 | Domain injection scoped to matching domain | TestAC5DomainInjectionScoping | 11 | `agent.py:1531-1556` | PASS |

## Production Code Changes

### agent.py (AC-3: URL encoding)
- **Removed**: Deferred `from urllib.parse import ...` (line 1750) -- uses module-level `import urllib.parse`
- **Removed**: `try/except` belt-and-suspenders (CQ-3) -- `urlparse()` never raises on string input
- **Removed**: `str.replace(" ", "%20")` fallback in both branches
- **Added**: Component-based encoding for non-query paths: `quote(unquote(parsed.path), safe='/')`
- **Added**: Debug logging for URL encoding and compiler branch matching (OB PB-4)

### agent.py (AC-5: Domain injection)
- **Replaced**: Substring `expected_domain not in step_url` with `urlparse().hostname` extraction
- **Added**: Exact match + subdomain match (`step_host.endswith(f".{expected_domain}")`)
- **Added**: Scheme-less URL guard (hostname=None -> skip with debug log) (R1-2)
- **Added**: Debug logging for domain injection skip reasons

### matcher.py (AC-4: Observability)
- **Added**: `import structlog` + `slog = structlog.get_logger(__name__)`
- **Added**: `slog.debug("skill_rejected_by_required_keywords", ...)` after gate rejection (OB PB-7)
- **Added**: Unicode-awareness comment on `\b` boundary (SEC PB-3)

## Test File
- `tests/unit/test_type_text_hardening.py` -- 39 tests total (4 + 8 + 10 + 6 + 11)

## Director Review Changes
- **Fixed bug**: Path spaces now encoded even when query params present (was mutually exclusive `if/elif`)
- **Added**: Unquoted Type text tests (AC-2: 3 new tests for regex `"?` optional quotes)
- **Added**: Port spoofing tests (AC-5: 2 new tests for `evil.com:443@target.com` and `target.com:8443`)
- **Evidence**: `urlparse()` never raises on any string input (verified: null bytes, 100k chars, garbage, surrogates)

## Known Limitations Documented in Tests
- AC-3 `test_encoded_slash_in_path_known_limitation`: `unquote()` decodes `%2F` to `/` when path has spaces
- AC-3 `test_encoded_slash_without_spaces_preserved`: `%2F` preserved when no spaces (no-op branch)
- AC-3 `test_ampersand_in_query_param_known_limitation`: `&` in query values splits on param separator (expand() gap)

---

# Type-Text Hardening: Traceability Matrix (Slice 2)

| AC | Description | Test Class | Test Count | Production File | Status |
|----|-------------|-----------|------------|-----------------|--------|
| AC-6a | get_scroll_position(axis=) | TestScrollPositionAxis | 8 | `applescript_actuator.py:313-355` | PASS |
| AC-6b | Axis-parametric S1 scroll verification | TestVerifierScrollS1Horizontal, TestVerifierScrollS1Vertical | 16 | `verifier.py:478-530` | PASS |
| AC-6c | Structured _scroll_before metadata | TestScrollDispatchMetadata | 5 | `agent.py:2304-2349` | PASS |
| AC-6 sign | Sign convention (down=+, right=+) | TestScrollSignConvention | 2 | (verification only) | PASS |
| PB4 | Word-boundary review | TestPB4WordBoundaryReview | 2 | `matcher.py:44-57` (read-only) | PASS |
| PB5 | Walmart stub cleanup review | TestPB5WalmartStubReview | 1 | `skills/library/` (read-only) | PASS |
| PB7 | Domain injection scope review | TestPB7DomainScopeReview | 1 | `agent.py:1520-1548` (read-only) | PASS |

## Production Code Changes (Slice 2)

### applescript_actuator.py (AC-6a: axis parameter)
- **Added**: `import structlog` + `slog = structlog.get_logger(__name__)`
- **Changed**: `get_scroll_position()` -> `get_scroll_position(axis="y")` with `axis` param
- **Added**: `ValueError` for invalid axis values (defense-in-depth)
- **Changed**: `js_prop` derived from axis ("window.scrollX" / "window.scrollY")
- **Fixed**: Exception handling `TypeError` -> `OSError` (correct exception for subprocess)
- **Added**: Debug logging on exception (replaces bare `pass`)

### verifier.py (AC-6b: axis-parametric S1)
- **Added**: Class-level `_SCROLL_DIRS_INCREASING = frozenset({"right", "down"})`
- **Added**: Class-level `_SCROLL_DIRS_DECREASING = frozenset({"left", "up"})`
- **Changed**: `_scroll_y_before` (bare int) -> `_scroll_before` (structured dict `{"axis", "value"}`)
- **Changed**: Hardcoded direction checks -> `direction in self._SCROLL_DIRS_INCREASING/DECREASING`
- **Changed**: `get_scroll()` -> `get_fn(axis=axis)` (axis-parametric call)
- **Added**: `axis_label = f"scroll{axis.upper()}"` for evidence messages
- **Added**: Structured debug logging for S1 confirmed/inconclusive verdicts

### agent.py (AC-6c: structured dispatch metadata)
- **Changed**: Axis derived from direction: `"x" if direction in ("left", "right") else "y"`
- **Changed**: `get_scroll()` -> `get_scroll(axis=axis)` (axis-parametric call)
- **Changed**: `result["_scroll_y_before"] = scroll_before` -> `result["_scroll_before"] = {"axis": axis, "value": scroll_before_val}`
- **Added**: Debug logging when scroll_before_val is None

### Existing Test Files Updated (backward compat)
- `tests/unit/test_scroll_verification.py`: `_scroll_y_before: int` -> `_scroll_before: {"axis": "y", "value": int}`
- `tests/integration/test_target_buy_fixes.py`: Same metadata format migration
- `tests/unit/test_scroll_action.py`: No changes needed (no scroll metadata assertions)

## Test File
- `tests/unit/test_type_text_hardening_scroll.py` -- 34 tests total (8 + 7 + 9 + 2 + 5 + 2 + 1 + 1)
