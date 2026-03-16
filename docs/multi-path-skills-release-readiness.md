# Multi-Path Skills — Release Readiness

## Backward Compatibility
- [ ] Existing skill files continue to load and function
- [ ] Old distiller/experience/librarian code still works (not removed)
- [ ] Existing tests pass without modification

## Migration
- [ ] No data migration needed (new paths/ directory created on first use)
- [ ] Config fields have sensible defaults (synthesis enabled by default)

## Rollback
- [ ] Set `skill_synthesis_enabled=false` to disable new system
- [ ] Old pipeline continues to function independently

## Observability
- [ ] structlog events for path recording, synthesis, instance creation
- [ ] Events logged to events.jsonl

## Security
- [ ] No new external inputs or untrusted data handling
- [ ] LLM outputs validated via parse_skill_file()

## Performance
- [ ] Synthesis is async, runs post-execution
- [ ] One LLM call per synthesis event, not per run

## Specialist Reviews
- [ ] Observability: pending
- [ ] Reliability: pending
- [ ] Security: pending
- [ ] Code & Design Quality: pending
