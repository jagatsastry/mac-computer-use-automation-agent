# Specialist Trigger Matrix — target-buy-fixes

| Specialist | Trigger | Applies? | Spawned? | Notes |
|-----------|---------|----------|----------|-------|
| Security | Shell commands (AppleScript), untrusted input (user prompts) | YES | Always | type_text focus, open_url changes touch AppleScript execution |
| Performance | Retry loops, screenshot diff computation | YES | Always | Scroll diff verification, type_text focus click add latency |
| Migration & Compatibility | Skill file format, skill router behavior changes | YES | Always | New skill file, router prompt changes affect existing skills |
| Observability | Logging changes, verification behavior changes | YES | Always | Scroll verification, open_url verification changes affect logging |
