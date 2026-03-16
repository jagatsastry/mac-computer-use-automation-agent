# Multi-Path Skills — Execution Assumptions

| # | Assumption | Why | Impact | Owner | Status |
|---|-----------|-----|--------|-------|--------|
| 1 | Existing distiller/experience/librarian code stays until Phase 4 cleanup | Allows incremental migration, dual-write during transition | New code coexists with old; no removals needed | Lead | active |
| 2 | `extract_site_entity()` is sufficient for cold-start intent grouping | Already handles site detection via preposition patterns + domain patterns | If grouping is too coarse, cold-start synthesis won't trigger | Engineer 1 | active |
| 3 | Step alignment by action+params similarity is accurate enough | Skill steps and trace steps use the same action vocabulary | Misalignment would cause wrong pivot detection | Engineer 1 | active |
| 4 | Instance files can reuse the existing `parse_skill_file()` format | Instance files have the same YAML+MD structure | Need to add instance-specific frontmatter fields | Engineer 2 | active |
| 5 | Gemini 2.5 Flash can reliably produce valid skill MD in JSON response | Used for synthesis prompts; max_tokens=8192 | Truncation or invalid MD would require repair logic | Engineer 3 | active |
