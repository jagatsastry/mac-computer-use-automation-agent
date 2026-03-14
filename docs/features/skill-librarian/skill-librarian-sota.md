# Skill Librarian: State-of-the-Art Research

## Problem Landscape

Our automation agent uses `.md` skill templates to guide task execution. After each run, a **SkillDistiller** extracts observations (structured as `SkillObservation` with category, condition, recommendation, rationale, and confidence) and appends them to JSONL sidecar files via `SkillExperienceStore`. These observations are injected into LLM context at runtime but **never update the canonical skill files**.

This is the "passive accumulation" anti-pattern identified across the literature: observations pile up in sidecars, context windows bloat, and proven insights remain second-class citizens. We need a **Librarian** component that evaluates accumulated observations and promotes high-confidence ones back into skill templates.

### Core Challenges

1. **When to promote**: How much evidence is "enough" to justify modifying a canonical file?
2. **What to promote**: Not all observations generalize; some are context-specific.
3. **How to avoid degradation**: Bad promotions can break working skills.
4. **How to rollback**: Promoted changes that hurt performance must be reversible.
5. **Lineage tracking**: Understanding where a skill modification came from.

---

## SOTA Approaches (Ranked by Relevance)

### 1. MACLA: Bayesian Procedural Memory (Most Relevant)

**Paper**: "Learning Hierarchical Procedural Memory for LLM Agents through Bayesian Selection and Contrastive Refinement" (Dec 2025)
**Source**: [arxiv.org/abs/2512.18950](https://arxiv.org/abs/2512.18950)

MACLA is the closest match to our problem. It maintains a frozen LLM and stores all adaptation in external procedural memory -- exactly our architecture.

**Key mechanisms**:
- **Beta posterior tracking**: Each procedure maintains `Beta(alpha, beta)` where `alpha` = successes, `beta` = failures. Reliability = `alpha / (alpha + beta)`. This gives a principled, incremental confidence measure.
- **Contrastive refinement**: Activates after accumulating >= 3 successes AND >= 3 failures. An LLM compares successful vs. failed contexts to identify discriminating preconditions, action repairs, and postcondition refinements.
- **Procedure specialization**: When distinct execution modes are detected during refinement, procedures split into variants (skill lineage).
- **Utility-based pruning**: Multi-factor score combining reliability (0.5 weight), frequency (0.3), and recency (0.2). Low-utility procedures are removed.

**Key thresholds**:
| Parameter | Value | Purpose |
|-----------|-------|---------|
| theta_dup | 0.85 | Cosine similarity for deduplication |
| theta_conf | 0.7 | Minimum confidence for procedure selection |
| theta_meta | 15% | Frequency threshold for meta-procedure formation |
| n_min | 3+3 | Min successes + failures before refinement |

**Relevance to us**: Direct mapping. Our `SkillObservation.confidence` maps to their Beta posterior mean. Their contrastive refinement is what our Librarian should do: compare success/failure patterns across observations before promoting changes.

---

### 2. ReMe: Dynamic Procedural Memory Lifecycle (Highly Relevant)

**Paper**: "Remember Me, Refine Me: A Dynamic Procedural Memory Framework for Experience-Driven Agent Evolution" (Dec 2025)
**Source**: [arxiv.org/abs/2512.10696](https://arxiv.org/abs/2512.10696)

ReMe directly addresses our "passive accumulation" problem with three mechanisms:

**Multi-faceted distillation** (maps to our SkillDistiller):
- Success pattern recognition
- Failure trigger analysis
- Comparative insight generation (success vs. failure trajectories)
- LLM-as-Judge validation before storage

**Utility-based refinement** (what our Librarian needs):
- Pruning formula: `remove(E) = 1[u(E)/f(E) <= beta]` if `f(E) >= alpha`, else 0
- `alpha = 5` (minimum 5 retrievals before considering removal)
- `beta = 0.5` (prune when success rate < 50%)
- Only successful trajectories are distilled into experiences

**Memory format**: Each experience is `(omega, e, kappa, c, tau)` = (usage scenario, content, keywords, confidence, tools). Stored in a vector database with scenario embeddings for retrieval.

**Key result**: ReMe "corrects 17 baseline-specific errors while introducing only 2 new ones" -- an 8.5:1 improvement-to-regression ratio. This is the kind of metric we should track.

---

### 3. ExpeL: Experiential Learning with Insight Operations (Highly Relevant)

**Paper**: "ExpeL: LLM Agents Are Experiential Learners" (AAAI 2024)
**Source**: [arxiv.org/abs/2308.10144](https://arxiv.org/abs/2308.10144)

ExpeL's insight management is the simplest and most directly applicable pattern:

**Operations on insight pool**:
- **ADD**: New insight discovered from trajectory analysis
- **EDIT**: Modify existing insight for clarity/accuracy
- **UPVOTE**: Confirm an existing insight (increment importance)
- **DOWNVOTE**: Contradict an existing insight (decrement importance)

**Insight lifecycle**:
- New insights start with importance count = 2
- UPVOTE/EDIT increment; DOWNVOTE decrements
- Insights reaching importance = 0 are automatically removed
- Cross-task learning: insights from one task transfer to others

**Relevance to us**: This is essentially what our Librarian should do. Observations in JSONL sidecars are the raw experiences. The Librarian should run ADD/UPVOTE/DOWNVOTE/EDIT operations, and promote insights above a threshold into the canonical skill file.

---

### 4. EvolveR: Self-Distillation with Principled Pruning (Relevant)

**Paper**: "EvolveR: Self-Evolving LLM Agents through an Experience-Driven Lifecycle" (Oct 2025)
**Source**: [arxiv.org/abs/2510.16079](https://arxiv.org/abs/2510.16079)

**Self-distillation**: The agent analyzes its own trajectories and distills "the core strategic insight into a concise natural language statement" -- guiding principles from successes, cautionary principles from failures. Directly analogous to our SkillDistiller.

**Structured repository**: Each principle has (1) natural language description + (2) structured knowledge triples. Stored with embedding for semantic retrieval.

**Quality gating**:
- Similarity threshold `theta_sim = 0.85` for deduplication
- Performance score: `s(p) = (c_succ(p) + 1) / (c_use(p) + 2)` (Laplace-smoothed success rate)
- Pruning threshold `theta_prune = 0.3` -- principles below this score are removed
- Novel principles are added; semantically equivalent ones merge

**Relevance**: The performance score formula is simple and effective. We could track `c_succ` and `c_use` per observation and promote when score exceeds a threshold.

---

### 5. Voyager: Skill Library with Self-Verification (Relevant Pattern)

**Paper**: "Voyager: An Open-Ended Embodied Agent with Large Language Models" (2023)
**Source**: [voyager.minedojo.org](https://voyager.minedojo.org/)

Voyager's skill library is the foundational reference for executable skill management:

**Skill verification before promotion**:
- GPT-4 acts as a critic, evaluating whether a generated program achieves the stated objective
- Only verified skills enter the library
- Failed skills trigger iterative refinement with environment feedback

**Skill format**: Executable code with natural language descriptions, indexed by description embeddings for retrieval. Skills are "temporally extended, interpretable, and compositional."

**Key insight for us**: Skills should only be modified after verification, not just observation. Our Librarian should verify that proposed changes don't break existing behavior before promoting them.

---

### 6. OpenAI Self-Evolving Agents Cookbook (Practical Reference)

**Source**: [developers.openai.com/cookbook/.../autonomous_agent_retraining](https://developers.openai.com/cookbook/examples/partners/self_evolving_agents/autonomous_agent_retraining/)

A practical retraining loop with concrete thresholds:

**Promotion gates**:
- At least 75% of graders pass individually, OR average score >= 0.85
- Maximum 3 optimization attempts per section
- Failed promotions retain the latest version and alert engineers

**Three optimization strategies** (progressive autonomy):
1. Manual iteration (human reviews, clicks "Optimize")
2. Semi-automated (humans diagnose; system suggests)
3. Fully automated (orchestration loop runs evals, detects failures, iterates)

**Relevance**: The graduated autonomy model (manual -> semi-auto -> full-auto) is the right adoption path for our Librarian. Start conservative with human approval.

---

### 7. CUA-Skill: Parameterized Execution Graphs (Architecture Reference)

**Paper**: "CUA-Skill: Develop Skills for Computer Using Agent" (Jan 2026)
**Source**: [arxiv.org/abs/2601.21123](https://arxiv.org/abs/2601.21123)

CUA-Skill encodes skills as parameterized execution graphs with typed preconditions and composability rules. Achieves 57.5% success rate on WindowsAgentArena (SOTA).

**Relevant pattern**: Skills have explicit composition graphs -- knowing which skills can chain together and under what conditions. This is relevant for our lineage tracking: when a skill is modified, its composition relationships should be tracked.

---

### 8. Agent Lineage Evolution (ALE): Generational Management

**Source**: [danieltan.weblog.lol/2025/06/agent-lineage-evolution](https://danieltan.weblog.lol/2025/06/agent-lineage-evolution-a-novel-framework-for-managing-llm-agent-degradation)

ALE manages agent versions as generational lineages rather than persistent entities:

**Succession triggers**:
- Quality degradation: Two consecutive sub-6/10 responses trigger succession
- Pattern repetition: Repeating documented predecessor failures triggers immediate succession

**Behavioral inheritance**: Successor agents receive explicit warnings about predecessor failures -- "behavioral overrides" that prevent regression.

**Relevance**: When our Librarian modifies a skill, the old version becomes a "predecessor" with documented failure patterns. The new version should inherit warnings about what not to do.

---

## Key Patterns and Algorithms

### Pattern 1: Bayesian Confidence Accumulation

The dominant approach across MACLA, EvolveR, and ReMe. Track success/failure counts per observation and compute confidence:

```
confidence = (successes + 1) / (successes + failures + 2)  # Laplace-smoothed
```

**Promotion threshold**: Promote when confidence > 0.75 AND total observations >= 5. This is consistent across MACLA (theta_conf = 0.7), ReMe (alpha = 5, beta = 0.5), and EvolveR (theta_prune = 0.3 for removal, implying >= 0.7 for promotion).

### Pattern 2: Contrastive Refinement Before Promotion

MACLA's core insight: don't promote raw observations. Before modifying the skill, compare successful and failed executions to extract discriminating conditions. This produces more targeted modifications.

**Algorithm**:
1. Collect observations with the same category
2. Separate into success-correlated and failure-correlated groups
3. Use LLM to identify discriminating patterns
4. Generate specific, conditioned modifications (not blanket changes)

### Pattern 3: ExpeL-style Importance Voting

Track importance per observation across runs:
- Same observation recurs in new run with success -> UPVOTE
- Same observation contradicted by new evidence -> DOWNVOTE
- Observation refined with more specificity -> EDIT
- Importance reaches 0 -> remove; importance exceeds threshold -> promote

### Pattern 4: Version-Controlled Skill Lineage

Every skill modification creates a versioned entry:
```
skill_v1.md (original)
  -> skill_v2.md (promoted observation: "click confirm after 2s delay")
    -> skill_v3.md (refined: "wait for loading spinner to disappear, then click confirm")
```

Git provides natural lineage tracking. Each promotion is a commit with structured metadata.

### Pattern 5: Graduated Autonomy

Start with human-in-the-loop approval for all promotions. As confidence in the system grows, relax:
1. **Stage 1**: Librarian proposes changes, human approves
2. **Stage 2**: Auto-promote high-confidence changes (>= 0.9), human reviews others
3. **Stage 3**: Auto-promote above threshold, auto-reject below threshold, human reviews edge cases

---

## Common Failure Modes

### 1. Overfitting to Specific Contexts

**Problem**: An observation that works for one app/site gets promoted as a general rule.
**Example**: "Always wait 3 seconds after clicking" -- true for a slow app, catastrophic as a universal rule.
**Mitigation**: Observations must include context conditions. Promote the condition+recommendation pair, not just the recommendation. MACLA's precondition refinement addresses this.

### 2. Cascading Degradation

**Problem**: A bad promotion introduces a subtle failure that generates more observations that reinforce the bad change.
**Cited in**: Microsoft's "Diagnosing instability in production-scale agent RL" -- divergence appears in tool-conditioned contexts while aggregate metrics remain stable.
**Mitigation**: Track per-skill success rate before and after promotion. Automatic rollback if success rate drops > 10% in the window after promotion.

### 3. The Intervention Paradox

**Paper**: "The Intervention Paradox: Accurate Failure Prediction Does Not Imply Effective Failure Prevention" (2026)
**Source**: [arxiv.org/abs/2602.03338](https://arxiv.org/abs/2602.03338)
**Problem**: Intervention (modifying skills) can disrupt already-correct trajectories. Simple heuristics like "avoid early intervention" match learned critic behavior.
**Mitigation**: Never promote changes that affect steps already succeeding. Only modify steps/sections related to observed failures.

### 4. Context Window Pollution

**Problem**: Too many observations injected at runtime degrade LLM performance.
**Cited in**: "Agent Drift: Quantifying Behavioral Degradation in Multi-Agent LLM Systems" (Jan 2026)
**Mitigation**: This is precisely why promotion matters -- promoted observations become part of the skill text and don't need separate injection. Cap injected observations (our current `top_for_context` with `limit=5` is correct).

### 5. Semantic Drift Through Editing

**Problem**: Repeated small edits to skill text gradually drift from the original intent.
**Mitigation**: Maintain the original skill text as an immutable "anchor." All modifications are additive sections (e.g., "## Learned Tips") that can be independently managed. Never modify the original Steps section programmatically.

---

## Recommended Approach for Our Context

Given our existing architecture (`SkillDistiller` -> `SkillExperienceStore` -> JSONL sidecars -> runtime injection), the Librarian should be a **periodic batch process** that:

### Architecture

```
JSONL Sidecars (observations)
    |
    v
SkillLibrarian.evaluate()
    |-- Group observations by (skill, category)
    |-- Compute Bayesian confidence per group
    |-- Filter: confidence >= 0.75 AND count >= 5
    |-- Contrastive check: compare success/failure contexts
    |-- Generate proposed skill modification (LLM)
    |
    v
Promotion Proposal
    |-- Validate: diff against current skill text
    |-- Gate: human approval (Stage 1) or auto (Stage 2+)
    |-- Write: append to "## Learned Tips" section in skill .md
    |-- Commit: git commit with structured metadata
    |-- Archive: mark promoted observations in JSONL
    |
    v
Post-Promotion Monitoring
    |-- Track success rate for N subsequent runs
    |-- Auto-rollback if success rate drops > 10%
    |-- Update observation confidence based on new evidence
```

### Key Design Decisions

1. **Additive-only modifications**: Never edit the human-authored Steps section. Add a `## Learned Tips` section at the end of skill files. This is reversible and preservable.

2. **Bayesian confidence with Laplace smoothing**: `confidence = (alpha + 1) / (alpha + beta + 2)` where alpha = corroborating runs, beta = contradicting runs. Promote at >= 0.75 with >= 5 total observations.

3. **Contrastive refinement before promotion**: Don't promote raw observations. Use LLM to compare success/failure contexts and generate a conditioned tip (MACLA pattern).

4. **Git-native lineage**: Each promotion is a git commit. Rollback = `git revert`. Lineage = `git log -- skills/library/<skill>.md`. No custom versioning needed.

5. **Graduated autonomy**: Start with `--dry-run` mode that proposes but doesn't apply. Then human-approved mode. Then auto-promote for high confidence.

6. **Observation lifecycle**: After promotion, mark observations as "promoted" in the JSONL. Stop injecting promoted observations at runtime (they're now in the skill text). Continue tracking new observations against the updated skill.

### Confidence Formula

Adapted from MACLA and EvolveR, tuned for our context:

```python
def promotion_score(obs_group: list[SkillObservation]) -> float:
    """Bayesian score for a group of related observations."""
    alpha = sum(1 for o in obs_group if o.confidence >= 0.6)  # corroborating
    beta = sum(1 for o in obs_group if o.confidence < 0.4)    # contradicting
    score = (alpha + 1) / (alpha + beta + 2)  # Laplace-smoothed
    return score

def should_promote(obs_group: list[SkillObservation]) -> bool:
    return (
        len(obs_group) >= 5
        and promotion_score(obs_group) >= 0.75
    )
```

### Promotion Safeguards

| Safeguard | Mechanism | Source |
|-----------|-----------|--------|
| Minimum evidence | >= 5 observations before considering | ReMe (alpha=5) |
| Confidence threshold | Bayesian score >= 0.75 | MACLA (theta_conf=0.7) |
| Deduplication | Cosine similarity >= 0.85 | MACLA, EvolveR |
| Scope limitation | Only additive changes to ## Learned Tips | Intervention Paradox |
| Rollback trigger | Success rate drop > 10% post-promotion | ALE succession triggers |
| Max retries | 3 LLM attempts to generate promotion text | OpenAI Cookbook |
| Human gate (Stage 1) | All promotions require approval | OpenAI graduated autonomy |

---

## RECOMMENDATION: PROCEED

**Rationale**: The research landscape is mature and convergent. Multiple independent systems (MACLA, ReMe, ExpeL, EvolveR) have validated the core pattern of Bayesian confidence tracking + contrastive refinement + utility-based pruning. Our existing architecture (`SkillDistiller` + `SkillExperienceStore`) already implements the first half of this pattern -- we just need the second half (evaluation + promotion).

**Risk level**: LOW. The additive-only approach (appending `## Learned Tips` sections) is inherently safe and reversible. Git provides natural lineage and rollback. Starting with human-in-the-loop approval eliminates the risk of bad auto-promotions.

**Estimated complexity**: MODERATE. The core Librarian logic (group observations, compute confidence, generate promotion text, write to file) is straightforward. The LLM-based contrastive refinement adds complexity but is optional for v1.

**Suggested phasing**:
- **v1**: Simple threshold-based promotion with human approval. Group observations by category, compute Bayesian score, propose additions to `## Learned Tips`.
- **v2**: Add contrastive refinement (compare success/failure contexts before promoting).
- **v3**: Add post-promotion monitoring and auto-rollback. Graduated autonomy for high-confidence promotions.

---

## References

- [MACLA: Learning Hierarchical Procedural Memory for LLM Agents](https://arxiv.org/abs/2512.18950) -- Bayesian selection and contrastive refinement
- [ReMe: Remember Me, Refine Me](https://arxiv.org/abs/2512.10696) -- Dynamic procedural memory lifecycle
- [ExpeL: LLM Agents Are Experiential Learners](https://arxiv.org/abs/2308.10144) -- Insight extraction with ADD/UPVOTE/DOWNVOTE/EDIT
- [EvolveR: Self-Evolving LLM Agents](https://arxiv.org/abs/2510.16079) -- Experience-driven self-distillation
- [Voyager: Open-Ended Embodied Agent](https://voyager.minedojo.org/) -- Skill library with self-verification
- [Agent Skills for LLMs: Architecture, Acquisition, Security](https://arxiv.org/abs/2602.12430) -- Comprehensive skill taxonomy and trust tiers
- [CUA-Skill: Computer Using Agent Skills](https://arxiv.org/abs/2601.21123) -- Parameterized execution graphs
- [Agent Lineage Evolution](https://danieltan.weblog.lol/2025/06/agent-lineage-evolution-a-novel-framework-for-managing-llm-agent-degradation) -- Generational agent management
- [OpenAI Self-Evolving Agents Cookbook](https://developers.openai.com/cookbook/examples/partners/self_evolving_agents/autonomous_agent_retraining/) -- Practical retraining loop
- [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366) -- Self-reflection and verbal feedback
- [SEAgent: Self-Evolving Computer Use Agent](https://arxiv.org/abs/2508.04700) -- Curriculum-based skill discovery
- [The Intervention Paradox](https://arxiv.org/abs/2602.03338) -- When intervention hurts performance
- [Agent Drift](https://arxiv.org/abs/2601.04170) -- Behavioral degradation in multi-agent systems
- [Goose: Stop Unwanted Code Changes](https://block.github.io/goose/blog/2025/12/10/stop-ai-agent-unwanted-changes/) -- Practical safe modification patterns
- [ICLR 2026 MemAgents Workshop](https://openreview.net/pdf?id=U51WxL382H) -- Memory for agentic systems
