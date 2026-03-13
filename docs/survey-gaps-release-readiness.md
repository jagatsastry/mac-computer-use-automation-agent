# Survey Gaps — Release Readiness

## Backward Compatibility
- All 7 features are additive; no existing behavior changes unless opted in via config

## Migration
- No data migration needed
- New config keys with sensible defaults (features off by default where risky)

## Rollback
- Revert commit; no persistent state changes

## Observability
- TBD after implementation

## Performance
- Lookahead: +1 VLM call per step (optional)
- Dual-res: +1 crop per screenshot (cheap)
- Embedding: one-time model load (~200MB)

## Security
- No new external inputs; embedding model loaded locally

## Specialist Sign-offs
- Observability: pending
- Reliability: pending
- Security: pending
- Code Quality: pending
