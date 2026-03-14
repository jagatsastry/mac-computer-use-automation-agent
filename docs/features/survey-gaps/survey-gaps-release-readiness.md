# Survey Gaps — Release Readiness

## Backward Compatibility
- All 7 features are additive; no existing behavior changes unless opted in via config
- All features default to OFF (som_enabled=False, dual_resolution_grounding=False, skill_embedding_enabled=False, lookahead_enabled=False, confirm_destructive=SMART)
- Existing tests pass without modification (1010 passed, 8 pre-existing failures)

## Migration
- No data migration needed
- New config keys with sensible defaults (features off by default)
- New optional dependency: `fastembed` for embedding retrieval (`pip install -e '.[embeddings]'`)

## Rollback
- Revert commit; no persistent state changes
- All features are config-gated — can be disabled without code changes

## Observability
- 16 new EventType enums, all wired to EventLogger (LOOKAHEAD_PREDICT/BLOCK/ERROR, SOM_ANNOTATE/PARSE/ERROR, EMBEDDING_BUILD/QUERY/RERANK_SKIP/ERROR, INFEASIBILITY_CHECK/ABORT, DESTRUCTIVE_CONFIRM/ERROR, DUAL_RES_GROUNDING, STATE_DIFF)
- Structured logging via structlog at all decision points
- Pre-existing orphans (PLAN_ERROR, SCREENSHOT_CAPTURE, SCREEN_DESCRIBE, AGENT_INIT, USER_RESUME, TASK_SUMMARY) not addressed — separate concern

## Performance
- Lookahead: +1 VLM call per destructive step (optional, off by default)
- Dual-res: +1 crop per screenshot above 1440px threshold (cheap)
- Embedding: one-time model load (~50MB fastembed), <10ms per query
- TOCTOU: +1 screenshot capture per hard-destructive confirmation (negligible)

## Security
- No new external inputs; embedding model loaded locally
- Auto-promoted skills marked `trusted: false` by default (requires human review for embedding index)
- TOCTOU mitigation: post-confirmation screenshot diff detects UI changes
- NFKC normalization on destructive keyword matching (Unicode bypass prevention)
- LITL-safe display sanitization for confirmation prompts

## Specialist Sign-offs
- Observability: APPROVED (conditional — 5 medium-severity follow-ups for pre-existing EventType orphans)
- Reliability: APPROVED (after lookahead wiring fix)
- Security: APPROVED (13 findings resolved, TOCTOU implemented)
- Code Quality: APPROVED (7 low-severity findings, 0 blocking)
