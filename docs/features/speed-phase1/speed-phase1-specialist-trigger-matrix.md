# Speed Phase 1 — Specialist Trigger Matrix

| Specialist | Trigger Evaluated | Triggered? | Why | Spawned | Status |
|-----------|-------------------|------------|-----|---------|--------|
| Security Reviewer | JS injection executes code in browser context via AppleScript | Yes | Shell command execution with JS strings | Always spawned | Pending |
| Performance Reviewer | Latency reduction is the core goal; must verify JS < 100ms | Yes | Core feature is about speed | Always spawned | Pending |
| Migration & Compatibility Reviewer | New config fields added; existing behavior gated | Yes | New env vars, config fields | Always spawned | Pending |
| Observability & Release Reviewer | New logging points for JS injection and confidence calibration | Yes | Operational observability of new paths | Always spawned | Pending |
