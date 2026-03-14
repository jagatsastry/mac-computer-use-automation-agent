# Survey Gaps — Codebase Analysis

This document maps each of the 7 survey-gaps features to existing code, data flows, integration points, and constraints. No code is written — only analysis.

---

## 1. Set-of-Mark Prompting

**Goal**: Overlay numbered labels on screenshots before sending to VLM, so the model can reference elements by number instead of parsing raw pixels.

### Existing Code

| File | What exists | Relevance |
|------|-------------|-----------|
| `actuator/applescript_actuator.py:186-278` | `get_accessibility_elements()` — JXA script walks AX tree (depth 6, 3s timeout), returns `List[dict]` with keys: `label, role, x, y, width, height, center_x, center_y`. Capped at interactive roles only. | **Primary element source** for SoM overlay. |
| `perception/accessibility.py:481-505` | `AccessibilityBridge.get_interactive_elements()` — walks pyobjc AX tree, returns `List[AXElement]` with `role, title, value, description, position, size, enabled, focused, center`. Filters by `_INTERACTIVE_ROLES` and `enabled + has position`. | **Alternative element source** (pyobjc-based, richer data). |
| `vision/coordinator.py:511-539` | `_build_candidate_prefix()` — already builds a numbered text list of up to 20 accessibility elements as a prompt prefix for `find_element`. Format: `1. "label" (role) at center (cx, cy)`. | **Closest existing implementation** — but text-only, not visual overlay. |
| `vision/coordinator.py:541-638` | `find_element()` — calls `_build_candidate_prefix()` when `candidates` is passed. Prepends text list to the `find_element.md` prompt template. | Integration point where SoM overlay would replace text candidates. |
| `orchestrator/agent.py:1261-1269` | `_find_element()` — calls `actuator.get_accessibility_elements()` and passes as `candidates` kwarg to `coordinator.find_element()`. | **Call site** where elements are gathered pre-grounding. |
| `vision/capture.py` | `ScreenCapture` — captures screenshots as base64 JPEG. No drawing/annotation capability. | Would need a new overlay/annotation function. |

### Data Flow (Current)
```
agent._find_element(desc)
  → actuator.get_accessibility_elements()  → List[dict]
  → coordinator.find_element(desc, screenshot_b64, candidates=elements)
    → _build_candidate_prefix(candidates, desc)  → text prefix
    → prompt = text_prefix + find_element.md template
    → _call_vision_model(prompt, screenshot_b64)  → raw response
    → _parse_coordinates(response)  → (x, y, conf) or None
```

### Integration Points for SoM
1. **New function needed**: `annotate_screenshot(screenshot_b64, elements) → annotated_b64` — draws numbered bounding boxes on the screenshot image using PIL.
2. **Modify** `coordinator.find_element()` — when SoM is enabled, call annotator before sending to VLM.
3. **Modify** `find_element.md` prompt — add instruction: "Elements are numbered on the image. Reference by number."
4. **Modify** `_parse_coordinates()` — add pattern for `FOUND: element_number=N` that maps back to the AX element's center.
5. **Config**: Add `config.som_enabled: bool` flag (default False).

### Constraints
- `_build_candidate_prefix()` already caps at 20 elements — SoM should respect same limit to avoid visual clutter.
- AX elements from JXA have `center_x, center_y` in **screen pixel** space; from pyobjc they have `position` in **logical** space. Coordinate mapping needed for overlay.
- The two element sources (`get_accessibility_elements` JXA vs `get_interactive_elements` pyobjc) return different formats. SoM should accept either.
- SoM labels must be drawn at the correct coordinates in **image space** (screenshot resolution), not screen space. Use `_screen_to_image_coords()` for mapping.

---

## 2. Evolving World-State Document

**Goal**: Maintain a structured, persistent state document across steps that the planner can use for context, replacing per-step screen descriptions.

### Existing Code

| File | What exists | Relevance |
|------|-------------|-----------|
| `actuator/applescript_actuator.py:305-366` | `get_state()` — returns `{app_name, app_bundle, window_title, browser_url, window_x, window_y, window_w, window_h}`. AppleScript-based, ~50ms. | **Primary state source** (cheap, fast). |
| `orchestrator/context_monitor.py` | `ContextMonitor` — wraps `AccessibilityBridge` for cheap AX updates. Maintains `DesktopContext` with `frontmost_app, window_title, interactive_elements, recently_clicked, recently_typed, form_fields_filled, navigation_history, last_vision_description, iteration_count`. | **The world-state document already partially exists here.** |
| `context_monitor.py:54-79` | `update_cheap()` — uses AX API to refresh `frontmost_app`, `window_title`, and `interactive_elements`. ~50ms. | Per-step cheap refresh. |
| `context_monitor.py:108-140` | `format_for_planner()` — serializes context as markdown sections: Desktop State, Interactive elements, Screen description, Form Progress. | **Already formats state for planner.** |
| `orchestrator/agent.py:148-158` | In `execute()`: calls `context_monitor.update_cheap()` before planning, `format_for_planner()` to get `desktop_context`. | Integration point — `desktop_context` is injected into plan prompt. |
| `orchestrator/agent.py:236-238` | Per-step: calls `context_monitor.update_cheap()` before each step. | Per-step state refresh exists. |
| `orchestrator/agent.py:268-270` | After each step: `_record_context(step)` — records click targets, typed text, navigation. | Post-step context recording exists. |
| `planner/prompts/plan_from_prompt.md` | `{{desktop_context}}` placeholder in plan prompt. | Planner already receives desktop context. |
| `planner/prompts/replan_from_state.md` | `{{desktop_context}}` placeholder in replan prompt. Also has `{{history}}` with step results. | Replan already receives state + history. |
| `shared_models.py:224-259` | `StepResult` — captures per-step outcomes: `success, verification_method, evidence, error, screenshot_path, retry_strategies_used, reflection_hint, reflection_observed, suggested_element`. | Rich step outcome data available for state doc. |

### Data Flow (Current)
```
execute(goal):
  context_monitor.update_cheap()  → DesktopContext
  context_monitor.format_for_planner()  → desktop_context string
  planner.plan(goal, desktop_context=desktop_context)  → plan

  for each step:
    context_monitor.update_cheap()  → refresh
    _execute_step(step)
    _record_context(step)  → updates recently_clicked/typed/navigation
```

### What's Missing for Full World-State Document
1. **No cumulative state tracking**: `DesktopContext` refreshes state each time but doesn't track **what has changed since the task started** (e.g., "we've navigated through 3 pages", "form is 60% filled").
2. **No error/obstacle state**: Failed steps and absent elements aren't persisted in `DesktopContext`. The replan prompt gets `{{history}}` separately.
3. **No page-level semantic state**: No tracking of "we're on the checkout page" or "modal dialog is open" — just raw AX data.
4. **No state diff**: The planner gets a flat snapshot, not what changed since last step.
5. **`needs_full_vision()` is crude**: refreshes every 3rd iteration regardless of whether anything changed.

### Integration Points
1. **Extend** `DesktopContext` with: `page_semantic_label: str`, `obstacles: List[str]`, `completed_milestones: List[str]`, `state_version: int`.
2. **Extend** `_record_context()` to track step outcomes (success/fail) and obstacles.
3. **Add** `format_state_diff()` to `ContextMonitor` — compares current state vs previous state and returns only changes.
4. **Modify** `format_for_planner()` — include cumulative progress, not just current snapshot.

### Constraints
- `DesktopContext` is a `@dataclass` — easy to extend.
- `format_for_planner()` output is injected via `{{desktop_context}}` placeholder — backward compatible.
- The planner prompt already has the `{{desktop_context}}` slot.

---

## 3. Embedding-Based Skill Retrieval

**Goal**: Replace keyword matching with semantic embedding similarity for skill lookup, enabling fuzzy matching and scaling to larger skill libraries.

### Existing Code

| File | What exists | Relevance |
|------|-------------|-----------|
| `skills/registry.py:156-253` | `SkillRegistryImpl.match()` — two-path matching: (1) LLM router via `SkillRouter.route()`, (2) keyword fallback via `match_skill()`. | **Primary integration point**. |
| `skills/router.py` | `SkillRouter` — sends all skill cards + user prompt to LLM, parses JSON response with top-k candidates, match_type, confidence. Uses `_build_skills_summary()` to format skill cards. | **Current LLM-based routing** — works but expensive (full LLM call per match). |
| `skills/matcher.py` | `match_skill()` — pure keyword overlap: counts trigger keyword hits, returns skill with highest hit count. | **Keyword fallback** — fast but fragile. |
| `skills/models.py:29-48` | `Skill` — has `name, description, trigger_keywords, parameters, tags, summary, skill_id`. | Fields available for embedding. |
| `skills/models.py:51-65` | `SkillCard` — compact card with `skill_id, title, summary, tags, required_apps, param_names`. | Used by router; would be the embedding target. |
| `shared_models.py:269-329` | `SkillRouteCandidate`, `SkillRouteResult`, `SkillMatchResult` — structured routing results. | Return types — embedding retrieval should produce these same types. |
| `skills/registry.py:100-109` | `_rebuild_router()` — rebuilds `SkillRouter` and cached `SkillCard` list when skills change. | **Hook point** for building/updating embedding index. |
| `protocols.py:166-192` | `SkillRegistry` protocol — `match(prompt) → Optional[SkillMatchResult]`. | Interface contract — embedding retrieval must produce `SkillMatchResult`. |

### Data Flow (Current)
```
registry.match(prompt):
  → router.route(prompt):
    → _build_skills_summary()  → markdown of all skill cards
    → _call_llm(prompt + summary)  → JSON with matches
    → _parse_response()  → SkillRouteResult
  → (fallback) match_skill(prompt, skills)  → keyword overlap
  → SkillMatchResult
```

### Integration Points for Embedding Retrieval
1. **New module**: `skills/embeddings.py` — manages embedding index:
   - `EmbeddingIndex.build(skills: Dict[str, Skill])` — compute embeddings for each skill's `summary + description + tags`.
   - `EmbeddingIndex.query(prompt: str, top_k: int) → List[SkillRouteCandidate]` — cosine similarity search.
2. **Modify** `_rebuild_router()` — also rebuild embedding index.
3. **Modify** `match()` — add embedding as a third path: embedding retrieval → LLM router (for top candidates only) → keyword fallback.
4. **Config**: `config.skill_embedding_model: str` (e.g., `all-MiniLM-L6-v2`), `config.skill_embedding_enabled: bool`.

### Constraints
- **~10 skills currently** (`skills/library/*.md`). Embedding adds overhead that only pays off at scale (>25 skills per router warning at line 103).
- Router `_build_skills_summary()` already concatenates all skill info — at >25 skills this exceeds context. Embedding retrieval would pre-filter to top-k candidates before LLM.
- `MIN_USEFUL_CONFIDENCE = 0.5` threshold in `router.py` — embedding similarity scores need calibration to match this threshold.
- Must maintain backward compatibility: `match()` returns `Optional[SkillMatchResult]` regardless of retrieval method.
- Embedding model dependency (sentence-transformers or similar) would be an optional `pip install -e ".[embeddings]"`.

---

## 4. Lookahead/Simulation

**Goal**: Before executing an action, ask the VLM to predict the outcome. Abort or adjust if the prediction suggests the action won't achieve the intended goal.

### Existing Code

| File | What exists | Relevance |
|------|-------------|-----------|
| `orchestrator/agent.py:398-565` | `_execute_step()` — the per-step execute→verify loop. Actions are dispatched then verified. | **Primary integration point** — lookahead would go between planning and dispatch. |
| `vision/coordinator.py:640-702` | `verify_condition(condition, screenshot_b64) → Optional[bool]` — checks a visual condition (YES/UNCLEAR/NO). | Could be used for "will this action succeed?" prediction. |
| `vision/coordinator.py:722-764` | `reflect_action_outcome()` — asks VLM "what happened after this action?" Returns `{worked, observed, hint}`. | **Post-action reflection** already exists. Lookahead would be the pre-action analog. |
| `vision/coordinator.py:766-807` | `suggest_alternative_affordance()` — finds fallback controls when target is missing. | Related: if lookahead predicts failure, suggest alternative. |
| `orchestrator/agent.py:1685-1736` | `_validate_candidate()` — pre-click validation with detail+context crops. Uses `verify_multiscale_target()` or `verify_condition()`. | **Pre-action validation exists for clicks** — lookahead would generalize this to all actions. |
| `orchestrator/agent.py:1753-1782` | `_reflect_failed_action()` — post-failure reflection. | Current post-hoc approach that lookahead would partially replace. |
| `shared_models.py:82-144` | `ActionStep` — has `action, params, verify, expected_observation, on_fail`. | `expected_observation` is the prediction target. |

### Data Flow (Current — No Lookahead)
```
_execute_step(step):
  _dispatch_action(step)  → actuator_result
  verifier.verify(step, actuator_result)  → StepResult
  (if failed + visible_effect) → _reflect_failed_action()
```

### Integration Points for Lookahead
1. **New method**: `coordinator.predict_action_outcome(action, params, expected_observation, screenshot_b64) → {likely_success: bool, predicted_state: str, risk: str}` — asks VLM to predict what will happen.
2. **New prompt template**: `vision/prompts/predict_outcome.md` — "Given this screenshot and the proposed action, what will the screen look like after?"
3. **Modify** `_execute_step()` — before `_dispatch_action()`, call `predict_action_outcome()`. If prediction says low chance of success, skip dispatch and go to retry/replan.
4. **Config**: `config.lookahead_enabled: bool` (default False — it's expensive, ~3-5s per VLM call).

### Constraints
- **Latency**: Each VLM call takes 3-10s. Adding pre-action prediction doubles per-step latency. Must be optional.
- **Accuracy**: VLM prediction accuracy for "what will happen" is uncertain. False negatives (skipping valid actions) are worse than false positives.
- `_validate_candidate()` already does pre-click validation — lookahead should not duplicate this.
- The `expected_observation` field on `ActionStep` is the natural input for lookahead comparison.
- Reflection (`_reflect_failed_action`) is currently post-hoc — lookahead and reflection are complementary, not replacements.

---

## 5. Infeasibility Detection

**Goal**: Detect when a task is impossible in the current page state and abort gracefully instead of spinning through retries.

### Existing Code

| File | What exists | Relevance |
|------|-------------|-----------|
| `planner/prompts/plan_from_prompt.md:32` | `done` action docs: `Optional: abort_reason (string) — set when the task is impossible in the current page state`. | **LLM already knows about abort_reason.** |
| `planner/prompts/replan_from_state.md:33` | Same `done` + `abort_reason` docs in replan prompt. | LLM can produce abort in replans too. |
| `replan_from_state.md:21-22` | `{{absent_elements}}` placeholder — injected with confirmed-absent elements. | **Absent elements already communicated to replan.** |
| `shared_models.py:90` | `ActionStep.action` valid set includes `"done"`. | `done` is a valid action. |
| `orchestrator/agent.py:420-430` | `_execute_step()` handles `done` + `abort_reason`: returns `StepResult(success=False, error=abort_reason)`. | **Abort handling exists.** |
| `orchestrator/agent.py:325-344` | After execution loop: checks if last step was `done` with `abort_reason`, returns `ExecutionResult(success=False)`. | **Abort result propagation exists.** |
| `orchestrator/agent.py:2024-2089` | `_handle_failure()` — tracks `not_found_count` for AC-4 element absence detection. When count ≥ 2, calls `_is_element_absent()`. Marks `result.error = "Element absent: ..."`. | **Element absence tracking exists.** |
| `orchestrator/agent.py:1986-2022` | `_is_element_absent()` — uses AX tree to structurally confirm absence. Falls back to True when AX is unavailable. | **Structural absence confirmation exists.** |
| `orchestrator/agent.py:2266-2272` | In `_replan_and_continue()`: collects `absent_elements` from step results with `"Element absent:"` prefix, passes to `planner.replan()`. | **Absent elements already passed to replan.** |
| `planner/planner.py:361-372` | `_build_replan_prompt()` — injects `absent_text` with guidance: "Consider that the task may be impossible... emit done with abort_reason." | **Infeasibility guidance already in replan prompt.** |

### What's Missing (Gap Analysis)
The pieces exist but are **loosely wired**:

1. **No proactive infeasibility check**: The agent only detects absence after N retries fail. It doesn't proactively ask "is this task achievable on this page?" before attempting.
2. **No structured infeasibility signal**: Absence detection produces a text error. There's no `InfeasibilityReason` dataclass or structured signal.
3. **No budget-aware abort**: The agent relies on `max_iterations` (a hard limit) rather than detecting diminishing returns (same actions keep failing).
4. **No user notification**: When abort happens, it's buried in the `ExecutionResult.error` field. No explicit "task impossible" UX.

### Integration Points
1. **Proactive check**: After replan, if the new plan has `done + abort_reason`, the orchestrator should recognize this as infeasibility and short-circuit.
2. **Structured signal**: Add `infeasibility_reason: Optional[str]` to `ExecutionResult`.
3. **Diminishing returns detector**: Track retry patterns — if same element fails N times across plans, trigger abort.
4. **Early abort in `_handle_failure()`**: If `_is_element_absent()` returns True for a critical-path element, don't wait for max retries.

### Constraints
- The LLM planner *can* produce `done + abort_reason` today — the gap is that it often doesn't (LLMs are optimistic).
- `_is_element_absent()` uses AX tree which may not be available on all machines (falls back to True).
- `absent_elements` list is already passed to replan — the infrastructure is there.
- Care needed to avoid false infeasibility (page still loading, element behind scroll).

---

## 6. User Confirmation for Destructive Actions

**Goal**: Before executing actions that have irreversible consequences (submit order, delete item, pay, send message), pause and ask the user for confirmation.

### Existing Code

| File | What exists | Relevance |
|------|-------------|-----------|
| `orchestrator/agent.py:36-40` | `_CRITICAL_ACTION_KEYWORDS = frozenset({"submit", "pay", "confirm", "reserve", "delete", "remove", "send"})` — already defined for confidence gating. | **Keyword set for destructive actions exists.** |
| `orchestrator/agent.py:37` | `_CRITICAL_CONFIDENCE_THRESHOLD = 0.9` — higher confidence required for critical actions. | Confidence gating already uses this. |
| `orchestrator/agent.py:1454-1463` | `_get_confidence_threshold(step)` — returns 0.9 if `step.verify` contains any critical keyword, else 0.5. | **Logic to detect critical steps exists.** |
| `orchestrator/agent.py:1080-1093` | In `_dispatch_action()` for click: confidence gate checks `location.confidence < threshold`. Rejects low-confidence clicks on critical elements. | **Pre-click confidence gate exists** — but it blocks, doesn't ask user. |
| `orchestrator/agent.py:1361-1440` | `_wait_for_user(step)` — polls for screen changes, prints message, returns after screen changes or timeout. | **Waiting mechanism exists** — but only triggered by `wait_for_user` action in plan. |
| `shared_models.py:90` | `ActionStep.action` valid set includes `"wait_for_user"`. Params: `message`, `condition`. | `wait_for_user` action exists. |
| `shared_models.py:94` | `ActionStep.on_fail` can be `"wait_for_user"`. | Failure can also defer to user. |

### Data Flow (Current)
```
_dispatch_action(click step):
  if "element" in params:
    location = _find_element(...)
    threshold = _get_confidence_threshold(step)  # 0.9 for critical
    if confidence < threshold:
      return {success: False, error: "low_confidence:..."}  # BLOCKS, no user ask
```

### Integration Points for User Confirmation
1. **New method**: `_should_confirm_with_user(step) → bool` — checks if step targets a destructive action based on:
   - `_CRITICAL_ACTION_KEYWORDS` in `step.verify` or `step.params.get("element", "")`
   - `on_fail == "wait_for_user"` (explicit signal from planner)
   - Config flag `config.confirm_destructive: bool`
2. **Modify** `_execute_step()` — before `_dispatch_action()`, check `_should_confirm_with_user()`. If True, print confirmation prompt and call `_wait_for_user()` (or a simpler blocking input).
3. **New action parameter**: `ActionStep.destructive: bool` — optional flag the planner can set.
4. **Modify planner prompt** — instruct: "For destructive actions (submit, pay, delete, etc.), set `destructive: true`."

### Constraints
- `_wait_for_user()` uses screen-change polling — not ideal for user confirmation (user might just press Enter).
- Need a simpler confirmation mechanism: `input("Press Enter to confirm...")` or similar.
- The `_CRITICAL_ACTION_KEYWORDS` set is English-only — may need expansion.
- Must not break `--dry-run` mode — confirmation should be skipped in dry runs.
- The planner may not reliably set `destructive: true` — need runtime detection as fallback.
- The existing confidence gate at 0.9 already protects against uncertain clicks on critical elements — user confirmation is a complementary layer for **intended** destructive actions (high confidence but irreversible).

---

## 7. Dual-Resolution Screenshots

**Goal**: Send both a full-page overview and a detail crop to the VLM during grounding, improving accuracy for small elements on large screens.

### Existing Code

| File | What exists | Relevance |
|------|-------------|-----------|
| `orchestrator/agent.py:1917-1967` | `_maybe_crop_screenshot()` — crops 512x512 around `last_successful_region` when image width > 1440px. Returns `(cropped_b64, (offset_x, offset_y))`. | **Region-based cropping exists** — but only for find_element, only after a prior successful click. |
| `orchestrator/agent.py:1738-1751` | `_crop_square_b64(img, center_x, center_y, size)` — crops square region, returns base64 JPEG. | **Generic crop utility exists.** |
| `orchestrator/agent.py:1685-1736` | `_validate_candidate()` — pre-click validation uses **two crops**: detail (128x128) + context (512x512). Calls `verify_multiscale_target()` if available, else `verify_condition()` on detail crop. | **Dual-resolution already exists for validation!** |
| `vision/coordinator.py:704-720` | `verify_multiscale_target(target_description, detail_b64, context_b64) → bool` — sends two images to VLM with a dual-image prompt. | **Multi-image VLM call exists.** |
| `vision/coordinator.py:215-227` | `_call_vision_model_with_images(prompt, screenshots_b64: Sequence[str])` — sends multiple images to VLM. Works with all backends (local, Anthropic, Gemini). | **Multi-image infrastructure exists.** |
| `orchestrator/verifier.py:461-468` | `_crop_click_region()` — crops 400x400 (half_size=200) around click point for local verification in Tier 2. | **Post-action crop exists for verification.** |
| `orchestrator/agent.py:1224-1310` | `_find_element()` — captures screenshot, optionally crops via `_maybe_crop_screenshot()`, calls `coordinator.find_element()`. | **Integration point for dual-res grounding.** |
| `orchestrator/agent.py:1271-1279` | In `_find_element()`: `_maybe_crop_screenshot()` only fires when `last_successful_region` is set AND image width > 1440. | **Current crop is narrowly scoped.** |
| `vision/coordinator.py:541-638` | `find_element()` — accepts `screenshot_b64` and `candidates`. Currently uses a single image. | **Would need modification** to accept/use dual images. |

### Data Flow (Current)
```
_find_element(description):
  screenshot_b64 = capture_screenshot()
  (maybe) cropped_b64, offset = _maybe_crop_screenshot(screenshot_b64)
    # Only fires if last_successful_region set AND width > 1440
  coordinator.find_element(description, screenshot_b64=cropped_or_full)
    # Single image sent to VLM
```

### Integration Points for Dual-Resolution
1. **Modify** `coordinator.find_element()` — accept optional `context_b64` (full overview) alongside `screenshot_b64` (detail crop). When both provided, use `_call_vision_model_with_images()`.
2. **New prompt template**: `vision/prompts/find_element_dual.md` — "Image 1 is a full overview of the screen. Image 2 is a zoomed detail of a region. Find the element in either image."
3. **Modify** `_find_element()` in agent — always send full screenshot as context, optionally add a detail crop when:
   - `last_successful_region` is set (current behavior)
   - OR screenshot resolution > threshold (broaden the trigger)
4. **Modify** `_maybe_crop_screenshot()` — instead of replacing the screenshot, return both full + crop.
5. **Config**: `config.dual_resolution_grounding: bool` (default False — adds latency from extra image).

### Constraints
- `_call_vision_model_with_images()` already handles multi-image for all backends — infrastructure ready.
- `verify_multiscale_target()` proves the dual-image pattern works — detail (128) + context (512).
- The `_maybe_crop_screenshot()` threshold of 1440px means dual-res is a no-op at default 1024px resolution. May need to lower or remove threshold.
- Adding a second image doubles the VLM input token cost. For Anthropic, each image is ~1600 tokens.
- `find_element.md` prompt template expects a single image — needs a dual-image variant.
- Coordinate mapping: when element is found in the detail crop, coordinates must be mapped back to full-image space. `crop_offset` handling already exists in `_find_element()` (lines 1291-1298).

---

## Cross-Cutting Concerns

### Test Infrastructure

| Area | Test files | Notes |
|------|-----------|-------|
| Orchestrator | `test_orchestrator_new.py`, `test_vision_arch_improvements.py`, `test_replan_failure_handling.py`, `test_element_absence.py`, `test_verification_bypass.py` | All use `_make_config(model_provider="local")` to avoid `.env` contamination. Mock `coordinator`, `actuator`, `planner`. |
| Verifier | `test_verifier.py` | Tests all 3 tiers. Mocks coordinator and actuator. |
| Coordinator | `test_coordinator.py`, `test_grounding_model.py`, `test_grounding_adversarial.py` | Tests coordinate parsing, conversion, model validation. |
| Skills | `test_skill_registry.py`, `test_router_v2.py`, `test_skill_learning.py`, `test_skill_librarian.py` | Tests matching, routing, expansion, distillation. |
| Planner | `test_planner.py`, `test_enhanced_planning.py`, `test_enhanced_planning_adversarial.py` | Tests prompt building and response parsing. |

**Key testing pattern**: All unit tests mock external dependencies (VLM, actuator, AX API). Tests use `pytest.mark.unit`. The `_make_config()` pattern with `model_provider="local"` is critical — without it, `.env` leaks `AGENT_MODEL_PROVIDER=anthropic` into pydantic-settings.

### Configuration System
- `config.py` uses Pydantic Settings with `AGENT_` prefix.
- New features should add config fields with sensible defaults (disabled).
- Feature flags should be `bool` with default `False` for backward compatibility.

### Protocol Pattern
- All components implement `@runtime_checkable Protocol` classes from `protocols.py`.
- New methods added to existing components should be added to the protocol too (or use `_has_explicit_method()` pattern for optional capabilities).
- The `_has_explicit_method()` guard (agent.py:993-1001) is used for optional coordinator methods like `reflect_action_outcome` and `verify_multiscale_target` — new optional methods should follow this pattern.

### Coordinate Space Awareness
- Multiple coordinate spaces exist: screen logical, image/screenshot, model-specific (0-100, 0-1000, pixel).
- `_screen_to_image_coords()` and `_image_to_screen_coords()` handle mappings.
- New features that touch coordinates (SoM overlay, dual-res crops) must be explicit about which space they operate in.

---

## Summary Table

| Gap | Closest Existing Code | Missing Piece | Integration Difficulty |
|-----|----------------------|---------------|----------------------|
| 1. Set-of-Mark | `_build_candidate_prefix()` (text-only) | Visual overlay drawing, number-based parsing | Medium — new annotator + prompt changes |
| 2. World-State Doc | `ContextMonitor` + `DesktopContext` | Cumulative tracking, state diffs, semantic labels | Low — extend existing `DesktopContext` |
| 3. Embedding Retrieval | `SkillRouter` (LLM-based) | Embedding model, vector index, similarity search | Medium — new module, optional dependency |
| 4. Lookahead/Simulation | `_validate_candidate()` (click-only), `reflect_action_outcome()` (post-hoc) | Pre-action prediction for all action types | High — adds latency, VLM accuracy uncertain |
| 5. Infeasibility Detection | `_is_element_absent()`, `done + abort_reason` | Proactive detection, structured signal, diminishing returns | Low — wiring existing pieces together |
| 6. User Confirmation | `_CRITICAL_ACTION_KEYWORDS`, `_wait_for_user()` | Confirmation prompt, runtime destructive-action detection | Low — simple integration |
| 7. Dual-Resolution | `_validate_candidate()` (dual-crop), `_call_vision_model_with_images()` | Dual-image grounding prompt, broader trigger | Medium — prompt + config changes |
