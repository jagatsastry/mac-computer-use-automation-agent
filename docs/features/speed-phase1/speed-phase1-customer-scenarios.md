# Customer Testing Scenarios: Speed Phase 1

## Scenario A1: Type text in Safari search bar
- **Category:** happy_path
- **Prompt:** "Open Safari and search for 'best hiking trails in Oregon'"
- **Expected outcome:** Safari is open with search results for "best hiking trails in Oregon"
- **Success criteria:**
  1. Safari is the frontmost app
  2. The search/URL bar or page content contains "hiking trails" or "Oregon"
  3. type_text verification resolved at Tier 1 (events.jsonl shows `verify_method: actuator_state` for type_text steps, NOT `vision`)
  4. Total run completes in under 120 seconds
- **Notes:** This is the primary happy path for JS injection — type_text should populate focused_value and Tier 1 should resolve without vision.

## Scenario A2: Navigate to a page and verify heading
- **Category:** happy_path
- **Prompt:** "Open Safari and go to apple.com"
- **Expected outcome:** Safari shows apple.com homepage
- **Success criteria:**
  1. Safari is frontmost showing apple.com
  2. open_url verification resolved at Tier 1 (URL match)
  3. Any post-navigation verify conditions check page_title or page_heading at Tier 1 before falling to vision
  4. events.jsonl shows `page_title` and/or `page_heading` populated in state snapshots
- **Notes:** Tests that JS-enriched page state (page_title, page_heading) is populated in get_state() for browser apps.

## Scenario B1: Calculator with AX grounding
- **Category:** happy_path
- **Prompt:** "Open Calculator and compute 42 times 7"
- **Expected outcome:** Calculator shows the result 294
- **Success criteria:**
  1. Calculator is frontmost
  2. Display shows 294
  3. AX grounding used for button clicks (events.jsonl shows `source: accessibility` in element_found events)
  4. AX confidence is calibrated (confidence values in events are between 0.6 and 0.95, not always 0.95)
- **Notes:** Calculator buttons have exact AX labels — tests that exact matches still get 0.95 confidence while the formula is working.

## Scenario C1: Type in a form field on a webpage
- **Category:** edge_case
- **Prompt:** "Open Safari, go to google.com, and type 'automation testing tools'"
- **Expected outcome:** Google search page with "automation testing tools" typed in the search box
- **Success criteria:**
  1. Safari shows google.com
  2. Search box contains "automation testing tools"
  3. type_text step verification uses Tier 1 JS (focused_value populated and matched)
  4. No Tier 2 vision escalation for the type_text step
- **Notes:** Google's search box is a standard input — good test for focused_value JS injection.

## Scenario D1: Non-browser app (control — no JS injection expected)
- **Category:** edge_case
- **Prompt:** "Open TextEdit and type 'Hello World'"
- **Expected outcome:** TextEdit is open with "Hello World" typed
- **Success criteria:**
  1. TextEdit is frontmost with "Hello World" visible
  2. type_text verification falls through to Tier 2 vision (JS injection returns None for non-browser apps)
  3. No errors or crashes from JS injection attempt on non-browser app
- **Notes:** Control scenario — verifies JS injection gracefully returns None for non-browser, and Tier 2 vision handles it correctly.
