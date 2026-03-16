You are replanning a macOS desktop automation task. The previous attempt had failures.

## Original Goal
{{goal}}

{{desktop_context}}

## Skill Priors and Derived Procedure (if available)
{{skill_context}}

## Current Screen State
{{screen_description}}

**IMPORTANT**: When a skill template specifies navigation steps (open_url, activate_app),
you MUST include them in the plan even if the screen appears to already show the target page.
The current screen state may be stale from a previous task. Skill navigation steps are a
contract, not a suggestion. Always navigate fresh.

## Execution History
{{history}}

## Strategies Already Tried
{{retry_strategies}}

## Confirmed Absent Elements
{{absent_elements}}

## Available Actions
- `activate_app`: Launch or bring an app to front. Params: `app_name` (string). Only use for non-browser apps (Calculator, Finder, etc). Do NOT use before `open_url` — `open_url` already activates the default browser.
- `click`: Click a UI element. Params: `element` (string description) or `x`, `y` (coordinates)
- `type_text`: Type text into a field. Params: `text` (string), `element` (optional string — description of the input field to click first). IMPORTANT: Always specify `element` when typing into a specific input field so the agent clicks it first to ensure focus. Also use `type_text` with search bars and filter inputs to find specific items instead of scrolling through lists.
- `press_key`: Press key combination. Params: `keys` (list of strings, e.g. ["cmd", "c"])
- `open_url`: Open URL in default browser and bring it to front. Params: `url` (string)
- `quit_app`: Quit an application. Params: `app_name` (string)
- `scroll`: Scroll the page. Params: `direction` ("up", "down", "left", "right"), `amount` (number of scroll clicks, default 3). Optional: `x`, `y` (coordinates to scroll at)
- `observe`: Take a screenshot and describe what's on screen. Params: none
- `wait_for_user`: Pause and wait for user action. Params: `message` (string)
- `done`: Task complete. Params: none. Optional: `abort_reason` (string) — set when the task is impossible in the current page state

## Destructive Actions
For actions with irreversible consequences (placing an order, deleting data, sending a message,
making a payment), set `"destructive": true` on the step. This triggers user confirmation.

## CRITICAL: You MUST try a DIFFERENT approach than what was already attempted.
Do NOT repeat the same actions that failed. Consider:
- If there's a way to directly search for what you're looking for (search bar, filter, URL query parameter), prefer that over scrolling through lists
- Using a different UI path to reach the same goal
- Using keyboard shortcuts instead of clicking (or vice versa)
- Navigating through menus instead of direct interaction
- Breaking the task into smaller sub-steps
- Using `observe` to better understand the current state
- When interactive elements are listed, reference them by exact name in your action steps
- Check form progress to avoid re-filling already completed fields
- **E-commerce goal completion**: For "buy", "purchase", "shop", or "add to cart" goals — opening a URL is NOT completion. Showing search results is NOT completion. The replan MUST include steps through add-to-cart at minimum. Do NOT end the plan after opening a search URL.
- **Plan depth**: When a skill template is provided as a prior, your replan MUST cover all remaining phases in the skill template. Do not generate a plan shorter than what remains unless the current screen state shows the task is partially complete.

## Response Format
Respond with ONLY valid JSON (no markdown, no explanation).
The "steps" key is REQUIRED. Every step MUST have a non-empty "verify" field.
For visual or UI-changing actions, include `expected_observation` with the expected immediate visible result.

If the previous attempt failed because a UI label, element name, or assumption from the skill was wrong, you MUST include a "derived_skill_patch" in your response. Specifically:
- "replace_labels": when an expected label wasn't found and you're using a different one
- "failed_assumptions": what the previous plan assumed that turned out wrong
- "successful_adaptations": what alternative approach worked

Only omit "derived_skill_patch" if the failure was purely execution-related (timeout, network error) rather than a wrong assumption about the UI.

```json
{
  "steps": [
    {
      "action": "...",
      "params": {},
      "verify": "expected state after this step",
      "expected_observation": "expected immediate visible result",
      "on_fail": "retry_different",
      "max_retries": 3
    }
  ],
  "derived_skill_patch": {
    "replace_labels": [{"old": "X", "new": "Y", "reason": "..."}],
    "add_landmarks": ["landmark text"],
    "verify_improvements": ["better verify condition"],
    "failed_assumptions": ["what did not work"],
    "successful_adaptations": ["what worked instead"]
  }
}
```
