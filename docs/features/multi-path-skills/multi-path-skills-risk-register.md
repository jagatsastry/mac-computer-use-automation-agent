# Multi-Path Skills — Risk Register

| # | Risk | Severity | Owner | Mitigation | Evidence | Status |
|---|------|----------|-------|-----------|----------|--------|
| 1 | LLM wording drift prevents observation grouping (legacy problem) | HIGH | Lead | New system groups by step index, not text | Plan design doc | mitigated-by-design |
| 2 | Cold-start intent grouping too coarse | MEDIUM | Eng 1 | Start with site+verb extraction; LLM fallback if pattern fails | TBD | open |
| 3 | Instance file proliferation | LOW | Eng 3 | Cap at max_instances_per_skill (default 5) | TBD | open |
