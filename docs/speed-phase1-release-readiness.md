# Speed Phase 1 — Release Readiness

## Backward Compatibility
- [ ] All existing tests pass with new code
- [ ] Feature gates default to safe values
- [ ] No public API changes

## Migration
- N/A (feature-gated additions only)

## Rollback
- Set `AGENT_JS_VERIFICATION_ENABLED=false` to disable JS injection
- Set `AGENT_AX_CONFIDENCE_CALIBRATED=false` to revert to hardcoded 0.95

## Observability
- [ ] structlog events for JS injection success/failure
- [ ] structlog events for calibrated confidence values

## Security
- [ ] JS injection does not execute user-controlled strings
- [ ] No secrets exposed via JS queries

## Performance
- [ ] JS injection < 100ms per call
- [ ] No regression in non-browser scenarios

## Sign-offs
- [ ] Tech Lead final review
- [ ] DE final architecture check
- [ ] PM acceptance
- [ ] Short-Seller CLEAR TO SHIP
- [ ] All specialist reviewers approved
