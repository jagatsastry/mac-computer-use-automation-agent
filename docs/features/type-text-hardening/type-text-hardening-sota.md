# Type-Text Hardening: State of the Art Research

## 1. URL-Based Search Bypass vs Type-and-Enter

### Problem Landscape

Web automation frameworks face a fundamental choice when executing search operations: navigate directly via a parameterized URL (e.g., `https://www.amazon.com/s?k=shampoo`) or simulate a user typing a query into a search box and pressing Enter.

**Our current approach**: Skill templates use direct URL navigation (`open_url` action) with `{{product}}` parameter substitution into URL query strings. The `_compile_skill_instruction()` method in `agent.py` detects `Navigate to <URL>` patterns and emits `open_url` ActionSteps. URL encoding is handled inline via `urllib.parse`.

### SOTA Approaches

#### 1a. Direct URL Navigation (Preferred by Major Frameworks)

**Playwright**: `page.goto(url)` is the primary navigation mechanism. Playwright documentation explicitly recommends direct URL navigation over UI interaction for deterministic test setup. Search is done via URL parameters when possible.

**Selenium**: `driver.get(url)` for navigation. The Selenium best practices guide recommends `driver.get()` over `find_element().send_keys()` + submit for search when the URL schema is known, because it eliminates timing issues with element readiness.

**Cypress**: `cy.visit(url)` is the standard. Cypress documentation states: "Anti-pattern: Using the UI to set up state. Instead, use `cy.visit()` with URL parameters."

**Trade-offs**:
- (+) Deterministic: no element-finding, no focus issues, no typing errors
- (+) Fast: single HTTP request vs multiple UI interactions
- (+) Resilient: immune to UI redesigns that move the search box
- (-) Requires knowing the URL schema for each site
- (-) May bypass client-side JavaScript that enriches search (rare for e-commerce)
- (-) Some sites reject direct URL navigation with anti-bot measures

#### 1b. URL Encoding Best Practices

**`urllib.parse.urlencode` vs manual encoding**: The Python standard library's `urllib.parse` module is the canonical approach. Key functions:

| Function | Purpose | When to Use |
|----------|---------|-------------|
| `urlencode(params, doseq=True)` | Encode dict to `key=value&key=value` | Building query strings from params |
| `quote(string, safe='')` | Percent-encode a string | Encoding individual path segments |
| `quote_plus(string)` | Like quote but spaces become `+` | Encoding form data values |
| `urlparse` / `urlunparse` | Parse/reconstruct URLs | Modifying existing URLs |
| `parse_qs` / `parse_qsl` | Parse query strings | Extracting params from URLs |

**Common pitfalls**:

1. **Double-encoding**: Calling `quote()` on an already-encoded string produces `%2520` instead of `%20`. Our current code at `agent.py:1750-1761` has a partial guard: it uses `parse_qs` + `urlencode` for URLs with query params, but falls back to `str.replace(" ", "%20")` for URLs without query params. The fallback is fragile -- it misses other special characters (`&`, `#`, `+`, etc.).

2. **`parse_qs` wraps values in lists**: `parse_qs("k=foo")` returns `{"k": ["foo"]}`, not `{"k": "foo"}`. The `doseq=True` flag on `urlencode` handles this correctly (our code does this).

3. **The `safe` parameter in `quote()`**: By default, `quote()` treats `/` as safe (not encoded). For query parameter values, use `quote(value, safe='')` to encode everything. For path segments, the default `safe='/'` is correct.

4. **Unicode handling**: `urllib.parse.quote()` handles Unicode correctly in Python 3 (encodes to UTF-8 then percent-encodes). Manual encoding with `str.replace` does not handle non-ASCII characters.

5. **Fragment handling**: URLs with `#` fragments need special care. `urlparse` correctly separates fragments, but `str.replace` on the whole URL would encode the `#`.

### Common Failure Modes

1. **Spaces in product names**: "queen size bed sheets" -> must become `queen+size+bed+sheets` or `queen%20size%20bed%20sheets` in the query string. Our skill templates embed `{{product}}` directly into URLs (e.g., `amazon.com/s?k={{product}}`), so the compiler must encode.

2. **Special characters**: Products like "USB-C cable" or "16\" laptop" contain characters that need encoding. The `&` in "bed & bath" would break query string parsing if unencoded.

3. **Pre-encoded URLs**: If the LLM generates a URL like `target.com/s?searchTerm=queen%20sheets`, re-encoding would double-encode. The current `parse_qs` + `urlencode` approach handles this correctly for query params but not for path segments.

4. **Empty query params**: `parse_qs` drops empty values by default. The `keep_blank_values=True` flag (present in our code) prevents this.

### Recommended Approach for Our Context

**PROCEED** with URL-based search bypass. It is the SOTA approach used by all major automation frameworks. Specific hardening suggestions:

1. **Replace manual `str.replace(" ", "%20")` fallback** with `urllib.parse.quote(destination, safe=':/?#[]@!$&\'()*+,;=-._~')` for full URL encoding when no query params are present. This handles all special characters, not just spaces.

2. **Add `quote_plus` for query parameter values** before template substitution. When `{{product}}` is substituted into `?k={{product}}`, the value should be `quote_plus()`-encoded BEFORE URL construction, not after.

3. **Guard against double-encoding**: Before encoding, check if the string is already encoded (contains `%XX` patterns). Use `urllib.parse.unquote()` first, then re-encode canonically.

---

## 2. Skill/Template Compiler Pattern Matching

### Problem Landscape

The skill compiler (`_compile_skill_instruction()` in `agent.py:1730-1926`) transforms natural language skill step instructions into executable `ActionStep` objects using a cascade of regex patterns. Each pattern attempts to match a specific instruction format (e.g., "Navigate to <URL>", "Click on <element>", "Type <text> and press Enter").

**Current patterns** (in match order):

| Priority | Pattern | Action |
|----------|---------|--------|
| 1 | `Use done` / `done` / `complete task` | `done` |
| 2 | `Navigate to <destination>` | `open_url` or `click` |
| 3 | `Use activate_app to open <app>` / `Open <app>` | `activate_app` (+ optional `open_url`) |
| 4 | `Use open_url to navigate to <URL>` | `open_url` |
| 5 | `Find <context> and click "<element>"` | `click` |
| 6 | `Find <element> and click it/them` | `click` |
| 7 | `Click on/the <element>` | `click` |
| 8 | `Type "<text>" in the <element> and press Enter` | `type_text` + `press_key` |
| 9 | `Press <key combo>` | `press_key` |

**Fallthrough**: If no pattern matches, the compiler returns `None`, and the instruction is sent to the LLM planner for interpretation.

### SOTA Approaches

#### 2a. Regex Pattern Cascades

The current approach is a **priority-ordered regex cascade** -- a well-established pattern used in many NLP preprocessing pipelines and command dispatchers. This is the same architecture used by:

- **Flask/FastAPI URL routing**: Priority-ordered regex matching against path patterns
- **Unix shell command parsing**: Ordered pattern matching for command interpretation
- **IRC/Slack bot frameworks**: Regex-based command dispatchers

**Trade-offs**:
- (+) Explicit, debuggable, no external dependencies
- (+) Fast: linear scan with early exit on first match
- (+) Predictable: match order is visually obvious in code
- (-) Fragile to instruction variations the LLM produces
- (-) Adding new patterns requires careful ordering analysis
- (-) Regex complexity grows with edge cases

#### 2b. Regex Best Practices Relevant to Our Compiler

**Greedy vs. Lazy matching**: The `type_match` pattern uses `(.+?)` (lazy) for the typed text, which is correct -- it avoids consuming "and press Enter" as part of the text. However, `click_match` uses `(.+)` (greedy), which is acceptable because it anchors to end-of-line.

**Word boundary (`\b`)**: The router's `_build_site_patterns()` in `router.py:44-55` uses `\b` for site entity extraction. Known issues with `\b`:
- `\b` is locale-dependent for Unicode. "cafe" matches `\b` but "caf\u00e9" may not, depending on the regex engine's Unicode support.
- In Python's `re` module with default flags, `\b` treats only `[a-zA-Z0-9_]` as word characters. Non-ASCII characters are NOT word characters unless `re.UNICODE` flag is used (which is default in Python 3, but worth being explicit).
- Hyphens in site names (e.g., "best-buy") are word boundaries, so `\bbest-buy\b` works correctly.

**Escaping user input**: The router correctly uses `re.escape()` on site names (`router.py:45`). The compiler does NOT escape user-supplied parameter values before embedding them in patterns, but this is safe because the compiler only uses `re.match/re.sub` on the instruction text, not on user input.

**Anchoring**: All compiler patterns use `^` (start anchor). This is correct for preventing partial matches in the middle of instructions.

#### 2c. Common Regex Edge Cases in Our Context

1. **Quoted text with internal quotes**: The type_match pattern `"?(.+?)"?` uses optional quotes. If the LLM produces `Type "queen "size" sheets"`, the lazy match would capture `queen ` and stop at the first closing quote. The current regex handles this acceptably because LLMs rarely produce nested quotes.

2. **Multi-line instructions**: The `re.IGNORECASE` flag is used but not `re.DOTALL`. Since instructions are parsed line-by-line by `_parse_skill_steps()`, multi-line instructions would be split before compilation. This is correct behavior.

3. **Trailing punctuation**: The compiler strips trailing periods (`text.rstrip(".")`) but not other punctuation (`,`, `;`, `!`). LLMs occasionally produce trailing commas in lists.

4. **URL extraction greediness**: The `Navigate to` handler uses `re.sub` to strip the prefix, then checks if the remainder is a URL. The URL regex `^https?://\S+` would match `https://example.com)` including the trailing parenthesis -- a common issue when URLs appear in parenthetical text.

5. **The `buy_on_target.md` blocker**: The skill template uses `Navigate to https://www.target.com/s?searchTerm={{product}}` where `{{product}}` gets substituted with user text like "queen size bed sheets". The spaces in the substituted text make the URL invalid. The compiler's URL-encoding logic at `agent.py:1750-1761` handles query params via `parse_qs`+`urlencode` but has a gap: if the URL has a query string with spaces (post-substitution), `urlparse` may misparse it because `urlparse` expects well-formed URLs.

### Common Failure Modes

1. **Pattern ordering conflicts**: If a new pattern is added that overlaps with an existing one, the first match wins. For example, adding "Navigate to <app>" would conflict with "Navigate to <URL>".

2. **LLM instruction drift**: The compiler's patterns are tightly coupled to the skill template format. If the LLM rephrases "Click on the Add to cart button" as "Select the Add to cart button", it falls through to the LLM planner (via `_ACTION_ALIASES`, "select" maps to "click", but the compiler regex won't match).

3. **Parameter substitution before compilation**: Parameters are substituted by `SkillRegistryImpl.expand()` BEFORE compilation. This means `{{product}}` is already replaced with the actual value (e.g., "queen size bed sheets") when the compiler sees it. Special characters in the substituted value can break regex patterns or URL structure.

### Recommended Approach for Our Context

**PROCEED** with the regex cascade compiler. It is appropriate for the current scale (~9 patterns) and deterministic instruction format. Specific hardening suggestions:

1. **Pre-encode URL parameters before template substitution**: In `SkillRegistryImpl.expand()` or in the skill template format itself, URL-encode parameter values before inserting them into URL templates. This prevents the compiler from needing to re-parse and re-encode.

2. **Add trailing punctuation stripping**: Extend `text.rstrip(".")` to `text.rstrip(".,;:!?")` to handle more LLM punctuation variance.

3. **URL boundary fix**: Change `\S+` to `[^\s)>]+` in URL-matching patterns to exclude common trailing delimiters (parentheses, angle brackets).

4. **Consider precompiling patterns**: Move `re.compile()` calls to class-level constants for marginal performance improvement and better readability.

---

## 3. Scroll Verification in UI Automation

### Problem Landscape

Verifying that a scroll action actually changed the viewport is one of the hardest problems in UI automation. Unlike click or type actions (which produce discrete, observable state changes), scroll produces a continuous visual change that may or may not have occurred depending on whether the page has more content to scroll.

**Our current approach** (3-tier scroll verification in `verifier.py:476-513`):

| Tier | Signal | Latency | Reliability |
|------|--------|---------|-------------|
| S1 | `window.scrollY` delta via AppleScript+JS | ~50ms | High for vertical, N/A for horizontal |
| S2 | Screenshot pixel-diff | ~500ms | Medium (false negatives on static content) |
| S3 | Actuator success fallback | ~0ms | Low (always true if pyautogui succeeds) |

### SOTA Approaches

#### 3a. JavaScript scrollY/scrollX Queries

**Playwright**: Uses `page.evaluate('window.scrollY')` before and after scroll, comparing delta. This is the most reliable method for web content. Playwright also provides `element.scrollIntoViewIfNeeded()` which combines scroll and verification.

**Selenium**: `driver.execute_script('return window.pageYOffset')` before and after. Selenium's `Actions.scroll_to_element()` uses this internally.

**Cypress**: `cy.scrollTo()` uses `window.scrollY` internally and verifies the scroll position matches the target.

**Trade-offs**:
- (+) Exact: gives pixel-precise scroll position
- (+) Fast: JavaScript execution is sub-millisecond
- (+) Works for both absolute and relative scroll verification
- (-) Only works in browsers (not native apps)
- (-) Requires browser-specific AppleScript bridges (our code handles Safari and Chrome)
- (-) Does not detect horizontal scroll (`window.scrollX` is available but our `get_scroll_position()` only returns `scrollY`)

**Our gap**: `get_scroll_position()` in `applescript_actuator.py:311-349` only returns vertical scroll position. Horizontal scroll (`scrollX`) is not queried, so Tier S1 always falls through to S2/S3 for horizontal scrolls. This is the PB8 bug referenced in the task list.

#### 3b. Horizontal Scroll Detection

Horizontal scroll verification requires `window.scrollX` (or `document.documentElement.scrollLeft`). The implementation pattern mirrors the vertical case:

```python
# Safari
'tell application "Safari" to do JavaScript "window.scrollX" in current tab of front window'
# Chrome
'tell application "Google Chrome" to execute front window\'s active tab javascript "window.scrollX"'
```

**Frameworks' approach**: Playwright and Cypress support horizontal scroll natively via `page.mouse.wheel(deltaX, deltaY)` and verify via `scrollX`. Selenium uses `Actions.scroll_by_amount(deltaX, deltaY)`.

**For native macOS apps**: No JavaScript bridge exists. Scroll verification must rely on:
1. Accessibility API: Some apps expose scroll position via `AXScrollBar` role elements
2. Screenshot pixel-diff: Compare before/after screenshots
3. Actuator trust: Accept that pyautogui's scroll command succeeded

#### 3c. Pixel-Diff Approaches for Scroll Confirmation

Screenshot comparison is the universal fallback for scroll verification when JavaScript is unavailable (native apps, non-standard browsers).

**Approaches**:

1. **Full-frame pixel comparison**: Compare entire screenshots before and after scroll. If > N% of pixels changed, scroll occurred. Our current `screenshot_diff` module uses this approach.
   - (+) Simple, works for any app
   - (-) False positives from animations, notifications, cursor blink
   - (-) False negatives when page content is visually uniform (e.g., a plain white page)

2. **Vertical strip comparison**: Compare only a narrow vertical strip (e.g., scrollbar region) before and after. If the scrollbar thumb moved, scroll occurred.
   - (+) More targeted, fewer false positives
   - (-) Some apps hide scrollbars (macOS "When scrolling" preference)

3. **Content displacement measurement**: Use image correlation (e.g., `cv2.matchTemplate`) to detect how many pixels the content shifted. If the shift matches the expected scroll amount, verification passes.
   - (+) Quantitative: measures actual displacement
   - (-) Expensive: requires OpenCV or similar library
   - (-) Fails on pages with repeating visual patterns

4. **Structural hash comparison**: Hash the screenshot into perceptual segments, compare before/after. More robust than pixel comparison for detecting meaningful changes.
   - (+) Robust to anti-aliasing and minor rendering differences
   - (-) More complex implementation

**Industry practice**: Most production UI automation frameworks (Playwright, Selenium, Cypress) do NOT use pixel-diff for scroll verification. They rely on JavaScript APIs. Pixel-diff is used only in visual regression testing tools (Percy, Applitools, Chromatic) where the goal is to detect ANY visual change, not specifically scroll.

### Common Failure Modes

1. **Scroll at page boundary**: Scrolling down when already at the bottom of a page produces `scrollY` delta = 0. The scroll "succeeded" (pyautogui sent the event) but had no effect. Our S1 tier correctly falls through when delta = 0, but S3 would report success (false positive).

2. **Lazy-loaded content**: Scrolling triggers content loading (infinite scroll). The `scrollY` may not change until new content renders. A small delay (our 0.5s sleep at `agent.py:2312`) helps but is not guaranteed.

3. **Horizontal scroll in vertical-only elements**: If an element only scrolls vertically, horizontal scroll attempts produce no visible change. `scrollX` stays at 0.

4. **Elastic/bounce scroll on macOS**: macOS allows "rubber-band" overscroll. `scrollY` may temporarily go negative (scroll past top) then bounce back. Capturing `scrollY` too early during the bounce gives a false delta.

5. **Scroll within iframes or shadow DOM**: `window.scrollY` returns the main frame's scroll position. If the scroll target is inside an iframe, the main `scrollY` won't change even though content scrolled.

### Recommended Approach for Our Context

**PROCEED** with the current tiered scroll verification (S1/S2/S3) with specific hardening:

1. **Add `scrollX` support to `get_scroll_position()`**: Extend the method to return both `scrollX` and `scrollY` (or accept a parameter for axis). Wire horizontal scroll verification through S1.

2. **Add boundary detection**: When S1 delta = 0 AND S2 pixel-diff is False, report scroll failure explicitly rather than falling through to S3 actuator trust. This prevents false-positive verification at page boundaries.

3. **Increase S2 delay for lazy-loaded content**: The current 0.5s delay before screenshot capture may be too short for slow-loading pages. Consider making it configurable or using a 1.0s default.

4. **Log scroll position in actuator result**: Include `_scroll_y_before` and `_scroll_y_after` in the actuator result for debugging. Currently only `_scroll_y_before` is stored.

---

## Overall Recommendation

**PROCEED** with the current architectural approach across all three areas:

| Area | Current Approach | Verdict | Key Hardening |
|------|-----------------|---------|---------------|
| Search bypass | URL-based `open_url` with template params | Correct (SOTA) | Fix URL encoding to use `urllib.parse` consistently; pre-encode params before substitution |
| Skill compiler | Regex cascade in `_compile_skill_instruction()` | Correct (appropriate scale) | Fix URL boundary regex; strip more trailing punctuation; consider pre-encoding |
| Scroll verification | 3-tier (JS scrollY -> pixel-diff -> actuator trust) | Correct (SOTA-aligned) | Add `scrollX` for horizontal; detect page boundaries; increase pixel-diff delay |

The most impactful fix is **URL encoding hardening** (Area 1), which directly unblocks the `buy_on_target` skill template. The second priority is **scroll S1 horizontal support** (Area 3), which closes the PB8 gap. The compiler regex improvements (Area 2) are lower priority but straightforward to implement alongside the URL encoding fix.
