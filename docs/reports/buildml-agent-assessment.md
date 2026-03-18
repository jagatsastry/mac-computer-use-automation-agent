# BuildML Agent Assessment Report

**Project**: macOS Desktop Automation Agent (`src/automation_agent/`)
**Basis**: "What Interviewers Ask After You Say 'I Built an AI Agent'" (BuildML)
**Scope**: 22 practical LLM/agent engineering topics assessed against current implementation

---

## Executive Summary

This agent is a grounded desktop automation system built on a **plan → execute → verify → replan** loop with skill retrieval, adaptive memory, and multimodal verification. It is not a classic document-RAG system — it is closer to a modular planner-executor with specialist subsystems for grounding, verification, skill routing, and learning.

### Scorecard

| # | Topic | Rating |
|---|-------|--------|
| 1 | End-to-end RAG pipeline | PARTIAL |
| 2 | Sparse, dense, hybrid retrieval | WEAK |
| 3 | Hallucination prevention | PARTIAL |
| 4 | Fine-tuning vs RAG decision | N/A (correct posture) |
| 5 | Cost-benefit tradeoff | **STRONG** |
| 6 | Multi-step evaluation | PARTIAL |
| 7 | Regression testing | PARTIAL |
| 8 | ReAct pattern | N/A (Plan-Execute) |
| 9 | Context window management | PARTIAL |
| 10 | Structured output (JSON) | **STRONG** |
| 11 | Prompting limits recognition | PARTIAL |
| 12 | Low recall / high precision impact | WEAK |
| 13 | Conflicting documents | WEAK |
| 14 | Minimal agent loop | **STRONG** |
| 15 | Loop failure prevention | **STRONG** |
| 16 | Prompt injection prevention | PARTIAL |
| 17 | Instruction overload | PARTIAL |
| 18 | Edge case escalation strategy | **STRONG** |
| 19 | Act vs think decision | PARTIAL |
| 20 | Memory retrieval design | PARTIAL |
| 21 | Human intervention signals | **STRONG** |
| 22 | Architecture choice | **STRONG** |

**Totals**: 7 STRONG, 9 PARTIAL, 3 WEAK, 3 N/A (well-reasoned)

### Narrative

The strongest areas are execution grounding, postcondition verification, agent loop structure, loop-failure prevention, human intervention signals, architecture decomposition, and the cost-benefit discipline of preferring code constraints over prompt bloat. The weakest areas are retrieval quality monitoring, conflict handling across retrieved evidence, and recall measurement. The biggest improvement opportunities cut across long-horizon evaluation, prompt-injection hardening, broader escalation policies, richer memory/provenance, and retrieval quality instrumentation.

---

## Detailed Assessment by Topic

### 1. End-to-End RAG Pipeline — PARTIAL

The agent has a **skill retrieval pipeline**, not a general document RAG pipeline.

**What exists:**

| Stage | Implementation | Location |
|-------|---------------|----------|
| Query rewriting | Site-entity extraction rewrites user goal into a targeted skill query | `skills/registry.py` |
| Embedding retrieval | `EmbeddingIndex` (fastembed, `bge-small-en-v1.5`) embeds `summary + description + tags` per skill; cosine similarity ranking | `skills/embeddings.py` |
| Re-ranking | LLM re-rank via `SkillRouter` when embedding gap is ambiguous | `skills/router.py` |
| Grounded generation | Retrieved skill context injected into planner prompt under `## Skill Priors` | `planner/prompts/plan_from_prompt.md` |
| Post-action verification | 3-tier verification validates executed actions had expected effect | `orchestrator/verifier.py` |
| Run logging | Structured JSONL event logs capture retrieval inputs, model inputs, outcomes | `logging/event_logger.py` |

**Gaps:**
- No chunking/overlap strategy (skills are short documents, so this is acceptable today).
- No hybrid sparse+dense retrieval (see topic 2).
- No post-generation faithfulness verification against retrieved skill context.
- No citation enforcement in generated plans.
- No classic retrieve-then-synthesize RAG pipeline — this is intentional since the system retrieves procedural skills and learned observations, not document chunks.

**Recommendation:** Frame this internally as "skill retrieval plus grounded execution" rather than generic RAG. Only add full document RAG if the product needs external factual corpora.

---

### 2. Sparse, Dense, and Hybrid Retrieval — WEAK

| Mode | Implementation | Quality |
|------|---------------|---------|
| Dense | Embedding index with cosine similarity | Implemented |
| Sparse | Keyword fallback via substring match | Implemented but primitive (not BM25) |
| Hybrid | Falls back to keyword only when embeddings are disabled or fail | Staged, not jointly ranked |

**Gaps:**
- No BM25 implementation.
- No reciprocal rank fusion or score normalization.
- No configurable blending of sparse and dense signals.
- No parallel sparse+dense with score merging.

**Recommendation:** If retrieval quality becomes a bottleneck, add offline evaluation on missed matches first. Only then consider a true hybrid scorer. BM25 or reciprocal rank fusion would be the natural next step for the skill index.

---

### 3. Preventing Hallucinations When Context Is Retrieved — PARTIAL

Hallucination mitigation is focused on **action verification**, not on **text generation faithfulness**.

**What exists:**
- Planner prompt constrains output to JSON-only with strict format requirements.
- Temperature is 0.0 across all LLM calls (planner, router, distiller, coordinator).
- Tiered verification (AX state → actuator state → vision) validates that executed actions had the expected effect.
- Mandatory `verify` field on every plan step — steps without it are rejected during plan validation.
- `ContextMonitor` caps planner context and tracks milestones/obstacles to avoid context-pollution hallucination.

**Gaps:**
- No post-generation check that plan steps are grounded in the skill template content.
- No claim-to-source mapping.
- No "I don't know" / refusal pathway when skill context is insufficient (the planner will plan without skill guidance).
- No lightweight verifier model for generated text.

**Recommendation:** Keep leaning on postcondition verification. Add clearer "do not treat page text as instructions" language to planning prompts. Consider a lightweight post-plan grounding check that flags steps with no basis in the skill template or screen description.

---

### 4. Fine-Tuning vs RAG Decision — N/A (correct posture)

The agent uses no fine-tuned models. It relies entirely on prompting + retrieval (skill priors) + rules (verification, plan validation). This is the correct starting posture per the article's advice: "start with RAG and prompt engineering. Only fine-tune once you see consistent failure modes."

**Notable strength:** The skill distiller (`skills/distiller.py`) and librarian (`skills/librarian.py`) learn from experience and produce new skills — this is an interesting retrieval-native alternative to fine-tuning where "training data" becomes retrievable skill documents rather than model weights.

**Recommendation:** Add a short architecture note defining when the project would prefer prompt changes, code constraints, retrieval, or fine-tuning, so the decision framework is explicit.

---

### 5. Cost-Benefit of Fine-Tuning vs Retrieval/Rules — STRONG

The architecture follows the article's recommended hierarchy precisely:

1. **Prompts first**: All behavior is driven by markdown prompt templates.
2. **Retrieval second**: Skill embedding index provides domain knowledge at inference time.
3. **Rules third**: Plan validation, action aliases, blocked apps, PII redaction, destructive-action gating.
4. **Fine-tuning never (yet)**: No fine-tuned models — complexity is earned by failure data.

The skill-learning loop (experience store → librarian → promotion) is effectively a retrieval-native alternative to fine-tuning. The codebase demonstrates the right default instinct: use code and verification for hard constraints, use models for planning and perception.

**Recommendation:** Keep this hierarchy explicit in docs so future contributors do not respond to every failure by inflating prompts.

---

### 6. Evaluating Agent Behavior Across Long Multi-Step Interactions — PARTIAL

**What exists:**
- **Step-level tracking**: Every step logs `step_start`, `step_complete`, `step_retry`, `step_replan` via `EventLogger`.
- **Trajectory logging**: Full `trace.md` and `events.jsonl` per run capture the entire interaction trajectory.
- **Scenario-based evaluation**: Customer test reports in `docs/reports/` evaluate multi-step scenarios (e.g., Amazon return, Walmart return) with PASS/FAIL/INSUFFICIENT_EVIDENCE.
- **FrustrationScore**: Tracks pathological behaviors (same state, identical actions, replan count).
- **Test layers**: `unit`, `integration`, `e2e`, and `manual` markers defined in `pyproject.toml`.

**Gaps:**
- No automated end-to-end success-rate metric aggregation across runs.
- No step-efficiency scoring (optimal steps vs actual steps).
- No cross-run behavioral regression detection.
- E2E tests (`tests/e2e/`) exist but require live macOS and are not part of CI.
- No "long-horizon" metrics like average turns per session or early abandonment rate.

**Recommendation:** Create a small canonical agent-behavior benchmark suite with outcome and trajectory metrics (success rate, step count, retries, loops, evidence quality), then run it regularly.

---

### 7. Regression Testing for Prompts, RAG Pipelines, or Agents — PARTIAL

**What exists:**
- ~15 specific regression tests across unit test files (grounding pipeline, adversarial, type-text hardening, scroll recovery, skill librarian).
- Prompts tested indirectly via mocked LLM calls in unit tests.
- Grounding benchmark (`scripts/benchmark_grounding.py`) with ScreenSpot dataset (1,272 samples) and point-in-bbox accuracy.

**Gaps:**
- No dedicated prompt regression harness (a fixed set of prompts with expected outputs).
- No prompt versioning or snapshotting — prompts are `.md` files with no version tags.
- No automated scoring pipeline that runs before/after prompt changes.
- No embedding-similarity or LLM-as-judge automated scoring.
- Replay capability is planned but not implemented.

**Recommendation:** Build a lightweight regression pack of representative tasks: a fixed evaluation set of (goal, screen state, expected plan) tuples with stable invariants like success, no looping, evidence present, and bounded step count. Version prompts with content hashes.

---

### 8. ReAct Pattern — N/A (Planner-Executor instead)

The agent does **not** use ReAct. It uses a **Plan-then-Execute** pattern:

1. Generate a full plan upfront (planner LLM call).
2. Execute steps sequentially with verification.
3. Replan on failure.

This is a deliberate architectural choice. The article notes ReAct is better for "multi-hop questions, workflows, or agent-like behaviors" — but a planner-executor is better for "dependent steps or workflows that must be audited" because it is easier to insert human approvals and to audit the plan.

The agent does incorporate ReAct-like elements: the `observe` action lets the agent gather information mid-execution, and replanning is triggered reactively when steps fail. This gives some interleaved reason-act behavior without abandoning plan auditability.

**Recommendation:** Keep the current architecture. Consider selective ReAct-style reflection only for high-ambiguity steps instead of converting the whole system into a free-form loop.

---

### 9. Context Window Management — PARTIAL

**What exists:**
- **Cheap state updates**: `ContextMonitor.update_cheap()` uses the Accessibility API (~50ms) instead of full VLM calls (3–7s).
- **Selective vision refresh**: `needs_full_vision()` triggers VLM refresh only every 3rd iteration or when no description exists.
- **Compact state format**: `format_for_planner()` produces structured text, not raw dumps.
- **Skill context budgeting**: Only top-k skill matches are injected into the prompt.
- **Context capping**: `ContextMonitor` caps planner input to bounded sections (recent milestones, obstacles, capped interactive elements).

**Gaps:**
- No explicit token budget enforcement on prompt construction.
- No summarization of prior steps before replanning (full step history is passed).
- No hierarchical memory — everything is in-context or discarded.
- State diffs help but long runs could still exceed context limits.

**Recommendation:** Move from fixed caps toward token-aware budgeting only if prompt size becomes a measurable problem. Summarize step history before replanning instead of passing raw results.

---

### 10. Consistent Structured Output (JSON) — STRONG

This is one of the best-implemented areas, with multiple layers of defense:

| Layer | Mechanism |
|-------|-----------|
| Schema definition | Plan output format fully specified in prompt with field-by-field definitions and concrete JSON example |
| Strict formatting | "Respond with ONLY valid JSON (no markdown, no explanation)" |
| Temperature | 0.0 across all generation calls |
| Action normalization | `_ACTION_ALIASES` maps LLM-generated action names to canonical forms |
| Parse recovery | `_parse_plan_response()` extracts JSON from markdown code blocks if model wraps output |
| Validation | `ActionStep.__post_init__` validates action names and `on_fail` values; `plan.validate()` rejects steps without `verify` fields |
| Grammar constraints | Ollama backend uses GBNF grammar for constrained JSON decoding |
| Repair on failure | Unknown actions mapped to likely candidates (`type_text` or `click`) rather than crashing |

**Recommendation:** Extend the same strictness to any future judge-model or evaluator outputs so telemetry and evals remain machine-checkable.

---

### 11. Signals That Prompting Has Reached Its Limits — PARTIAL

The codebase shows evidence of hitting prompting limits and responding with system-level fixes:

- **Skill learning pipeline** was built because prompting alone couldn't encode domain-specific procedures (e.g., Amazon returns navigation).
- **Tiered verification** was added because prompting the planner to produce correct `verify` fields wasn't reliable enough.
- **Plan hardening** (`_harden_plan()`) adds programmatic post-processing rules because the planner doesn't consistently produce navigation steps, fallback strategies, or correct `on_fail` values.
- **Type-text hardening** adds rule-based fixes (clear-first, slow-type) because prompt instructions for text input edge cases were fragile.

**Gaps:**
- No systematic tracking of where prompt changes cause regressions (which would formalize the decision to move away from prompting).
- No per-failure-mode classification that tags "this is a prompting problem" vs "this needs system design."
- No simple rubric in docs for when to stop prompt iteration and move to system changes.

**Recommendation:** Add an engineering guideline: "if a fix requires more than one new prompt rule or causes regressions elsewhere, escalate to routing, preprocessing, verification, or code."

---

### 12. Low Recall / High Precision Impact on Downstream Generation — WEAK

The skill retrieval system has no explicit recall/precision monitoring.

**What exists:**
- `EmbeddingIndex.query()` returns `top_k=5` candidates with similarity scores.
- LLM re-rank fires when the top similarity is below threshold or gap between top-1 and top-2 is small.

**What's missing:**
- When no skill matches, the planner simply plans without skill guidance — there is no explicit "I couldn't find a relevant skill" signal.
- No retrieval quality metrics (recall@k, MRR).
- No tracking of "correct skill was available but not retrieved."
- No fallback behavior when recall is low (the planner just wings it).
- No monitoring of retrieval quality over time.

**Recommendation:** Build a retrieval evaluation set for skill routing and learned observation recall. Track "correct skill was available but not retrieved" cases before changing router complexity.

---

### 13. Conflicting Retrieved Documents — WEAK

The skill system does not handle conflicting skill matches at retrieval time.

**What exists:**
- When multiple skills match, `SkillRouter` picks one via LLM re-rank.
- `SkillLibrarian` has duplicate prevention (`_compute_score` and dedup logic), but this operates at skill creation time, not at retrieval time.

**What's missing:**
- No conflict detection between retrieved skill candidates (e.g., two skills for the same task with different procedures).
- No mechanism to surface disagreements to the user.
- No metadata-based resolution (e.g., prefer newer or higher-confidence skills).
- Accessibility, actuator-state, and vision signals are checked in tiers, but disagreement is not modeled as first-class evidence.

**Recommendation:** Add explicit conflict handling for cases where verifier signals disagree or where page state contradicts a prior assumption. Surface conflict to the user when resolution is ambiguous.

---

### 14. Minimal Agent Loop Components — STRONG

All five required components from the article are present and well-implemented:

| Component | Implementation |
|-----------|---------------|
| **Perception** | `ContextMonitor` (AX API, ~50ms), `VisionCoordinator` (screenshots + VLM, 3–7s) |
| **State/Memory** | `DesktopContext` (short-term working state), `SkillExperienceStore` (long-term learning) |
| **Policy** | Planner LLM + plan hardening + confirmation logic |
| **Action interface** | 10 bounded actions via `AppleScriptActuator` |
| **Termination** | `done` action, `max_iterations`, infeasibility detection, user denial |

The loop is well-structured: observe (AX + optional VLM) → plan (LLM) → execute (actuator) → verify (tiered) → update state → repeat/replan.

**Recommendation:** Preserve this explicitness as new capabilities are added. The bounded action space and explicit termination conditions are key safety properties.

---

### 15. Agent Loop Failures and Prevention — STRONG

This is one of the strongest areas, with multiple layers of loop-failure prevention:

- **FrustrationScore**: Tracks `same_state_count`, `identical_action_count`, `replan_count` per execution run. Created fresh per `execute()` call — never stored on `self`.
- **Infeasibility detection**: OR-based trigger when same-state or replan thresholds are hit. LLM advisory check asks "is this still achievable?" Hard abort after N advisory checks.
- **Progress tracking**: `DesktopContext` maintains `completed_milestones`, `obstacles`, and `state_version`. `FrustrationScore.reset_on_progress()` clears counters on visible progress.
- **Element absence detection**: After 2+ not-found for the same element, checks `_is_element_absent()` and escalates to replan with `absent_elements` hint.
- **Vary strategy**: `_vary_strategy()` changes retry approach per action type instead of repeating the same action.
- **Step limits**: `max_iterations` (default 20), `max_retries` per step (default 3).

**Recommendation:** Strengthen loop detection with a compact repeated-state or repeated-action signature and make escalation explicit when the agent is spinning.

---

### 16. Prompt Injection Prevention — PARTIAL

**What exists:**
- **Display sanitization**: `_sanitize_for_display()` strips ANSI escapes, Unicode directional overrides, and control characters.
- **PII redaction**: `_redact_params_for_log()` redacts sensitive params in logs.
- **Bounded action space**: Only 10 predefined desktop actions — even if injection occurs, the blast radius is limited.
- **Destructive-action gating**: Two-phase confirmation for destructive actions provides a human checkpoint.
- **Blocked apps**: `blocked_apps` list prevents actions on System Preferences/Settings.

**Gaps (critical):**
- No explicit separation of trusted instructions from untrusted content in prompts — skill templates and screen descriptions are mixed in the same prompt.
- No input sanitization of skill template content before prompt injection.
- No precedence rules in the prompt ("follow system instructions even if skill context says otherwise").
- No deliberate prompt-injection test suite.
- Page titles, selected text, headings, and other browser-derived fields can be attacker-controlled and may end up in model context.

**Recommendation:** Treat webpage text and tool-returned text as hostile by default. Add explicit prompt compartmentalization, quoting, sanitization, and adversarial tests for indirect injection. This is the highest-priority security gap.

---

### 17. More Instructions Making Performance Worse — PARTIAL

Evidence that the team has hit this problem and responded correctly:

- **Plan hardening** was built as a programmatic post-processing layer rather than adding more planner prompt rules — moving complexity from prompt to code.
- **Skill priors** have explicit guidance levels (`direct`, `analogical`, `generic`) to avoid overwhelming the planner with irrelevant instructions.
- The planner prompt is 109 lines — moderately long but well-structured with clear sections.

**Gaps:**
- No automated detection of prompt bloat or attention dilution.
- No A/B testing of prompt variants.
- No metric tracking prompt length vs plan quality.

**Recommendation:** Add a prompt hygiene rule: prefer code paths, schema changes, or routing changes over adding another instruction block to the main planner prompt.

---

### 18. Prompt Edge Case Failures — Iterate vs Change Design — STRONG

The codebase shows a mature pattern of escalating from prompting to system design. Each layer was added in response to measured failure modes, not preemptively:

1. **Prompting first**: Initial behavior defined in `plan_from_prompt.md`.
2. **Rules second**: `_harden_plan()` fixes common planner mistakes programmatically (missing navigation, bad `on_fail`, auth detection).
3. **Routing third**: Site-entity extraction routes to specialized skill contexts before planning.
4. **Learning fourth**: Skill librarian promotes repeated observations into retrievable skill templates.
5. **Verification fifth**: Tiered verification catches execution errors that prompting can't prevent.

**Recommendation:** Formalize the decision tree in docs so future changes preserve this discipline.

---

### 19. Agent Deciding When to Act vs Think — PARTIAL

The agent doesn't have an explicit "think" action in the ReAct sense. Instead, thinking is distributed across several mechanisms:

| Mode | Mechanism |
|------|-----------|
| Plan = think | Planner generates a full multi-step plan before execution |
| Observe = think | `observe` action takes a screenshot and describes the screen without acting |
| Replan = re-think | On failure, agent generates a new plan incorporating what it learned |
| Infeasibility advisory = reflect | LLM is asked "is this still achievable?" before hard abort |

**Gaps:**
- No mid-execution reasoning step — the agent can't pause between steps to reconsider the plan without a failure trigger.
- No "should I replan proactively?" heuristic based on state drift.
- The agent only reflects when forced by failure, not when it detects the situation has changed.

**Recommendation:** Introduce a narrow reflection action for ambiguous or repeated-failure states rather than expanding the whole loop into free-form reasoning.

---

### 20. Memory Retrieval Without Overwhelming Context Window — PARTIAL

**What exists:**
- **Short-term**: `DesktopContext` is compact and structured. `format_for_planner()` produces minimal text.
- **Long-term**: `SkillExperienceStore` persists observations to JSONL. `top_for_context()` retrieves only top-N relevant observations.
- **Skill context**: Only the matched skill (not all candidates) is injected into the prompt.

**Gaps:**
- No hard token budget for memory/context sections.
- No summarization of step history before replanning (full step history is passed).
- No hierarchical memory (working memory vs episodic memory vs semantic memory).
- No explicit memory retrieval by relevance at replan time — full step history is passed.

**Recommendation:** If cross-run learning becomes more important, add a small retrieval layer over structured past outcomes rather than expanding planner context directly. Summarize step history at replan time.

---

### 21. Signals for Human Intervention — STRONG

Multiple escalation pathways covering different risk profiles:

| Signal | Mechanism |
|--------|-----------|
| Destructive action | Two-phase confirmation with auto-deny timeout |
| Authentication needed | `wait_for_user` action + `_harden_plan()` detects login/sign-in patterns |
| Step failure | Steps can specify `on_fail: "wait_for_user"` to escalate |
| Infeasibility | After repeated failures + advisory checks, agent aborts with explanation |
| High-risk grounding | Critical actions (submit, pay, confirm, delete) require grounding confidence ≥ 0.9 |
| Predicted negative outcome | VLM lookahead predicts outcome of destructive actions; blocks on negative prediction |

**Recommendation:** Add one generic `request_human_review` path with a machine-generated handoff summary for cases that don't fit existing escalation categories — unresolved ambiguity, repeated low-confidence grounding, or contradictory evidence.

---

### 22. Choosing Agent Architecture — STRONG

The architecture matches the article's recommendations for "many dependent steps or workflows that must be audited":

- **Planner + executor**: Planner generates the step list; executor runs each step with verification.
- **Hierarchical skill routing**: Top-level skill matcher routes to specialized skill contexts.
- **Model mix**: Smaller/cheaper models for grounding and routing; larger models for planning and verification. Per-step model routing via `resolve_step_model()`.
- **Provenance**: `EventLogger` tracks prompt, model, retrieved skill, step outcomes, and screenshots per run.
- **State representation**: `DesktopContext` with known facts (milestones), obstacles, navigation history, and form progress.
- **Cost budgeting**: Cheap AX updates (~50ms) vs expensive VLM calls (3–7s), triggered selectively.

**Recommendation:** Keep the current architecture narrative clear: this project is a grounded desktop automation agent with modular specialist components, not a generic autonomous swarm.

---

## Strength Clusters

### Where this agent excels (interview-ready)

**Execution Grounding & Verification** (topics 3, 10, 14, 15): The 3-tier verification stack (AX state → actuator state → vision), mandatory postconditions on every step, structured output with multi-layer parsing recovery, and comprehensive loop-failure detection make this one of the most robustly verified agent architectures.

**Architecture & Design Discipline** (topics 5, 18, 22): The hierarchy of prompts → retrieval → rules → verification mirrors the article's recommended approach exactly. Each system layer was added in response to measured failure modes, not preemptively. The plan-hardening pattern of moving fragile prompt instructions into deterministic code is a particularly mature design decision.

**Safety & Human-in-the-Loop** (topics 15, 21): FrustrationScore, infeasibility detection, destructive-action confirmation, auth detection, confidence gating, and VLM lookahead provide defense-in-depth against runaway execution.

### Where this agent is adequate but could mature (conversation-ready)

**Retrieval & Memory** (topics 1, 2, 9, 20): The skill retrieval pipeline works but lacks hybrid retrieval, recall measurement, and token-aware budgeting. Memory is skill-centric, not a general episodic retrieval layer.

**Evaluation & Regression** (topics 6, 7, 11): Step-level telemetry is strong but there is no durable long-horizon benchmark harness, no prompt regression suite, and no automated cross-run behavioral metrics.

**Resilience & Adaptability** (topics 8, 19): The planner-executor pattern is well-chosen but the agent only reflects when forced by failure. No proactive reasoning about state drift or plan validity.

### Where this agent needs investment (be honest about gaps)

**Retrieval Quality Monitoring** (topics 2, 12, 13): No recall@k, MRR, or conflict detection. No tracking of missed skills. No retrieval quality trends.

**Prompt Injection Hardening** (topic 16): Browser-derived content enters model context without sanitization or compartmentalization. No adversarial test suite.

---

## Priority Actions (ordered by impact)

### P0 — Security

1. **Prompt injection hardening**: Treat webpage text and tool-returned text as untrusted by default. Add prompt compartmentalization, quoting, sanitization, and an adversarial test suite for indirect injection via skill templates and screen descriptions.

### P1 — Evaluation & Regression

2. **Long-horizon regression suite**: Build a small canonical benchmark that replays a fixed set of realistic tasks and scores success rate, step count, retries, loops, and evidence quality. Run it regularly.
3. **Prompt regression harness**: Build a fixed evaluation set of (goal, screen state, expected plan) tuples. Run before/after prompt changes with automated scoring. Version prompts with content hashes.

### P2 — Retrieval & Memory

4. **Retrieval quality monitoring**: Add recall@k, MRR, and conflict-detection metrics for skill retrieval. Instrument "correct skill was available but not retrieved" cases. Consider BM25 or hybrid retrieval.
5. **Token budget enforcement**: Set hard token limits for each prompt section (skill context, step history, desktop state). Summarize step history before replanning instead of passing raw results.

### P3 — Resilience & Escalation

6. **Generic human escalation**: Add a `request_human_review` path with a machine-generated handoff summary for unresolved ambiguity, repeated low-confidence grounding, or contradictory evidence.
7. **Post-generation grounding check**: After the planner generates a plan, verify that plan steps reference elements or actions from the skill template or screen description. Flag hallucinated steps.
8. **End-to-end metrics pipeline**: Aggregate success rate, steps-to-completion, tool error rate, and replan frequency across runs. Track trends over time. Gate releases on metric thresholds.

---

## Interview Framing

If discussing this repo in an interview, the most accurate framing:

> "This is a grounded desktop automation agent with planner-executor control, multimodal verification, skill retrieval, adaptive memory, and safety gates. It is strongest in execution grounding, postcondition checking, architecture decomposition, and the discipline of escalating from prompts to system design. Its next maturity steps are long-horizon evals, injection hardening, retrieval quality monitoring, broader escalation policies, and richer memory/provenance."

**Key talking points:**
- 3-tier verification (AX → actuator → vision) with mandatory postconditions
- Plan-hardening as a pattern for moving fragile prompt logic into deterministic code
- Skill learning loop as a retrieval-native alternative to fine-tuning
- FrustrationScore and infeasibility detection for loop prevention
- Per-step model routing for cost optimization
- The deliberate choice of Plan-then-Execute over ReAct, with ReAct-like elements (observe, replan) for flexibility

**Be ready to discuss gaps:**
- Retrieval recall measurement and hybrid retrieval
- Prompt injection surface area (browser-derived content in model context)
- Lack of a durable long-horizon evaluation harness
- Memory is skill-scoped, not general episodic
