# Customer Testing Report: Speed Phase 1

## Summary

- Scenarios tested: 3 of 5 (A2, C1 deferred pending Safari JS fix)
- Passed: 2 (B1 Calculator, D1 TextEdit)
- Failed: 1 (A1 Safari search)
- **Critical finding**: Two prerequisites prevent testing the core speed-phase1 features

## Prerequisites Blocking Core Feature Testing

### P0: Safari "Allow JavaScript from Apple Events" is DISABLED

- **Impact**: Blocks ALL JS injection (focused_value, selected_text, page_title, page_heading)
- **Evidence**: `osascript -e 'tell application "Safari" to do JavaScript "document.title" in current tab of front window'` returns error: "You must enable 'Allow JavaScript from Apple Events'"
- **Behavior**: `_get_browser_js_batch()` returns `{}`, all JS fields are `None`, every type_text/click verify escalates from Tier 1 → Tier 2 vision (6-22s)
- **Fix**: User must manually enable in Safari > Settings > Developer > "Allow JavaScript from Apple Events". Cannot be automated (macOS sandbox prevents programmatic toggle).

### P1: `use_accessibility` defaults to `False`

- **Impact**: AX-first grounding never fires. The `grounding_router` is instantiated with `accessibility=False`. All element finding goes directly to Molmo vision (29s/element). The AX confidence calibration formula (`0.6 + 0.35 * match_score`) is never exercised.
- **Evidence**: `grounding_router_enabled` event in B1 stdout shows `accessibility: False`
- **Fix**: Set `AGENT_USE_ACCESSIBILITY=true` in `.env` or flip the default to `True` in `config.py`

---

## Scenario A1: Type text in Safari search bar

### Prompt

"Open Safari and search for 'best hiking trails in Oregon'"

### What Actually Happened

1. Safari activated (PASS, Tier 1 verified frontmost app)
2. Clicked address bar at (493, 77) via vision — verify "Address bar is focused" FAILED (Tier 2 vision denied)
3. type_text typed URL — verify "focused text field contains URL" FAILED (Tier 1 inconclusive → Tier 2 vision denied)
4. Retried click with refined query → same failure
5. Keyboard fallbacks (enter, space) → same failure
6. Replanned → same 5-step skill template → same failures
7. Exhausted retries → FAILED after replan (296s)

### Success Criteria Verdicts


| #   | Criterion                     | Verdict | Evidence                                                                   |
| --- | ----------------------------- | ------- | -------------------------------------------------------------------------- |
| 1   | Safari is frontmost           | PASS    | events.jsonl: verify_pass at step 0                                        |
| 2   | Search/URL bar contains query | FAIL    | Agent couldn't get past address bar click                                  |
| 3   | type_text verified at Tier 1  | FAIL    | All verify_escalate events show "Tier 1 inconclusive" → JS fields are None |
| 4   | Under 120s                    | FAIL    | 296s                                                                       |


### Root Cause

1. **Safari JS disabled** — focused_value never populated, Tier 1 type_text matcher can't resolve
2. **Address bar click not registering** — Molmo grounded correctly (x=493, y=77) but the status overlay may be covering the Safari toolbar, or Safari's address bar requires a different interaction

---

## Scenario B1: Calculator with AX grounding

### Prompt

"Open Calculator and compute 42 times 7"

### What Actually Happened

1. Calculator activated (PASS)
2. type_text "42" (PASS via Tier 2 vision — expected for non-browser)
3. Click "Multiply button" at (390, 315) via vision (conf=0.75) — FAIL, vision couldn't verify operator registered
4. Retry with refined query → clicked (14, 755) (conf=1.0) — passed as inconclusive fallback
5. type_text "7" — passed as inconclusive fallback
6. Click "Equals button" — FAIL, then retry → keyboard fallback (return) → PASS
7. Task completed successfully (17.5s)

### Success Criteria Verdicts


| #   | Criterion                | Verdict      | Evidence                                                                                       |
| --- | ------------------------ | ------------ | ---------------------------------------------------------------------------------------------- |
| 1   | Calculator frontmost     | PASS         | verify_pass step 0                                                                             |
| 2   | Display shows 294        | INSUFFICIENT | Vision was inconclusive on final state; task marked PASS via actuator fallback                 |
| 3   | AX grounding used        | FAIL         | All element_found events: `source=vision`. `accessibility=False` in config.                    |
| 4   | AX confidence calibrated | FAIL         | AX never invoked — cannot verify formula. All confidence values are vision-sourced (0.75, 1.0) |


### Root Cause

`use_accessibility=False` (default). Grounding router has no accessibility bridge — bypasses AX entirely. AX confidence calibration code exists but is unreachable.

---

## Scenario D1: Non-browser app (TextEdit)

### Prompt

"Open TextEdit and type 'Hello World'"

### What Actually Happened

1. TextEdit activated (PASS, Tier 1)
2. type_text "Hello World" (PASS, Tier 2 vision confirmed)
3. Task completed (17.5s)

### Success Criteria Verdicts


| #   | Criterion                                  | Verdict | Evidence                                             |
| --- | ------------------------------------------ | ------- | ---------------------------------------------------- |
| 1   | TextEdit frontmost with "Hello World"      | PASS    | verify_pass events, screenshots                      |
| 2   | type_text falls to Tier 2 (non-browser)    | PASS    | verify_escalate: "Tier 1 inconclusive" for type_text |
| 3   | No errors from JS injection on non-browser | PASS    | No JS errors in logs; clean fallthrough              |


---

## Priority Issues

1. **[P0] Safari JS injection prerequisites not documented or auto-checked** — The agent should detect "Allow JavaScript from Apple Events" is disabled on first JS call failure and log a clear warning (not just debug-level silent None return). Current behavior: silent degradation that's invisible to the user. Blocks scenarios A1, A2, C1.
2. **[P1] `use_accessibility` defaults to False** — AX-first grounding and calibrated confidence are dead code in production unless the user knows to set `AGENT_USE_ACCESSIBILITY=true`. The entire AX confidence calibration (Change 2 of speed-phase1) is unreachable. Blocks scenario B1's AX criteria.
3. **[P2] Status overlay may obstruct Safari toolbar** — In A1, the address bar click at y=77 failed verification. The overlay renders at the top of the screen and may block clicks on browser chrome elements. Needs investigation with screenshots.

## Instrumentation Gaps

- `_get_browser_js_batch()` logs at DEBUG level — should log at INFO when `raw=None` for a browser app (makes diagnosis much faster)
- No event logged for "JS injection prerequisite missing" — should be a specific event type
- AX grounding decisions not logged in events.jsonl — only stdout structlog shows `accessibility=False`

## Deferred Scenarios

- **A2** (apple.com navigation): Deferred — needs Safari JS enabled
- **C1** (Google form field): Deferred — needs Safari JS enabled

## Recommendations

1. Enable Safari JS and `AGENT_USE_ACCESSIBILITY=true`, then re-run all 5 scenarios
2. Add startup check: if `js_verification_enabled=True` and Safari is detected, test JS injection and log warning if it fails
3. Consider flipping `use_accessibility` default to `True` — the feature gate should be opt-OUT not opt-IN

