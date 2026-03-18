# AI Agent Interview Checklist Report

This report summarizes practical questions and system engineering principles for building, evaluating, and scaling Large Language Model (LLM) agents and Retrieval-Augmented Generation (RAG) pipelines.

## 1. RAG & Retrieval Pipelines
- [ ] **Walk me through an end-to-end RAG pipeline, from user query to final answer**
  - **Hygiene & Rewrite:** Clean input, identify query type, rewrite for clarity (e.g., expand acronyms).
  - **Retrieval:** Preprocess docs (chunk, embed, index). Run parallel keyword (BM25) and dense (vector) searches.
  - **Re-ranking:** Pass top results (e.g., top 50) through a cross-encoder to refine relevance scoring.
  - **Context Building:** Select best chunks, fit into context window, and attach source metadata.
  - **Generation:** Prompt model explicitly to use only provided sources and cite them. Use low temperature for facts.
  - **Verification:** Post-generation check to ensure claims map back to retrieved chunks.
  - **Output & Logging:** Format with citations. Monitor quality and user query drift in production.
- [ ] **How do you decide between sparse, dense, and hybrid retrieval for a given use case?**
  - **Sparse (BM25):** Best for specific terms, IDs, exact phrases. Fast, cheap, easy to debug.
  - **Dense (Embeddings):** Best for semantic queries, underspecified questions, paraphrases.
  - **Hybrid:** Runs both in parallel, merges, and re-ranks. Best for most production systems, balancing recall and precision.
- [ ] **If retrieval recall is low but precision is high, how does that affect downstream generation?**
  - The model may hallucinate to fill in gaps if the right information is missing.
  - Leads to partial, incomplete answers.
  - Creates unstable generation behavior across similar queries.
- [ ] **What happens when retrieved documents conflict with each other, and how should the system respond?**
  - Detect conflicts (e.g., mismatched extracted fields).
  - Use metadata (recency, authority) to resolve if there's a clear winner.
  - If unresolved, surface the disagreement explicitly to the user with citations rather than letting the model blindly guess.

## 2. Prompting, Hallucinations, and Output Formatting
- [ ] **How do you prevent hallucinations even when the correct context is retrieved?**
  - Keep context small, highly relevant, and clearly labeled with metadata.
  - Explicit prompting: "only use provided context", "say 'I don't know' if unsupported".
  - Force citations/evidence use (e.g., quoting exact sentences).
  - Use low temperature and constrained decoding.
  - Add a lightweight post-generation verification step.
- [ ] **How would you design a prompt for consistent structured output at scale (e.g., JSON)?**
  - Define exact schema outside the prompt first.
  - Prompt directly: ask for a single JSON object, list fields/types/constraints, use negative constraints ("do not invent values").
  - Provide examples (one clean, one tricky case).
  - Use low temperature (near zero).
  - **Crucial:** Implement strict post-validation and a repair step (re-prompting or programmatic fixes) rather than failing silently.
- [ ] **In what ways can prompt injection occur, and how do you design prompts to reduce its impact?**
  - **Sources:** Direct user injection, indirect injection via retrieved docs, tool output injection, contextual injection over turns.
  - **Mitigation:** Strictly separate instructions from data, add explicit precedence rules, sanitize/constrain retrieved content, limit model action boundaries (permissions), and design for graceful refusal.
- [ ] **Describe a scenario where adding more instructions made model performance worse. Why does that happen?**
  - **Scenario:** Adding too many edge-case rules to a prompt.
  - **Causes:** Attention dilution, conflicting constraints, pattern-matching disruption, prompt length overshadowing early rules, overfitting to rare cases.
- [ ] **If a prompt consistently fails for a minority of edge cases, how would you decide whether to keep iterating on the prompt or change the system design?**
  - Measure failure rate and type.
  - If failure is information-based &rarr; use retrieval.
  - If input inconsistency &rarr; preprocess inputs.
  - If rare/costly &rarr; add routing/classification.
  - If consistent behavior-level issue &rarr; fine-tune.
  - Stop iterating on prompts when they become brittle and unreadable.
- [ ] **What signals tell you that a task has reached the limits of what prompting alone can reliably achieve?**
  - Brittleness (small tweaks break other things).
  - Prompt bloat.
  - Needing constant negative instructions.
  - Inconsistent outputs at low temperature.
  - Scaling failures.

## 3. Fine-Tuning vs. Rules vs. RAG
- [ ] **When would you choose to fine-tune a model instead of using RAG, or combine both?**
  - **Fine-tuning:** To change behavior, tone, output format, or implicit procedural knowledge.
  - **RAG:** For external, changing, or large amounts of factual information.
  - **Combine:** Fine-tune for reasoning/format behavior, RAG for retrieving facts at inference.
- [ ] **How do you reason about the cost–benefit tradeoff of fine-tuning versus adding retrieval or rules?**
  - Start with prompts, retrieval, rules (fast, cheap, easy to rollback).
  - Use RAG for missing/changing knowledge.
  - Use rules for correctness/safety (catching the last 5-10% of errors).
  - Fine-tune only for stable, recurring behavior failures (has upfront/long-term costs, but can lower per-query latency/cost at scale).

## 4. Agent Architecture & Behavior
- [ ] **Walk me through a minimal agent loop. What components are required for autonomy?**
  - **Observation:** Explicit perception of goal, context, tool outputs.
  - **State/Memory:** Short-term scratchpad, long-term memory store.
  - **Policy (Prompt + Logic):** Decides next action based on observation and state.
  - **Action Interface:** Bounded set of allowed tools.
  - **Environment Response:** Feedback from action execution.
  - **Termination Condition:** Clear rule to stop the loop.
- [ ] **Explain the ReAct pattern and why it improves agent reliability compared to single-step prompting**
  - **ReAct (Reason + Act):** Alternates thinking and acting.
  - Improves reliability by separating planning from execution, making errors visible/correctable, grounding actions in explicit tool evidence, and allowing strict control/validation of intermediate steps.
- [ ] **Why do agents tend to fail in loops or get stuck, and how do you prevent that?**
  - **Causes:** Weak termination signals, poor state representation, ambiguous tool feedback, reactive planning without long-term strategy.
  - **Prevention:** Make progress explicit in state, add stopping rules (max steps/repeats), force reflection steps, improve tool feedback clarity, log/analyze loops.
- [ ] **How does an agent decide when to act versus when to think?**
  - Based on uncertainty and cost. Thinking is cheap/safe; acting is powerful/risky.
  - Shaped by policy prompt and control logic.
  - **Think when:** goal is underspecified, comparing options, previous action failed.
  - **Act when:** next step is obvious, low risk, delaying adds no new info.
- [ ] **How would you design memory retrieval for an agent without overwhelming the context window?**
  - Split memory: keep recent state verbatim, move long-term to vector store.
  - Retrieve targeted memories based on current context (embed query, rank by similarity/recency/salience).
  - Compress/summarize raw memories before inserting into the prompt.
  - Enforce a hard token budget. Allow the agent to explicitly call a tool for "more memory" if needed.
- [ ] **What signals would you use to decide whether an agent should ask for human intervention?**
  - High uncertainty (low confidence, explicit "I don't know").
  - High risk (irreversible actions, changing data, financial moves).
  - Repeated failure/looping (not making progress).
  - Conflicting evidence with no clear resolution rule.
  - Policy/constraint violations. (Provide an explicit "request_human_review" action).
- [ ] **How do you choose an agent architecture for a given problem?**
  - **Single-step:** Low risk, retrieval/summarization &rarr; simple prompt + optional tool.
  - **ReAct loop:** Multi-step, explicit tool use/evidence.
  - **Planner + Executor:** Dependent steps, auditable workflows.
  - **Hierarchical:** Heterogeneous skills, strong security boundaries.
  - **Search-based (e.g., Tree of Thoughts):** High-assurance reasoning, exploring alternatives (expensive, use sparingly).

## 5. Context Limits and Evaluation
- [ ] **How do context window limits affect what kinds of problems LLMs can and cannot solve?**
  - Determines if critical information can fit cleanly. Long docs require lossy chunking.
  - Limits reasoning across many steps (forgetting early constraints).
  - Distraction from noisy text near the token limit.
  - Forces explicit design of external memory and structured retrieval.
- [ ] **How would you evaluate agent behavior across long multi-step interactions?**
  - Evaluate trajectories, not single turns.
  - Define "good behavior" (making progress, reasonable actions, error recovery, proper stopping).
  - **Offline:** Scenario-based evaluations with clear success criteria (completion rate, step efficiency).
  - **Online:** Long-horizon metrics (task success over time, loop rates, human intervention rates).
  - Analyze internal decisions/state to ensure the agent succeeded for the right reasons.
- [ ] **What does regression testing look like for prompts, RAG pipelines, or agents?**
  - Maintain a curated baseline evaluation set.
  - **Prompts:** Check output invariants (formats, policy adherence).
  - **RAG:** Test retrieval recall/precision separately from generation faithfulness.
  - **Agents:** Replay interaction trajectories and compare outcomes (reached goal, used tools, stopped properly).
  - Snapshot/version all components. Treat test regressions as deployment blockers.

## 6. Final Takeaways for Reliable Systems
- [ ] **Treat uncertainty as a signal:** Surface and reason about low recall or conflicts instead of papering over them.
- [ ] **Separate concerns:** Keep retrieval, reasoning, memory, and validation distinct.
- [ ] **Optimize for recall and grounding:** Missing info hurts worse than noisy info.
- [ ] **Start simple:** Complexity should be earned by real failure modes (Prompt &rarr; ReAct &rarr; Planner).
- [ ] **Ensure progress and stopping:** Explicit state tracking and hard stop rules prevent loops.
- [ ] **Memory is a retrieval problem:** Don't stuff context; compress and rank memories.
- [ ] **Build guardrails outside the model:** Let the system decide what's allowed, not the model.
- [ ] **Logging and evaluation:** Version prompts, track steps, and use production data for architecture decisions.
