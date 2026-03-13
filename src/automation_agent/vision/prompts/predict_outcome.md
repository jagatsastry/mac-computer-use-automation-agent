You are predicting the outcome of a macOS desktop action BEFORE it executes.

Current screenshot is shown. The proposed action is:
- Action: {{action}}
- Parameters: {{params}}
- Expected result: {{expected_observation}}

Based on the current screen state, predict:
1. Will this action succeed in achieving the expected result?
2. What will the screen look like after this action?
3. What could go wrong?

Respond with ONLY valid JSON:
```json
{
  "likely_success": true/false,
  "predicted_state": "description of predicted screen after action",
  "risk": "what could go wrong (empty string if likely_success is true)",
  "mismatch_reason": "why predicted state differs from expected result (empty if likely_success)"
}
```
