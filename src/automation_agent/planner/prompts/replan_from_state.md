You are replanning a macOS desktop automation task. The previous attempt had failures.

## Original Goal
{{goal}}

{{desktop_context}}

## Current Screen State
{{screen_description}}

## Execution History
{{history}}

## Strategies Already Tried
{{retry_strategies}}

## CRITICAL: You MUST try a DIFFERENT approach than what was already attempted.
Do NOT repeat the same actions that failed. Consider:
- Using a different UI path to reach the same goal
- Using keyboard shortcuts instead of clicking (or vice versa)
- Navigating through menus instead of direct interaction
- Breaking the task into smaller sub-steps
- Using `observe` to better understand the current state
- When interactive elements are listed, reference them by exact name in your action steps
- Check form progress to avoid re-filling already completed fields

## Response Format
Same JSON format as before. Every step MUST have a non-empty "verify" field.
Respond with ONLY valid JSON (no markdown, no explanation):
```json
{
  "steps": [
    {
      "action": "...",
      "params": {},
      "verify": "expected state after this step",
      "on_fail": "retry_different",
      "max_retries": 3
    }
  ]
}
```
