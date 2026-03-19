# Speed Phase 1: State-of-the-Art Research

**Date**: 2026-03-16
**Author**: SOTA Researcher (Claude Opus 4.6)
**Scope**: JS injection, AX confidence calibration, fast browser verification, AX vs DOM tradeoffs

---

## 1. Browser Automation via JS Injection through AppleScript

### Problem Landscape

macOS provides a unique channel for browser automation: Apple Events allow external processes to execute arbitrary JavaScript inside Safari and Chrome tabs via `osascript`. This is the same mechanism used by Keyboard Maestro, Hammerspoon, Shortcat, and other macOS automation tools. The channel runs JavaScript in the **exact same V8/JavaScriptCore context** as user-triggered code, with full access to cookies, localStorage, and the authenticated DOM.

Two distinct browser APIs exist:
- **Safari**: `tell application "Safari" to do JavaScript "..." in current tab of front window`
- **Chrome**: `tell application "Google Chrome" to execute front window's active tab javascript "..."`

Firefox does **not** expose a scripting definition to AppleScript, so it is permanently excluded from this channel.

### SOTA Approaches (Ranked by Relevance)

#### 1. Single-Call JSON State Extraction (Highest Relevance)

Package multiple DOM queries into one JS expression that returns a JSON string. This avoids multiple osascript round-trips.

```javascript
JSON.stringify({
  url: location.href,
  title: document.title,
  readyState: document.readyState,
  activeTag: document.activeElement?.tagName,
  activeValue: document.activeElement?.value || '',
  scrollY: window.scrollY,
  selectedText: window.getSelection().toString()
})
```

**Critical constraint**: `do JavaScript` can only return **string primitives** to AppleScript. DOM objects, arrays, and complex objects return `missing value`. Always wrap results in `JSON.stringify()` or extract `.innerHTML`, `.value`, `.textContent`, etc.

**References**:
- [alexwlchan: Get and manipulate contents of a page in Safari with "do JavaScript"](https://alexwlchan.net/til/2024/applescript-do-javascript/)
- [AppleScript + Chrome: The Browser Automation Cheat Code](https://dev.to/haoyang_pang_a9f08cdb0b6c/the-browser-automation-cheat-code-nobody-talks-about-applescript-chrome-52ha)
- [Kevin Marsden: AppleScript Executing JavaScript in Safari and Chrome](https://kmarsden.com/2016/06/applescript-executing-javascript-in-safari-and-chrome/)

#### 2. JXA (JavaScript for Automation) via osascript -l JavaScript

JXA avoids AppleScript's string escaping hell. It runs the same JavaScriptCore engine as Safari but in an `osascript` context with Automation object access.

```bash
osascript -l JavaScript -e '
var chrome = Application("Google Chrome");
var tab = chrome.windows[0].activeTab;
tab.execute({javascript: "JSON.stringify({url: location.href, title: document.title})"});
'
```

**Advantages**: Cleaner quoting, ES6 support, no AppleScript compilation overhead.
**Disadvantage**: Still ~2s per call round-trip for basic execution.

**References**:
- [JXA Cookbook: Using JavaScript for Automation](https://github.com/JXA-Cookbook/JXA-Cookbook/wiki/Using-JavaScript-for-Automation)
- [Michael Bianco: Scripting macOS with Javascript Automation](https://mikebian.co/scripting-macos-with-javascript-automation/)

#### 3. Keyboard Maestro Pattern: Front Browser Abstraction

Keyboard Maestro uses a "Front Browser" abstraction that auto-targets whichever browser (Chrome/Safari) was most recently active. Under the hood, it uses the same Apple Events + JS injection mechanism but wraps it with:
- XPath-based field identification
- `document.querySelector` fallback for dynamic forms
- A `document.kmvar` dictionary for passing variables from the macro engine into the browser JS context

**References**:
- [Keyboard Maestro Wiki: Web Browser Automation](https://wiki.keyboardmaestro.com/assistance/Web_Browser_Automation)
- [Keyboard Maestro Wiki: Browser Form Actions](https://wiki.keyboardmaestro.com/actions/Browser_Form_Actions)

#### 4. Hammerspoon + JXA Bridge

Hammerspoon lacks native browser APIs but calls JXA via `hs.osascript.javascript()`. This adds a Lua-to-JXA bridge layer that can execute browser JavaScript. The pattern is typically used for tab management (find tab by title, get URL) rather than DOM manipulation.

**References**:
- [Hammerspoon GitHub](https://github.com/Hammerspoon/hammerspoon)
- [Automators Talk: Anyone using Hammerspoon?](https://talk.automators.fm/t/anyone-using-hammerspoon/6588)

#### 5. PyObjC Direct (No osascript)

Instead of shelling out to `osascript`, use PyObjC's `NSAppleScript` or `OSAScript` to compile and execute AppleScript directly from Python. This eliminates the ~50ms process spawn overhead per call.

**References**:
- [Michael Bianco: Scripting macOS with Javascript Automation](https://mikebian.co/scripting-macos-with-javascript-automation/) (mentions PyObjC perf advantage)

### Key Failure Modes

| Failure Mode | Cause | Mitigation |
|---|---|---|
| **10-15s latency** on Big Sur+ | Safari's "Automatically Show Web Inspector for JSContexts" enabled | Uncheck in Develop menu; documented fix drops latency to ~0.27s |
| **"JavaScript from Apple Events is turned off"** | User hasn't enabled the Develop > Allow JavaScript from Apple Events setting | Detect with a canary JS call; guide user through setup |
| **`missing value` return** | Returning DOM objects/arrays instead of strings | Always wrap in `JSON.stringify()` or use `.textContent`/`.value` |
| **Chrome window not visible to AppleScript** | Multi-profile Chrome windows may not be enumerable | System Events + DevTools Console fallback (pbcopy + Cmd+Option+J) |
| **Async result not available** | `fetch()` or other async JS hasn't resolved yet | Use `document.title` as data channel: write async result there, read back via AppleScript after a small delay |
| **TCC permission denial** | Automation permission not granted in System Settings | Check permission at startup; fail fast with clear error |
| **React/SPA change events not firing** | Setting `.value` directly doesn't trigger React's synthetic event system | Dispatch `new Event('input', {bubbles: true})` after setting value |

**References**:
- [Keyboard Maestro Forum: Executing JavaScript in Safari Weirdly Slow on Big Sur](https://forum.keyboardmaestro.com/t/executing-javascript-in-safari-weirdly-slow-on-big-sur/22607)
- [MacScripter: Javascript execution in Safari is very slow](https://www.macscripter.net/t/javascript-execution-in-safari-is-very-slow/73051)
- [Apple Developer Forums: AppleScript Safari "do JavaScript"](https://developer.apple.com/forums/thread/679233)
- [HackTricks: macOS TCC](https://book.hacktricks.wiki/en/macos-hardening/macos-security-and-privilege-escalation/macos-security-protections/macos-tcc/index.html)

### Recommended Approach for Our Context

Our codebase already has `_get_browser_js()` in `applescript_actuator.py` that supports both Safari and Chrome JS injection with a 2s timeout. The key optimization is:

1. **Consolidate to single-call JSON state extraction**: Instead of multiple `_get_browser_js()` calls for URL, scroll position, active element value, and selected text, compose one JS expression that returns everything as a single JSON blob. This reduces 4x osascript round-trips (~8s worst case) to 1x (~0.3s with Web Inspector disabled).

2. **Add `document.elementFromPoint(x, y)` verification**: After a click at coordinates (x, y), inject JS to verify the element at that point matches expectations. This gives sub-50ms post-click verification without vision model calls.

3. **Use `document.readyState` + `MutationObserver` for page load detection**: Instead of waiting for vision model to confirm a page loaded, use JS-based detection.

---

## 2. Accessibility API Grounding Confidence Calibration

### Problem Landscape

Our `AccessibilityBridge._element_match_score()` currently uses a hand-tuned scoring function with token overlap (0.45 + 0.4 * overlap ratio). This needs calibration against known-good matches to determine optimal thresholds for different action types (click vs. type vs. submit).

### SOTA Approaches (Ranked by Relevance)

#### 1. Similo: Multi-Attribute Weighted Similarity (Highest Relevance)

The **Similo algorithm** (Nass & Ahlgren, ACM TOSEM 2023) is the SOTA for robust web element localization. It uses 14 properties with weighted scoring:

| Property | Similarity Function | Weight |
|---|---|---|
| Tag | Equality (0 or 1) | 1.5 |
| Class | Levenshtein Similarity | 0.5 |
| Name | Equality | 1.5 |
| ID | Equality | 1.5 |
| HRef | Levenshtein Similarity | 0.5 |
| Alt | Levenshtein Similarity | 0.5 |
| Absolute XPath | Levenshtein Similarity | 0.5 |
| ID-XPath | Levenshtein Similarity | 0.5 |
| Is Button | Equality | 0.5 |
| Location | Euclidean Distance | 0.5 |
| Area | Euclidean Distance | 0.5 |
| Shape | Euclidean Distance | 0.5 |
| Visible Text | Levenshtein Similarity | 1.5 |
| Neighbor Texts | Word set similarity | 1.5 |

**Scoring formula**: `Score(T,C) = SUM(weight_i * similarity_i(T.attr_i, C.attr_i))`

Key insight: **Stable properties** (tag, name, id, visible text, neighbor texts) get weight **1.5**; volatile properties (class, href, xpath, location, area, shape) get weight **0.5**.

Result: 88% correct localization vs. 76% for single-locator approaches.

**References**:
- [Similo: Similarity-based Web Element Localization for Robust Test Automation (ACM TOSEM)](https://dl.acm.org/doi/10.1145/3571855)
- [Ranking approaches for similarity-based web element location (JSS 2024)](https://www.sciencedirect.com/science/article/pii/S0164121224003303)
- [Web Element Relocalization in Evolving Web Applications (2025)](https://arxiv.org/html/2505.16424v1)

#### 2. RapidFuzz Token-Level Scoring

For string similarity within each attribute comparison, RapidFuzz (MIT-licensed C++ implementation) is the standard:

- **`ratio()`**: Levenshtein similarity normalized to 0-100. Good for short strings (button labels).
- **`partial_ratio()`**: Best substring match. Handles "Submit Order" matching against "Submit".
- **`token_sort_ratio()`**: Order-invariant token comparison. Handles "Search button" vs. "button Search".

**Recommended thresholds** from production fuzzy matching systems:
- **90+**: High confidence match (equivalent to exact for most purposes)
- **75-89**: Probable match (good for fuzzy label matching)
- **60-74**: Possible match (needs secondary confirmation)
- **<60**: Likely mismatch

For our context, use `score_cutoff` to short-circuit computation: RapidFuzz returns 0 immediately for scores below cutoff, saving CPU.

**References**:
- [RapidFuzz Documentation](https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html)
- [RapidFuzz GitHub](https://github.com/rapidfuzz/RapidFuzz)

#### 3. AXorcist: Multi-Strategy Matching with Depth-Limited Search

AXorcist (Swift, macOS 14+) implements multiple matching strategies against AX elements:
- **Exact**: Strict string equality
- **Contains**: Case-insensitive substring
- **Prefix/Suffix**: String start/end matching
- **Regex**: Full regex support
- **ContainsAny**: Comma-separated value matching

Searchable attributes: role, subrole, identifier, title, value, description, help, placeholder, DOM classes, DOM ID, computed name.

It uses **depth-limited path-based navigation** with configurable max depth, not full tree traversal.

**References**:
- [AXorcist GitHub](https://github.com/steipete/AXorcist)

#### 4. Appium Image Locator Confidence

Appium's image-based element locator uses OpenCV template matching with a configurable threshold:
- **Default threshold**: 0.4 (quite permissive)
- **Range**: 0.0 (match anything) to 1.0 (pixel-perfect)
- **Guidance**: Start at default, lower incrementally if missing matches, raise if false positives

**References**:
- [Appium: Finding Elements By Image](https://appium.github.io/appium.io/docs/en/advanced-concepts/image-elements/)
- [Appium: Image Comparison Features](https://appium.readthedocs.io/en/latest/en/writing-running-appium/image-comparison/)

#### 5. Self-Healing Locators (testRigor, Functionize)

Modern AI test automation tools use **intent-based element identification** rather than fixed selectors:
- **testRigor**: Stores "user intent" alongside locator data; when locator fails, uses ML to find element matching the original intent. Reports 40-70% maintenance reduction.
- **Functionize**: Uses NLP + ML Element Identification with "Adaptive Event Analysis" to auto-update selectors based on application changes.

The common pattern: record multiple attributes when an element is first identified, then use a multi-signal scoring function to relocate it when any single attribute changes.

**References**:
- [testRigor: AI-Based Self-Healing](https://testrigor.com/ai-based-self-healing/)
- [Functionize: Self-Healing Test Automation](https://www.functionize.com/automated-testing/self-healing-test-automation)

### Common Failure Modes for Confidence Scoring

| Failure Mode | Description | Mitigation |
|---|---|---|
| **False high confidence on generic elements** | "OK" button matches with high score but is the wrong "OK" button | Use neighbor text / spatial context as tiebreaker |
| **Token overlap inflates score** | "Add to Cart" partially matches "Cart" at 0.65 | Normalize by max(len(query), len(field)) tokens |
| **Role mismatch ignored** | AXGroup matches when AXButton was expected | Weight role match heavily (Similo gives role weight 1.5) |
| **Focused/enabled bias** | Our current +0.05/+0.02 bonuses can push borderline matches over threshold | Keep state bonuses < 0.05; they should only break ties |
| **Threshold doesn't vary by action risk** | Same threshold for "click newsletter checkbox" vs. "click Place Order" | Use variable thresholds: 0.5 for benign clicks, 0.9 for submit/pay/delete |

### Recommended Approach for Our Context

Our existing `_element_match_score()` in `accessibility.py` uses a reasonable but under-parameterized scoring function. Recommended improvements:

1. **Adopt Similo-style multi-attribute scoring**: Extend beyond title/value/description to include role, position, size, neighbor text, and focused state as weighted attributes.

2. **Replace token overlap with RapidFuzz `partial_ratio`**: The current `0.45 + 0.4 * overlap` heuristic can be replaced with `rapidfuzz.fuzz.partial_ratio / 100` which handles substrings, transpositions, and near-matches more robustly. RapidFuzz is MIT-licensed and has zero Python dependencies beyond the C extension.

3. **Tiered confidence thresholds** (already partially implemented at the orchestrator level with 0.5/0.9 gates):
   - >= 0.90: High confidence, skip visual validation
   - 0.50-0.89: Medium confidence, proceed but verify with JS injection
   - < 0.50: Low confidence, fall through to vision model

4. **Add neighbor text scoring**: Collect visible text from sibling and parent elements as additional signal. Similo's research shows neighbor text is one of the highest-value attributes (weight 1.5).

---

## 3. Fast Browser State Verification Without Vision Models

### Problem Landscape

Our Tier 2 verification (vision screenshot + VLM) takes 2-5 seconds per step. For browser-based workflows, many postconditions can be verified in <50ms using JavaScript DOM queries injected via the existing AppleScript channel.

### SOTA Approaches (Ranked by Relevance)

#### 1. Playwright's Auto-Retrying Assertions Pattern (Highest Relevance)

Playwright's web-first assertions combine **DOM queries** with **auto-retry** loops. The key insight: don't check once; poll with a short interval until the condition passes or a timeout expires.

Core assertion categories directly applicable to our `verify` field:
- **`toHaveURL(pattern)`** → `location.href.includes("checkout")` or regex match
- **`toHaveTitle(text)`** → `document.title.includes("Order Confirmation")`
- **`toHaveText(text)`** → `document.querySelector(sel).textContent.includes(text)`
- **`toHaveValue(text)`** → `document.querySelector('input').value === "expected"`
- **`toBeVisible()`** → `el.offsetParent !== null && getComputedStyle(el).display !== 'none'`
- **`toBeChecked()`** → `document.querySelector('input[type=checkbox]').checked`

Default assertion timeout: 5s with auto-retry. For our use case, 1-2 retries with 200ms intervals should suffice.

**References**:
- [Playwright: Assertions](https://playwright.dev/docs/test-assertions)
- [Playwright: Auto-waiting](https://playwright.dev/docs/actionability)
- [Playwright: Evaluating JavaScript](https://playwright.dev/docs/evaluating)

#### 2. Composite JS State Snapshot

Instead of individual queries, execute a single comprehensive state check:

```javascript
JSON.stringify({
  url: location.href,
  title: document.title,
  readyState: document.readyState,
  // Active element info
  activeTag: document.activeElement?.tagName?.toLowerCase(),
  activeType: document.activeElement?.type,
  activeValue: document.activeElement?.value || '',
  activeName: document.activeElement?.name || '',
  // Selection
  selectedText: window.getSelection().toString(),
  // Scroll
  scrollX: window.scrollX,
  scrollY: window.scrollY,
  scrollHeight: document.documentElement.scrollHeight,
  viewportHeight: window.innerHeight,
  // Specific element checks (parameterized)
  targetExists: !!document.querySelector('[data-testid="checkout-btn"]'),
  targetText: document.querySelector('.confirmation')?.textContent || ''
})
```

This returns everything needed for most verification conditions in a single osascript call (~300ms).

**References**:
- [MDN: Document.activeElement](https://developer.mozilla.org/en-US/docs/Web/API/Document/activeElement)
- [MDN: Window.scrollY](https://developer.mozilla.org/en-US/docs/Web/API/Window/scrollY)

#### 3. `document.elementFromPoint()` for Click Verification

After clicking at coordinates (x, y), verify the correct element was targeted:

```javascript
var el = document.elementFromPoint(x, y);
JSON.stringify({
  tag: el?.tagName,
  text: el?.textContent?.substring(0, 100),
  role: el?.getAttribute('role'),
  ariaLabel: el?.getAttribute('aria-label'),
  href: el?.href || '',
  isButton: el?.tagName === 'BUTTON' || el?.type === 'submit'
})
```

Playwright uses this internally for its clickability check: get the element's bounding rect, compute center, then call `elementFromPoint()` to verify the target element is on top (not occluded by modals, overlays, etc.).

**References**:
- [MDN: Document.elementFromPoint()](https://developer.mozilla.org/en-US/docs/Web/API/Document/elementFromPoint)
- [Playwright Clickability Check (Medium)](https://medium.com/@divyakandpal93/how-to-check-if-an-element-is-clickable-in-playwright-with-javascript-9c4886479082)

#### 4. Puppeteer's `page.$eval()` Pattern

Puppeteer's `page.$eval(selector, fn)` runs a function on the first matching element. The selector-first approach is useful when you know what element to verify:

```javascript
// Verify search results appeared
document.querySelectorAll('.search-result').length > 0

// Verify cart count updated
document.querySelector('.cart-badge')?.textContent?.trim()

// Verify form submission succeeded (redirect)
location.pathname === '/order/confirmation'
```

All expressions return JSON-serializable values compatible with AppleScript's `do JavaScript`.

**References**:
- [Puppeteer: page.$eval()](https://pptr.dev/api/puppeteer.page._eval)
- [Puppeteer: Page interactions](https://pptr.dev/guides/page-interactions)

#### 5. Selenium's JavaScriptExecutor Pattern

Selenium's `executeScript()` is used for cases where standard WebDriver interactions fail. Common state verification patterns:

```javascript
// Page load complete
document.readyState === 'complete'

// Specific element visible
var el = document.querySelector('#success-message');
el !== null && el.offsetHeight > 0

// Form field has expected value
document.querySelector('input[name="email"]').value === 'test@example.com'
```

**References**:
- [BrowserStack: JavascriptExecutor in Selenium](https://www.browserstack.com/guide/javascriptexecutor-in-selenium)
- [Selenium: Interacting with web elements](https://www.selenium.dev/documentation/webdriver/elements/interactions/)

### Reliable JS Queries for Common Verification Scenarios

| Scenario | JS Expression | Notes |
|---|---|---|
| **URL contains text** | `location.href.includes('checkout')` | Most reliable — immune to rendering delays |
| **Page title matches** | `document.title.includes('Confirmation')` | Fast but SPAs may not update title |
| **Element with text exists** | `!!document.querySelector(':contains("text")')` or manual traverse | `:contains` is not standard; use `[...document.querySelectorAll('*')].some(e => e.textContent.includes('text'))` |
| **Form field value** | `document.querySelector('input[name=q]').value` | Works for input, textarea, select |
| **Checkbox checked** | `document.querySelector('#agree').checked` | Boolean return |
| **Element visible** | `var e=document.querySelector(s); e && e.offsetParent !== null` | `offsetParent` is null for `display:none` elements |
| **Scroll position** | `window.scrollY` (or `pageYOffset` for older browsers) | Cross-browser: `window.pageYOffset \|\| document.documentElement.scrollTop \|\| document.body.scrollTop` |
| **Selected text** | `window.getSelection().toString()` | Empty string if nothing selected |
| **Active/focused element** | `document.activeElement.tagName + ':' + document.activeElement.name` | Useful for verifying focus moved to correct field |
| **Page loaded** | `document.readyState === 'complete'` | 'loading' -> 'interactive' -> 'complete' |

### Common Failure Modes

| Failure Mode | Cause | Mitigation |
|---|---|---|
| **SPA doesn't update URL** | Client-side routing may use hash fragments or history.pushState inconsistently | Also check `document.title` and specific DOM elements |
| **Element exists in DOM but not visible** | `display:none`, `visibility:hidden`, `opacity:0`, off-screen | Combine `document.querySelector` with visibility checks (`offsetParent`, `getBoundingClientRect`) |
| **Timing: JS runs before DOM update** | React/Vue batch DOM updates | Add 100-200ms delay or use retry loop (Playwright pattern) |
| **Cross-origin iframe blocks access** | `document.querySelector` can't reach into cross-origin iframes | Fall back to AX tree or vision for iframe content |
| **Shadow DOM elements hidden** | `document.querySelector` doesn't pierce shadow roots | Use `element.shadowRoot.querySelector()` but requires knowing the shadow host |
| **Content in `<canvas>` or `<video>`** | No DOM text representation | Must use vision model — JS cannot inspect rendered canvas content |

### Recommended Approach for Our Context

Our verifier already has a Tier 1 with URL/domain matching and scroll verification. The expansion path:

1. **Implement `get_page_state()` method** in `applescript_actuator.py` that executes a single composite JS query returning URL, title, readyState, activeElement info, scroll position, and selectedText as JSON. This replaces the current separate `get_scroll_position()`, `get_active_element_value()`, and `get_selected_text()` calls.

2. **Add `verify_element_at_point(x, y, expected_text)` method** using `document.elementFromPoint()` for post-click verification. This is the fastest way to confirm a click hit the right target.

3. **Add parameterized DOM checks** to Tier 1 verification: parse the `verify` field for patterns like "text X is visible", "field Y has value Z", "checkbox is checked" and translate them to JS queries.

4. **Implement retry loop** with 200ms interval, 3 retries (total 600ms max) for JS-based verification before falling through to Tier 2 vision.

---

## 4. AX API vs DOM for Web Content

### Problem Landscape

macOS exposes web page content through two distinct channels:
1. **Accessibility API (AX)**: The browser constructs an accessibility tree from the DOM and exposes it via `NSAccessibility`/`AXUIElement`. External processes query it via `AXUIElementCopyAttributeValue()`.
2. **DOM via JS injection**: Execute JavaScript directly in the browser's JS context to query the DOM.

The key question: when should we use each, and what are the performance/depth/reliability tradeoffs?

### AX API Characteristics for Web Content

#### Safari's AX Tree Structure

Safari exposes web content through a hierarchy:
```
AXApplication "Safari"
  └─ AXWindow
       └─ AXGroup (toolbar area)
       └─ AXGroup (content area)
            └─ AXScrollArea
                 └─ AXWebArea          ← Root of web page content
                      └─ AXGroup       ← <div>, <section>, <article>
                           └─ AXStaticText  ← text nodes
                           └─ AXLink       ← <a> tags
                           └─ AXButton     ← <button> tags
                           └─ AXTextField  ← <input> tags
                      └─ AXGroup
                           └─ ...
```

**Depth to reach web content**: Typically 5-7 levels from the application root to reach AXWebArea, then 2-10+ more levels within the web content depending on DOM complexity.

**Performance characteristics**:
- Each `AXUIElementCopyAttributeValue("AXChildren")` call is an IPC round-trip to the target process
- Iterating through the entire accessibility hierarchy starting from the topmost application node is **slow** — the AX docs explicitly warn against this for programmatic control
- Full tree traversal on large web pages is **particularly slow**: a complex web page may expose hundreds to thousands of AX elements
- Safari's AXWebArea is described as "a very complex API"
- iframe AXWebArea does **not** support search predicates — you must recursively fetch children, with significant performance implications for deeply nested iframes

**Observed latencies** (from user reports and Mozilla's Mac accessibility work):
- Simple native app AX queries: ~1-5ms per attribute
- Full window tree traversal (native app): ~50-200ms
- Web content tree traversal in Safari: Can take **seconds** for complex pages
- Firefox's VoiceOver performance was historically "unusable" with 30s cursor navigation delays (since improved)
- Our codebase sets `max_depth=8` and uses a 3s timeout on JXA AX queries (in `get_accessibility_elements()`)

#### Chrome's AX Tree Architecture

Chrome maintains a **cached copy** of the entire accessibility tree in the browser (main) process, separate from the renderer process. This means:
- AX queries are answered from the cache, not via IPC to the renderer
- Updates are pushed from renderer to browser process on DOM changes
- Chromium optimized this with enum-based task scheduling (20%+ improvement) and fast bounding-box serialization paths
- Scrolling updates sent 20+ times/second with ~66ms latency for instant scrolling, ~124ms for smooth scrolling

Chrome's architecture means AX queries can be faster than Safari's for large pages, since Chrome doesn't need to cross-process for each attribute query.

**References**:
- [Chromium: How Chrome Accessibility Works](https://chromium.googlesource.com/chromium/src/+/main/docs/accessibility/browser/how_a11y_works.md)
- [Chromium: Accessibility Overview](https://chromium.googlesource.com/chromium/src/+/main/docs/accessibility/overview.md)
- [Chrome Blog: Improving Chromium Accessibility Performance](https://developer.chrome.com/blog/chromium-accessibility-performance)
- [Chrome Blog: Full Accessibility Tree in DevTools](https://developer.chrome.com/blog/full-accessibility-tree)

### DOM via JS Injection Characteristics

**Performance**: A simple `do JavaScript` call returning a string takes **~0.27s** (with Web Inspector debug mode disabled) on both Safari and Chrome. With Web Inspector JSContext debugging enabled, this degrades to **10-15s**.

**Depth**: Unlimited. JS has full access to the entire DOM, including:
- Shadow DOM (via `element.shadowRoot`)
- Computed styles (`getComputedStyle()`)
- Element positions (`getBoundingClientRect()`)
- Form state (`document.forms`, `.value`, `.checked`)
- Dynamic content (AJAX-loaded content, SPA state)
- `localStorage`, `sessionStorage`, cookies

**Reliability**: Very high for DOM content. The JS runs in the same context as the page's own scripts.

### When to Use Each

| Scenario | Best Channel | Why |
|---|---|---|
| **Native app elements** (toolbar, tabs, menubar) | AX API | Only channel available; DOM doesn't exist |
| **Verify button/link text on web page** | AX API (if shallow) or JS | AX is faster for elements at shallow depth; JS for deep/dynamic content |
| **Check form field value** | JS injection | Fastest: `document.activeElement.value` returns in ~300ms total |
| **Verify page URL/title** | JS injection | `location.href` is more reliable than AX window title parsing |
| **Find specific element by text** | AX API first, JS fallback | AX tree search is structured; JS requires knowing the selector or doing full text search |
| **Verify scroll position** | JS injection | `window.scrollY` is exact; AX scroll position is less precise |
| **Detect modal/overlay** | AX API | Modals create new AX windows/groups; in JS you'd need to know the modal's selector |
| **Cross-origin iframe content** | AX API | JS blocked by same-origin policy; AX tree can traverse into iframes |
| **Canvas/WebGL content** | Neither (use vision) | Neither AX nor JS can inspect rendered canvas pixels |
| **Enumerate all interactive elements** | AX API | AX tree naturally identifies interactive roles; in JS you'd need complex heuristics |
| **Complex page with 1000+ elements** | JS injection | AX tree traversal of 1000+ elements is too slow for real-time verification |

### Hybrid Approach: Screen2AX Research

Screen2AX (MacPaw, 2025) proposes an alternative: use vision models (YOLOv11) to detect UI elements from screenshots, then generate accessibility metadata. This achieved:
- **F1 score of 79%** for hierarchy reconstruction
- **2.2x improvement** over native AX metadata for GUI grounding on ScreenSpot benchmark
- Faster than full AX tree traversal for element detection

This suggests that for complex pages, vision-based approaches may outperform AX tree traversal for element discovery, while AX and JS remain superior for state verification.

**References**:
- [Screen2AX: Vision-Based Approach for Automatic macOS Accessibility Generation](https://arxiv.org/html/2507.16704v1)
- [MacPaw: Parsing macOS application UI — Techniques and Tools](https://research.macpaw.com/publications/how-to-parse-macos-app-ui)

### Tools Using the AX Tree for Fast Element Access

Several macOS tools demonstrate that fast AX-based element access is possible with the right approach:

- **Shortcat**: Uses AX API with fuzzy search to overlay keyboard hints on interactive elements. Emphasizes speed — "fuzzy search allows users to quickly locate and interact with UI elements."
- **Homerow/Vimac**: Generates letter-based "hints" for all actionable elements in the frontmost window. Uses AX tree traversal with depth limits.
- **macapptree** (MacPaw): Parses accessibility hierarchies from Safari/Chrome to JSON format for analysis.
- **pyax**: Python client for macOS accessibility that wraps AXUIElement and AXObserver.
- **atomacos**: Python library for macOS GUI testing via AX API with direct pyobjc access.

**References**:
- [Shortcat](https://shortcat.app/)
- [Homerow GitHub](https://github.com/nchudleigh/homerow)
- [pyax GitHub](https://github.com/eeejay/pyax)
- [atomacos Documentation](https://daveenguyen.github.io/atomacos/readme.html)
- [macapptree GitHub](https://github.com/MacPaw/macapptree)

### Recommended Approach for Our Context

Our codebase already uses both channels:
- AX API via `perception/accessibility.py` (max_depth=8, full tree traversal + element search)
- JS injection via `actuator/applescript_actuator.py` (`_get_browser_js()`, `get_scroll_position()`, `get_active_element_value()`, `get_selected_text()`)

The recommended hybrid strategy:

1. **Use AX API for element discovery and native app interaction** (Tier 0 verification): Find elements by role/title, check focused state, detect modals. Keep max_depth=8 for native apps, but consider **depth-limited search** (max_depth=4-5) for web content to avoid the deep-tree performance trap.

2. **Use JS injection for all browser state verification** (enhanced Tier 1): URL, title, form values, scroll position, element-at-point checks, visibility. This is faster and more reliable than AX for web-specific state.

3. **Use AX API as fallback for cross-origin iframes** and content that JS can't reach due to same-origin policy.

4. **Never traverse the full AX tree for web pages**: For complex pages, the AX tree can have thousands of nodes. Instead, either (a) query specific elements by role/title with early termination, or (b) use JS injection for targeted DOM queries.

---

## RECOMMENDATION: PROCEED

### Rationale

All four research areas converge on a clear, low-risk optimization path:

1. **JS injection layer** is well-understood and already partially implemented in our codebase. The main optimization (single-call JSON state extraction) is straightforward and directly reduces verification latency from ~8s (4 osascript calls) to ~0.3s (1 call).

2. **AX confidence calibration** has strong academic backing (Similo algorithm) and production validation (testRigor, Functionize self-healing). The Similo scoring formula is simple enough to implement in a day, and RapidFuzz is a drop-in replacement for our hand-tuned token overlap.

3. **JS-based verification** is the standard approach in all major browser automation frameworks (Playwright, Puppeteer, Selenium). The specific JS queries needed are well-documented and reliable.

4. **AX vs DOM tradeoffs** are clear: use AX for element discovery and native apps; use JS for browser state verification. No architectural risk.

### Expected Impact

- **Tier 1 verification latency**: From ~50ms (URL only) to ~300ms (full page state including URL, title, form values, scroll, focused element) — but covering 60-80% of verification conditions that currently fall through to Tier 2
- **Tier 2 avoidance rate**: Estimated 60-80% of browser verification conditions can be resolved at Tier 1 with JS injection, saving 2-5s per step
- **Per-task improvement**: A 5-step browser workflow currently spending ~15s on vision verification could drop to ~3s total verification time

### Key Risks

| Risk | Severity | Mitigation |
|---|---|---|
| "Allow JavaScript from Apple Events" not enabled | Medium | Detect at startup; degrade gracefully to AX+vision path |
| Safari Web Inspector debug mode causes 10s+ latency | Low | Detect via timing canary; warn user |
| React SPAs don't update URL/title predictably | Low | Combine URL + DOM element checks; fall through to vision if JS inconclusive |
| RapidFuzz dependency adds weight | Very Low | MIT license, C extension, 2MB wheel, zero Python deps |
| Cross-origin iframes block JS verification | Low | Fall back to AX tree for iframe content |

### No Pivots or Aborts Needed

The research confirms our existing architecture is sound. These optimizations are additive (new capabilities layered onto existing tiers) rather than requiring rewrites. The main risk is the "Allow JavaScript from Apple Events" prerequisite, which affects only the JS injection layer — AX and vision paths remain fully functional as fallbacks.
