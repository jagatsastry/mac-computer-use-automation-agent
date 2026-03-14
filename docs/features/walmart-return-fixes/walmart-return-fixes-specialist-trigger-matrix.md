# Specialist Trigger Matrix: walmart-return-fixes

## Evaluation Date: 2026-03-13

| Specialist | Trigger Evaluation | Spawned? | Reason |
|-----------|-------------------|----------|--------|
| Security Reviewer | No untrusted input changes, no shell command changes, no credential handling changes | Always spawned (required) | Required by process |
| Performance Reviewer | No latency/throughput changes, no retry loop changes beyond existing patterns | No | Bug fixes don't introduce performance-sensitive paths |
| Migration & Compatibility Reviewer | No public API changes, no config changes, no schema changes | No | Internal bug fixes only, no interface changes |
| Observability & Release Reviewer | Screenshot saving fix adds observability, but no deployable service changes | No | Screenshot fix is observability improvement but doesn't need ops review |
| Observability Specialist | Always required | Yes | Required by process |
| Reliability Specialist | Always required — scroll recovery and retry logic are reliability concerns | Yes | Required by process; P1-2 scroll recovery is core reliability |
| Security Specialist | Always required | Yes | Required by process |
| Code & Design Quality Specialist | Always required | Yes | Required by process |

## Notes
- This is a bug-fix project, not a new feature
- No elastic specialists triggered beyond the 4 always-required ones
- Re-evaluate if any fix changes public interfaces or adds config options
