# Survey Gaps — Risk Register

| # | Risk | Severity | Owner | Mitigation | Evidence | Status |
|---|------|----------|-------|------------|----------|--------|
| 1 | Embedding model adds ~50MB dependency | Low | Lead | Optional dep via `.[embeddings]`; lazy import with ImportError fallback | AC-19 tests pass; graceful degradation verified | closed |
| 2 | Lookahead doubles VLM calls per step | Medium | Lead | Off by default; only for destructive steps; timeout protection | config gate + timeout tests pass | closed |
| 3 | Set-of-mark overlays may confuse VLM | Low | Lead | Configurable (som_enabled=False default); falls through to standard grounding on failure | SoM error fallback tested | closed |
| 4 | TOCTOU between grounding and dispatch | Medium | Lead | Post-confirmation screenshot diff for hard-destructive steps | TOCTOU test added; diff_ratio > threshold aborts step | closed |
| 5 | Auto-promoted skills in embedding index | Medium | Lead | Librarian injects `trusted: false`; registry filters by trusted=True for embeddings | Librarian code updated | closed |
| 6 | Pre-existing EventType orphans (PLAN_ERROR etc.) | Low | Lead | Not from survey-gaps; tracked as separate tech debt | 6 pre-existing orphans unrelated to this feature | accepted |
