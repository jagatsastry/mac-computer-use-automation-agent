# Type-Text Hardening: Product Requirements Document

## Problem Statement

The automation agent's skill compiler and verification pipeline have several hardening gaps exposed during end-to-end testing of e-commerce purchase flows. Two commits (1c1535a, ad751b5) already fixed the critical blockers — the `buy_on_target` skill now uses a direct search URL instead of fragile type-and-enter, the compiler extracts element descriptions from "in the..." clauses, URL encoding handles spaces via `urllib.parse`, word-boundary matching prevents substring false positives, and domain injection is scoped to matching-domain steps only. One gap remains open: horizontal scroll verification (PB8) silently falls through Tier S1 because the verifier only checks `scrollY`, treating `left`/`right` directions as dead code.

This PRD defines the acceptance criteria for confirming the shipped fixes and closing the remaining PB8 gap, along with regression coverage for all changes.

## Acceptance Criteria

### Already shipped (commits 1c1535a, ad751b5) — must be verified by tests

**AC-1: Direct search URL in buy_on_target skill**
`buy_on_target.md` must use a direct search URL pattern (`Navigate to https://www.target.com/s?searchTerm={{product}}`) instead of a type-and-enter flow. The skill must not contain any `Type ... and press Enter` step for the initial product search. Additionally, when `{{product}}` is substituted with a multi-word string (e.g., "queen size bed sheets"), the end-to-end flow through param substitution + skill compilation + URL encoding must produce a valid `open_url` ActionStep with properly encoded query parameters (e.g., `searchTerm=queen+size+bed+sheets`).

**AC-2: Compiler element extraction from "in the..." clause**
When the skill compiler encounters `Type "X" in the <field> and press Enter`, it must extract the field description from the "in the..." clause and pass it as the `element` parameter on the resulting `type_text` ActionStep. If no "in the..." clause is present, the element must default to `"search or text input field"`. This AC verifies the existing shipped behavior only — the default is a heuristic appropriate for the current e-commerce skill templates where type-and-enter is overwhelmingly used for search. Hardening the default for non-search contexts (login forms, comment boxes) is out of scope for this pass.

**AC-3: URL encoding via urllib.parse**
The skill compiler's URL encoding path must use `urllib.parse` (specifically `urlparse`, `parse_qs`, `urlencode`) to handle spaces and special characters in query parameters. For URLs without query strings that contain spaces, the fallback must use `urllib.parse.quote(url, safe=':/?#[]@!$&\'()*+,;=-._~')` instead of naive `str.replace(" ", "%20")`, ensuring special characters like `&`, `#`, and `+` are correctly handled. Product names with spaces (e.g., "queen size bed sheets") and special characters (e.g., "bed & bath towels") must be correctly encoded in all URL positions.

**AC-4: Word-boundary matching in required-keywords gate**
The `match_skill` function must use `\b` regex anchors around each required keyword in the `required-keywords` gate. Substring matches must be rejected — e.g., the keyword `"target"` must NOT match prompts containing only `"retarget"` or `"untargeted"`. Note: word-boundary matching applies only to `required-keywords` (hard gate), not to `trigger-keywords` (soft scoring signal). The `trigger-keywords` scoring uses plain substring matching intentionally — false positives in scoring are tolerable because they only affect ranking, not gate pass/fail.

**AC-5: Domain injection scoped to matching-domain steps**
`_inject_domain_verification` must only append domain verification (`AND browser domain is {domain}`) to `open_url` steps whose URL contains the expected domain. Steps with non-matching URLs (e.g., a Google redirect in a Target flow) must not receive domain injection.

### Open (PB8) — must be implemented

**AC-6: Horizontal scroll verification via scrollX**
Scroll verification Tier S1 must handle horizontal scroll directions (`"left"` and `"right"`) by comparing `scrollX` deltas, mirroring the existing `scrollY` logic for `"down"` and `"up"`. The actuator must expose horizontal scroll position via a new `get_scroll_position_x()` method (option A — no breaking change to the existing `get_scroll_position()` return type). For horizontal scrolls, the dispatch side must capture `_scroll_x_before` metadata and must NOT capture `_scroll_y_before` — keep axis-specific metadata clean, no dual-axis capture. Delta=0 behavior for horizontal scrolls must match vertical: fall through to S2 (inconclusive), consistent with the existing tiered design.

### Regression safety

**AC-7: No test regressions**
All existing tests in `tests/unit/` and `tests/integration/` must continue to pass after all changes. The regression scope is `pytest tests/unit/ tests/integration/` — `tests/e2e/` is excluded because it requires a live macOS desktop and may have pre-existing failures unrelated to this hardening pass.

**AC-8: New test coverage for all hardening changes**
Each of AC-1 through AC-6 must have at least one dedicated positive test AND at least one negative test where applicable. Specifically:

- AC-1 tests must include an end-to-end case: substitute `{{product}}` with `"queen size bed sheets"`, compile, and assert the resulting `open_url` step has encoded query params (no raw spaces).
- AC-4 tests must include negative cases: `"retarget"` does NOT match keyword `"target"`, `"untargeted"` does NOT match keyword `"target"`.
- AC-5 tests must include a negative case: an `open_url` step with a non-matching domain does NOT receive domain injection.
- AC-6 tests must include: horizontal scroll with positive delta (success), horizontal scroll with delta=0 (falls through to S2).

Tests must follow existing patterns: `_make_config()` helper, mocked protocol components, `@pytest.mark.unit` or `@pytest.mark.integration` markers.

## Out of Scope

- Rewriting the skill compiler regex cascade into a different parsing architecture (grammar-based, LLM-based, etc.)
- Hardening the `type_text` element default for non-search contexts (login forms, comment boxes) — the current `"search or text input field"` default is appropriate for e-commerce skill templates
- Adding `scrollX` support for native (non-browser) macOS apps — horizontal scroll verification is browser-only (Safari/Chrome), matching the existing vertical implementation
- Pixel-diff (Tier S2) improvements for scroll verification
- Pre-encoding URL parameters in `SkillRegistryImpl.expand()` before template substitution (a future improvement noted in SOTA research but not required for this hardening pass)
- Extending URL boundary regex to exclude trailing delimiters (`)`/`>`) — **known gap** (SOTA section 2c, point 4: `\S+` in URL patterns matches trailing `)` in parenthetical text), but no current skill templates use parenthetical URLs, so risk is minimal. Track as future hardening if skill templates adopt richer formatting.
- Adding `scrollY_after` logging to actuator results — debugging improvement, not a correctness fix
- Elastic/bounce scroll handling on macOS
- Regex ambiguity when typed text contains "in the" (e.g., `Type "sign in the app" in the username field and press Enter`) — the quoted form is handled correctly by quote delimiters; the unquoted form has a known lazy-match ambiguity but skill templates always use quotes, so this is not a current failure mode

## Success Metrics

1. **All 8 acceptance criteria pass** — verified by automated tests
2. **Zero test regressions** — `pytest tests/unit/ tests/integration/` clean
3. **buy_on_target e2e flow** completes successfully with product names containing spaces — deferred to QA phase (not a gate for engineering completion; requires live macOS desktop with browser)
4. **Horizontal scroll** correctly verifies at Tier S1 instead of falling through to S3 actuator trust

## References

- [State of the Art Research](type-text-hardening-sota.md) — URL encoding best practices, regex cascade patterns, scroll verification approaches
- [Codebase Research](type-text-hardening-codebase.md) — file:line references, dead code trace for PB8, test patterns, integration points
- Commits: 1c1535a (type_text blocker + URL encoding), ad751b5 (PB4/PB5/PB7 fixes)
