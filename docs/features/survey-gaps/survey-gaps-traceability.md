# Survey Gaps — Traceability Matrix

AC-to-spec mapping. Every acceptance criterion from the PRD is traced to a specific section in `survey-gaps-spec.md`.

| AC | PRD Description | Spec Section | Slice | Key Implementation |
|----|----------------|--------------|-------|--------------------|
| AC-1 | Frustration score: same-state, identical-action, replan count | Gap 5 → Data Shapes: `FrustrationScore` dataclass | 1 | `orchestrator/agent.py` — `FrustrationScore` with `reset_on_progress()` |
| AC-2 | OR-based threshold trigger + advisory reset | Gap 5 → Control Flow: `is_triggered()` + advisory cap | 1 | `orchestrator/agent.py` — `_check_infeasibility()` |
| AC-3 | `infeasibility_reason` on `ExecutionResult` | Gap 5 → Data Shapes: `ExecutionResult.infeasibility_reason` | 1 | `shared_models.py` — new field |
| AC-4 | Critical-path absence immediate trigger (click steps only) | Gap 5 → Control Flow: `_is_element_absent()` check | 1 | `orchestrator/agent.py` — `force=True` path |
| AC-5 | Configurable thresholds | Gap 5 → Config fields + Configuration Summary | 1 | `config.py` — 3 new int fields |
| AC-6 | Destructive step classification (2 signals: keyword + flag) | Gap 6 → `_is_destructive_step()` | 1 | `orchestrator/agent.py` — keyword scan (verify + element for all steps) + planner flag; AC-6c subsumed by AC-6a (short-seller issue 4: type_text verify path was dead code, removed); NFKC normalization + expanded keywords (security finding 12) |
| AC-7 | Raw action display before execution | Gap 6 → `_prompt_user_confirmation()` → `ConfirmationHandler.confirm()` | 1 | Injectable handler protocol; default `ConsoleConfirmationHandler` uses `asyncio.to_thread(input, ...)` (DE R3 issues 12+15) |
| AC-8 | Three-mode confirmation config (smart logic revised) | Gap 6 → `_should_confirm_phase1()` + `_should_confirm_phase2()` + Config | 1 | Two-phase gate: pre-grounding (planner flag, always mode) + post-grounding (keyword match with confidence check) |
| AC-9 | Dry-run skips confirmation | Gap 6 → Control Flow: `if not config.dry_run` | 1 | `orchestrator/agent.py` — gate |
| AC-10 | JSONL audit log for every confirmation decision | Gap 6 → `_log_confirmation()` + EventType | 1 | `logging/models.py` — `DESTRUCTIVE_CONFIRM` |
| AC-11 | `annotate_screenshot()` draws numbered boxes | Gap 1 → `vision/annotator.py` | 2 | New module with coordinate mapping |
| AC-12 | 20-element cap on SoM labels | Gap 1 → `annotate_screenshot(max_labels=20)` | 2 | `vision/annotator.py` — `elements[:max_labels]` |
| AC-13 | `FOUND: element_number=N` parse + mapping | Gap 1 → `_parse_som_response()` | 2 | `vision/coordinator.py` — regex + candidates lookup |
| AC-14 | Fallback to raw coords when < 3 AX elements | Gap 1 → `find_element()` SoM gate: `len(candidates) >= 3` | 2 | `vision/coordinator.py` — condition check |
| AC-15 | `config.som_enabled` gate (default False) | Gap 1 → Config + coordinator gate | 2 | `config.py` — bool field |
| AC-16 | `EmbeddingIndex` with `build()` + `query()` | Gap 3 → `skills/embeddings.py` | 3 | fastembed + cosine similarity |
| AC-17 | Three-stage pipeline: embed → re-rank → keyword | Gap 3 → `registry.py` match() rewrite | 3 | Conditional LLM re-rank; skip threshold raised to 0.92 + gap 0.15 (short-seller fix) |
| AC-18 | Embed from summary + description + tags | Gap 3 → `EmbeddingIndex.build()` | 3 | Concatenation of 3 metadata fields |
| AC-19 | Optional install extra `[embeddings]` | Gap 3 → Dependency Configuration | 3 | `pyproject.toml` + ImportError fallback |
| AC-20 | `config.skill_embedding_enabled` gate | Gap 3 → Config + registry gate | 3 | `config.py` — bool field |
| AC-21 | `find_element_dual()` with context_b64 | Gap 7 → New Method (Protocol-Extended) + Protocol Extension | 2 | `vision/coordinator.py` — `capabilities()` gate (DE R3 issue 12) |
| AC-22 | `find_element_dual.md` prompt template | Gap 7 → New Prompt Template | 2 | Image 1 = overview, Image 2 = detail |
| AC-23 | Dual-res trigger: width > threshold OR last_successful_region | Gap 7 → Orchestrator Integration | 2 | `orchestrator/agent.py` — `_find_element()` |
| AC-24 | Crop coordinate mapping to full-image space | Gap 7 → Orchestrator Integration: `crop_offset` | 2 | Existing `crop_offset` pattern reused |
| AC-25 | `config.dual_resolution_grounding` gate | Gap 7 → Config | 2 | `config.py` — bool field |
| AC-26 | DesktopContext: page_semantic_label, obstacles, milestones, version | Gap 2 → Data Shapes: extended DesktopContext | 3 | `context_monitor.py` — 4 new fields |
| AC-27 | `format_state_diff()` with changes/new/removed | Gap 2 → `StateDiff` dataclass + method | 3 | Diffs only high-signal fields (round 3 issue 15) |
| AC-28 | `format_for_planner()` includes cumulative progress | Gap 2 → updated `format_for_planner()` | 3 | Milestones + obstacles + diff in output |
| AC-29 | Step outcomes persisted via `record_step_outcome()` | Gap 2 → `record_step_outcome()` + `_record_context()` | 3 | Writers for all AC-26 fields specified; obstacles truncated to 200 chars (security finding 13) |
| AC-30 | `predict_action_outcome()` with 4-field return | Gap 4 → New Method on coordinator | 3 | `vision/coordinator.py` — VLM prediction |
| AC-31 | Lookahead only for destructive steps + skip-when-confirmed | Gap 4 → Orchestrator Integration | 3 | Uses `_is_destructive_step()` from Gap 6 |
| AC-32 | Skip dispatch on predicted failure | Gap 4 → Orchestrator Integration | 3 | Returns StepResult with "lookahead" method |
| AC-33 | `config.lookahead_enabled` gate | Gap 4 → Config | 3 | `config.py` — bool field |
