# Adaptive Skill System: State-of-the-Art Research

This document surveys state-of-the-art approaches relevant to the four core problems in our Adaptive Skill System design (`docs/features/adaptive-skill-system/adaptive-skill-system.md`):

1. Analogical skill/plan transfer
2. Run-local procedure correction / online plan repair
3. Skill/knowledge promotion and curation
4. Top-k retrieval for LLM-based routing

For each problem we summarize the landscape, rank approaches by relevance to our system, identify common failure modes, and recommend a path.

---

## 1. Analogical Skill/Plan Transfer

### Problem

When no exact skill exists for a user prompt (e.g., "return my Walmart order"), the system should retrieve structurally similar skills (e.g., `amazon_return`) and adapt them by analogy rather than planning from scratch.

### SOTA Approaches (ranked by relevance)

#### 1a. SkillWeaver (OSU NLP Group, Apr 2025)

**Most relevant.** A skill-centric framework for web agents that autonomously discovers, practices, and synthesizes reusable skills as callable APIs.

- **Three-stage pipeline**: Skill Proposal (LLM-driven curriculum discovers site functionalities) -> Skill Practice & Synthesis (successful trajectories distilled into Python APIs) -> Skill Honing (test-case-driven debugging).
- **Transfer**: Skills synthesized on one site transfer to weaker agents and across sites, yielding 31.8-54.3% success rate improvements on WebArena.
- **Key insight**: Skills are stored as *APIs with docstrings*, not raw procedures. The docstring enables analogical retrieval; the code enables direct execution.

**Relevance to us**: SkillWeaver's three-stage pipeline (discover -> practice -> distill) maps directly to our run-local derived procedure -> post-run distillation -> promotion pipeline. Our `.md` skill format serves the same role as their Python API docstrings. Their "skill honing" stage validates that a derived procedure actually works before promotion -- a pattern we should adopt.

#### 1b. Voyager (Wang et al., NeurIPS 2023, updated 2024)

**Foundational.** The first LLM-powered lifelong learning agent with a growing skill library.

- **Skill library**: Executable JavaScript functions indexed by embedding of their description. Top-5 retrieval by cosine similarity for each new task.
- **Self-verification**: GPT-4 acts as a critic to verify task completion. Only verified skills enter the library.
- **Iterative refinement**: Environment feedback + execution errors + self-verification in a retry loop until the skill passes.
- **Compositional reuse**: New skills call previously verified skills, compounding capability.

**Relevance to us**: Voyager's "only verified skills enter the library" is the exact quality gate our promotion pipeline needs. Their embedding-based top-5 retrieval is a precursor to our top-k routing, though we correctly identify that LLM-based reranking outperforms pure embedding similarity for procedural applicability.

#### 1c. SayCan + Inner Monologue (Google, 2022-2023)

**Influential pattern.** SayCan scores individual skills by both semantic relevance (LLM) and physical feasibility (affordance function). Inner Monologue adds closed-loop feedback.

- **Affordance-weighted routing**: `P(skill | instruction) * P(skill succeeds | state)` -- combines LLM reasoning with grounded feasibility.
- **Inner Monologue**: Environment feedback (success/failure signals, scene descriptions) fed back to the LLM for replanning.
- **Extensibility**: New skills added by providing new value functions and examples -- no retraining.

**Relevance to us**: The `confidence * feasibility` scoring pattern is directly applicable to our router's match confidence. Our 3-tier verification already plays the "affordance function" role. Inner Monologue's closed-loop feedback mirrors our replan cycle.

#### 1d. DEPS (Wang et al., 2023)

**Plan repair via self-explanation.** When initial plans fail, DEPS:
- Describes execution state
- Self-explains why the failure happened
- Generates a corrected plan with a goal selector that ranks candidate sub-goals

**Relevance to us**: DEPS's "describe + self-explain + replan" pattern maps to our replan prompt. The "goal selector" that ranks sub-goals is analogous to our derived procedure patch mechanism.

#### 1e. AgentTrek (Dec 2024)

**Data synthesis from tutorials.** Converts publicly available web tutorials into structured task specifications, then replays them with a VLM agent.

- **Tutorial -> procedure**: Automated pipeline harvests tutorial text, structures it into step-by-step instructions, and validates via VLM execution.
- **Cost**: $0.55 per high-quality trajectory.
- **Transfer**: Trajectories trained on one set of sites generalize to novel sites on WebArena and ScreenSpot benchmarks.

**Relevance to us**: AgentTrek shows that *structured procedure templates* (analogous to our `.md` skills) are an effective substrate for cross-site transfer. Our skill card abstraction-first summaries serve the same role as their structured task specifications.

### Key Pattern: Abstraction-First Descriptions Enable Transfer

All successful transfer systems share one pattern: skills are indexed by **abstract procedural descriptions** rather than literal site-specific steps. Voyager uses natural language descriptions; SkillWeaver uses API docstrings; SayCan uses natural language skill names; our spec uses abstraction-first skill card summaries.

**This validates our design**: the spec's emphasis on "Navigate a retailer's order history..." over "Return an item on Amazon" is exactly the right approach.

---

## 2. Run-Local Procedure Correction / Online Plan Repair

### Problem

When a step fails during execution, the agent must (a) recover tactically and (b) update its working hypothesis about the procedure so subsequent steps benefit from the correction.

### SOTA Approaches (ranked by relevance)

#### 2a. ReCAP (Stanford/MIT, Oct 2025)

**Most relevant for our replanning design.** Recursive Context-Aware Reasoning and Planning.

- **Recursive decomposition**: Tasks decomposed into subtasks; each subtask outcome is re-injected into the parent context.
- **Backtracking**: When a subtask fails, the parent receives the failure signal and revises the remaining plan.
- **Shared context**: All recursion depths share one LLM context, so high-level goals and low-level execution stay aligned.
- **Key advantage over ReAct**: ReAct gets trapped in loops; ReCAP detects failure signals, backtracks, and replans.
- **Cost**: ~3x ReAct cost per run, but significantly higher success rate.

**Relevance to us**: ReCAP's "re-inject subtask outcome into parent context" is exactly what our derived procedure patch mechanism does. The key lesson is that **the correction must flow back up to the procedure level**, not just fix the immediate next step. Our spec already mandates this ("replanning must return both next steps AND a patch to the derived procedure").

#### 2b. Reflexion (Shinn et al., NeurIPS 2023)

**Foundational for verbal self-correction.**

- **Verbal reinforcement**: After each episode, the agent generates a natural language reflection about what went wrong and stores it.
- **Memory injection**: Reflections are injected as context in the next attempt.
- **No weight updates**: All learning happens through in-context prompt augmentation.

**Known failure mode**: Degeneration-of-thought -- the agent repeats the same flawed reasoning even when explicit failures are identified. Mitigated by external verification signals.

**Relevance to us**: Our observation distiller (`SkillDistiller`) already implements the Reflexion pattern for post-run learning. The derived procedure patch extends this to **within-run** correction, which Reflexion does not do.

#### 2c. ExpeL (AAAI 2024)

**Experience extraction from trial-and-error.**

- **Two learning modes**: (1) Store successful trajectories for episodic recall; (2) Extract high-level insights (rules/patterns) from success/failure pairs.
- **Insight extraction**: LLM compares successful vs. failed trajectories and generates generalizable rules.
- **No parametric updates**: All learning through context augmentation.

**Relevance to us**: ExpeL's insight extraction is analogous to our post-run observation distillation. Their key innovation -- comparing success/failure *pairs* -- suggests our distiller should receive both the original failed attempts and the successful recovery path, not just the final trace.

#### 2d. Plan-and-Act (2025)

**Dual-agent architecture for web tasks.**

- **Planner agent**: Generates high-level plan.
- **Actor agent**: Executes steps and reports back.
- **Replanning**: The planner replans at each step based on actor feedback.
- **Training data generation**: The framework can generate training data from task execution.

**Relevance to us**: Our orchestrator already follows this pattern (planner + actuator + verifier). Plan-and-Act validates the architecture. The lesson is that replanning at *every* step (not just on failure) can improve success but at higher cost. Our current "replan on verification failure" is the right default; "replan every step" should be a configurable option for high-stakes tasks.

#### 2e. WebATLAS (NeurIPS 2025 Workshop)

**Experience-driven memory with action simulation.**

- **Planner-Actor-Critic loop**: Planner decomposes tasks; Actor proposes candidates; Critic simulates outcomes and picks the safest.
- **Cognitive map**: Persistent memory built from interaction experience, queried on demand.
- **Hypothetical rollouts**: Before acting, the agent simulates candidate actions in "cognitive space."
- **63% success on WebArena-Lite** (vs. 53.9% prior SOTA), no fine-tuning needed.

**Relevance to us**: WebATLAS's "simulate before acting" pattern could enhance our verification step -- instead of only verifying *after* execution, we could optionally pre-verify actions using LLM simulation. However, this adds latency and cost. For MVP, our post-execution verification is sufficient.

### Key Pattern: Corrections Must Persist Within the Run

All successful online correction systems share one principle: **corrections must be stored and re-injected into subsequent decisions within the same run.** Stateless replanning (replanning from scratch each time) loses learned corrections. This directly validates our spec's derived procedure mechanism.

### Common Failure Mode: Degeneration-of-Thought

Reflexion and ReAct both suffer from this: the agent recognizes a failure but generates the same flawed approach. **Mitigation**: Use external verification signals (our 3-tier verifier) rather than relying solely on LLM self-assessment. Our architecture already handles this correctly.

---

## 3. Skill/Knowledge Promotion and Curation

### Problem

After a run, the system must decide what to remember: nothing, an observation note, a parent skill patch, a new sibling skill, or a new ancestor skill. Promoting too aggressively pollutes the library; promoting too conservatively loses learning.

### SOTA Approaches (ranked by relevance)

#### 3a. SkillWeaver's Skill Honing (Apr 2025)

**Most relevant.** After synthesizing a skill API, SkillWeaver:

1. Generates test cases automatically.
2. Runs the skill against test cases.
3. Debugs and refines using environmental feedback.
4. Only promotes skills that pass test cases.

**Quality gate**: Test-case-based verification before library entry. 26.1% of community-contributed skills contain vulnerabilities (per the Agent Skills survey, arXiv 2602.12430), validating the need for quality gates.

**Relevance to us**: We should require at minimum one successful re-execution before promoting a derived procedure to the canonical library. The spec's "conservative promotion" rule is correct, and SkillWeaver shows *how* to implement it: automated test generation + re-execution.

#### 3b. Voyager's Self-Verification Gate

**Simple and effective.** Voyager only adds a skill to the library when GPT-4 confirms task completion. No explicit test suite -- just LLM-as-judge on the execution outcome.

**Limitation**: Single-sample verification. A skill might pass once by luck.

**Relevance to us**: For MVP, LLM-as-judge (our existing verifier) is sufficient as a first gate. For long-term, we should track success count (`n >= 2` successful runs before promotion).

#### 3c. ExpeL's Dual Learning Modes

**Insight vs. trajectory storage.** ExpeL distinguishes:

- **Episodic memory**: Raw successful trajectories stored for retrieval.
- **Semantic memory**: Generalized insights/rules extracted by comparing success/failure pairs.

**Promotion rule**: Insights are only extracted when there are both successful *and* failed examples to compare.

**Relevance to us**: Our spec's three-level learning (observation / session / library) maps well:
- Observation = ExpeL's insight extraction
- Session = episodic trajectory storage
- Library = promoted canonical skill

#### 3d. Agent Skills Survey (arXiv 2602.12430, Feb 2026)

**Comprehensive systematization.** Defines:

- **Seven-stage lifecycle**: Draft -> Review -> Published -> Deprecated -> Archived -> etc.
- **Four trust tiers**: Untrusted -> Verified -> Trusted -> Core, with gates between each tier.
- **Applicability gates**: Pre-conditions that must be satisfied before a skill can execute.
- **LLM-mediated routing**: LLMs decide skill applicability, not just keyword matching.

**Key finding**: Curated skills provide *quantifiable improvement* in agent success rates compared to self-generated ones. This means our canonical library should remain human-reviewed (or at least LLM-reviewed with high confidence thresholds).

**Relevance to us**: The four-tier trust model maps to our promotion pipeline:
- Untrusted = run-local derived procedure
- Verified = candidate after successful re-execution
- Trusted = promoted to canonical library
- Core = stable, well-tested skill

#### 3e. AppAgent v2's RAG-Based Knowledge Management

**Practical implementation.** AppAgent v2 maintains a knowledge base of prior trajectories and documentation, retrievable via RAG. Updates are incremental -- new trajectories augment rather than replace.

**Relevance to us**: Our JSONL-based `SkillExperienceStore` already implements this pattern. AppAgent v2 validates the approach of incremental knowledge accumulation without destructive updates.

### Key Pattern: Conservative Promotion with Evidence Thresholds

All successful systems gate promotion by evidence strength:
- Voyager: LLM verification
- SkillWeaver: Test-case execution
- ExpeL: Success/failure pair comparison
- Agent Skills: Four-tier trust model

**Our spec is aligned**: "Low-confidence outcomes should remain episodic instead of polluting the canonical library." For implementation, we should require `n >= 2` successful runs + LLM confidence >= 0.8 before promotion.

### Common Failure Mode: Library Pollution

When promotion thresholds are too low, the library fills with narrow, fragile, or contradictory skills. Voyager partially suffers from this (no explicit curation after initial verification). SkillWeaver mitigates it with test-based honing.

**Mitigation for us**: Keep the spec's librarian LLM role. After each promotion candidate, the librarian should check for conflicts with existing skills and merge/supersede rather than duplicate.

---

## 4. Top-k Retrieval for LLM-Based Routing

### Problem

Given a user prompt, retrieve the top 3 most relevant skills and label each as direct/analogical/fallback. For small libraries, send all cards to the LLM. For large libraries, use a cheap shortlist stage before LLM reranking.

### SOTA Approaches (ranked by relevance)

#### 4a. RankRAG (NeurIPS 2024)

**Unified context ranking + generation.** A single LLM instruction-tuned for both reranking retrieved contexts and generating answers.

- **Key finding**: Adding a small fraction of ranking data to instruction tuning makes LLMs surprisingly good rerankers.
- **Outperforms**: Dedicated reranking models (cross-encoders) while also generating the answer.
- **Optimal k**: Around 10 retrieved contexts for long-document QA; accuracy saturates beyond that.

**Relevance to us**: For our small library (dozens of skills), sending all skill cards to one LLM call is exactly the right approach. RankRAG validates that LLMs can both rank *and* reason about applicability in a single call. We don't need a separate embedding stage for MVP.

#### 4b. Tool-to-Agent Retrieval (arXiv 2511.01854, Nov 2025)

**Shared vector space for tools and agents.** Embeds both tools and their parent agents, traverses metadata relationships for retrieval.

- **Granular retrieval**: Tool-level or agent-level, avoiding context dilution from chunking many tools together.
- **Metadata relationships**: Tools linked to agents via explicit metadata, not just embedding similarity.

**Relevance to us**: For large-library retrieval (Phase 5 in our spec), this pattern -- embedding skill cards in a shared vector space with metadata relationships (parent/child/sibling) -- is the right architecture. Not needed for MVP.

#### 4c. LLM-as-Reranker (Production Pattern, 2024-2025)

**Practical guide for production RAG.** Use a cheap first-stage retriever (BM25, embedding search) to get 20-50 candidates, then use an LLM to rerank the top k.

- **Speed**: LLM-based rerankers engineered to be 5x faster while maintaining reliability.
- **Trade-off**: Smaller k compromises recall; larger k introduces noise. Sweet spot is 10-20 candidates for LLM reranking.

**Relevance to us**: This directly validates our spec's Stage B retrieval strategy. For MVP (Stage A), we skip the first-stage retriever and send all cards. For scaling, we add BM25/embedding shortlist -> LLM rerank.

#### 4d. Agentic Skills Routing (arXiv 2602.12430)

**LLM-mediated routing with applicability gates.**

- **Pre-flight checks**: Before routing to a skill, verify its applicability conditions are met (correct OS, required apps installed, etc.).
- **LLM routing**: LLM scores skill relevance, but the routing is gated by deterministic pre-conditions.
- **Hybrid**: Deterministic filters first, then LLM semantic ranking.

**Relevance to us**: We already implement OS-based filtering in `_os_matches()`. The pattern of "deterministic pre-filter -> LLM semantic ranking" is exactly right. For skill cards, we should add pre-conditions (required_apps, required_os) as deterministic filters before the LLM ranking step.

### Key Pattern: LLM-Heavy Routing is Correct for Small Libraries

All evidence confirms that for libraries of dozens to low hundreds of skills, sending all compact cards to an LLM for ranking + labeling in a single call is:
- Simpler than building an embedding pipeline
- More accurate for *procedural applicability* (not just semantic similarity)
- Fast enough (single LLM call)

**This directly validates our spec's approach.** Only add a shortlist stage when the library exceeds what fits comfortably in one prompt (~100-200 compact cards).

### Common Failure Mode: Premature Optimization of Retrieval

Building embedding pipelines, vector databases, and multi-stage retrieval for a small library adds complexity without benefit. Multiple papers (RankRAG, the Agent Skills survey) show that LLM-based ranking outperforms pure embedding retrieval for procedural applicability.

**Mitigation**: Resist adding retrieval infrastructure until the library exceeds ~100 skills. Our spec already specifies this correctly.

---

## Cross-Cutting Patterns

### Pattern 1: Three-Phase Skill Lifecycle

Every successful system follows the same lifecycle:
1. **Discovery/Creation**: Skill proposed during execution (Voyager, SkillWeaver) or from external sources (AgentTrek)
2. **Verification/Honing**: Skill tested and refined before promotion (Voyager self-verification, SkillWeaver test cases)
3. **Promotion/Curation**: Verified skill added to library with quality gates (trust tiers, confidence thresholds)

Our spec captures this as: run-local derived procedure -> post-run distillation -> librarian promotion.

### Pattern 2: Separation of Tactical and Strategic Correction

- **Tactical** (within-run): Fix the immediate problem, update the working procedure (ReCAP backtracking, our derived procedure patch)
- **Strategic** (post-run): Decide what to remember, what to promote, what to discard (ExpeL insight extraction, our distiller + librarian)

These must be separate systems. Mixing them risks either corrupting the canonical library (too aggressive) or losing within-run corrections (too conservative).

### Pattern 3: LLM-as-Judge for Quality Gating

Voyager, SkillWeaver, ExpeL, and the Agent Skills framework all use LLM-based evaluation as a quality gate. The key is using the LLM to evaluate *outcomes*, not just *plans*.

Our 3-tier verifier already serves this role for within-run verification. For promotion, the librarian LLM serves the same role.

### Pattern 4: Abstraction-First Indexing Enables Transfer

Skills indexed by abstract descriptions transfer better than skills indexed by literal steps. This is the single most important design decision for analogical reuse.

Our spec's skill card format with abstraction-first summaries is correct and well-supported by the literature.

---

## Recommended Approach for Our Context

Based on the SOTA research, the spec in `docs/features/adaptive-skill-system/adaptive-skill-system.md` is **well-aligned with current best practices**. The following refinements are recommended:

### MVP Refinements

1. **Skill cards**: Keep compact JSON format. Use abstraction-first summaries (already specified). Add `required_apps` and `required_os` as deterministic pre-filters.

2. **Top-k routing**: Send all cards to LLM in one call. Ask for top-3 with direct/analogical/fallback labels and confidence scores. Do NOT build embedding infrastructure yet.

3. **Derived procedure**: Implement as a structured scratchpad (already specified). Ensure corrections flow from replan -> derived procedure -> subsequent replans (ReCAP pattern).

4. **Replan contract**: Replans should return both (a) next steps and (b) procedure patches. Feed the *updated* derived procedure into all subsequent LLM calls within the run.

5. **Post-run distillation**: Continue using `SkillDistiller`. Enhancement: feed both failed *and* successful trajectory segments for comparison (ExpeL pattern).

### Post-MVP Refinements

6. **Promotion gate**: Require `n >= 2` successful runs before promotion. Use LLM-as-judge (librarian) to check for conflicts with existing skills.

7. **Skill honing**: Before promoting a derived procedure, generate test scenarios and verify the procedure works on them (SkillWeaver pattern). This can be LLM-simulated rather than requiring real execution.

8. **Large-library retrieval**: Only when library exceeds ~100 skills, add BM25/embedding shortlist -> LLM rerank (Stage B). Not needed for MVP.

### What NOT to Do

- Do not build embedding infrastructure for retrieval (premature for our library size)
- Do not use pure embedding similarity for skill selection (misses procedural applicability)
- Do not allow automatic promotion without verification gates (library pollution risk)
- Do not replan statelessly -- always include the derived procedure in replan context
- Do not mix tactical and strategic correction (run-local vs. post-run must be separate)

---

## Failure Modes to Watch

| Failure Mode | Seen In | Mitigation |
|---|---|---|
| Degeneration-of-thought | Reflexion, ReAct | Use external verification (our 3-tier verifier), not just LLM self-assessment |
| Library pollution | Voyager (partial) | Conservative promotion: n>=2 successes + librarian review |
| Premature retrieval infrastructure | Common in RAG systems | LLM-only routing until library exceeds ~100 skills |
| Stateless replanning | Naive plan-and-execute | Derived procedure carries corrections across replans |
| Analogical skills treated as literal | Common in plan transfer | Router labels direct vs. analogical; planner instructions explicit |
| Single-sample verification for promotion | Voyager | Require multiple successful runs before promotion |

---

## References

### Analogical Transfer and Skill Libraries
- [Voyager: An Open-Ended Embodied Agent with Large Language Models](https://voyager.minedojo.org/) (Wang et al., 2023)
- [SkillWeaver: Web Agents can Self-Improve by Discovering and Honing Skills](https://arxiv.org/abs/2504.07079) (OSU NLP Group, Apr 2025)
- [SayCan: Grounding Language in Robotic Affordances](https://say-can.github.io/) (Google, 2022)
- [Inner Monologue: Embodied Reasoning through Planning with Language Models](https://innermonologue.github.io/) (Google, 2022)
- [DEPS: Describe, Explain, Plan and Select](https://arxiv.org/abs/2302.01560) (Wang et al., 2023)
- [AgentTrek: Agent Trajectory Synthesis via Guiding Replay with Web Tutorials](https://arxiv.org/abs/2412.09605) (Dec 2024)

### Online Plan Repair and Self-Correction
- [ReCAP: Recursive Context-Aware Reasoning and Planning](https://arxiv.org/abs/2510.23822) (Stanford/MIT, Oct 2025)
- [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366) (Shinn et al., 2023)
- [ExpeL: LLM Agents Are Experiential Learners](https://arxiv.org/abs/2308.10144) (AAAI 2024)
- [Plan-and-Act: Improving Planning of Agents for Long-Horizon Tasks](https://arxiv.org/abs/2503.09572) (2025)
- [WebATLAS: An LLM Agent with Experience-Driven Memory and Action Simulation](https://arxiv.org/abs/2510.22732) (NeurIPS 2025 Workshop)

### Skill Promotion and Curation
- [Agent Skills for Large Language Models: Architecture, Acquisition, Security, and the Path Forward](https://arxiv.org/abs/2602.12430) (Feb 2026)
- [Agent Skill Framework: Perspectives on Small Language Models in Industrial Environments](https://arxiv.org/abs/2602.16653) (Feb 2026)
- [AppAgent v2: LLM-Based Multimodal Agent with RAG Knowledge Management](https://github.com/showlab/Awesome-GUI-Agent)

### Top-k Retrieval and Routing
- [RankRAG: Unifying Context Ranking with Retrieval-Augmented Generation in LLMs](https://arxiv.org/abs/2407.02485) (NeurIPS 2024)
- [Tool-to-Agent Retrieval: Bridging Tools and Agents](https://arxiv.org/abs/2511.01854) (Nov 2025)
- [Using LLMs as a Reranker for RAG: A Practical Guide](https://fin.ai/research/using-llms-as-a-reranker-for-rag-a-practical-guide/)

### GUI Agent Memory and Learning
- [CogAgent: A Visual Language Model for GUI Agents](https://openaccess.thecvf.com/content/CVPR2024/papers/Hong_CogAgent_A_Visual_Language_Model_for_GUI_Agents_CVPR_2024_paper.pdf) (CVPR 2024)
- [MGA: Memory-Driven GUI Agent for Observation-Centric Interaction](https://arxiv.org/abs/2510.24168) (2025)

---

## RECOMMENDATION: PROCEED

**Verdict: The spec is sound and well-aligned with SOTA. Proceed with implementation.**

The design in `docs/features/adaptive-skill-system/adaptive-skill-system.md` correctly captures the key patterns from the research literature:

1. **Abstraction-first skill cards** for analogical transfer (validated by Voyager, SkillWeaver, AgentTrek)
2. **LLM-heavy top-k routing** over embedding-only retrieval (validated by RankRAG, Agent Skills survey)
3. **Run-local derived procedures** with corrections flowing across replans (validated by ReCAP, Reflexion)
4. **Conservative post-run promotion** with quality gates (validated by SkillWeaver, Voyager, Agent Skills trust tiers)
5. **Separation of tactical and strategic correction** (validated by ExpeL, Reflexion)

No pivot needed. The spec does not reinvent the wheel -- it synthesizes proven patterns into a coherent architecture appropriate for a macOS desktop automation agent with a small-to-medium skill library.

**Minor additions worth considering**:
- SkillWeaver-style test-case honing before promotion (post-MVP)
- ExpeL-style success/failure pair comparison in the distiller
- Trust tier labeling on promoted skills (untrusted -> verified -> trusted)
- `n >= 2` success threshold before promotion

None of these block MVP. All are additive refinements.
