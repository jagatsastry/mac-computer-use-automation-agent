# Survey Gaps — Specialist Trigger Matrix

| Specialist | Trigger | Applies? | Spawned? | Why |
|-----------|---------|----------|----------|-----|
| Security | Shell commands in actuator, embedding model input | Yes | Pending | Set-of-mark overlay code, embedding input sanitization |
| Performance | Lookahead doubles VLM calls, embedding model load time | Yes | Pending | Lookahead latency, embedding cold start |
| Migration & Compatibility | New config keys, new optional dependencies | Yes | Pending | pip extras, config schema changes |
| Observability & Release | New logging for world-state, infeasibility detection | Yes | Pending | Operator visibility into new features |
