# Agent Checklist Report

Source basis: "What Interviewers Ask After You Say 'I Built an AI Agent'" from BuildML, converted into an implementation checklist and a repo-specific assessment for this project.

Status legend:
- `[x]` Strong
- `[-]` Partial
- `[ ]` Missing or not yet first-class

## Executive Summary

This repo already has a serious desktop-agent foundation. Its strongest areas are grounded execution, multimodal verification, structured step execution, safety checks for destructive actions, and local trace logging.

The biggest mismatch with the article is that this is not a classic document-RAG system. It is closer to a modular `plan -> execute -> verify -> replan` automation agent with skill retrieval, adaptive skill memory, and strong UI grounding.

The main improvement opportunities are:
- stronger long-horizon regression/eval harnesses
- broader escalation and uncertainty handling beyond destructive confirmation
- better separation of trusted instructions from untrusted page/tool content
- richer semantic memory retrieval across prior runs
- more complete provenance for prompts, model versions, and evidence lineage

## Checklist By Topic

Each topic below includes:
- a practical checklist derived from the article
- a repo-specific assessment grounded in the current implementation

## 1. End-to-End RAG Pipeline

Checklist
- [ ] Query classification or routing is explicit.
- [ ] Query rewriting exists when user phrasing is underspecified.
- [ ] Retrieval pulls from a maintained corpus with chunking and metadata.
- [ ] Retrieval is followed by re-ranking and context selection.
- [ ] Generation is explicitly grounded to selected evidence.
- [ ] Post-generation checks reject unsupported claims.
- [ ] Logs capture retrieval inputs, retrieved items, model inputs, and outcomes.

Repo assessment
- Status: `partial`
- Evidence: skill retrieval exists in [src/automation_agent/skills/registry.py](src/automation_agent/skills/registry.py), grounded execution exists in [src/automation_agent/orchestrator/verifier.py](src/automation_agent/orchestrator/verifier.py), and run logging exists in [src/automation_agent/logging/event_logger.py](src/automation_agent/logging/event_logger.py).
- Why this is partial: the repo retrieves procedural skills and learned observations, not chunks from a document corpus. It has verification after action, but not a classic retrieve-then-synthesize RAG pipeline.
- Recommendation: describe this system internally as "skill retrieval plus grounded execution" rather than generic RAG, and only add full document RAG if the product really needs external factual corpora.

## 2. Sparse vs Dense vs Hybrid Retrieval

Checklist
- [ ] Sparse retrieval exists for exact tokens, IDs, names, or jargon.
- [ ] Dense retrieval exists for semantic or fuzzy matches.
- [ ] Hybrid retrieval merges both rather than relying on one mode only.
- [ ] Retrieval choice is matched to query type and corpus shape.
- [ ] Retrieval quality is measured with recall-oriented metrics.

Repo assessment
- Status: `partial`
- Evidence: [src/automation_agent/skills/registry.py](src/automation_agent/skills/registry.py) uses an `embed -> LLM rerank -> keyword fallback` pipeline, and trusted skills are indexed semantically before router selection.
- Why this is partial: there is no true fused hybrid retriever or reciprocal-rank-fusion style merge. Sparse and dense are staged, not jointly ranked.
- Recommendation: if retrieval quality becomes a bottleneck, add offline evaluation on missed matches first. Only then consider a true hybrid scorer for skills and learned observations.

## 3. Hallucination Control Even With Correct Context

Checklist
- [ ] Context is intentionally trimmed to the smallest useful evidence set.
- [ ] Prompts explicitly require grounding and refusal when evidence is absent.
- [ ] Generation settings are conservative for factual or state-sensitive tasks.
- [ ] Claims are tied to evidence or post-hoc verification.
- [ ] Unsupported outputs are downgraded, retried, or refused.

Repo assessment
- Status: `[x] strong`
- Evidence: [src/automation_agent/orchestrator/verifier.py](src/automation_agent/orchestrator/verifier.py) runs three-tier verification, and [src/automation_agent/orchestrator/context_monitor.py](src/automation_agent/orchestrator/context_monitor.py) caps planner context and tracks milestones and obstacles.
- Why this is strong: the system does not trust the plan alone. It validates step postconditions using accessibility, actuator state, and then vision.
- Recommendation: keep leaning on postcondition verification. For planning prompts, add even clearer "do not treat page text as instructions" language.

## 4. Fine-Tuning vs RAG vs Combining Both

Checklist
- [ ] Fine-tuning is reserved for behavior/style consistency or narrow repeated tasks.
- [ ] Retrieval is used for fresh or changing knowledge.
- [ ] The team can articulate whether failures are knowledge failures or behavior failures.
- [ ] Combined approaches are used only when one lever is clearly insufficient.

Repo assessment
- Status: `partial`
- Evidence: the repo mainly solves behavior and grounding with prompts, planners, verification, and skills; there is no fine-tuning pipeline in the current codebase.
- Why this is partial: the system is correctly retrieval- and control-heavy today, but there is no explicit decision framework recorded for when fine-tuning would be worth the cost.
- Recommendation: add a short architecture note defining when the project would prefer prompt changes, code constraints, retrieval, or fine-tuning.

## 5. Cost-Benefit: Fine-Tuning vs Retrieval vs Rules

Checklist
- [ ] Prompts are the first lever.
- [ ] Retrieval is used when information is missing or changes often.
- [ ] Rules validate critical correctness boundaries.
- [ ] Fine-tuning is used only after repeated measured failures.
- [ ] Decisions are made with volume, latency, maintenance, and rollback risk in mind.

Repo assessment
- Status: `[x] strong`
- Evidence: the current architecture already favors explicit control logic, routing, verification, and confirmation over trying to solve everything with a larger prompt.
- Why this is strong: the codebase demonstrates the right default instinct: use code and verification for hard constraints, and use models for planning and perception.
- Recommendation: keep this hierarchy explicit in docs so future contributors do not respond to every failure by inflating prompts.

## 6. Evaluating Agent Behavior Across Long Multi-Step Interactions

Checklist
- [ ] Success is defined over trajectories, not just final answers.
- [ ] Scenario-based tasks exist with clear pass/fail criteria.
- [ ] Progress, tool choices, retries, and recoveries are logged.
- [ ] Metrics include success rate, step efficiency, loops, and human intervention.
- [ ] Stress tests cover ambiguity, partial failures, and bad intermediate results.

Repo assessment
- Status: `partial`
- Evidence: [pyproject.toml](pyproject.toml) defines `unit`, `integration`, `e2e`, and `manual` test layers; [TESTS.md](TESTS.md) records a large non-E2E baseline; scenario reports already exist under [docs/reports/](docs/reports/) and in [docs/speed-phase1-customer-report.md](docs/speed-phase1-customer-report.md).
- Why this is partial: the repo has many tests and scenario docs, but not yet a single durable long-horizon benchmark harness that replays a fixed scenario set across builds.
- Recommendation: create a small canonical agent-behavior benchmark suite with outcome and trajectory metrics, then run it regularly.

## 7. Regression Testing for Prompts, RAG Pipelines, and Agents

Checklist
- [ ] A fixed benchmark set exists and changes rarely.
- [ ] Prompt regressions are tested with invariants, not exact string matching.
- [ ] Retrieval is evaluated separately from generation.
- [ ] End-to-end trajectories are replayed after prompt or planner changes.
- [ ] Prompt, retrieval, and policy versions are snapshot-friendly.

Repo assessment
- Status: `partial`
- Evidence: unit and integration coverage is broad, and prompt/template tests exist, but most prompt tests are structural rather than behavioral.
- Why this is partial: there is limited evidence of golden prompt-output suites, full prompt diffing, or systematic end-to-end replay for agent regressions.
- Recommendation: add a lightweight regression pack of representative tasks with stable invariants such as success, no looping, evidence present, and bounded step count.

## 8. ReAct Pattern and Why It Helps

Checklist
- [ ] The system can alternate between reasoning and acting rather than only planning once.
- [ ] Tool outcomes can change the next reasoning step.
- [ ] Failures are observable and can trigger strategy changes.
- [ ] The architecture exposes action history and intermediate observations.

Repo assessment
- Status: `partial`
- Evidence: [src/automation_agent/orchestrator/agent.py](src/automation_agent/orchestrator/agent.py) supports retries, recovery, and replanning, but the main shape is still plan-first rather than think-act every turn.
- Why this is partial: this is closer to planner-executor with recovery than canonical ReAct.
- Recommendation: keep the current architecture, but consider selective ReAct-style reflection for high-ambiguity steps instead of converting the whole system into a free-form loop.

## 9. Context Window Limits

Checklist
- [ ] Only the most relevant state is passed into the model.
- [ ] Long histories are summarized or compressed.
- [ ] The system distinguishes recent working state from long-term memory.
- [ ] There is a hard budget for context, not just "stuff until it breaks."

Repo assessment
- Status: `[x] strong`
- Evidence: [src/automation_agent/orchestrator/context_monitor.py](src/automation_agent/orchestrator/context_monitor.py) trims planner input to bounded sections such as recent milestones, obstacles, and a capped set of interactive elements.
- Why this is strong: the system already treats context as scarce and uses cheap state refreshes plus selective full vision refreshes.
- Recommendation: move from fixed caps toward token-aware budgeting only if prompt size becomes a measurable problem.

## 10. Structured Output at Scale

Checklist
- [ ] Schemas are defined outside the prompt.
- [ ] The model is instructed to return one structure only.
- [ ] Required fields, enums, and null behavior are explicit.
- [ ] Outputs are parsed and validated before use.
- [ ] Repair loops or safe fallbacks exist when validation fails.

Repo assessment
- Status: `[x] strong`
- Evidence: planning depends on structured plan objects and step validation before execution, rather than free-form action text.
- Why this is strong: the architecture already assumes the planner can be wrong and validates the resulting structure before dispatch.
- Recommendation: extend the same strictness to any future judge-model or evaluator outputs so telemetry and evals remain machine-checkable.

## 11. Signals That Prompting Alone Has Hit Its Limit

Checklist
- [ ] Small wording changes do not cause large regressions.
- [ ] Prompts are not growing uncontrollably.
- [ ] The same edge case is not being "fixed" repeatedly with more instructions.
- [ ] Failures are categorized as prompt, retrieval, tool, or policy failures.

Repo assessment
- Status: `partial`
- Evidence: the repo already uses code-level constraints, routing, and verification instead of relying on prompting alone, which is a good sign.
- Why this is partial: there is still no simple rubric in docs for when to stop prompt iteration and move to system changes.
- Recommendation: add a short engineering guideline: "if a fix requires more than one new prompt rule or causes regressions elsewhere, escalate to routing, preprocessing, verification, or code."

## 12. Low Recall but High Precision Retrieval

Checklist
- [ ] Retrieval quality is judged on recall, not just precision.
- [ ] Multi-part tasks are evaluated for missing evidence.
- [ ] The generator can refuse when evidence is incomplete.
- [ ] Retrieval misses are logged and inspectable.

Repo assessment
- Status: `partial`
- Evidence: semantic skill matching and keyword fallback exist, but there is little explicit recall measurement for missed procedural matches or memory misses.
- Why this is partial: re-ranking and verification cannot save a skill or memory that was never retrieved.
- Recommendation: build a retrieval evaluation set for skill routing and learned observation recall before changing router complexity.

## 13. Conflicting Retrieved Evidence

Checklist
- [ ] Conflicts across sources are detected explicitly.
- [ ] The system has a rule for freshness, authority, or escalation.
- [ ] The model is allowed to surface disagreement instead of guessing.
- [ ] Conflicts are logged for corpus or policy cleanup.

Repo assessment
- Status: `partial`
- Evidence: the skill router handles multi-site ambiguity conservatively, but broader cross-source conflict handling is limited.
- Why this is partial: accessibility, actuator-state, and vision signals are checked in tiers, but the system does not yet model disagreement as first-class evidence that should sometimes be surfaced or escalated.
- Recommendation: add explicit conflict handling for cases where verifier signals disagree or where page state contradicts a prior assumption.

## 14. Minimal Agent Loop and Required Components

Checklist
- [ ] Observation is explicit.
- [ ] State is explicit.
- [ ] Policy chooses between actions rather than only generating text.
- [ ] Actions are bounded by tool interfaces.
- [ ] Environment responses feed back into the next step.
- [ ] Clear termination conditions exist.

Repo assessment
- Status: `[x] strong`
- Evidence: [src/automation_agent/orchestrator/agent.py](src/automation_agent/orchestrator/agent.py), [src/automation_agent/orchestrator/context_monitor.py](src/automation_agent/orchestrator/context_monitor.py), and [src/automation_agent/orchestrator/verifier.py](src/automation_agent/orchestrator/verifier.py) cover these components directly.
- Why this is strong: the system is not a vague chat loop. It has explicit actions, state tracking, verification, and stopping points.
- Recommendation: preserve this explicitness as new capabilities are added.

## 15. Why Agents Loop or Get Stuck

Checklist
- [ ] Progress is represented explicitly.
- [ ] Repeated identical actions are detectable.
- [ ] The agent can stop or escalate when no new information is being added.
- [ ] Tool failures are clearly distinguishable from meaningful partial success.

Repo assessment
- Status: `partial`
- Evidence: [src/automation_agent/orchestrator/agent.py](src/automation_agent/orchestrator/agent.py) has `FrustrationScore`, same-state tracking, replan limits, and infeasibility checks.
- Why this is partial: loop detection is heuristic, and identical-action tracking is not as central as it could be in final stop logic.
- Recommendation: strengthen loop detection with a compact repeated-state or repeated-action signature and make escalation explicit when the agent is spinning.

## 16. Prompt Injection and Untrusted Content

Checklist
- [ ] Instructions are strictly separated from user content and page content.
- [ ] Retrieved or page text is labeled as untrusted data.
- [ ] The model is explicitly told not to obey instructions found in content.
- [ ] Tool inputs and outputs are validated before reuse.
- [ ] Injection tests exist in evaluation.

Repo assessment
- Status: `[ ] missing`
- Evidence: planner and replanner prompts interpolate desktop context, history, and browser-derived content, but there is not yet a strong untrusted-content boundary across the stack.
- Why this matters: page titles, selected text, headings, and other browser-derived fields can be attacker-controlled and may end up in model context.
- Recommendation: treat webpage text and tool-returned text as hostile by default. Add explicit prompt compartmentalization, quoting, sanitization, and adversarial tests for indirect injection.

## 17. When More Instructions Make Performance Worse

Checklist
- [ ] Prompts stay short enough to preserve priority clarity.
- [ ] Rare edge cases are not handled by endlessly stacking rules.
- [ ] Conflicting instructions are minimized.
- [ ] Common cases remain optimized even after edge-case fixes.

Repo assessment
- Status: `partial`
- Evidence: the codebase already pushes many hard constraints into code, which reduces prompt bloat risk.
- Why this is partial: planner prompts are still a major control surface, and there is no visible prompt-complexity budget or prompt review rule.
- Recommendation: add a prompt hygiene rule: prefer code paths, schema changes, or routing changes over adding another instruction block to the main planner prompt.

## 18. When to Keep Iterating on Prompts vs Change System Design

Checklist
- [ ] Edge cases are grouped by root cause before fixing.
- [ ] Prompt fixes are used only for low-impact, low-complexity issues.
- [ ] Input normalization is used for format problems.
- [ ] Retrieval is used for knowledge gaps.
- [ ] Routing or review paths handle rare but costly failures.

Repo assessment
- Status: `[x] strong`
- Evidence: the repo already uses verification, confirmation, fallback strategies, skill routing, and domain-specific recovery rather than treating prompt edits as the only answer.
- Why this is strong: the architecture direction is already systems-first.
- Recommendation: formalize the decision tree in docs so future changes preserve this discipline.

## 19. When Should an Agent Think vs Act

Checklist
- [ ] The action space includes pause, reflect, ask, or act.
- [ ] The agent can detect uncertainty before committing to risky actions.
- [ ] High-risk actions require stronger evidence or explicit confirmation.
- [ ] Failed actions can trigger reflection instead of blind retry.

Repo assessment
- Status: `partial`
- Evidence: the agent can retry, replan, or wait for user input, and destructive actions go through confirmation gates.
- Why this is partial: reflective thinking is present more as replan or recovery behavior than as a first-class "reason before acting" control mode.
- Recommendation: introduce a narrow reflection action for ambiguous or repeated-failure states rather than expanding the whole loop.

## 20. Memory Retrieval Without Overwhelming Context

Checklist
- [ ] Memory is split into recent state and long-term memory.
- [ ] Long-term memory is ranked by relevance, not dumped wholesale.
- [ ] Memory is compressed before insertion into prompt context.
- [ ] The system enforces a hard memory budget.
- [ ] The agent can request deeper history only when needed.

Repo assessment
- Status: `partial`
- Evidence: [src/automation_agent/orchestrator/context_monitor.py](src/automation_agent/orchestrator/context_monitor.py) handles recent state, while skill observations and derived-skill patches provide longer-lived memory.
- Why this is partial: memory retrieval is still mostly skill-centric and not a general semantic episodic retrieval layer over prior runs, states, and failure patterns.
- Recommendation: if cross-run learning becomes more important, add a small retrieval layer over structured past outcomes rather than expanding planner context directly.

## 21. Human Intervention Signals

Checklist
- [ ] Low-confidence, high-risk, or conflicting states can escalate.
- [ ] The agent can summarize what it tried before handoff.
- [ ] There are explicit approval points for irreversible actions.
- [ ] Escalation is broader than login pauses or destructive confirmation only.

Repo assessment
- Status: `partial`
- Evidence: destructive actions are confirmed and `wait_for_user` exists, but broader human-in-the-loop triggers are limited.
- Why this is partial: uncertainty, repeated low-signal grounding, and unresolved conflicts do not yet appear to have a strong runtime escalation policy.
- Recommendation: add one generic `request_human_review` path with a short machine-generated handoff summary and the last few pieces of evidence.

## 22. Choosing an Agent Architecture

Checklist
- [ ] The architecture matches task length, risk, and tool complexity.
- [ ] Single-step prompting is used only where it is enough.
- [ ] Planner-executor or ReAct is used when explicit tool use matters.
- [ ] Specialized subcomponents have bounded responsibilities.
- [ ] More complex multi-agent patterns are justified by actual failure modes.

Repo assessment
- Status: `[x] strong`
- Evidence: the current system already looks like a modular planner-executor with separate grounding, verification, skill retrieval, and learning subsystems.
- Why this is strong: the architecture is decomposed in a practical way without overcommitting to gratuitous multi-agent complexity.
- Recommendation: keep the current architecture narrative clear: this project is a grounded desktop automation agent with modular specialist components, not a generic autonomous swarm.

## Priority Actions

1. Add an executable long-horizon regression suite that replays a small fixed set of realistic tasks and scores success, step count, retries, loops, and evidence quality.
2. Add prompt-injection hardening across planner, replanner, and browser-derived context. Treat page text and tool-returned text as untrusted by default.
3. Add a generic `request_human_review` escalation path for unresolved ambiguity, repeated low-confidence grounding, or contradictory evidence.
4. Improve provenance capture so prompt versions, model identities, and evidence lineage are easier to compare across regressions.
5. Add semantic retrieval over prior run outcomes and learned observations only if repeated failures show that skill-scoped memory is too narrow.

## Interview Framing

If this repo were discussed in an interview, the most accurate framing would be:

"This is a grounded desktop automation agent with planner-executor control, multimodal verification, skill retrieval, adaptive memory, and safety gates. It is strongest in execution grounding and postcondition checking. Its next maturity steps are long-horizon evals, injection hardening, broader escalation, and richer memory/provenance."
