# macOS Accessibility API and Web Browsers: Research Findings

**Date**: 2026-03-16
**Purpose**: Understand how the macOS Accessibility API interacts with web browser content (Safari, Chrome), identify practical approaches for reading web page state, and evaluate alternatives for our automation agent.

---

## Table of Contents

1. [Can macOS AX API Access Web Page Content?](#1-can-macos-ax-api-access-web-page-content)
2. [AXFocusedUIElement for Browsers](#2-axfocusedulement-for-browsers)
3. [Alternative Approaches: JavaScript Injection](#3-alternative-approaches-javascript-injection)
4. [What Existing macOS Automation Tools Do](#4-what-existing-macos-automation-tools-do)
5. [Chrome DevTools Protocol as Alternative](#5-chrome-devtools-protocol-as-alternative)
6. [Comparison Matrix](#6-comparison-matrix)
7. [Recommendations for Our Agent](#7-recommendations-for-our-agent)
8. [Sources](#8-sources)

---

## 1. Can macOS AX API Access Web Page Content?

### 1.1 Safari and AXWebArea

**Yes, Safari exposes web page DOM elements via the Accessibility API.** Safari implements the NSAccessibility protocol for its web content. The root of the web content area has the role `AXWebArea`, and child elements map to HTML elements with corresponding AX roles.

**How it works:**
- The `AXWebArea` element is the root of the web content accessibility tree
- It is nested several `AXGroup` levels deep inside the Safari window hierarchy:
  `AXApplication > AXWindow > AXGroup > ... > AXWebArea`
- Child elements of `AXWebArea` correspond to rendered DOM elements
- Not every DOM node appears -- only semantically meaningful elements (headings, links, buttons, form fields, landmarks, etc.)
- Elements expose attributes like `AXRole`, `AXValue`, `AXTitle`, `AXDOMIdentifier`, `AXDOMClassList`

**HTML-to-AX role mappings (macOS):**

| HTML Element | AX Role | Notes |
|---|---|---|
| `<body>` content root | `AXWebArea` | Root of web content |
| `<input type="text">` | `AXTextField` | AXValue contains field text |
| `<textarea>` | `AXTextArea` | AXValue contains field text |
| `<button>` | `AXButton` | |
| `<a href="...">` | `AXLink` | |
| `<h1>`-`<h6>` | `AXHeading` | With AXValue for heading level |
| `<img>` | `AXImage` | |
| `<select>` | `AXPopUpButton` | |
| `<input type="checkbox">` | `AXCheckBox` | |
| `<nav>`, `<main>`, etc. | `AXGroup` with `AXSubrole` | Landmark roles |
| `<table>` | `AXTable` | |
| `<iframe>` | `AXUnknown` (hidden) | Child content gets its own `AXWebArea` |
| `<div>`, `<span>` | Usually pruned | Only present if they carry ARIA roles |

**Key attributes available on web elements:**
- `AXRole` -- the accessibility role
- `AXSubrole` -- specialized role info
- `AXValue` -- text content / field value
- `AXTitle` -- element label
- `AXDOMIdentifier` -- the HTML `id` attribute
- `AXDOMClassList` -- the HTML `class` attribute values
- `AXDescription` -- accessible description
- `AXURL` -- available on `AXWebArea`, contains page URL
- `AXLinkUIElements` -- direct access to all links on page (faster than full traversal)
- `AXFocused` -- whether the element has focus

**Conditions for access:**
- The calling process must have Accessibility permission (System Settings > Privacy & Security > Accessibility)
- Safari's accessibility tree is always active (unlike Chrome) since Apple maintains both Safari and the AX API
- VoiceOver works best with Safari specifically because Apple develops both

**Limitations:**
- The tree is large and deeply nested on complex pages
- `AXWebArea` is described as "a very complex API, not easy to use with GUI scripting"
- `<iframe>` elements map to `AXUnknown` and are hidden; the child frame gets its own `AXWebArea` but may not support search predicates, requiring recursive child fetching
- CSS-only visual elements (pseudo-elements, backgrounds, etc.) have no AX representation
- Dynamic content may have stale representations until re-queried

### 1.2 Chrome and AX Tree Exposure

**Chrome also exposes web page elements via the macOS Accessibility API, but accessibility is disabled by default for performance reasons.**

**How Chrome's accessibility works:**
- Chrome (Chromium) maintains a cached accessibility tree in the browser process
- The internal tree maps Blink's DOM nodes to platform-native accessibility objects
- On macOS, these are exposed via the NSAccessibility protocol (same as Safari)
- The root web content element is also `AXWebArea`
- Historical note: Chrome's internal AX role names closely match macOS conventions because Chromium was originally based on WebKit, whose accessibility code was written by Apple

**The critical difference from Safari -- lazy initialization:**
- Chrome's accessibility features are **off by default** and only enabled on-demand
- Chrome detects assistive technology by checking if a client has set `AXEnhancedUserInterface` on the main application window
- Without this signal, **the accessibility tree is not built**, and queries return empty/minimal results

**How to enable Chrome's accessibility:**
1. **Command-line flag**: Launch Chrome with `--force-renderer-accessibility`
   ```
   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --force-renderer-accessibility
   ```
2. **Manual toggle**: Visit `chrome://accessibility/` and enable per-tab or globally
3. **Programmatic**: Set `AXEnhancedUserInterface` to `true` on Chrome's window AXUIElement
4. **Alternative**: Set `AXManualAccessibility` to `true` (Electron-specific, avoids side effects)

**Performance impact of enabling Chrome accessibility:**
- Enabling "Web accessibility" mode triggers `RenderAccessibilityImpl::SendPendingAccessibilityEvents`, which can consume ~500ms on complex pages, essentially doubling page load time
- Chrome engineers confirmed this: any app with accessibility permissions that interacts with the system can inadvertently trigger Chrome's full accessibility tree construction
- Window manager apps (Rectangle, Magnet) that use the AX API for window positioning have been shown to trigger Chrome performance degradation because setting `AXEnhancedUserInterface` cascades into enabling Chrome's full web accessibility monitoring
- The `--force-renderer-accessibility` flag has granularity options: `=basic`, `=form-controls`, `=complete`

**The `AXEnhancedUserInterface` problem:**
- Setting this attribute on Chrome's `AXWindow` enables accessibility but also breaks window positioning for window managers
- Mozilla filed a bug about this: setting `AXEnhancedUserInterface` to `true` is the mechanism for VoiceOver detection, but it has window management side effects
- There is no clean way to temporarily enable accessibility, query elements, then disable it without side effects

### 1.3 Safari vs Chrome: Key Differences

| Aspect | Safari | Chrome |
|---|---|---|
| AX tree always available | Yes | No -- must be explicitly enabled |
| Root web content role | `AXWebArea` | `AXWebArea` |
| DOM attributes exposed | `AXDOMIdentifier`, `AXDOMClassList` | Same, when accessibility enabled |
| Performance impact | Minimal (always on) | Significant (~500ms on complex pages) |
| Activation mechanism | Automatic | `--force-renderer-accessibility`, `chrome://accessibility`, or `AXEnhancedUserInterface` |
| VoiceOver integration | Excellent (Apple maintains both) | Good, but lazy initialization can cause delays |
| Accessibility Inspector support | Full | Full (when enabled) |
| `<iframe>` handling | Child `AXWebArea` (no search predicates) | Similar structure |

---

## 2. AXFocusedUIElement for Browsers

### 2.1 Can We Get the Focused Element Inside a Web Page?

**Yes, in principle.** The standard approach:

```
System-wide AXUIElement
  -> AXFocusedUIElement attribute
    -> Returns the focused element (e.g., AXTextField inside AXWebArea)
```

When a user focuses an `<input>` field on a web page:
- Safari exposes it as an `AXTextField` or `AXTextArea` element within the `AXWebArea` subtree
- The element's `AXValue` contains the current field text
- `AXSelectedText` returns currently selected text
- `AXSelectedTextRange` returns cursor position and selection range
- `AXNumberOfCharacters` returns total character count

**Performance is excellent** -- sub-millisecond for reading AXValue from a focused element (measured at ~0.07ms).

### 2.2 The "Can't Convert Types" JXA Error

**The Error -1700 ("Can't convert types") in JXA is a type marshalling problem, not an accessibility limitation.**

Root causes:
1. **JXA-to-AppleScript bridge type mismatch**: JXA (JavaScript for Automation) uses a bridge to communicate with AppleScript-based APIs. When the AX API returns certain types (like `AXUIElement` references, `AXValue` structs containing `CFRange`, or `AXTextMarker` objects), JXA's bridge cannot automatically convert them to JavaScript types
2. **The `focusedUIElement()` method in JXA's System Events**: This method works fine in AppleScript but fails in JXA because the return type (`specifier` referencing an accessibility object) cannot be automatically marshalled across the language bridge
3. **Not browser-specific**: This error occurs with any application, not just browsers. It's a JXA limitation with the accessibility bridge

**Workarounds:**

1. **Use AppleScript instead of JXA** for accessibility queries:
   ```applescript
   tell application "System Events"
     tell process "Safari"
       set focusedEl to value of attribute "AXFocusedUIElement"
       -- Access properties of focusedEl
     end tell
   end tell
   ```

2. **Use the C-level AX API directly** via Python (pyobjc) or Swift:
   ```python
   from ApplicationServices import (
       AXUIElementCreateSystemWide,
       AXUIElementCopyAttributeValue,
   )
   import Quartz

   system_wide = AXUIElementCreateSystemWide()
   err, focused = AXUIElementCopyAttributeValue(
       system_wide, "AXFocusedUIElement", None
   )
   if err == 0:
       err, value = AXUIElementCopyAttributeValue(focused, "AXValue", None)
   ```

3. **Use Hammerspoon's `hs.axuielement`** which wraps the C API in Lua:
   ```lua
   local ax = require("hs.axuielement")
   local systemElement = ax.systemWideElement()
   local focused = systemElement:attributeValue("AXFocusedUIElement")
   local value = focused:attributeValue("AXValue")
   ```

4. **Use JXA's `ObjC.import` to call the C API directly** (advanced):
   Import `ApplicationServices` framework via JXA's Objective-C bridge to bypass the AppleScript type conversion issue.

### 2.3 Known Issues with Browser Focused Elements

- **Chrome**: If accessibility is not enabled, `AXFocusedUIElement` returns the Chrome window or toolbar element, not the web content element. Must enable accessibility first.
- **Electron apps**: Return `{ location = 0, length = 0 }` for `AXSelectedTextRange` regardless of actual cursor position, due to Mac App Store restrictions on private APIs.
- **`kAXErrorCannotComplete`**: Can occur when the browser is unresponsive, performing modal processing, or when the calling app is sandboxed. This is a timeout issue -- retrying often succeeds.
- **`contentEditable` divs**: These expose as `AXTextArea` but may have inconsistent behavior for `AXValue` and `AXSelectedText` across browsers.

---

## 3. Alternative Approaches: JavaScript Injection

### 3.1 The Standard Approach: AppleScript JavaScript Execution

**This is the standard and most reliable approach used by macOS automation tools for reading/writing web page state.**

**Safari:**
```applescript
tell application "Safari"
  do JavaScript "document.activeElement.value" in document 1
end tell
```

**Chrome:**
```applescript
tell application "Google Chrome"
  tell active tab of front window
    execute javascript "document.activeElement.value"
  end tell
end tell
```

### 3.2 Capabilities

**Reading values:**
- `document.activeElement.value` -- get current input field value
- `document.activeElement.tagName` -- identify element type
- `document.getElementById('id').value` -- get specific field value
- `document.querySelector('selector').innerHTML` -- get element content
- `document.title` -- page title
- `document.readyState` -- page load state ("loading", "interactive", "complete")

**Selected text:**
- `window.getSelection().toString()` -- get user-selected text on page
- `document.activeElement.selectionStart` / `selectionEnd` -- cursor position in input

**Setting values:**
- `document.getElementById('field').value = 'text'` -- set field value
- Note: programmatically set values may not trigger change/input events. Workaround: dispatch events manually:
  ```javascript
  var el = document.getElementById('field');
  el.value = 'text';
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  ```

**Page source:**
- Safari: `source of document 1` (original HTML, pre-JS execution)
- For live DOM: `do JavaScript "document.documentElement.outerHTML"` (post-JS)

### 3.3 Security and Sandboxing Restrictions

**Safari:**
- Requires "Allow JavaScript from Apple Events" to be enabled
- Located in: Safari > Settings > Developer > Allow JavaScript from Apple Events
- Must first enable Develop menu: Safari > Settings > Advanced > Show features for web developers
- **Cannot be reliably enabled programmatically**: `defaults write com.apple.Safari AllowJavaScriptFromAppleEvents -bool TRUE` writes to the plist but Safari's sandboxed container may not read it. The setting makes "some other change somewhere deeper in the OS."
- Workaround: Use AppleScript GUI scripting to click the menu item in the Develop menu
- The calling process also needs Automation permission for Safari in System Settings > Privacy & Security > Automation

**Chrome:**
- JavaScript execution via AppleScript is **disabled by default** in Chrome stable
- Must be enabled: View > Developer > Allow JavaScript from Apple Events
- Error when disabled: "Execution of JavaScript via AppleScript is disabled"
- Chrome recommends using Chrome Extensions with Native Messaging as the long-term alternative
- **Cannot be enabled programmatically** without GUI scripting

**Firefox:**
- **Does not support JavaScript execution via AppleScript at all**
- Cannot get URL, title, or run JS through AppleScript
- Only limited window-level accessibility is available

**General macOS restrictions:**
- The calling process needs Automation permission (TCC) for the target browser
- macOS Sequoia (15) has tightened TCC enforcement; some apps may not trigger the permission prompt correctly
- Content Security Policy (CSP) on web pages does not block AppleScript-injected JavaScript (it runs in the page context)
- SIP (System Integrity Protection) does not directly block AppleScript JavaScript execution
- Recent TCC vulnerabilities (CVE-2025-43530, CVE-2025-31250) demonstrate the security sensitivity of the automation/accessibility permission chain

### 3.4 Return Value Handling

- AppleScript `do JavaScript` returns primitive types: strings, numbers, booleans
- DOM objects **cannot** be returned -- you must access `.innerHTML`, `.value`, `.textContent`, or similar string properties
- Chrome's `execute javascript` has a known bug where it sometimes does not return values reliably
- JSON serialization (`JSON.stringify()`) can be used for structured return data

---

## 4. What Existing macOS Automation Tools Do

### 4.1 Keyboard Maestro

**Primary approach: JavaScript injection via AppleScript**
- Requires both Automation permission and "Allow JavaScript from Apple Events" in the browser
- Provides "Browser Window Actions" and "Browser Form Actions" as high-level abstractions
- Supports Safari and Chrome variants
- Uses `do JavaScript` / `execute javascript` under the hood for form filling and content reading
- Falls back to GUI scripting (keystroke/click simulation) for browsers without JS injection support

### 4.2 Hammerspoon

**Primary approach: AX API via `hs.axuielement`**
- Can traverse the full accessibility tree of any application
- `hs.axuielement.axtextmarker` submodule provides text marker support specifically for WebKit-based apps (Safari)
- `elementSearch` is powerful but slow on deep trees -- recommended to start close to the target element
- Hammerspoon can read `AXValue` from focused input fields at sub-millisecond speed
- For Chrome, must first enable accessibility by setting `AXEnhancedUserInterface` on the Chrome window
- Also has `hs.webview` for its own embedded web views
- **Does not use JavaScript injection** -- purely AX API based

### 4.3 AppleScript / System Events GUI Scripting

**Layered approach:**
1. **Application scripting** (preferred): Direct commands like Safari's `do JavaScript`, Chrome's `execute javascript`, browser-specific properties (`URL of front document`, `name of active tab`)
2. **GUI scripting** (fallback): System Events process suite -- `click`, `keystroke`, `entire contents` on UI elements. Relies on the AX API. Very slow for web content traversal.
3. **JavaScript injection** for web content manipulation

**Known limitations:**
- `entire contents` of a browser's web area is extremely slow on complex pages
- GUI scripting can only interact with elements visible in the AX tree
- Element addressing is fragile (depends on hierarchy position)

### 4.4 Accessibility Inspector (Xcode)

- Apple's built-in tool for inspecting the AX tree of any running application
- Can browse Safari's `AXWebArea` and all child elements
- Shows all AX attributes, roles, subroles, values
- Useful for discovery but not for automation
- Confirms that Safari's AX tree includes web content by default

### 4.5 UI Browser (by PFiddlesoft)

- Third-party AX tree browser, more powerful than Accessibility Inspector
- Can generate AppleScript code for accessing specific elements
- Shows the full element path from application to target
- Useful for understanding the AX hierarchy of browser web content

### 4.6 VoiceOver

- Apple's built-in screen reader
- **Works best with Safari** because Apple develops both
- When entering a web page, VoiceOver announces the page title and "web content"
- Navigates through AXWebArea children sequentially
- Uses `Control-Option-Shift-Down Arrow` to enter the web content area
- Reads element roles, names, values, and states
- Proves that the full web accessibility tree is available in Safari

### 4.7 AXorcist (Swift)

- Modern Swift wrapper for macOS Accessibility API (requires macOS 14+)
- Supports chainable, fuzzy-matched queries
- Exposes web-specific attributes: `AXDOMClassList`, `AXDOMIdentifier`, `computedname`
- Supports multiple matching strategies: exact, contains, regex, prefix/suffix
- Path-based navigation through UI hierarchies with configurable search depth
- Batch attribute fetching for performance

### 4.8 pyax (Python)

- Python client for macOS accessibility
- Command-line tool with `-w` flag to output only the web view subtree
- Can traverse `AXWebArea` children and read `AXDOMIdentifier` for DOM correlation
- Example:
  ```bash
  pyax tree Safari -w -a AXTitle -a AXValue -a AXSubrole
  ```
- Useful for debugging and prototyping AX-based browser queries

### 4.9 Appium (macOS driver)

- Uses Apple's XCTest framework (Mac2 driver) for native app automation
- Separate Safari driver for web automation using W3C WebDriver protocol
- The native driver uses "absolute AXPath" selectors based on accessibility identifiers
- Requires Accessibility permission
- For web content in browsers, delegates to WebDriver rather than traversing the AX tree directly

---

## 5. Chrome DevTools Protocol as Alternative

### 5.1 Overview

The Chrome DevTools Protocol (CDP) provides direct, low-level access to browser internals via WebSocket. It offers far richer capabilities than the AX API or JavaScript injection for interacting with web content.

### 5.2 Enabling CDP

Launch Chrome with remote debugging:
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

**Important:** All existing Chrome instances must be closed first. Chrome only starts the debugging server on fresh launch.

**Endpoints exposed:**
- `http://localhost:9222/json` -- list available debugging targets (tabs)
- `http://localhost:9222/json/version` -- browser version and WebSocket URL
- `ws://127.0.0.1:9222/devtools/browser/...` -- WebSocket for bidirectional CDP communication

### 5.3 CDP Capabilities Relevant to Our Agent

| CDP Domain | Capability | Equivalent AX/JS Approach |
|---|---|---|
| `DOM.getDocument` | Full DOM tree snapshot | AX tree traversal (slower, less complete) |
| `DOM.querySelector` | Find elements by CSS selector | `do JavaScript "document.querySelector()"` |
| `Runtime.evaluate` | Execute arbitrary JavaScript | `do JavaScript` / `execute javascript` |
| `Page.navigate` | Navigate to URL | `open location` AppleScript |
| `DOMSnapshot.captureSnapshot` | Full page state + computed styles | No equivalent |
| `Page.captureScreenshot` | Screenshot of page/element | Screenshot via screencapture |
| `Input.dispatchMouseEvent` | Synthetic clicks at coordinates | AX `AXPress` action or cliclick |
| `Input.dispatchKeyEvent` | Synthetic keyboard input | `keystroke` in AppleScript |
| `Accessibility.getFullAXTree` | Chrome's internal AX tree | External AX API queries |
| `Network.*` | Network monitoring | No equivalent |
| `Page.handleJavaScriptDialog` | Handle alerts/confirms | Difficult via other methods |

### 5.4 Playwright / Puppeteer Integration

**Playwright `connectOverCDP`:**
```javascript
const browser = await chromium.connectOverCDP('http://localhost:9222');
const context = browser.contexts()[0];
const page = context.pages()[0];
// Now have full Playwright API over the user's actual browser
```

**How browser-use does it (Python):**
- Migrated from Playwright to direct CDP for better performance
- Eliminated the Node.js relay layer that added latency
- Uses `DOMSnapshot.captureSnapshot()` for page state
- Developed "super-selectors" combining `targetId`, `frameId`, `backendNodeId`, coordinates
- Event-driven architecture for detecting DOM changes, downloads, crashes
- Handles Out-Of-Process iframes (OOPIFs) properly

### 5.5 CDP Limitations

- **Chrome-only**: Does not work with Safari or Firefox (Firefox has its own remote protocol)
- **Requires special launch**: Chrome must be started with `--remote-debugging-port`. Cannot attach to an already-running Chrome instance that wasn't started with this flag.
- **Chrome v136+**: No longer supports being driven over CDP while using the default `--user-data-dir` profile (requires a separate profile)
- **Security risk**: CDP provides full control over the browser -- never expose to public networks
- **Single client**: Only one CDP client can connect at a time (DevTools or automation, not both)
- **Lower fidelity than Playwright protocol**: Playwright's native connection provides richer capabilities than CDP connection

### 5.6 Safari Web Inspector Protocol

Safari has its own remote debugging protocol (Web Inspector), but:
- It is not as well-documented or widely supported as CDP
- Primarily designed for Safari's built-in Web Inspector, not third-party automation
- `safaridriver` (Apple's WebDriver implementation) is the supported automation interface
- Limited compared to CDP in terms of low-level DOM/network access

---

## 6. Comparison Matrix

| Approach | Safari | Chrome | Firefox | Speed | Richness | Setup Complexity |
|---|---|---|---|---|---|---|
| AX API (AXWebArea traversal) | Excellent | Good (must enable) | Minimal | Slow for full tree, fast for focused element | Medium | Low (just needs AX permission) |
| JS injection (AppleScript) | Good | Good (must enable) | None | Fast (~50ms) | High (full DOM access) | Medium (must enable in browser) |
| CDP | None | Excellent | None | Fast | Very High | High (special Chrome launch) |
| GUI scripting (click/keystroke) | Works | Works | Works | Slow | Very Low | Low |
| `source of document` | HTML only | N/A | N/A | Fast | Low (pre-JS HTML) | Low |

---

## 7. Recommendations for Our Agent

### 7.1 For type_text Verification (Primary Use Case)

**Recommended approach: JavaScript injection via AppleScript**

```applescript
-- Safari
tell application "Safari"
  set fieldValue to do JavaScript "document.activeElement.value" in document 1
end tell

-- Chrome
tell application "Google Chrome"
  tell active tab of front window
    set fieldValue to execute javascript "document.activeElement.value"
  end tell
end tell
```

**Why this is best:**
- Fast (~50ms round trip)
- Directly answers "what text is in the focused field?"
- Works with any input type (`<input>`, `<textarea>`, `contentEditable`)
- Returns the live DOM value (post-JS mutation)
- No tree traversal overhead
- Standard approach used by Keyboard Maestro, other macOS automation tools

**Caveats:**
- Requires "Allow JavaScript from Apple Events" enabled in both browsers
- Chrome has this disabled by default -- must be enabled manually or via GUI scripting
- Cannot be enabled programmatically via `defaults write` (Safari sandbox issue)
- Consider pre-flight check at agent startup and warn user if not enabled

### 7.2 For General Element State Queries

**Recommended approach: AX API for focused element, JS injection for everything else**

- Use `AXFocusedUIElement` -> `AXValue` for fast focused-field reads (sub-ms)
- Use `AXFocusedUIElement` -> `AXRole` to identify what type of element is focused
- Use JavaScript injection for complex queries (querySelector, checking checkboxes, reading dropdown values)

### 7.3 For the JXA "Can't Convert Types" Issue

**Fix: Use AppleScript (not JXA) for accessibility queries, or use the C-level AX API via Python/pyobjc**

Our current JXA code in `get_accessibility_elements()` should:
1. Use `osascript -e '...'` with AppleScript syntax for the `focusedUIElement` call
2. Or better: use Python's `pyobjc` to call `AXUIElementCopyAttributeValue` directly, avoiding the JXA bridge entirely
3. The JXA `ObjC.import('ApplicationServices')` bridge is another option but more complex

### 7.4 For Chrome Accessibility Enablement

If we want AX-based queries to work with Chrome:
1. Check if Chrome was launched with `--force-renderer-accessibility`
2. If not, consider setting `AXEnhancedUserInterface` on Chrome's window (but beware of window manager side effects)
3. For minimal impact, use `--force-renderer-accessibility=form-controls` to only expose form elements
4. Alternatively, skip AX entirely for Chrome and use JavaScript injection exclusively

### 7.5 Long-term: Consider CDP for Chrome

For richer Chrome interaction:
- Detect if Chrome is running with `--remote-debugging-port`
- If available, use CDP for DOM queries, element interaction, page state
- Falls back to JavaScript injection if CDP not available
- This is what browser-use, Playwright MCP, and other modern agents use

### 7.6 Priority Implementation Order

1. **Immediate (P0)**: Fix JXA type conversion -- switch focused element queries to AppleScript or pyobjc
2. **Short-term (P1)**: Add JavaScript injection for type_text verification: `document.activeElement.value`
3. **Short-term (P1)**: Add `window.getSelection().toString()` for selected text verification
4. **Medium-term (P2)**: Add startup check for "Allow JavaScript from Apple Events" in Safari/Chrome
5. **Long-term (P3)**: Evaluate CDP integration for Chrome-specific richer automation

---

## 8. Sources

### Apple Developer Documentation
- [Accessibility API | Apple Developer Documentation](https://developer.apple.com/documentation/accessibility/accessibility-api)
- [AXUIElement.h | Apple Developer Documentation](https://developer.apple.com/documentation/applicationservices/axuielement_h)
- [Accessibility Programming Guide for OS X](https://developer.apple.com/library/archive/documentation/Accessibility/Conceptual/AccessibilityMacOSX/)
- [Accessibility Inspector | Apple Developer Documentation](https://developer.apple.com/library/archive/documentation/Accessibility/Conceptual/AccessibilityMacOSX/OSXAXTestingApps.html/)
- [Mac Automation Scripting Guide: Automating the User Interface](https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/AutomatetheUserInterface.html)
- [Changing Developer settings in Safari](https://developer.apple.com/documentation/safari-developer-tools/developer-settings)

### Chromium / Chrome
- [Chromium Accessibility Overview](https://chromium.googlesource.com/chromium/src/+/main/docs/accessibility/overview.md)
- [How Chrome Accessibility Works](https://chromium.googlesource.com/chromium/src/+/main/docs/accessibility/browser/how_a11y_works.md)
- [Chromium Accessibility Technical Documentation](https://www.chromium.org/developers/design-documents/accessibility/)
- [Information for Third-party Applications on Mac (Chrome AppleScript)](https://www.chromium.org/developers/applescript/)
- [Full accessibility tree in Chrome DevTools](https://developer.chrome.com/blog/full-accessibility-tree)
- [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/)
- [Chrome DevTools Protocol - Accessibility domain](https://chromedevtools.github.io/devtools-protocol/tot/Accessibility/)
- [Chrome performance hit caused by Rectangle's accessibility permissions](https://github.com/rxhanson/Rectangle/issues/1065)
- [Chrome/Chromium/Firefox accessibility support (Vimac issue)](https://github.com/nchudleigh/vimac/issues/78)
- [macOS: "Allow JavaScript From Apple Events" (Chromium bug)](https://issues.chromium.org/issues/40092604)

### W3C Standards
- [HTML Accessibility API Mappings 1.0](https://w3c.github.io/html-aam/)
- [Core Accessibility API Mappings 1.2](https://www.w3.org/TR/core-aam-1.2/)

### Tools and Libraries
- [AXorcist - Swift wrapper for macOS Accessibility](https://github.com/steipete/AXorcist)
- [pyax - Python client for macOS accessibility](https://github.com/eeejay/pyax)
- [Hammerspoon hs.axuielement documentation](https://www.hammerspoon.org/docs/hs.axuielement.html)
- [hs._asm.axuielement - Accessing Accessibility Objects](https://github.com/asmagill/hs._asm.axuielement)
- [Appium Mac2 Driver](https://github.com/appium/appium-mac2-driver)
- [macos-automator-mcp (AppleScript/JXA MCP server)](https://github.com/steipete/macos-automator-mcp)
- [jxa-ui-explorer](https://github.com/stephancasas/jxa-ui-explorer)

### Automation Techniques
- [AppleScript - Executing JavaScript in Safari and Chrome](https://kmarsden.com/2016/06/applescript-executing-javascript-in-safari-and-chrome/)
- [Get and manipulate page contents in Safari with do JavaScript](https://alexwlchan.net/til/2024/applescript-do-javascript/)
- [Keyboard Maestro Web Browser Automation](https://wiki.keyboardmaestro.com/assistance/Web_Browser_Automation)
- [Retrieving input field values with Hammerspoon](https://balatero.com/writings/hammerspoon/retrieving-input-field-values-and-cursor-position-with-hammerspoon/)
- [AppleScript/JXA browser URL and title extraction](https://gist.github.com/vitorgalvao/5392178)
- [Scripting Safari with AppleScript and JavaScript](https://forum.latenightsw.com/t/scripting-safari-for-basic-control-with-applescript-and-javascript/996)
- [Getting selected text in Safari](https://www.macscripter.net/t/getting-selected-text-in-safari/72721)

### Browser Automation Agents
- [browser-use: Closer to the Metal - Leaving Playwright for CDP](https://browser-use.com/posts/playwright-to-cdp)
- [Playwright connectOverCDP](https://playwright.dev/docs/api/class-browsertype)
- [Connecting Playwright to an Existing Browser](https://www.browserstack.com/guide/playwright-connect-to-existing-browser)
- [MCP Playwright CDP Server](https://github.com/lars-hagen/mcp-playwright-cdp)
- [Building Browser Agents: Architecture, Security, and Practical Solutions (arXiv)](https://arxiv.org/html/2511.19477v1)

### macOS Security
- [A Guide to TCC Services on macOS Sequoia 15.0](https://atlasgondal.com/macos/priavcy-and-security/app-permissions-priavcy-and-security/a-guide-to-tcc-services-on-macos-sequoia-15-0/)
- [New TCC Bypass CVE-2025-43530](https://securityonline.info/new-tcc-bypass-cve-2025-43530-exposes-macos-to-unchecked-automation/)
- [Can You Really Trust That Permission Pop-Up on macOS? (CVE-2025-31250)](https://wts.dev/posts/tcc-who/)
- [Accessibility APIs: A Key To Web Accessibility (Smashing Magazine)](https://www.smashingmagazine.com/2015/03/web-accessibility-with-accessibility-api/)
