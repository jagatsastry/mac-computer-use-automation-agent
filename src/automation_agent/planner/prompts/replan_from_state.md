You are replanning a macOS desktop automation task. The previous attempt had failures.

## Original Goal
{{goal}}

{{desktop_context}}

## Skill Priors and Derived Procedure (if available)
{{skill_context}}

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
Respond with ONLY valid JSON (no markdown, no explanation).
The "steps" key is REQUIRED. Every step MUST have a non-empty "verify" field.
For visual or UI-changing actions, include `expected_observation` with the expected immediate visible result.
Optionally include a "derived_skill_patch" if you discovered corrections
that should be remembered for the rest of this run:
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
The "derived_skill_patch" field is optional. If you have no corrections, omit it.
