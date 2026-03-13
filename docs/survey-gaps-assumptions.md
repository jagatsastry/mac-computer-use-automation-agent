# Survey Gaps — Execution Assumptions

| # | Assumption | Why | Impact | Owner | Status |
|---|-----------|-----|--------|-------|--------|
| 1 | All 7 gaps can be implemented independently | Survey treats them as orthogonal improvements | Enables parallel engineering | Lead | active |
| 2 | Existing test infrastructure (pytest, mocks) sufficient | Codebase already has 500+ unit tests | No new test tooling needed | Lead | active |
| 3 | Embedding retrieval can use sentence-transformers locally | Avoid API dependency for skill matching | May need new pip dependency | Lead | active |
| 4 | Lookahead uses existing VLM for prediction | No new model needed | Reuses vision coordinator | Lead | active |
