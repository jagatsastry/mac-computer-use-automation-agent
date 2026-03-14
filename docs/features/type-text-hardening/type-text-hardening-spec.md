# Type-Text Hardening: Architecture Spec

## Overview

Two domain slices implementing 8 acceptance criteria: verify 5 shipped fixes (AC-1 through AC-5), implement PB8 horizontal scroll S1 (AC-6), zero regressions (AC-7), and test coverage (AC-8).

---

## Slice 1 (Engineer 1): Harden Blocker Fix

**Scope**: Verify shipped commits (1c1535a, ad751b5) via tests. One production code change: tighten the URL-encoding fallback (AC-3).

### Files

| File | Action | Purpose |
|------|--------|---------|
| `src/automation_agent/skills/library/buy_on_target.md` | Read-only | Verify direct search URL format (AC-1) |
| `src/automation_agent/orchestrator/agent.py` | **Modify** | Tighten URL fallback (AC-3): component-based encoding; Fix domain injection substring bug (AC-5): `urlparse`-based domain matching |
| `src/automation_agent/skills/matcher.py` | Read-only | Verify word-boundary matching (AC-4) |
| `tests/unit/test_type_text_hardening.py` | **Create** | All AC-1 through AC-5 unit tests |

### AC-1: Direct Search URL in buy_on_target.md

**What to test**: The skill template uses `Navigate to https://www.target.com/s?searchTerm={{product}}` and does NOT contain any `Type ... and press Enter` step for the initial product search.

**Test cases**:

```python
class TestAC1DirectSearchURL:
    """AC-1: buy_on_target.md uses direct search URL, not type-and-enter."""

    def test_step1_is_navigate_to_url(self):
        """Step 1 instruction starts with 'Navigate to https://www.target.com/s?searchTerm='."""
        # Load skill, parse step 1, assert starts with Navigate to URL

    def test_no_type_and_enter_for_search(self):
        """No step in buy_on_target.md contains 'Type' + 'press Enter' for initial search."""
        # Load skill, scan all steps, assert no type+enter pattern

    def test_url_encoding_after_param_substitution(self):
        """When {{product}} = 'queen size bed sheets', the compiled URL has encoded spaces."""
        # Substitute params, compile step 1 via _compile_skill_instruction,
        # assert 'queen+size+bed+sheets' or 'queen%20size%20bed%20sheets' in URL

    def test_ac1_expand_then_compile_end_to_end(self):
        """End-to-end: expand() substitutes params, then _compile_skill_instruction encodes.
        Exercises the actual expand()->compile pipeline rather than testing
        _compile_skill_instruction in isolation with pre-substituted text."""
        # 1. Load buy_on_target.md skill
        # 2. Call expand(params={"product": "queen size bed sheets"})
        # 3. Extract step 1 instruction text from expanded result
        # 4. Pass to _compile_skill_instruction
        # 5. Assert resulting ActionStep has action="open_url" with encoded URL
```

**Data shapes**:
- Input: skill step text after `SkillRegistryImpl.expand()` substitutes `{{product}}` → `"queen size bed sheets"`
- Expected output from `_compile_skill_instruction`: `ActionStep(action="open_url", params={"url": "https://www.target.com/s?searchTerm=queen+size+bed+sheets"}, ...)`

### AC-2: Compiler Element Extraction from "in the..." Clause

**What to test**: `_compile_skill_instruction` extracts the field description from "in the..." clause and passes it as `element` param. Default is `"search or text input field"`.

**Security note (SEC PB-6)**: The Type regex uses lazy quantifiers (`(.+?)`) that could backtrack on pathological input. In practice, `_compile_skill_instruction` is ONLY called with human-authored skill template text from `_parse_skill_steps` (agent.py:1587) — never with LLM planner output. Skill instructions are ~50-100 chars. As defense-in-depth, the spec does NOT add a length guard here (the input is already bounded by `.md` file authoring). If `_compile_skill_instruction` is ever exposed to LLM-generated text, add `if len(instruction) > 500: return None` at the top of the method.

**Test cases**:

```python
class TestAC2CompilerElementExtraction:
    """AC-2: Type instruction extracts element from 'in the' clause."""

    def test_type_with_in_the_extracts_element(self):
        """'Type "foo" in the search bar and press Enter' -> element='search bar'."""
        # Compile instruction, check type_text step params["element"] == "search bar"

    def test_type_without_in_the_defaults_to_search_field(self):
        """'Type "foo" and press Enter' -> element='search or text input field'."""
        # Compile instruction, check type_text step params["element"]

    def test_type_produces_two_steps(self):
        """Type+Enter always produces [type_text, press_key(return)]."""
        # Compile, assert len(steps) == 2
        # steps[0].action == "type_text", steps[1].action == "press_key"

    def test_type_text_step_has_verify(self):
        """type_text step has non-empty verify containing the typed text."""
        # Compile, assert typed_text in steps[0].verify

    def test_type_with_in_the_in_quoted_text(self):
        """'Type "sign in" in the username field and press Enter'
        -> typed_text='sign in', element='username field'.
        Validates PRD assertion that quoted form correctly handles
        typed text containing 'in the' (quotes anchor the lazy match)."""
        # Compile instruction
        # assert steps[0].params["text"] == "sign in"
        # assert steps[0].params["element"] == "username field"
```

**Data shapes**:
- Input: `instruction="Type \"queen sheets\" in the search bar and press Enter"`, `verify="Results page visible"`
- Regex: `^Type\s+"?(.+?)"?\s+(?:in the (.+?)\s+)?(?:and|then)\s+press\s+Enter$`
- Group 1 → `typed_text`, Group 2 → `element_desc` (or None → default `"search or text input field"`)
- Output: `[ActionStep(action="type_text", params={"text": "queen sheets", "element": "search bar"}, verify='...'), ActionStep(action="press_key", params={"keys": ["return"]}, verify="Results page visible")]`

### AC-3: URL Encoding via urllib.parse

**What to test**: The compiler's URL encoding path uses `urllib.parse` for ALL URL encoding — both query params and non-query URLs. The naive `str.replace(" ", "%20")` fallback is **no longer permitted** (PRD update from round 1 review).

**Production code change** (agent.py:1750-1761): Replace BOTH fallback paths with component-based encoding. Also remove the deferred `from urllib.parse import ...` (line 1750) — `urllib.parse` is already imported at module level (line 10). Use `urllib.parse.*` style consistently with the rest of the file.

```python
# BEFORE (agent.py:1750-1761):
from urllib.parse import quote, urlparse, urlunparse, parse_qs, urlencode  # deferred import (unnecessary)
try:
    parsed = urlparse(destination)
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        encoded_query = urlencode(params, doseq=True)
        destination = urlunparse(parsed._replace(query=encoded_query))
    elif " " in destination:
        destination = destination.replace(" ", "%20")      # BUG: misses &, #, +
except Exception:
    destination = destination.replace(" ", "%20")          # BUG: same

# AFTER (uses module-level `import urllib.parse` — no deferred import):
raw_destination = destination  # capture for debug logging
parsed = urllib.parse.urlparse(destination)
if parsed.query:
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    encoded_query = urllib.parse.urlencode(params, doseq=True)
    destination = urllib.parse.urlunparse(parsed._replace(query=encoded_query))
elif " " in destination:
    # Component-based: parse URL, quote only the path, reassemble
    encoded_path = urllib.parse.quote(urllib.parse.unquote(parsed.path), safe='/')
    destination = urllib.parse.urlunparse(parsed._replace(path=encoded_path))
if destination != raw_destination:
    slog.debug("url_encoded", before=raw_destination, after=destination)
# No try/except: urlparse() never raises on string input (CQ-3).

# Log which compiler branch matched and extracted params (OB PB-4)
slog.debug(
    "compiler_branch_matched",
    branch="navigate_url",
    destination=destination,
    encoding_method="query_param" if parsed.query else "component_path",
)
```

**Import cleanup** (DE PB-13): The deferred `from urllib.parse import ...` at line 1750 is removed. `urllib.parse` is stdlib — always available — so a deferred import is a code smell reserved for optional dependencies. The module-level `import urllib.parse` (line 10) is used instead, making all `urlparse` usage in the file consistently prefixed with `urllib.parse.`.

**Dead code removal** (CQ-3): The outer `except Exception` with nested try/except belt-and-suspenders pattern was removed. `urlparse()` never raises on string input — the exception handler was 6 lines of unreachable, untestable code. The `str.replace(" ", "%20")` ultimate fallback was also dead code within dead code.

**Why component-based encoding** (Short-Seller fix): Quoting a full URL with `quote(url, safe='...&...')` cannot distinguish structural `&` (query separator) from user-content `&` (in "bed & bath"). Verified empirically:
```python
quote('https://target.com/bed & bath', safe=':/?#[]@!$&...')
# -> 'https://target.com/bed%20&%20bath'  (&  preserved = WRONG)
```
The fix: `urlparse` the URL, `quote()` only the path component with `safe='/'`, then `urlunparse`. This correctly produces `bed%20%26%20bath`.

**Why `unquote()` before `quote()` — and its limitation**: Prevents double-encoding of `%20`. If the path already contains `%20`, `quote('/search%20term', safe='/')` would produce `/search%2520term`. The `unquote` first normalizes to `/search term`, then `quote` encodes to `/search%20term`.

**KNOWN LIMITATION**: `unquote()` also decodes `%2F` back to `/`, destroying encoded-slash semantics. Example: `/search/bed%2Fbath/results` becomes `/search/bed/bath/results` after `unquote()` — an incorrect 4-segment path instead of a 3-segment path with a literal slash in the second segment. In practice, skill template URLs from `expand()` never contain pre-encoded slashes (they come from human-authored `.md` files with literal paths). This is documented as an acceptable trade-off: double-encoding protection for `%20` (common) outweighs encoded-slash preservation (never occurs in current skill templates). If encoded slashes become necessary in future skills, replace `unquote()` with targeted `path.replace('%20', ' ')` before `quote()`.

**Test cases**:

```python
class TestAC3URLEncoding:
    """AC-3: URL encoding uses urllib.parse for all paths."""

    def test_spaces_in_query_param_encoded(self):
        """'Navigate to https://target.com/s?searchTerm=queen size sheets' -> encoded."""
        # Compile, assert "queen+size+sheets" or "queen%20size%20sheets" in URL

    def test_url_without_query_uses_component_encoding(self):
        """'Navigate to https://target.com/queen size sheets' -> %20 via urlparse+quote."""
        # Compile, assert "queen%20size%20sheets" in URL

    def test_special_chars_in_path_encoded(self):
        """'Navigate to https://target.com/bed & bath' -> & encoded as %26 in path."""
        # Compile, assert "%26" in URL path
        # CRITICAL: would FAIL with quote(url, safe='...&...') or str.replace

    def test_already_encoded_path_not_double_encoded(self):
        """'Navigate to https://target.com/queen%20sheets' -> no %2520."""
        # Compile, assert "%2520" NOT in URL
        # Tests the unquote-before-quote guard

    def test_multiple_query_params_preserved(self):
        """URL with multiple params keeps all after encoding."""
        # Compile 'Navigate to https://example.com?a=foo bar&b=baz'
        # assert "a=foo+bar" and "b=baz" both in URL

    def test_empty_query_value_preserved(self):
        """parse_qs with keep_blank_values=True preserves empty values."""
        # Compile 'Navigate to https://example.com?k=&q=test'
        # assert "k=" in URL

    def test_encoded_slash_in_path_known_limitation(self):
        """KNOWN LIMITATION: unquote() decodes %2F back to /, destroying encoded-slash
        semantics. /search/bed%2Fbath -> /search/bed/bath (wrong segment count).
        In practice, skill template URLs never contain pre-encoded slashes.
        Documents the trade-off: %20 double-encoding protection > encoded-slash preservation."""
        # Compile 'Navigate to https://target.com/search/bed%2Fbath/results'
        # Assert %2F is decoded to / (documents the limitation)

    # NOTE: test_exception_fallback removed — the except block itself was
    # removed from production code per CQ-3 (urlparse never raises on
    # string input). No production code to test, no test to write.

    def test_ampersand_in_query_param_known_limitation(self):
        """KNOWN LIMITATION: & in query param value splits on param separator.
        parse_qs('searchTerm=bed & bath') -> {'searchTerm': ['bed '], ' bath': ['']}
        This is a pre-existing bug in expand() which does raw str substitution
        without URL-encoding parameter values. Fixing requires pre-encoding in
        SkillRegistryImpl.expand() — explicitly Out of Scope per PRD.
        This test documents the limitation so it's visible and intentional."""
        # Compile 'Navigate to https://target.com/s?searchTerm=bed & bath'
        # Assert the & is misinterpreted (documents the known gap)
```

**Known limitation — `&` in query parameter values**: When `expand()` substitutes `{{product}}` with a value containing `&` (e.g., `"bed & bath"`), the resulting URL `searchTerm=bed & bath` is passed to `parse_qs()`, which misinterprets the content `&` as a parameter separator: `{'searchTerm': ['bed '], ' bath': ['']}`. The proper fix is URL-encoding values in `expand()` before substitution, but this is Out of Scope per PRD (requires changes to `SkillRegistryImpl.expand()` which affects all skills, not just URL parameters).

**Data shapes**:
- **Query param path** (primary — unchanged):
  - Input: `instruction="Navigate to https://www.target.com/s?searchTerm=queen size bed sheets"`
  - Code path: `urlparse` → `parse_qs(keep_blank_values=True)` → `urlencode(doseq=True)` → `urlunparse`
  - Output: `ActionStep(action="open_url", params={"url": "https://www.target.com/s?searchTerm=queen+size+bed+sheets"}, ...)`
- **Non-query path** (fallback — now component-based):
  - Input: `instruction="Navigate to https://target.com/bed & bath towels"`
  - Code path: `urlparse` → `quote(unquote(parsed.path), safe='/')` → `urlunparse`
  - Output: `https://target.com/bed%20%26%20bath%20towels` (both spaces AND `&` encoded)

### AC-4: Word-Boundary Matching in Required-Keywords Gate

**What to test**: `match_skill` uses `\b` regex anchors around each required keyword. Substring matches rejected.

**Test cases**:

```python
class TestAC4WordBoundaryMatching:
    """AC-4: required-keywords use word-boundary matching."""

    def test_exact_word_matches(self):
        """Prompt 'buy on target' matches required-keyword 'target'."""

    def test_substring_retarget_rejected(self):
        """Prompt 'retarget the ad' does NOT match required-keyword 'target'."""

    def test_substring_untargeted_rejected(self):
        """Prompt 'untargeted campaign' does NOT match required-keyword 'target'."""

    def test_domain_form_matches(self):
        """Prompt 'buy on target.com' matches required-keyword 'target.com'."""

    def test_case_insensitive_match(self):
        """Prompt 'Buy on TARGET' matches required-keyword 'target'."""

    def test_any_keyword_sufficient(self):
        """With required-keywords ['target', 'target.com'], matching either suffices."""
```

**Data shapes**:
- Input: `prompt="retarget the ad"`, `skills=[Skill(metadata={"required-keywords": ["target"]})]`
- Code (matcher.py:43-49): `re.search(r"\b" + re.escape(rk) + r"\b", prompt_lower)` → None (no match) → skill skipped
- Output: `match_skill(prompt, skills)` → `None`

**Security note (SEC PB-3)**: `\b` is Unicode-aware (`re.UNICODE` is Python 3 default). For ASCII keywords (all current skill templates), this is safe. If skill keywords or prompts ever include non-ASCII text, `\b` boundary semantics may produce surprising matches — Unicode letters/digits are word characters, so `\b` won't fire at Unicode letter boundaries. Add a comment in `matcher.py`:
```python
# NOTE: \b is Unicode-aware (re.UNICODE default). Safe for ASCII keywords;
# may produce surprising boundaries with non-ASCII text.
```

**Observability (OB PB-7)**: Add debug logging to `matcher.py` when a skill is rejected by the required-keywords gate. This is the first logging in `matcher.py` and directly supports AC-4 debugging:
```python
# matcher.py — add after the required-keywords loop rejects a skill
slog.debug(
    "skill_rejected_by_required_keywords",
    skill_name=skill.metadata.get("name", ""),
    required_keywords=skill.metadata.get("required-keywords", []),
    prompt=prompt_lower[:100],
)
```

### AC-5: Domain Injection Scoped to Matching-Domain Steps

**What to test**: `_inject_domain_verification` only appends `AND browser domain is {domain}` to `open_url` steps whose URL matches the expected domain (proper domain extraction, not substring).

**Production code change** (agent.py:1531-1534): Replace substring `in` check with `urlparse`-based domain extraction to prevent false positives on domain suffixes (e.g., `nottarget.com` should NOT match `target.com`).

```python
# BEFORE (agent.py:1531-1534):
step_url = step.params.get("url", "")
if step_url and expected_domain not in step_url:
    continue

# AFTER (uses module-level `import urllib.parse` — no deferred import):
step_url = step.params.get("url", "")
if step_url:
    step_host = urllib.parse.urlparse(step_url).hostname
    if step_host is None:
        # Scheme-less URL (e.g., "target.com/page") — urlparse returns
        # hostname=None. Skip injection with debug log rather than
        # silently proceeding. (R1-2)
        slog.debug(
            "domain_injection_skipped",
            step_url=step_url, expected_domain=expected_domain,
            reason="no_hostname_parsed",
        )
        continue
    if step_host != expected_domain and not step_host.endswith(f".{expected_domain}"):
        slog.debug(
            "domain_injection_skipped",
            step_url=step_url, step_host=step_host,
            expected_domain=expected_domain, reason="domain_mismatch",
        )
        continue
```

**Why**: Raw substring `expected_domain not in step_url` matches `target.com` inside `nottarget.com`. The fix uses `urllib.parse.urlparse().hostname` for proper domain extraction, then checks exact match or subdomain match (e.g., `www.target.com` ends with `.target.com`). This is consistent with the verifier's `_extract_base_domain` logic at verifier.py:405-408. Uses the module-level `import urllib.parse` (line 10), not a deferred import (per PB-13).

**Test cases**:

```python
class TestAC5DomainInjectionScoping:
    """AC-5: Domain injection only on matching-domain open_url steps."""

    def test_matching_domain_gets_injection(self):
        """open_url with target.com URL gets 'AND browser domain is target.com' appended."""

    def test_subdomain_gets_injection(self):
        """open_url with www.target.com URL gets injection (subdomain of target.com)."""

    def test_non_matching_domain_skipped(self):
        """open_url with google.com URL does NOT get target.com domain injection."""

    def test_domain_injection_not_triggered_by_substring_overlap(self):
        """open_url with nottarget.com URL does NOT get target.com domain injection.
        Regression test: raw 'target.com in url' would false-positive here.
        The urlparse-based check correctly rejects nottarget.com != target.com."""

    def test_idempotent_no_double_injection(self):
        """If verify already contains 'browser domain is', no duplicate added."""

    def test_multi_step_plan_selective_injection(self):
        """Plan with target.com and google.com URLs: only target.com step gets injected."""

    def test_schemeless_url_skips_injection(self):
        """open_url with scheme-less URL (e.g., 'target.com/page') skips injection.
        urlparse().hostname returns None for scheme-less URLs. (R1-2)"""

    def test_userinfo_at_host_not_tricked(self):
        """Security: target.com@evil.com -> hostname='evil.com' -> NOT injected.
        Validates that urlparse().hostname correctly extracts the actual host
        from userinfo@host URLs, preventing domain spoofing. (SEC PB-2)"""
        # plan with open_url(url="https://target.com@evil.com/redirect")
        # expected_domain="target.com"
        # assert step.verify does NOT contain "browser domain is target.com"

    def test_open_url_without_url_param_gets_injection(self):
        """open_url step with empty/missing URL param gets domain injection (safe default).
        When step_url is empty, the 'if step_url:' guard is False, so the
        hostname check is skipped and the step falls through to injection. (CQ-5)"""
        # plan with open_url(params={}) and open_url(params={"url": ""})
        # expected_domain="target.com"
        # assert both steps get "browser domain is target.com" in verify
```

**Data shapes**:
- Input: `plan=ActionPlan(steps=[open_url(url="https://www.target.com/s?q=test"), open_url(url="https://nottarget.com/redirect"), open_url(url="https://google.com/search")])`, `expected_domain="target.com"`
- Code (agent.py:1531-1538): iterates steps, extracts `urlparse(step_url).hostname`, checks exact/subdomain match → skip non-matching; else appends marker
- Output: step[0].verify ends with `" AND browser domain is target.com"`, step[1].verify unchanged (nottarget.com rejected), step[2].verify unchanged (google.com rejected)

**Security: no TOCTOU on subdomain matching (SEC PB-5)**: `_inject_domain_verification` accepts legitimate subdomains (e.g., `www.target.com`, `m.target.com`) via `.endswith(f".{expected_domain}")`. This also accepts attacker-controlled subdomains like `evil.target.com`. However, this is NOT the sole defense — the verifier independently checks the browser's actual domain at verification time (verifier.py:399-418). The injection method only *annotates* the plan step with a marker (`AND browser domain is target.com`); the verifier *reads the actual browser URL* and compares via `_extract_base_domain(browser_url)`. So even if a redirect occurs after injection, the verifier catches the mismatch. Domain injection is a plan-time hint, not a security gate. The verifier is the security gate.

**Security: `javascript:` and `data:` URL schemes (SEC PB-7)**: `urlparse("javascript:alert(1)").hostname` returns `None`, so the scheme-less URL guard skips injection. The `open_url` actuator (`open location` via AppleScript) does NOT validate URL schemes — it will open any scheme. This is a **pre-existing gap** not introduced by this spec. Documented as out-of-scope for future hardening: add `http`/`https`-only scheme validation in `open_url()`.

---

## Slice 2 (Engineer 2): Fix PB8 + Review PB4/PB5/PB7

**Scope**: Implement horizontal scroll verification in S1 (AC-6). Production code changes + tests.

### Files

| File | Action | Purpose |
|------|--------|---------|
| `src/automation_agent/actuator/applescript_actuator.py` | **Modify** | Add `axis` param to `get_scroll_position()`, fix exception handling |
| `src/automation_agent/orchestrator/verifier.py` | **Modify** | Axis-parametric S1 scroll verification |
| `src/automation_agent/orchestrator/agent.py` | **Modify** | Structured `_scroll_before` metadata for horizontal scrolls |
| `tests/unit/test_type_text_hardening_scroll.py` | **Create** | AC-6 unit tests + PB4/PB5/PB7 review tests |

### AC-6: Horizontal Scroll Verification via scrollX

#### 6a. Actuator: Add `axis` Parameter to `get_scroll_position()`

> **PRD OVERRIDE**: The PRD (AC-6) specifies "a new `get_scroll_position_x()` method (option A)" and separate `_scroll_x_before` metadata. This spec overrides that design based on DE review PB-1/PB-2/PB-3 findings. The parameterized approach below eliminates 95% code duplication and is extensible to N axes. The PRD should be updated by the PM to reflect this revised design.

**Design decision** (revised after DE review): Parameterize the existing `get_scroll_position()` with `axis: str = "y"` rather than adding a separate method. Rationale:
- `get_scroll_position` is NOT in the `Actuator` protocol (`protocols.py:183-224`). It's accessed exclusively via `getattr()` duck typing (`agent.py:2285`, `verifier.py:481`). No formal callers to break.
- Default `axis="y"` preserves backward compatibility — existing `getattr` callers work unchanged.
- Eliminates 95% code duplication that two methods would create.
- Clean extension point for diagonal/zoom if ever needed.
- Also fixes an existing bug: exception clause changes from `TypeError` to `OSError` (the correct exception for `subprocess.run` failures).

**Implementation**:

```python
# applescript_actuator.py — REPLACE existing get_scroll_position()
def get_scroll_position(self, axis: str = "y") -> Optional[int]:
    """Get scrollX or scrollY from the frontmost browser tab.

    Args:
        axis: "x" for horizontal (scrollX), "y" for vertical (scrollY).

    Returns:
        Integer scroll position, or None if not in a browser or on error.

    Raises:
        ValueError: If axis is not "x" or "y".
    """
    if axis not in ("x", "y"):
        raise ValueError(f"axis must be 'x' or 'y', got {axis!r}")
    js_prop = "window.scrollX" if axis == "x" else "window.scrollY"
    # PERF NOTE (R1-1): get_state() calls _run_osascript() with
    # TIMEOUT_SECONDS=10s. Combined with the 3s scroll query below,
    # worst case per call is 13s. Called twice per scroll step
    # (dispatch pre-scroll + verifier post-scroll) = 26s worst case.
    # This is pre-existing behavior — not introduced by this spec.
    # If latency becomes an issue, consider caching get_state() or
    # extracting only app_name with a lighter AppleScript.
    state = self.get_state()
    app_name = state.get("app_name", "")
    lower = app_name.lower()

    if "safari" in lower:
        script = (
            f'tell application "Safari" to do JavaScript '
            f'"{js_prop}" in current tab of front window'
        )
    elif "chrome" in lower:
        script = (
            f'tell application "Google Chrome" to execute '
            f"front window's active tab javascript "
            f'"{js_prop}"'
        )
    else:
        return None

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(float(result.stdout.strip()))
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        slog.debug("get_scroll_position_error", axis=axis, error=str(exc))
    return None
```

**SECURITY NOTE**: `js_prop` is NOT user input — it's derived from a hardcoded `"x"/"y"` string. No injection risk. The f-string only interpolates `"window.scrollX"` or `"window.scrollY"`.

**Error handling changes**:
- `TypeError` → `OSError`: `subprocess.run` raises `OSError` for missing executables / permission errors, not `TypeError`. This is a bugfix.
- `ValueError`: Covers `int(float(...))` parse failures — unchanged.
- `subprocess.TimeoutExpired`: Covers the `timeout=3` guard — unchanged.

#### 6b. Verifier: Axis-Parametric S1 Scroll Verification

> **PRD OVERRIDE**: The PRD (AC-6) specifies separate `_scroll_x_before`/`_scroll_y_before` metadata keys. This spec uses a unified `_scroll_before = {"axis": ..., "value": ...}` dict instead, per DE review PB-2. See 6a PRD OVERRIDE for rationale.

**Implementation** — modify `_verify_tier1` scroll section (verifier.py:476-498):

```python
# verifier.py — class-level constants (add to StepVerifier class body)
_SCROLL_DIRS_INCREASING = frozenset({"right", "down"})
_SCROLL_DIRS_DECREASING = frozenset({"left", "up"})

# verifier.py — REPLACE scroll verification section (axis-parametric, zero duplication)
if step.action == "scroll":
    direction = step.params.get("direction", "down")
    scroll_meta = actuator_result.get("_scroll_before")

    # Tier S1: JS scroll delta (axis-parametric)
    # Defensive: use .get() for structured metadata to guard against
    # malformed dicts (e.g., missing "axis" or "value" key).
    if scroll_meta is not None:
        axis = scroll_meta.get("axis")
        scroll_before_val = scroll_meta.get("value")
        get_fn = getattr(actuator, "get_scroll_position", None)
        if axis and get_fn is not None and scroll_before_val is not None:
            scroll_after = get_fn(axis=axis)
            if scroll_after is not None:
                delta = scroll_after - scroll_before_val
                # Sign convention: browser JS scrollY increases downward,
                # scrollX increases rightward. So delta > 0 means
                # down/right, delta < 0 means up/left.
                axis_label = f"scroll{axis.upper()}"
                if direction in self._SCROLL_DIRS_INCREASING and delta > 0:
                    slog.debug(
                        "scroll_s1_confirmed",
                        axis=axis, direction=direction,
                        before=scroll_before_val, after=scroll_after,
                        delta=delta, verdict="confirmed",
                    )
                    return (
                        True,
                        f"Scroll confirmed via {axis_label} delta "
                        f"({scroll_before_val} -> {scroll_after})",
                    )
                if direction in self._SCROLL_DIRS_DECREASING and delta < 0:
                    slog.debug(
                        "scroll_s1_confirmed",
                        axis=axis, direction=direction,
                        before=scroll_before_val, after=scroll_after,
                        delta=delta, verdict="confirmed",
                    )
                    return (
                        True,
                        f"Scroll confirmed via {axis_label} delta "
                        f"({scroll_before_val} -> {scroll_after})",
                    )
                # delta == 0 falls through to S2
                slog.debug(
                    "scroll_s1_inconclusive",
                    axis=axis, direction=direction,
                    before=scroll_before_val, after=scroll_after,
                    delta=delta, verdict="inconclusive_fallthrough",
                )

    # Tier S2: Screenshot pixel-diff (unchanged)
    pixel_changed = actuator_result.get("_scroll_pixel_changed")
    if pixel_changed is True:
        return (
            True,
            "Scroll confirmed via screenshot pixel diff",
        )

    # Tier S3: Actuator success fallback (unchanged)
    if actuator_result.get("success", False):
        return (
            True,
            "Scroll accepted via actuator success (no JS or pixel signal)",
        )
```

**Key decisions** (revised after DE review):
- Single axis-parametric code path replaces the duplicated `if/else` split — ~15 lines, zero duplication
- `_scroll_before` is a structured dict `{"axis": "x"|"y", "value": int}` instead of two separate keys
- Direction-to-sign mapping uses `_SCROLL_DIRS_INCREASING`/`_SCROLL_DIRS_DECREASING` sets — trivially extensible to new axes
- S2 (pixel diff) and S3 (actuator success) are shared — they don't depend on axis

**Backward compatibility**: Existing tests that pass `{"_scroll_y_before": N}` in `actuator_result` will need updating to use `{"_scroll_before": {"axis": "y", "value": N}}`. This is a breaking change to the internal metadata format, but the format was never part of any protocol — it's internal coupling between `agent.py` dispatch and `verifier.py`. Affected test files:
- `tests/unit/test_scroll_verification.py` — update `_scroll_y_before` keys
- `tests/integration/test_target_buy_fixes.py` — update scroll test fixtures
- `tests/unit/test_scroll_action.py` — update `TestScrollOrchestratorDispatch` scroll metadata assertions

> **SCROLL ANIMATION TIMING (R2-2)**: The S1 check in the verifier calls `get_scroll_position()` after the scroll action returns. On macOS, smooth scroll animations may still be in-flight when `scrollY`/`scrollX` is queried, causing a partial delta that underreports the actual scroll. The existing `asyncio.sleep(0.5)` in the dispatch path (for screenshot diff) provides some settle time, but S1 reads scroll position in the verifier which runs after dispatch returns — by which point the 0.5s sleep has already elapsed. In practice, pyautogui scroll events complete near-instantly (they inject discrete wheel events, not smooth scroll). If smooth scroll becomes an issue, add a brief `asyncio.sleep(0.1)` before the `get_scroll_position` call in the verifier's S1 block — but this is not included in the current spec since discrete wheel events don't animate.

> **ATOMIC DEPLOYMENT (R1-4)**: The `agent.py` dispatch change (6c: writes `_scroll_before`) and the `verifier.py` change (6b: reads `_scroll_before`) MUST land in the same commit. If dispatch changes but verifier doesn't (or vice versa), scroll verification silently degrades to S2/S3 for ALL scrolls because the metadata key won't match. Engineer 2 must implement 6b and 6c together, update all 3 test files above in the same commit, and include an integration test (`test_scroll_dispatch_to_verifier_roundtrip`) that verifies the dispatch→verifier metadata contract end-to-end.

#### 6c. Agent Dispatch: Structured `_scroll_before` Metadata

> **PRD OVERRIDE**: The PRD specifies axis-specific `_scroll_x_before`/`_scroll_y_before` keys with "no dual-axis capture." This spec uses a single `_scroll_before` key with structured value, per DE review PB-2. See 6a PRD OVERRIDE for rationale.

**Implementation** — modify scroll dispatch section (agent.py:2283-2310):

```python
# agent.py — modified scroll dispatch metadata capture
elif action == "scroll":
    direction = params.get("direction", "down")
    try:
        amount = abs(int(params.get("amount", 3)))
    except (ValueError, TypeError):
        amount = 3

    # Capture scroll position before scroll for verification
    axis = "x" if direction in ("left", "right") else "y"
    scroll_before_val = None
    get_scroll = getattr(self.actuator, "get_scroll_position", None)
    if get_scroll is not None:
        scroll_before_val = get_scroll(axis=axis)
        if scroll_before_val is None:
            slog.debug(
                "scroll_before_position_unavailable",
                axis=axis, direction=direction,
                reason="get_scroll_position_returned_none",
            )

    if self.screenshot_diff:
        self.screenshot_diff.capture_before()

    if direction in ("left", "right"):
        clicks = amount if direction == "right" else -amount
        result = self.actuator.scroll(
            clicks,
            x=params.get("x"),
            y=params.get("y"),
            horizontal=True,
        )
    else:
        clicks = amount if direction == "up" else -amount
        result = self.actuator.scroll(
            clicks,
            x=params.get("x"),
            y=params.get("y"),
        )

    # Store structured scroll verification metadata
    if scroll_before_val is not None:
        result["_scroll_before"] = {"axis": axis, "value": scroll_before_val}
    if self.screenshot_diff:
        await asyncio.sleep(0.5)
        result["_scroll_pixel_changed"] = (
            self.screenshot_diff.screen_changed()
        )
```

**Key decisions** (revised after DE review):
- Single `_scroll_before` key with structured value `{"axis": "x"|"y", "value": int}` replaces `_scroll_x_before`/`_scroll_y_before`
- Axis selection is data-driven from `direction` — one code path for both horizontal and vertical pre-capture
- `get_scroll_position(axis=axis)` — single method call, no `getattr` branching for x vs y
- Graceful fallback when actuator lacks `get_scroll_position` (e.g., Hammerspoon backend) via existing `getattr` pattern

### AC-6 Test Cases

```python
class TestAC6ActuatorScrollPosition:
    """AC-6: get_scroll_position(axis=) in applescript_actuator.py."""

    def test_get_scroll_position_axis_x_safari(self):
        """get_scroll_position(axis='x') returns int for Safari (scrollX)."""

    def test_get_scroll_position_axis_x_chrome(self):
        """get_scroll_position(axis='x') returns int for Chrome (scrollX)."""

    def test_get_scroll_position_axis_y_default_unchanged(self):
        """get_scroll_position() (no axis) returns scrollY — backward compat."""

    def test_get_scroll_position_non_browser_returns_none(self):
        """get_scroll_position(axis='x') returns None for Finder."""

    def test_get_scroll_position_timeout_returns_none(self):
        """get_scroll_position(axis='x') returns None on subprocess timeout."""

    def test_get_scroll_position_oserror_returns_none(self):
        """get_scroll_position catches OSError (e.g., missing osascript)."""

    def test_get_scroll_position_invalid_axis_raises(self):
        """get_scroll_position(axis='z') raises ValueError."""
        # Security defense-in-depth: explicit contract on valid axis values


class TestAC6VerifierAxisParametric:
    """AC-6: Axis-parametric S1 scroll verification in verifier.py."""

    def test_scroll_right_js_confirmed(self):
        """scrollX 0->300 -> S1 pass for scroll right."""
        # actuator_result={"_scroll_before": {"axis": "x", "value": 0}}
        # actuator.get_scroll_position = MagicMock(return_value=300)
        # assert result[0] is True, "scrollX" in result[1]

    def test_scroll_left_js_confirmed(self):
        """scrollX 300->100 -> S1 pass for scroll left."""

    def test_scroll_down_js_confirmed_new_format(self):
        """scrollY 0->500 with new _scroll_before format -> S1 pass for down."""

    def test_scroll_up_js_confirmed_new_format(self):
        """scrollY 500->200 with new _scroll_before format -> S1 pass for up."""

    def test_scroll_right_zero_delta_falls_through(self):
        """scrollX 0->0 -> S1 inconclusive, falls to S2/S3."""

    def test_scroll_left_zero_delta_falls_through(self):
        """scrollX stays same -> falls through."""

    def test_scroll_no_scroll_before_metadata_falls_through(self):
        """No _scroll_before in actuator_result -> S1 skipped entirely."""

    def test_scroll_no_get_scroll_position_falls_through(self):
        """Actuator without get_scroll_position -> S1 skipped."""

    def test_malformed_scroll_before_missing_axis(self):
        """_scroll_before={"value": 100} (no axis) -> S1 skipped, falls through."""
        # axis = scroll_meta.get("axis") returns None -> guard fails

    def test_malformed_scroll_before_missing_value(self):
        """_scroll_before={"axis": "y"} (no value) -> S1 skipped, falls through."""
        # scroll_before_val = scroll_meta.get("value") returns None -> guard fails

    def test_scroll_right_no_horizontal_scrollbar_falls_through(self):
        """Page has no horizontal scrollbar: scrollX=0 before AND after scroll right.
        Delta=0 -> S1 inconclusive, falls to S2/S3.
        Most web pages are responsive/fluid with no horizontal scroll. This test
        documents that the delta=0 fallthrough handles this common case correctly,
        preventing future engineers from trying to 'fix' a non-bug."""
        # actuator_result={"_scroll_before": {"axis": "x", "value": 0}}
        # actuator.get_scroll_position = MagicMock(return_value=0)
        # direction="right" -> delta=0 -> S1 does NOT confirm -> falls to S2/S3

    def test_scroll_sign_convention_down_positive_delta(self):
        """Verify sign convention: scroll down -> scrollY increases -> delta > 0 -> S1 pass.
        Browser JS: scrollY=0 is top of page, increases downward.
        This is opposite to pyautogui (positive clicks = scroll UP)."""
        # actuator_result={"_scroll_before": {"axis": "y", "value": 100}}
        # actuator.get_scroll_position = MagicMock(return_value=400)
        # direction="down" -> delta=300 > 0 -> S1 confirms

    def test_scroll_sign_convention_right_positive_delta(self):
        """Verify sign convention: scroll right -> scrollX increases -> delta > 0 -> S1 pass.
        Browser JS: scrollX=0 is leftmost, increases rightward."""
        # actuator_result={"_scroll_before": {"axis": "x", "value": 0}}
        # actuator.get_scroll_position = MagicMock(return_value=200)
        # direction="right" -> delta=200 > 0 -> S1 confirms


class TestAC6DispatchScrollMetadata:
    """AC-6: Structured _scroll_before metadata in agent.py dispatch."""

    async def test_dispatch_horizontal_captures_scroll_before_axis_x(self):
        """_dispatch_action for direction='right' stores _scroll_before with axis='x'."""

    async def test_dispatch_vertical_captures_scroll_before_axis_y(self):
        """_dispatch_action for direction='down' stores _scroll_before with axis='y'."""

    async def test_dispatch_scroll_before_is_structured_dict(self):
        """_scroll_before value is {"axis": str, "value": int}, not a bare int."""

    async def test_dispatch_scroll_no_get_scroll_position_no_metadata(self):
        """Actuator without get_scroll_position -> no _scroll_before in result."""

    async def test_scroll_dispatch_to_verifier_roundtrip(self):
        """End-to-end: dispatch writes _scroll_before metadata, verifier reads it.
        Validates the metadata contract between agent.py dispatch and verifier.py.
        If either side changes the key name or dict structure independently,
        this test fails — preventing silent S1 degradation to S2/S3."""
        # 1. Mock actuator with get_scroll_position returning 0, then 500
        # 2. Call _dispatch_action(scroll, direction=down)
        # 3. Assert result contains _scroll_before={"axis": "y", "value": 0}
        # 4. Feed that result dict directly to _verify_tier1
        # 5. Assert S1 confirms (not falls through to S2/S3)
```

### PB4/PB5/PB7 Review Tests

These verify the shipped fixes from commit ad751b5 remain intact. These go in the same test file as AC-6 tests.

```python
class TestPB4WordBoundaryReview:
    """PB4 review: word-boundary in required-keywords (same as AC-4, different angles)."""

    def test_hyphenated_site_name_matches(self):
        """'best-buy' as required-keyword matches 'buy on best-buy'."""

    def test_possessive_form_matches(self):
        """'target' matches 'buy from target's website'."""

class TestPB7DomainScopeReview:
    """PB7 review: domain injection scoped to matching-domain steps (same as AC-5)."""

    def test_multi_domain_plan_only_target_injected(self):
        """Plan with target.com + google.com: only target.com steps get injection."""
```

---

## Testing Strategy

### Test Organization

| Test File | Marker | Slice | ACs Covered |
|-----------|--------|-------|-------------|
| `tests/unit/test_type_text_hardening.py` | `@pytest.mark.unit` | Slice 1 | AC-1, AC-2, AC-3, AC-4, AC-5 |
| `tests/unit/test_type_text_hardening_scroll.py` | `@pytest.mark.unit` | Slice 2 | AC-6, PB4/PB5/PB7 review |

### Test Helpers

Both test files use the standard pattern:

```python
def _make_config(**overrides) -> AgentConfig:
    """Standard test config factory. model_provider="local" is MANDATORY to
    prevent .env leaking AGENT_MODEL_PROVIDER=anthropic into pydantic-settings."""
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",  # MUST pin — .env may override via pydantic
        "grounding_model": "",
        "grounding_server_url": "",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)
```

**NOTE**: The `model_provider="local"` key is **mandatory** in all `_make_config()` helpers across both new test files. Without it, `.env` can leak `AGENT_MODEL_PROVIDER=anthropic` via pydantic-settings, causing test failures in local dev environments. This matches the existing pattern in `test_vision_arch_improvements.py` and `test_site_routing.py`.

Agent construction follows `test_site_routing.py:585-605` pattern — mock all protocol components.

### Regression Safety (AC-7)

Command: `.venv/bin/python -m pytest tests/unit/ tests/integration/ -x -q`

All existing tests in `tests/unit/` and `tests/integration/` must continue to pass. The `tests/e2e/` directory (if present) is excluded because it requires a live macOS desktop.

### Edge Cases to Cover

| Edge Case | AC | Test |
|-----------|----|----|
| Product name with spaces | AC-1, AC-3 | `"queen size bed sheets"` → encoded URL |
| Product name with `&` in path | AC-3 | `"bed & bath"` → `%26` via `quote()` (no longer out of scope — PRD tightened) |
| Substring false positive | AC-4 | `"retarget"` → NOT match `"target"` |
| Multi-domain plan | AC-5 | Only matching-domain steps get injection |
| scrollX delta > 0 for right | AC-6 | S1 pass via `_scroll_before.axis="x"` |
| scrollX delta < 0 for left | AC-6 | S1 pass via `_scroll_before.axis="x"` |
| scrollX delta == 0 | AC-6 | S1 inconclusive, falls to S2/S3 |
| No `get_scroll_position` | AC-6 | S1 skipped, falls to S2/S3 |
| Vertical scroll new format | AC-6, AC-7 | `_scroll_before.axis="y"` backward compat |
| OSError in subprocess | AC-6 | `get_scroll_position` returns None (bugfix from TypeError) |
| Malformed `_scroll_before` (missing axis) | AC-6 | S1 skipped, falls through to S2/S3 |
| Malformed `_scroll_before` (missing value) | AC-6 | S1 skipped, falls through to S2/S3 |
| `&` in query param value | AC-3 | Known limitation — `parse_qs` splits on `&` (Out of Scope) |
| expand() → compile pipeline | AC-1 | End-to-end test exercises real expand+compile path |
| Sign convention verification | AC-6 | down→positive delta, right→positive delta |
| Quoted text containing "in the" | AC-2 | `"sign in"` in username field — quotes anchor lazy match |
| No horizontal scrollbar on page | AC-6 | scrollX=0 before and after → delta=0 → falls to S2/S3 |
| Domain substring false positive | AC-5 | `nottarget.com` does NOT match `target.com` (urlparse fix) |
| Encoded slash `%2F` in path | AC-3 | Known limitation — `unquote()` decodes to `/`, documents trade-off |
| Elastic bounce overscroll | AC-6 | delta=0 falls through to S2/S3 (Out of Scope per PRD) |
| Exception fallback dead code | AC-3 | `urlparse` never throws on string — both test AND dead code removed (CQ-3) |
| Scheme-less URL in domain injection | AC-5 | `urlparse().hostname` returns None — skip injection with debug log (R1-2) |
| Dispatch→verifier metadata contract | AC-6 | Roundtrip integration test validates `_scroll_before` format end-to-end (R1-4) |
| Invalid axis parameter | AC-6 | `get_scroll_position(axis='z')` raises `ValueError` — defense-in-depth (SEC PB-1) |
| `userinfo@host` URL spoofing | AC-5 | `target.com@evil.com` → hostname=`evil.com` → NOT injected (SEC PB-2) |
| Unicode `\b` word boundary | AC-4 | ASCII-safe; documented limitation for non-ASCII keywords (SEC PB-3) |
| Subdomain TOCTOU on domain injection | AC-5 | Not sole defense — verifier independently checks browser URL post-navigation (SEC PB-5) |
| ReDoS on Type regex | AC-2 | Input is human-authored skill text, never LLM output; document length guard for future (SEC PB-6) |
| `javascript:`/`data:` URL schemes | AC-5 | Pre-existing gap in `open_url` actuator — out of scope, documented for future (SEC PB-7) |

---

## SOTA Approach References

| Decision | SOTA Source | Approach |
|----------|-----------|----------|
| URL-based search bypass | SOTA §1a (Playwright, Selenium, Cypress) | Direct URL navigation, SOTA-aligned |
| `urllib.parse` for encoding | SOTA §1b + Short-Seller fix | Component-based: `urlparse` + `quote(path, safe='/')` + `urlunparse` |
| `\b` word boundaries | SOTA §2b | Python 3 default `re.UNICODE` handles ASCII correctly |
| `window.scrollX` via JS | SOTA §3a (Playwright `page.evaluate`) | Same AppleScript+JS bridge as scrollY |
| Parameterized vs separate method | DE review PB-1 | `get_scroll_position(axis="y")` — no protocol break, zero duplication |
| Structured scroll metadata | DE review PB-2 | `_scroll_before = {"axis": "x/y", "value": int}` — extensible to N axes |
| Axis-parametric verifier | DE review PB-3 | Single code path with `_SCROLL_DIRS_INCREASING`/`_SCROLL_DIRS_DECREASING` sets |
| Domain injection fix | DE review PB-12 | `urlparse().hostname` replaces substring `in` — prevents `nottarget.com` false positive |
| Import consistency | DE review PB-13 | Remove deferred `from urllib.parse import ...`; use module-level `urllib.parse.*` throughout |
| Direction constants | SS Issue 8 | `_SCROLL_DIRS_INCREASING`/`_SCROLL_DIRS_DECREASING` as class-level `frozenset` constants |

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `get_scroll_position(axis="x")` permission denied in Safari | Medium | Same error handling as existing method — returns None, falls to S2/S3 |
| Existing scroll tests break from metadata format change | Medium | `_scroll_y_before` → `_scroll_before.axis="y"` requires updating 3 test files (listed in spec 6b) |
| `TypeError` → `OSError` exception change | Low | Correct fix; `TypeError` was never raised by `subprocess.run` |
| Double-encoding from pre-encoded URLs | Low | `unquote()` before `quote()` normalizes; tested with `%20` input |
| ~~Component encoding in except fallback~~ | ~~Low~~ | Removed (CQ-3): `urlparse` never throws on string input — entire except block was dead code |
| `\b` regex edge cases with Unicode | Low | Python 3 `re.UNICODE` is default; skill templates use ASCII keywords |
| Domain injection substring false positive | Medium | Fixed: `urlparse().hostname` replaces `in` substring check; tested with `nottarget.com` |
| `urlparse` per-step cost in domain injection | Negligible | Conscious decision: 1-3 `open_url` steps per plan × microsecond `urlparse` = negligible vs. correctness gain (PB-14) |
| macOS elastic bounce scroll | Low | scrollY/scrollX snaps back to boundary after overscroll → delta=0 → falls to S2/S3. Out of Scope per PRD; no special handling needed since delta=0 fallthrough is correct |
| `unquote()` destroys encoded slashes (`%2F`) | Low | Skill template URLs never contain pre-encoded slashes. Trade-off documented; replace with targeted `%20` decode if needed in future |
| `rstrip(".")` destroys URLs ending in `.` | Low | Pre-existing issue in compiler regex cascade (not introduced by this spec). Amplified by encoding changes since more URLs now flow through the encoding path. Out of scope for this feature — tracked as known pre-existing issue |
