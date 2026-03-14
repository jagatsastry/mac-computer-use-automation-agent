# Survey Gaps — Product Requirements Document

**Date**: 2026-03-13
**Author**: Product Manager
**Status**: Approved (3 review rounds completed)
**Depends on**: [survey-gaps-sota.md](survey-gaps-sota.md), [survey-gaps-codebase.md](survey-gaps-codebase.md)

---

## Problem Statement

The macOS automation agent completes multi-step desktop tasks using vision AI and accessibility APIs. Field testing and a survey of recent computer-use-agent literature reveal seven capability gaps that cause real user pain:

1. **Coordinate grounding errors** — The VLM must predict raw (x,y) coordinates for every click. Small or visually ambiguous elements are frequently missed, causing misclicks and retries. Set-of-Mark prompting lets the model pick a numbered label instead, eliminating coordinate prediction entirely.

2. **Stateless planning** — The planner receives a flat snapshot of the current screen with no memory of what changed between steps. Multi-step tasks that span pages or require "what changed?" reasoning break because the planner lacks cumulative context.

3. **Brittle skill matching** — Skill lookup uses keyword overlap, which fails for paraphrased or synonym-heavy queries (e.g., "send back my Walmart purchase" does not match trigger keyword "return"). Embedding-based retrieval handles semantic similarity naturally.

4. **No pre-action safety net** — The agent executes actions and only discovers failure after the fact. For high-risk actions, predicting the outcome before execution could prevent irreversible mistakes.

5. **Infinite retry loops on impossible tasks** — When a task is genuinely infeasible (e.g., a "Return" button does not exist on the page), the agent exhausts all retries and the iteration budget before giving up, wasting time and tokens.

6. **Destructive actions execute without consent** — The agent can click "Place Order," "Delete," or "Send" without pausing for user approval. One misgrounded click on a payment button has real financial consequences.

7. **Poor grounding on high-DPI screens** — On large or Retina displays, small icons and text are barely visible in a single downscaled screenshot. Sending a full overview plus a zoomed crop improves precision for small targets.

---

## Priority

Derived from SOTA research impact vs. implementation complexity (sota.md §Summary, codebase.md §Summary Table):

| Priority | Gaps | Rationale |
|----------|------|-----------|
| **P0** | 5 (Infeasibility Detection), 6 (User Confirmation) | Safety and UX. Low complexity. Immediate user-visible impact. |
| **P1** | 1 (Set-of-Mark), 3 (Embedding Retrieval), 7 (Dual-Resolution) | Accuracy improvements. Build on existing infrastructure (AX elements, skill registry, crop logic). |
| **P2** | 2 (World-State Document), 4 (Lookahead/Simulation) | Higher infrastructure cost. Start with lightweight versions only. |

---

## Acceptance Criteria

### P0 — Safety

#### Gap 5: Infeasibility Detection

The codebase already has `done + abort_reason` handling, `_is_element_absent()`, and absent-element injection into replan prompts (codebase.md §5). The gap is that these pieces are loosely wired: no proactive check, no structured signal, no diminishing-returns detection.

- **AC-1**: The orchestrator must track a "frustration score" comprising: (a) consecutive same-state-after-action count, (b) identical action retry count, (c) replan count. Each metric must be independently queryable. **Reset semantics**: (a) resets to 0 after any step where `_image_diff_ratio()` exceeds 0.05 (screen visibly changed); (b) resets to 0 when the action+params combination differs from the previous attempt; (c) never resets within a single `execute()` invocation (cumulative). All three reset to 0 at the start of each `execute()` call.
  *Evidence*: sota.md §5 recommends frustration-based detection; codebase.md §5 notes no budget-aware abort exists today.

- **AC-2**: When frustration score exceeds a configurable threshold (default: 3 same-state **OR** 2 identical replans — disjunctive), the orchestrator must invoke a dedicated infeasibility-check prompt asking the planner whether the task is achievable, passing current absent elements and failure history. If the planner responds "still achievable," the triggering counter resets to 0 and execution continues. However, the total number of advisory checks (planner said "achievable") is capped at `config.infeasibility_max_advisory_checks: int` (default 2). On the (N+1)th trigger, the orchestrator must hard-abort with `infeasibility_reason = "Exhausted N advisory checks without progress"` regardless of planner response. This prevents optimistic planners from burning the full `max_iterations` budget through repeated "still achievable" responses. `config.max_iterations` remains the independent hard ceiling.
  *Evidence*: sota.md §5 recommends planner verification after threshold; ImpossibleBench `flag_for_human_intervention` pattern. sota.md §5 notes LLMs are overconfident — capping advisory resets prevents the optimism loop.

- **AC-3**: When the planner responds that the task is infeasible, the orchestrator must return `ExecutionResult` with `success=False` and a new `infeasibility_reason: str` field containing a human-readable explanation. **Note**: `infeasibility_reason` is a structured field for programmatic consumers (skill learning, dashboards, test assertions). The human-readable message for CLI/overlay display is already carried by `ExecutionResult.message` and `ExecutionResult.error`. No new UX surface is required for this field.
  *Evidence*: codebase.md §5 notes missing structured infeasibility signal.

- **AC-4**: If `_is_element_absent()` confirms absence for a **critical-path element**, the orchestrator must trigger the infeasibility check immediately without waiting for the frustration threshold. A "critical-path element" is defined as `step.params.get("element")` for `click` actions only — `type_text`, `press_key`, `scroll`, and other actions without an element target are excluded from this early-abort path.
  *Evidence*: codebase.md §5 recommends early abort on confirmed critical-path absence. `ActionStep.params["element"]` is the only element target field (shared_models.py:91).

- **AC-5**: Frustration thresholds must be configurable via `config.infeasibility_same_state_limit: int` (default 3), `config.infeasibility_replan_limit: int` (default 2), and `config.infeasibility_max_advisory_checks: int` (default 2).

#### Gap 6: User Confirmation for Destructive Actions

The codebase already has `_CRITICAL_ACTION_KEYWORDS`, a confidence gate at 0.9 for critical elements, and a `wait_for_user` action (codebase.md §6). The gap is that there is no pre-execution confirmation prompt for intended destructive actions.

- **AC-6**: The orchestrator must classify an action step as destructive if any of: (a) `step.verify` or `step.params["element"]` contains a keyword from `_CRITICAL_ACTION_KEYWORDS`, (b) the planner sets a new optional `destructive: bool` flag on the step, (c) the action is `type_text` into a field whose label contains a critical keyword.
  *Evidence*: codebase.md §6 notes existing keyword set; sota.md §6 recommends action-keyword classification + planner flag.

- **AC-7**: Before executing a destructive step, the agent must display a confirmation prompt containing: `step.action`, `step.params` (full dict), and `step.verify` (the postcondition). All three fields must be shown verbatim from the `ActionStep` — no LLM-generated summaries or rephrasing.
  *Evidence*: sota.md §6 cites LITL attack — LLM-generated summaries can be manipulated. `step.verify` provides the user with intent context that raw `params` alone lacks (e.g., params `{"element": "Place Order"}` + verify `"Order confirmation page appears"` makes the consequence clear).

- **AC-8**: Confirmation behavior must be configurable via `config.confirm_destructive: str` with three modes:
  - `"always"` — confirm every step classified as destructive.
  - `"smart"` (default) — decision matrix:
    - **Confirm**: planner set `destructive: true` (AC-6b) — regardless of confidence. The planner explicitly recognized this action has consequences; this is the highest-signal case.
    - **Confirm**: runtime keyword match (AC-6a/c) AND confidence < `_CRITICAL_CONFIDENCE_THRESHOLD` (0.9) — uncertain grounding on a keyword-flagged element warrants user review.
    - **Skip**: runtime keyword match (AC-6a/c) AND confidence >= 0.9 AND planner did NOT set `destructive: true` — likely a false positive (e.g., user typing "send this back" into a search box triggers keyword "send" but the planner knows it's not destructive).
  - `"never"` — no confirmation prompts (unattended/headless mode).
  *Evidence*: sota.md §6 recommends three-mode configuration. The existing 0.9 threshold from `_get_confidence_threshold()` (agent.py:1454-1463) provides the confidence signal. The planner flag is the highest-quality destructive signal — it should increase confirmation likelihood, not decrease it.

- **AC-9**: In `--dry-run` mode, destructive action confirmation must be skipped (no blocking prompt).
  *Evidence*: codebase.md §6 constraint — must not break dry-run.

- **AC-10**: Every confirmation decision must be logged in the JSONL event log with the action details and timestamp. Valid decision values: `"user_approved"`, `"user_denied"`, `"auto_skipped_never_mode"`, `"skipped_dry_run"`. In `--dry-run` mode, the log entry must still be emitted with decision `"skipped_dry_run"` so the audit trail reflects that a destructive action was detected but confirmation was bypassed due to mode.
  *Evidence*: sota.md §6 cites EU AI Act audit trail requirement.

### P1 — Accuracy

#### Gap 1: Set-of-Mark Prompting

The codebase has `get_accessibility_elements()` returning up to 20 labeled elements and `_build_candidate_prefix()` that formats them as a numbered text list (codebase.md §1). The gap is that labels are text-only — not drawn on the screenshot image.

- **AC-11**: A new `annotate_screenshot(screenshot_b64, elements) -> annotated_b64` function must draw numbered bounding boxes on the screenshot image. The function must convert AX element coordinates from screen-pixel space to image space using `_screen_to_image_coords()` (or equivalent scaling based on screenshot dimensions vs. screen dimensions) before drawing. It must accept elements from either `get_accessibility_elements()` (JXA, keys: `center_x, center_y, width, height`) or `get_interactive_elements()` (pyobjc, keys: `position, size`).
  *Evidence*: codebase.md §1 identifies this as the primary missing piece and notes the two element source formats and coordinate space mapping requirement; sota.md §1 describes SoM overlay.

- **AC-12**: The maximum number of SoM labels drawn must respect the existing 20-element cap from `_build_candidate_prefix()`.
  *Evidence*: codebase.md §1 constraint — same cap to avoid visual clutter; sota.md §1 failure mode on dense UIs.

- **AC-13**: When SoM is enabled and annotations are drawn, `_parse_coordinates()` must accept VLM responses in the format `FOUND: element_number=N` and map the number back to the corresponding AX element's center coordinates. It must also continue to accept raw coordinate responses (`FOUND: x=... y=...`) as a secondary pattern, since the VLM may ignore labels and output coordinates anyway.
  *Evidence*: codebase.md §1 integration point #4 — new parse pattern needed.

- **AC-14**: SoM fallback is a **prompt-level switch**: when the accessibility tree returns fewer than 3 elements, `find_element()` must NOT call `annotate_screenshot()` and must use the standard `find_element.md` prompt (raw coordinate grounding). When >= 3 elements are available, it must use the annotated screenshot + a SoM-specific prompt instructing the VLM to respond with `element_number=N`. The decision is made per-call, not globally.
  *Evidence*: sota.md §1 recommended hybrid approach; AX tree misses dynamically-rendered elements. Prompt switching avoids confusion where the VLM sees numbers on the image but is asked for coordinates or vice versa.

- **AC-15**: SoM must be gated behind `config.som_enabled: bool` (default `False`).

#### Gap 3: Embedding-Based Skill Retrieval

The skill registry currently uses LLM-based routing (expensive) with keyword fallback (fragile) (codebase.md §3). Embedding retrieval adds a fast, semantic middle layer.

- **AC-16**: A new `skills/embeddings.py` module must provide `EmbeddingIndex` with `build(skills)` and `query(prompt, top_k) -> List[SkillRouteCandidate]` methods using a local embedding model. The default backend must be `fastembed` with the `BAAI/bge-small-en-v1.5` model (~50MB download, no torch dependency). `sentence-transformers` with `all-MiniLM-L6-v2` is an acceptable alternative but must not be the default due to its ~2GB torch dependency. The backend must be selectable via `config.skill_embedding_backend: str` (default `"fastembed"`).
  *Evidence*: sota.md §3 recommends sentence-transformers for <10ms local inference. fastembed supports the same model family without torch — critical for a local-first agent where 2GB dependencies matter.

- **AC-17**: The `match()` method in `SkillRegistryImpl` must use a three-stage pipeline: (1) embedding similarity top-5, (2) **conditional** LLM re-rank, (3) keyword fallback if both return no match. LLM re-rank fires only when the results are ambiguous: top candidate similarity < `config.skill_embedding_rerank_threshold: float` (default 0.85) OR gap between top-1 and top-2 similarity < 0.1. When the top candidate exceeds the threshold with clear separation, it is returned directly without an LLM call. This preserves the latency benefit of embeddings (sub-10ms) for high-confidence matches while using the LLM (3-10s) only for ambiguous cases.
  *Evidence*: sota.md §3 recommends two-stage retrieval; codebase.md §3 notes LLM router is expensive at scale. The threshold avoids paying LLM latency on every match when embeddings already have a clear winner.

- **AC-18**: Each skill's embedding must be computed from a concatenation of `summary + description + tags`. The embedding index must rebuild automatically when `_rebuild_router()` fires.
  *Evidence*: codebase.md §3 integration points; LoSemB finding that metadata improves retrieval.

- **AC-19**: The embedding dependency (`fastembed` by default, or `sentence-transformers` as alternative) must be an optional install extra: `pip install -e ".[embeddings]"`. When not installed, the system must fall back to existing LLM + keyword matching without error.
  *Evidence*: codebase.md §3 constraint on optional dependency.

- **AC-20**: Embedding retrieval must be gated behind `config.skill_embedding_enabled: bool` (default `False`).

#### Gap 7: Dual-Resolution Screenshots

The codebase already has `_maybe_crop_screenshot()`, `_validate_candidate()` with dual crops, `verify_multiscale_target()`, and `_call_vision_model_with_images()` (codebase.md §7). The gap is that dual-resolution is used only for post-grounding validation, not during grounding itself.

- **AC-21**: The coordinator must expose a new method `find_element_dual(description, detail_b64, context_b64, candidates) -> Optional[FindElementResult]` for dual-resolution grounding. This must NOT modify the existing `find_element()` signature in the `ScreenCoordinator` protocol. The orchestrator must use the `_has_explicit_method()` guard pattern (agent.py:993-1001) to check for `find_element_dual` availability and fall back to single-image `find_element()` when the coordinator does not implement it.
  *Evidence*: codebase.md §7 integration point #1; multi-image infrastructure already exists. codebase.md §Cross-Cutting notes `_has_explicit_method()` is the established pattern for optional coordinator capabilities (used for `reflect_action_outcome`, `verify_multiscale_target`).

- **AC-22**: A new prompt template `find_element_dual.md` must instruct the VLM that Image 1 is a full-page overview and Image 2 is a zoomed detail crop, and to locate the target element in either image.
  *Evidence*: codebase.md §7 integration point #2.

- **AC-23**: `_find_element()` in the orchestrator must send both full + crop when the screenshot width exceeds a configurable threshold (`config.dual_res_threshold: int`, default 1440px) OR when `last_successful_region` is set.
  *Evidence*: codebase.md §7 notes current crop trigger is narrowly scoped.

- **AC-24**: When the VLM returns coordinates from a detail crop, the orchestrator must map them back to full-image space using the existing `crop_offset` handling.
  *Evidence*: codebase.md §7 notes coordinate mapping already exists at lines 1291-1298.

- **AC-25**: Dual-resolution grounding must be gated behind `config.dual_resolution_grounding: bool` (default `False`).

### P2 — Infrastructure

#### Gap 2: Evolving World-State Document

`ContextMonitor` and `DesktopContext` already provide per-step state snapshots and `format_for_planner()` (codebase.md §2). The gap is no cumulative tracking, no state diffs, and no semantic page labels.

- **AC-26**: `DesktopContext` must be extended with: `page_semantic_label: str`, `obstacles: List[str]`, `completed_milestones: List[str]`, and `state_version: int`. Each field has a designated writer and trigger:
  - `page_semantic_label`: Written by `_record_context()` after each successful `activate_app`, `open_url`, or `click` step that changes `window_title` (detected by comparing pre/post `get_state()` results). Value is derived from the new `window_title` + `app_name` (e.g., `"Safari — Amazon.com: Your Orders"`). No VLM call — pure AX/state data.
  - `obstacles`: Appended by `_handle_failure()` when a step fails. Each entry is the `step.error` or `_is_element_absent()` result string. Deduplicated by content.
  - `completed_milestones`: Appended by `_record_context()` after each successful step. Each entry is `step.verify` text from the completed step (e.g., `"Order details page is visible"`). Capped at 20 entries (oldest dropped).
  - `state_version`: Incremented by `update_cheap()` on every call. Starts at 0.
  *Evidence*: codebase.md §2 integration point #1. `_record_context()` (agent.py:268-270) and `_handle_failure()` (agent.py:2024-2089) are the natural write sites since they already fire at the correct lifecycle points.

- **AC-27**: `ContextMonitor` must provide a `format_state_diff()` method that compares current state vs. previous state and returns a structured diff with `changes`, `new_elements`, and `removed_elements`. The diff must cover only high-signal fields: `frontmost_app`, `window_title`, `page_semantic_label`, `interactive_elements` (count change + new/removed element labels only — not positional changes), and `form_fields_filled` (new/changed entries). Excluded from diff: `recently_clicked`, `recently_typed`, `last_vision_description`, `iteration_count`, `state_version` (these are either transient tracking fields or monotonic counters that produce noise, not signal).
  *Evidence*: codebase.md §2 identifies missing state diff; sota.md §2 recommends diff-based state tracking.

- **AC-28**: `format_for_planner()` must include cumulative progress (milestones completed, obstacles encountered) in addition to the current snapshot. The output must remain compatible with the existing `{{desktop_context}}` placeholder.
  *Evidence*: codebase.md §2 notes backward compatibility via existing placeholder.

- **AC-29**: Step outcomes (success/fail, error messages, elements not found) must be persisted in `DesktopContext` via `_record_context()` so the world-state document reflects execution history.
  *Evidence*: codebase.md §2 gap #2 — failed steps and absent elements not persisted.

#### Gap 4: Lookahead/Simulation

- **AC-30**: A new `coordinator.predict_action_outcome(action, params, expected_observation, screenshot_b64) -> dict` method must ask the VLM to predict what the screen will look like after the proposed action, returning `{likely_success: bool, predicted_state: str, risk: str, mismatch_reason: str}`. The `mismatch_reason` field must explain the gap between the predicted state and the `expected_observation` when `likely_success` is `False` (e.g., `"Predicted: dropdown menu appears. Expected: order confirmation page. The button likely opens a menu, not a submission."`). When `likely_success` is `True`, `mismatch_reason` must be an empty string. This field feeds directly into the replan prompt as the analog of `reflect_action_outcome()`'s `hint` field.
  *Evidence*: sota.md §4 recommends 1-step text-based lookahead; codebase.md §4 integration point #1. codebase.md §4 notes `_reflect_failed_action()` passes `observed` and `hint` to replan — lookahead needs analogous fields for the replan path.

- **AC-31**: Lookahead must only activate for steps classified as destructive (using the same `_CRITICAL_ACTION_KEYWORDS` set from Gap 6). Routine navigation and typing steps must skip lookahead. **Interaction with user confirmation (AC-6/AC-7)**: When both lookahead and confirmation are enabled for the same step, `config.lookahead_skip_when_confirmed: bool` (default `True`) controls the behavior:
  - When `True` (default): skip lookahead for destructive steps; rely on user confirmation as the safety gate. This avoids stacking 3-5s lookahead latency on top of user response time.
  - When `False`: run lookahead first. If lookahead predicts failure, skip both dispatch AND confirmation (go to retry/replan). If lookahead predicts success, proceed to confirmation.
  **Latency note**: A destructive step with both gates active costs 3-5s (lookahead) + user response time (5-30s) + 2-5s (verification) = 10-40s. For tasks with multiple destructive steps this is a UX concern, which is why the default skips lookahead when confirmation is active. Lookahead provides its primary value in `"never"` confirmation mode (unattended) where it is the only safety gate.
  *Evidence*: sota.md §4 recommends lookahead for high-risk actions only to manage latency.

- **AC-32**: If lookahead predicts the action will not achieve the expected postcondition, the orchestrator must skip dispatch and proceed to the retry/replan path.
  *Evidence*: codebase.md §4 integration point #3.

- **AC-33**: Lookahead must be gated behind `config.lookahead_enabled: bool` (default `False`).

---

## Out of Scope

- **Full MCTS or tree search** — ProAct and ExACT use MCTS which requires fine-tuned world models and H100 training. Only 1-step text-based lookahead is in scope (sota.md §4).
- **Fine-tuned world model** — MobileDreamer requires ~110K state-transition samples. Out of scope (sota.md §4).
- **Offline app crawling** — ActionEngine's State Machine Graph requires crawling each app offline. Only lightweight diff-based state tracking is in scope (sota.md §2).
- **Segmentation-based SoM** — Full SAM/SEEM segmentation is tested only with GPT-4V. Use accessibility-tree-based SoM only (sota.md §1).
- **Custom embedding model training** — Use off-the-shelf `all-MiniLM-L6-v2`. No fine-tuning (sota.md §3).
- **Role-based approval workflows** — Permit.io-style async approval with multiple roles. Only single-user synchronous confirmation is in scope (sota.md §6).
- **Multi-language destructive keyword detection** — `_CRITICAL_ACTION_KEYWORDS` remains English-only for now (codebase.md §6).
- **CogAgent-style dual encoder architecture** — No model architecture changes. Use existing VLM with two images sent sequentially (sota.md §7).

---

## Success Metrics

| Metric | Baseline | Target | How to Measure |
|--------|----------|--------|----------------|
| **Infeasible task abort time** | Exhausts full iteration budget (60s+) | Abort within 3 failed iterations (<15s) | Run 5 known-impossible tasks, measure time-to-abort |
| **Destructive action safety** | 0% of destructive actions confirmed | 100% of destructive actions trigger confirmation in `always` mode | Run 10 tasks with destructive steps, verify confirmation prompt fires |
| **Grounding accuracy (SoM)** | 75% (molmo-mlx, ScreenSpot-20) | 85%+ with SoM on AX-available elements | Run ScreenSpot benchmark with SoM enabled |
| **Skill match recall** | Keyword matching misses paraphrased queries | Top-5 embedding recall ≥ 90% on a 20-query test set | Create test set with paraphrased skill triggers |
| **Dual-res grounding lift** | Baseline find_element accuracy at 1440px+ | ≥ 5% accuracy improvement on high-DPI test set | Run grounding benchmark at 2x resolution, compare with/without dual-res |
| **World-state planner context** | Flat snapshot, no diff | Planner receives cumulative milestones + diff | Manual inspection of planner prompts across 5 multi-step tasks |
| **Lookahead error prevention** | 0 pre-action predictions | ≥ 50% of destructive action failures caught pre-execution | Run 10 tasks with intentional misgrounding on destructive elements |

---

## Research References

| Gap | SOTA Reference | Codebase Reference |
|-----|---------------|-------------------|
| 1. Set-of-Mark | sota.md §1: SoM (Yang 2023), OmniACT (2024), hybrid approach recommendation | codebase.md §1: `_build_candidate_prefix()`, `get_accessibility_elements()`, integration points |
| 2. World-State | sota.md §2: Web Agents with World Models (Chae 2024), ActionEngine (2026), diff recommendation | codebase.md §2: `ContextMonitor`, `DesktopContext`, `format_for_planner()`, 5 missing pieces |
| 3. Embedding Retrieval | sota.md §3: ToolBench/Gorilla (2023-24), ToolGen (ICLR 2025), LoSemB (2025) | codebase.md §3: `SkillRouter`, `match_skill()`, `_rebuild_router()` hook point |
| 4. Lookahead | sota.md §4: ProAct (2026), MobileDreamer (2026), 1-step text recommendation | codebase.md §4: `_validate_candidate()`, `reflect_action_outcome()`, `expected_observation` field |
| 5. Infeasibility | sota.md §5: GAIA, ImpossibleBench (2025), AGENTRX (2026), frustration-based recommendation | codebase.md §5: `done + abort_reason`, `_is_element_absent()`, `absent_elements` injection |
| 6. User Confirmation | sota.md §6: EU AI Act Art 14, LangGraph interrupt, LITL attack (2025) | codebase.md §6: `_CRITICAL_ACTION_KEYWORDS`, `_wait_for_user()`, confidence gate at 0.9 |
| 7. Dual-Resolution | sota.md §7: CogAgent (CVPR 2024), SeeClick (2024), 25x compute saving | codebase.md §7: `_maybe_crop_screenshot()`, `verify_multiscale_target()`, `_call_vision_model_with_images()` |
