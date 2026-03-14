---
name: type-text-hardening-verification-report
---

# Type-Text Hardening: Verification Report (Slice 1)

## Test Results

| Suite | Count | Result |
|-------|-------|--------|
| Slice 1 tests (`test_type_text_hardening.py`) | 39 | 39/39 PASS |
| Full unit suite (`tests/unit/`) | 1292 | 1292/1292 PASS |
| Integration (non-e2e) | 30/31 | 1 pre-existing failure (live desktop) |

## Test Breakdown by AC

| AC | Class | Tests | Status |
|----|-------|-------|--------|
| AC-1 | TestAC1DirectSearchURL | 4 | PASS |
| AC-2 | TestAC2CompilerElementExtraction | 8 | PASS |
| AC-3 | TestAC3URLEncoding | 10 | PASS |
| AC-4 | TestAC4WordBoundaryMatching | 6 | PASS |
| AC-5 | TestAC5DomainInjectionScoping | 11 | PASS |

## TDD Process Followed

1. **Tests written first**: 32 tests created before any production changes
2. **Expected failures**: 6 tests failed (AC-3 URL encoding x2, AC-5 domain injection x4)
3. **Production changes**: AC-3 (agent.py URL encoding), AC-5 (agent.py domain injection), AC-4 (matcher.py logging)
4. **All tests pass**: 32/32 after production changes
5. **Adversary review**: 2 additional tests added (AC-3 edge cases), 1 production fix (encoding_method log)
6. **Director review**: Fixed path+query encoding bug, added 5 tests (3 AC-2 unquoted, 2 AC-5 port spoofing)
7. **Final count**: 39/39 tests pass, 1292/1292 unit suite pass, zero regressions

## Production Changes Summary

### agent.py
- AC-3: Replaced deferred import + try/except with component-based `urllib.parse` encoding
- AC-3: Fixed path+query interaction: path encoding now runs independently of query encoding (was mutually exclusive `if/elif`)
- AC-5: Replaced substring `in` check with `urlparse().hostname` domain extraction
- Added debug logging for both changes

### matcher.py
- AC-4: Added `structlog` logging when required-keywords gate rejects a skill
- Added Unicode-awareness comment on `\b` boundary

## Known Limitations (Documented in Tests)

1. **Encoded slash decode** (AC-3): `unquote()` decodes `%2F` to `/` when path also has spaces
2. **Encoded slash preserved** (AC-3): `%2F` preserved when path has no spaces (no-op branch)
3. **Ampersand in query values** (AC-3): `&` in param values misinterpreted as separator (expand() gap)

## Safety Evidence

- **urlparse() never raises**: Verified with null bytes, 100k-char URLs, garbage input, and surrogates -- all return ParseResult without exception. The removed try/except was dead code (CQ-3).

## Pre-existing Integration Failures (Not Related to Changes)

- `test_accuracy_architecture_integration.py::test_agent_multiscale_validation_and_reflection_work_together` -- requires live vision server
- `test_scroll_replan_integration.py::TestFailedStepTriggersReplan` -- `_execute_step` monkey-patching prevents replan trigger
- `test_status_and_skill_fallback_integration.py::test_real_amazon_skill_fallback_executes_browser_steps` -- skill routing issue

---

# Type-Text Hardening: Verification Report (Slice 2)

## Test Results

| Suite | Count | Result |
|-------|-------|--------|
| Slice 2 tests (`test_type_text_hardening_scroll.py`) | 34 | 34/34 PASS |
| Scroll verification (`test_scroll_verification.py`) | 25 | 25/25 PASS |
| Scroll action (`test_scroll_action.py`) | 47 | 47/47 PASS |
| Target buy fixes (`test_target_buy_fixes.py`) | 36 | 36/36 PASS |
| Full unit suite (`tests/unit/`) | 1282 | 1282/1282 PASS |

## Test Breakdown by AC

| AC | Class | Tests | Status |
|----|-------|-------|--------|
| AC-6a | TestScrollPositionAxis | 8 | PASS |
| AC-6b (horizontal) | TestVerifierScrollS1Horizontal | 7 | PASS |
| AC-6b (vertical) | TestVerifierScrollS1Vertical | 9 | PASS |
| AC-6 (sign) | TestScrollSignConvention | 2 | PASS |
| AC-6c | TestScrollDispatchMetadata | 5 | PASS |
| PB4 | TestPB4WordBoundaryReview | 2 | PASS |
| PB5 | TestPB5WalmartStubReview | 1 | PASS |
| PB7 | TestPB7DomainScopeReview | 1 | PASS |

## TDD Process Followed

1. **Tests written first**: 34 tests created in `test_type_text_hardening_scroll.py`
2. **Production changes**: AC-6a (actuator), AC-6b (verifier), AC-6c (agent dispatch)
3. **Backward compat**: Updated 3 existing test files for `_scroll_before` format migration
4. **All tests pass**: 34/34 new + full unit suite, zero regressions

## Production Changes Summary

### applescript_actuator.py (AC-6a)
- Added `axis` parameter to `get_scroll_position()` (default `"y"` for backward compat)
- Added `ValueError` for invalid axis values
- Fixed exception handling: `TypeError` -> `OSError`
- Added structlog debug logging on exception

### verifier.py (AC-6b)
- Added `_SCROLL_DIRS_INCREASING` and `_SCROLL_DIRS_DECREASING` class-level frozensets
- Replaced vertical-only S1 block with axis-parametric version
- Changed metadata key from `_scroll_y_before` (bare int) to `_scroll_before` (structured dict)
- Added structured debug logging for S1 verdict

### agent.py (AC-6c)
- Axis derived from direction: `"x"` for left/right, `"y"` for up/down
- `get_scroll_position(axis=axis)` call replaces bare `get_scroll()`
- Structured `_scroll_before = {"axis": axis, "value": int}` replaces `_scroll_y_before = int`
- Added debug logging when pre-scroll position is unavailable
