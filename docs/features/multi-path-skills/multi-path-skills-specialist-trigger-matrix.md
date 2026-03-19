# Multi-Path Skills — Specialist Trigger Matrix

| Specialist | Trigger Condition | Applies? | Spawned? | Why |
|-----------|-------------------|----------|----------|-----|
| Security Reviewer | LLM output processed as code/config; file system writes | Yes | Always (core reviewer) | Skill files written to disk from LLM output; path traversal risk |
| Performance Reviewer | JSONL append-only storage grows unbounded | Maybe | On trigger | Observation files could grow large over many runs |
| Migration & Compatibility Reviewer | Config field additions; new file format (instance files) | Yes | On trigger | New config fields; new directory structure under skills/library/ |
| Observability & Release Reviewer | New subsystem with logging; replaces existing pipeline | Yes | Always (core reviewer) | Need structured logging for path recording and synthesis |
