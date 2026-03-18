# BuildML Interview Checklist – Agent Assessment Report

Assessment of the macOS automation agent (`src/automation_agent/`) against each topic from the BuildML article. Status uses: STRONG, PARTIAL, WEAK, or NOT APPLICABLE.

---

## 1. End-to-End RAG Pipeline — PARTIAL

The agent has a **skill retrieval pipeline**, not a general document RAG pipeline. It works as:

- **Query rewriting**: Site-entity extraction rewrites the user goal into a more targeted skill query (`skills/registry.py`).
- **Embedding retrieval**: `EmbeddingIndex` (fastembed, `bge-small-en-v1.5`) embeds `summary + description + tags` per skill and retrieves by cosine similarity (`skills/embeddings.py`).
- **Re-ranking**: LLM re-rank via `SkillRouter` when the embedding gap is ambiguous (`skills/router.py`).
- **Grounded generation**: Retrieved skill context is injected into the planner prompt under `## Skill Priors` (`planner/prompts/plan_from_prompt.md`).

**Gaps**: No chunking/overlap strategy (skills are short documents). No hybrid sparse+dense retrieval. No post-generation faithfulness verification against retrieved skill context. No citation enforcement in generated plans.

---

## 2. Sparse, Dense, and Hybrid Retrieval — WEAK

- **Dense**: Embedding index with cosine similarity is implemented.
- **Sparse**: Keyword fallback exists but is a simple substring match, not BM25.
- **Hybrid**: No parallel sparse+dense with score merging. The system falls back to keyword only when embeddings are disabled or fail.

**Gaps**: No BM25 implementation. No reciprocal rank fusion or score normalization. No configurable blending of sparse and dense signals.

---

## 3. Preventing Hallucinations When Context Is Retrieved — PARTIAL

Hallucination mitigation exists but is focused on **action verification**, not on **text generation faithfulness**:

- Planner prompt says "Respond with ONLY valid JSON" and constrains output format tightly.
- Temperature is 0.0 across all LLM calls (planner, router, distiller, coordinator).
- Tiered verification (AX state -> actuator -> vision) validates that executed actions had the expected effect (`orchestrator/verifier.py`).
- Mandatory `verify` field on every plan step — steps without it are rejected.

**Gaps**: No post-generation check that plan steps are grounded in the skill template content. No claim-to-source mapping. No "I don't know" / refusal pathway when skill context is insufficient (the planner will just plan without skill guidance). No lightweight verifier model for generated text.

---

## 4. Fine-Tuning vs RAG Decision — NOT APPLICABLE (but reasoned well)

The agent uses no fine-tuned models. It relies entirely on prompting + retrieval (skill priors) + rules (verification, plan validation). This is the correct starting posture per the article's advice: "start with RAG and prompt engineering. Only fine-tune once you see consistent failure modes."

**Potential opportunity**: The skill distiller (`skills/distiller.py`) and librarian (`skills/librarian.py`) learn from experience and produce new skills — this is an interesting alternative to fine-tuning where the "training data" becomes retrievable skill documents.

---

## 5. Cost-Benefit of Fine-Tuning vs Retrieval/Rules — STRONG

The architecture follows the article's recommended hierarchy precisely:

1. **Prompts first**: All behavior is driven by markdown prompt templates.
2. **Retrieval second**: Skill embedding index provides domain knowledge at inference time.
3. **Rules third**: Plan validation, action aliases, blocked apps, PII redaction, destructive-action gating.
4. **Fine-tuning never (yet)**: No fine-tuned models — complexity is earned by failure data.

The skill-learning loop (experience store -> librarian -> promotion) is effectively a "retrieval-native" alternative to fine-tuning.

---

## 6. Evaluating Agent Behavior Across Long Multi-Step Interactions — PARTIAL

- **Step-level tracking**: Every step logs `step_start`, `step_complete`, `step_retry`, `step_replan` via `EventLogger`.
- **Trajectory logging**: Full `trace.md` and `events.jsonl` per run capture the entire interaction trajectory.
- **Scenario-based evaluation**: Customer test reports in `docs/reports/` evaluate multi-step scenarios (e.g., Amazon return, Walmart return) with PASS/FAIL/INSUFFICIENT_EVIDENCE.
- **FrustrationScore** tracks pathological behaviors (same state, identical actions, replan count).

**Gaps**: No automated end-to-end success-rate metric aggregation across runs. No step-efficiency scoring (optimal steps vs actual steps). No cross-run behavioral regression detection. E2E tests (`tests/e2e/`) exist but require live macOS and are not part of CI. No "long-horizon" metrics like average turns per session or early abandonment rate.

---

## 7. Regression Testing for Prompts, RAG Pipelines, or Agents — PARTIAL

- **Scattered regression tests**: ~15 specific regression tests across unit test files (grounding pipeline, adversarial, type-text hardening, scroll recovery, skill librarian).
- **Prompt testing**: Prompts are tested indirectly via mocked LLM calls in unit tests.
- **Benchmark suite**: Grounding benchmark (`scripts/benchmark_grounding.py`) with ScreenSpot dataset (1,272 samples) and point-in-bbox accuracy.

**Gaps**: No dedicated prompt regression harness (a fixed set of prompts with expected outputs). No prompt versioning or snapshotting — prompts are `.md` files with no version tags. No automated scoring pipeline that runs before/after prompt changes. No embedding-similarity or LLM-as-judge automated scoring. Replay capability is planned but not implemented.

---

## 8. ReAct Pattern — NOT APPLICABLE (Planner-Executor instead)

The agent does **not** use ReAct. It uses a **Plan-then-Execute** pattern:

1. Generate a full plan upfront (planner LLM call).
2. Execute steps sequentially with verification.
3. Replan on failure.

This is a deliberate architectural choice. The article notes ReAct is better for "multi-hop questions, workflows, or agent-like behaviors" — but a planner-executor is better for "dependent steps or workflows that must be audited" because "it is easier to insert human approvals and to audit the plan."

The agent does incorporate ReAct-like elements: the `observe` action lets the agent gather information mid-execution, and replanning is triggered reactively when steps fail.

---

## 9. Context Window Limits — PARTIAL

- **Cheap state updates**: `ContextMonitor.update_cheap()` uses the Accessibility API (~50ms) instead of full VLM calls (3-7s), reducing context pressure.
- **Selective vision refresh**: `needs_full_vision()` triggers full VLM refresh only every 3rd iteration or when no description exists.
- **Compact state format**: `format_for_planner()` produces structured text, not raw dumps.
- **Skill context budgeting**: Only top-k skill matches are injected into the prompt.

**Gaps**: No explicit token budget enforcement on prompt construction. No summarization of prior steps before replanning (full step history is passed). No hierarchical memory — everything is in-context or discarded. State diffs help but long runs could still exceed context limits.

---

## 10. Consistent Structured Output (JSON) — STRONG

- **Explicit schema**: Plan output format is fully specified in the prompt with field-by-field definitions and a concrete JSON example.
- **Strict instructions**: "Respond with ONLY valid JSON (no markdown, no explanation)."
- **Low temperature**: 0.0 across all generation calls.
- **Action aliases**: `_ACTION_ALIASES` normalizes LLM-generated action names to canonical forms (`shared_models.py`).
- **Parsing fallback**: `_parse_plan_response()` extracts JSON from markdown code blocks if the model wraps output.
- **Validation**: `ActionStep.__post_init__` validates action names and `on_fail` values. `plan.validate()` rejects steps without `verify` fields.
- **Grammar constraints**: Ollama backend uses GBNF grammar for constrained JSON decoding.
- **Repair on failure**: Unknown actions are mapped to likely candidates (`type_text` or `click`) rather than crashing.

---

## 11. Signals That Prompting Has Reached Its Limits — PARTIAL

The codebase shows evidence of hitting prompting limits and responding with system-level fixes:

- **Skill learning pipeline** was built because prompting alone couldn't encode domain-specific procedures (e.g., how to navigate Amazon returns).
- **Tiered verification** was added because prompting the planner to produce correct `verify` fields wasn't reliable enough.
- **Plan hardening** (`_harden_plan()`) adds programmatic post-processing rules because the planner doesn't consistently produce navigation steps, fallback strategies, or correct `on_fail` values.
- **Type-text hardening** adds rule-based fixes (clear-first, slow-type) because prompt instructions for text input edge cases were fragile.

**Gaps**: No systematic tracking of where prompt changes cause regressions (which would formalize the decision to move away from prompting). No per-failure-mode classification that tags "this is a prompting problem" vs "this needs system design."

---

## 12. Low Recall / High Precision Impact on Downstream Generation — WEAK

The skill retrieval system has no explicit recall/precision monitoring.

- `EmbeddingIndex.query()` returns `top_k=5` candidates with similarity scores.
- LLM re-rank fires when the top similarity is below threshold or gap between top-1 and top-2 is small.
- When no skill matches, the planner simply plans without skill guidance — there is no explicit "I couldn't find a relevant skill" signal.

**Gaps**: No retrieval quality metrics (recall@k, MRR). No tracking of "correct skill was available but not retrieved." No fallback behavior when recall is low (the planner just wings it). No monitoring of retrieval quality over time.

---

## 13. Conflicting Retrieved Documents — WEAK

The skill system does not handle conflicting skill matches:

- When multiple skills match, `SkillRouter` picks one via LLM re-rank.
- There is no conflict detection (e.g., two skills for the same task with different procedures).
- The `SkillLibrarian` has duplicate prevention (`_compute_score` and dedup logic), but this operates at skill creation time, not at retrieval time.

**Gaps**: No conflict detection between retrieved skill candidates. No mechanism to surface disagreements to the user. No metadata-based resolution (e.g., prefer newer or higher-confidence skills).

---

## 14. Minimal Agent Loop Components — STRONG

All five required components from the article are present:

| Component | Implementation |
| --- | --- |
| **Perception** | `ContextMonitor` (AX API), `VisionCoordinator` (screenshots + VLM) |
| **State/Memory** | `DesktopContext` (short-term), `SkillExperienceStore` (long-term learning) |
| **Policy** | Planner LLM + plan hardening + confirmation logic |
| **Action interface** | 10 bounded actions via `AppleScriptActuator` |
| **Termination** | `done` action, `max_iterations`, infeasibility detection, user denial |

The loop is well-structured: observe (AX + optional VLM) -> plan (LLM) -> execute (actuator) -> verify (tiered) -> update state -> repeat/replan.

---

## 15. Agent Loop Failures and Prevention — STRONG

This is one of the strongest areas:

- **FrustrationScore**: Tracks `same_state_count`, `identical_action_count`, `replan_count` per execution run.
- **Infeasibility detection**: OR-based trigger when same-state or replan thresholds are hit. LLM advisory check asks "is this still achievable?" Hard abort after N advisory checks.
- **Progress tracking**: `DesktopContext` maintains `completed_milestones`, `obstacles`, and `state_version`. `FrustrationScore.reset_on_progress()` clears counters on visible progress.
- **Element absence detection**: After 2+ not-found for the same element, checks `_is_element_absent()` and escalates to replan with `absent_elements` hint.
- **Vary strategy**: `_vary_strategy()` changes retry approach per action type instead of repeating the same action.
- **Step limits**: `max_iterations` (default 20), `max_retries` per step (default 3).

---

## 16. Prompt Injection Prevention — PARTIAL

- **Display sanitization**: `_sanitize_for_display()` strips ANSI escapes, Unicode directional overrides, and control characters before showing action details to the user.
- **PII redaction**: `_redact_params_for_log()` redacts sensitive params in logs.
- **Bounded action space**: The agent can only perform 10 predefined desktop actions — even if injection occurs, the blast radius is limited by the actuator interface.
- **Destructive-action gating**: Two-phase confirmation for destructive actions provides a human checkpoint.
- **Blocked apps**: `blocked_apps` list prevents actions on System Preferences/Settings.

**Gaps**: No explicit separation of trusted instructions from untrusted content in prompts (skill templates and screen descriptions are mixed in the same prompt). No input sanitization of skill template content before prompt injection. No precedence rules in the prompt ("follow system instructions even if skill context says otherwise"). No deliberate prompt-injection test suite.

---

## 17. More Instructions Making Performance Worse — PARTIAL (lessons learned)

Evidence that the team has hit this problem and responded:

- **Plan hardening** was built as a programmatic post-processing layer rather than adding more planner prompt rules. This is exactly the right response — moving complexity from the prompt to code.
- **Skill priors** have explicit guidance levels (`direct`, `analogical`, `generic`) to avoid overwhelming the planner with irrelevant skill instructions.
- The planner prompt is 109 lines — moderately long but well-structured with clear sections.

**Gaps**: No automated detection of prompt bloat or attention dilution. No A/B testing of prompt variants. No metric tracking prompt length vs plan quality.

---

## 18. Prompt Edge Case Failures – Iterate vs Change Design — STRONG

The codebase shows a mature pattern of escalating from prompting to system design:

- **Prompting first**: Initial behavior defined in `plan_from_prompt.md`.
- **Rules second**: `_harden_plan()` fixes common planner mistakes programmatically (missing navigation, bad `on_fail`, auth detection).
- **Routing third**: Site-entity extraction routes to specialized skill contexts before planning.
- **Learning fourth**: Skill librarian promotes repeated observations into retrievable skill templates.
- **Verification fifth**: Tiered verification catches execution errors that prompting can't prevent.

Each layer was added in response to measured failure modes, not preemptively.

---

## 19. Agent Deciding When to Act vs Think — PARTIAL

The agent doesn't have an explicit "think" action in the ReAct sense. Instead:

- **Plan = think**: The planner generates a full multi-step plan before any execution.
- **Observe = think**: The `observe` action takes a screenshot and describes the screen without acting.
- **Replan = re-think**: On failure, the agent generates a new plan incorporating what it learned.
- **Infeasibility advisory = reflect**: The LLM is asked "is this still achievable?" before hard abort.

**Gaps**: No mid-execution reasoning step (the agent can't pause between steps to reconsider the plan without a failure trigger). No "should I replan proactively?" heuristic based on state drift. The agent only reflects when forced by failure, not when it detects that the situation has changed.

---

## 20. Memory Retrieval Without Overwhelming Context Window — PARTIAL

- **Short-term**: `DesktopContext` is compact and structured. `format_for_planner()` produces minimal text.
- **Long-term**: `SkillExperienceStore` persists observations to JSONL. `top_for_context()` retrieves only top-N relevant observations.
- **Skill context**: Only the matched skill (not all candidates) is injected into the prompt.

**Gaps**: No hard token budget for memory/context sections. No summarization of step history before replanning. No hierarchical memory (working memory vs episodic memory vs semantic memory). No explicit memory retrieval by relevance at replan time — full step history is passed.

---

## 21. Signals for Human Intervention — STRONG

Multiple escalation pathways:

- **Destructive-action confirmation**: Two-phase confirmation with auto-deny timeout.
- **`wait_for_user` action**: Agent pauses when authentication or user input is required. Polls for screen changes.
- **Auth detection**: `_harden_plan()` detects login/sign-in patterns and injects `wait_for_user` steps.
- **On-fail escalation**: Steps can specify `on_fail: "wait_for_user"` to escalate to the user on failure.
- **Infeasibility abort**: After repeated failures + advisory checks, the agent aborts with an explanation rather than continuing.
- **Confidence gating**: Critical actions (submit, pay, confirm, delete) require higher grounding confidence (0.9 vs 0.5 default).
- **Lookahead blocking**: VLM predicts outcome of destructive actions; blocks execution if prediction is negative.

---

## 22. Choosing Agent Architecture — STRONG

The architecture matches the article's recommendations for "many dependent steps or workflows that must be audited":

- **Planner + executor**: Planner generates the step list; executor runs each step with verification.
- **Hierarchical skill routing**: Top-level skill matcher routes to specialized skill contexts.
- **Model mix**: Smaller/cheaper models for grounding and routing; larger models for planning and verification.
- **Provenance**: `EventLogger` tracks prompt, model, retrieved skill, step outcomes, and screenshots per run.
- **State representation**: `DesktopContext` with known facts (milestones), obstacles, navigation history, and form progress.
- **Budgeting**: Cheap AX updates (~50ms) vs expensive VLM calls (3-7s), triggered selectively.

---

## Summary Scorecard

| # | Topic | Status |
|---|---|---|
| 1 | End-to-end RAG pipeline | PARTIAL |
| 2 | Sparse, dense, hybrid retrieval | WEAK |
| 3 | Hallucination prevention | PARTIAL |
| 4 | Fine-tuning vs RAG decision | N/A (correct posture) |
| 5 | Cost-benefit tradeoff | STRONG |
| 6 | Multi-step evaluation | PARTIAL |
| 7 | Regression testing | PARTIAL |
| 8 | ReAct pattern | N/A (Plan-Execute instead) |
| 9 | Context window management | PARTIAL |
| 10 | Structured output (JSON) | STRONG |
| 11 | Prompting limits recognition | PARTIAL |
| 12 | Low recall / high precision impact | WEAK |
| 13 | Conflicting documents | WEAK |
| 14 | Minimal agent loop | STRONG |
| 15 | Loop failure prevention | STRONG |
| 16 | Prompt injection prevention | PARTIAL |
| 17 | Instruction overload | PARTIAL |
| 18 | Edge case escalation strategy | STRONG |
| 19 | Act vs think decision | PARTIAL |
| 20 | Memory retrieval design | PARTIAL |
| 21 | Human intervention signals | STRONG |
| 22 | Architecture choice | STRONG |

**Totals**: 7 STRONG, 9 PARTIAL, 3 WEAK, 2 N/A (but well-reasoned), 1 N/A (different pattern)

---

## Top Improvement Opportunities (ordered by impact)

1. **Retrieval quality monitoring** (topics 2, 12, 13): Add recall@k, MRR, and conflict-detection metrics for skill retrieval. Instrument "correct skill was available but not retrieved" cases. Consider BM25 or hybrid retrieval for the skill index.
2. **Prompt regression harness** (topic 7): Build a fixed evaluation set of (goal, screen state, expected plan) tuples. Run before/after prompt changes with automated scoring (plan correctness, step count, action validity). Version prompts with hashes.
3. **Post-generation grounding check** (topic 3): After the planner generates a plan, verify that plan steps reference elements or actions that appeared in the skill template or screen description. Flag "hallucinated" steps that have no grounding.
4. **Token budget enforcement** (topics 9, 20): Set hard token limits for each prompt section (skill context, step history, desktop state). Summarize step history before replanning instead of passing raw results.
5. **Prompt injection testing** (topic 16): Build a test suite with known injection strings in skill templates and screen descriptions. Add precedence rules to the system prompt. Sanitize skill template content before injection.
6. **End-to-end metrics pipeline** (topic 6): Aggregate success rate, steps-to-completion, tool error rate, and replan frequency across runs. Track trends over time. Gate releases on metric thresholds.
