# Survey Gaps: State-of-the-Art Research

Research date: 2026-03-13

This document covers SOTA findings for 7 gaps identified from the computer use agents survey paper. Each section covers: what it is, key papers, trade-offs, implementation complexity, failure modes, and recommended approach for our codebase.

---

## 1. Set-of-Mark (SoM) Prompting

### What It Is
Overlay numbered bounding boxes/labels from segmentation or accessibility APIs onto screenshots before sending to a VLM. The model then references elements by number instead of predicting raw coordinates.

### Key Papers & Implementations
- **SoM (Yang et al., 2023)** — Microsoft. Uses SAM/SEEM segmentation to partition images into regions, overlays alphanumeric labels. GPT-4V with SoM in zero-shot outperforms SOTA fully-finetuned models on RefCOCOg. Code: [github.com/microsoft/SoM](https://github.com/microsoft/SoM).
- **OmniACT (2024)** — First benchmark for generating executable scripts from screenshots + natural language. 9.8K paired tasks across macOS, Windows, Linux, web. GPT-4 reaches only 15% of human proficiency. Uses visual grounding with numbered labels on interactive elements.
- **Magma (Yang et al., CVPR 2025)** — Foundation model for multimodal AI agents, extends SoM concepts into agentic pipelines with GPT-4o/o3/GPT-5 backbones.
- **LiteWebAgent (2025)** — Open-source SoM agent for web automation using accessibility trees with numbered labels.

### Trade-offs

| Pro | Con |
|-----|-----|
| Eliminates coordinate prediction errors entirely | Requires segmentation/AX pass before each VLM call |
| Works with any VLM (no fine-tuning needed) | Label overlay can occlude small UI elements |
| Reduces hallucinated clicks on wrong elements | SoM with segmentation marks does NOT transfer well to open-source VLMs (tested only on GPT-4V/4o) |
| Maps naturally to accessibility tree elements | Adds 200-500ms latency for segmentation + rendering |

### Implementation Complexity
**Medium.** We already have `get_accessibility_elements()` in AppleScriptActuator returning up to 20 elements. The work is: (1) render numbered labels onto screenshot, (2) modify VLM prompt to reference elements by number, (3) parse number from VLM response instead of coordinates.

### Failure Modes
- Label clutter on dense UIs (e.g., spreadsheets with 100+ cells)
- Accessibility tree misses dynamically-rendered or custom-drawn elements
- Open-source VLMs (Molmo, Qwen) may ignore or misread overlaid labels
- Numbering changes between steps can confuse multi-step plans

### Recommended Approach
**Hybrid:** Use accessibility-tree-based SoM as a primary path (elements get numbered labels), fall back to raw coordinate grounding when AX returns < 3 elements or the target isn't in the AX list. Test with Molmo before assuming label-reading works — may need to keep coordinate grounding as fallback for local models.

---

## 2. Evolving World-State Document

### What It Is
Maintain a structured text document describing current application state (open windows, form values, navigation position) that the planner reads at each step, instead of relying solely on screenshot descriptions.

### Key Papers & Implementations
- **Web Agents with World Models (Chae et al., 2024)** — Transition-focused observation abstraction. Uses Hungarian algorithm to match UI elements between states, extracts UPDATED/DELETED/ADDED diffs, generates natural language state descriptions. Fine-tuned Llama-3.1-8B as world model. On WebArena: 13.5% success (vs 9.4% baseline), +181% on GitLab, +92% on Maps.
- **ActionEngine (2026)** — State Machine Graph (SMG): nodes = application states, edges = operations. Crawling Agent builds graph offline. Distinguishes static atoms (nav bars) vs dynamic atoms (data-dependent). Reddit: 95% success vs 66% baseline, 2x faster, 11.8x cheaper. Reduces reasoning from O(N) per-step to O(1) one-shot planning.
- **Mind2Web / Online-Mind2Web** — 300 diverse tasks across 136 websites. Benchmark for grounded web agent evaluation with structured state tracking.

### Trade-offs

| Pro | Con |
|-----|-----|
| Planner sees structured context, not raw pixels | Requires building + maintaining state extraction |
| Enables diff-based reasoning ("what changed?") | World model hallucination: 42% of failures in Chae et al. are counterfactual errors (imagined elements) |
| Reduces token usage vs. full screenshot descriptions | State schema must be designed per-app-category |
| Supports replay and debugging (human-readable state log) | Dynamic content (live feeds, animations) can cause state drift |

### Implementation Complexity
**Medium-High.** The lightweight version: after each action, run AX extraction + screenshot, produce a structured diff. The full version (ActionEngine-style) requires offline crawling to build state machine graphs, which is much heavier.

### Failure Modes
- Counterfactual errors (world model predicts elements that don't exist)
- State drift in apps with heavy dynamic content
- Generic/vague predictions in high-diversity domains (e-commerce)
- Weak understanding of interactive components (dropdowns, modals)

### Recommended Approach
**Lightweight diff-based state tracking.** After each action: (1) capture AX elements + screenshot description, (2) diff against previous state, (3) append structured diff to planner context. Skip the offline crawling / fine-tuned world model — too heavy for a local-first agent. The diff format should be: `{step: N, changes: [{element, old_value, new_value}], new_elements: [...], removed_elements: [...]}`.

---

## 3. Embedding-Based Skill Retrieval

### What It Is
Replace keyword matching for skill lookup with embedding similarity — encode user intent and skill descriptions into a shared vector space, retrieve by cosine similarity.

### Key Papers & Implementations
- **ToolBench + Gorilla (Berkeley, 2023-2024)** — Gorilla: LLM fine-tuned for 1,600+ tool APIs. GRETEL framework improves Pass Rate@10 from 0.690 to 0.826 on ToolBench by adding execution-based filtering.
- **ToolGen (ICLR 2025)** — Embeds each tool as a virtual token in the LLM vocabulary. Three-stage training: tool memorization, retrieval training, agent training. Handles 47,000+ tools. Unifies retrieval and calling into generation.
- **Tool-to-Agent Retrieval (2025)** — Shared vector space for tools AND agents. +19.4% Recall@5, +17.7% over baselines. Metadata traversal from tool → parent agent for context.
- **LoSemB (2025)** — Logic-guided semantic bridging for inductive tool retrieval. Addresses the semantic-functional gap where textually similar tools may be functionally incompatible.

### Trade-offs

| Pro | Con |
|-----|-----|
| Handles paraphrased/synonym queries naturally | Requires embedding model + vector index |
| Scales to hundreds of skills without prompt bloat | Semantic similarity != functional correctness (LoSemB finding) |
| Sub-millisecond retrieval at query time | Cold start: need enough skills to justify the infrastructure |
| Supports fuzzy matching for novel tasks | Embedding drift if skill descriptions change without re-indexing |

### Implementation Complexity
**Low-Medium.** We currently use keyword matching in the SkillRegistry. Minimal version: (1) embed skill descriptions with a small local model (e.g., `all-MiniLM-L6-v2`, 80MB), (2) embed user query, (3) cosine similarity top-k. No fine-tuning needed. Libraries: `sentence-transformers` or `fastembed`.

### Failure Modes
- False positives: semantically similar but wrong skill (e.g., "return Walmart order" matches "return Amazon order")
- Embedding model quality matters — small models may conflate domain concepts
- Re-ranking needed if top-k contains multiple plausible matches

### Recommended Approach
**Two-stage retrieval.** (1) Embedding similarity top-5 candidates, (2) LLM re-rank with skill metadata (trigger keywords, parameters, OS requirements). This combines the recall of embeddings with the precision of LLM judgment. Use `sentence-transformers` with `all-MiniLM-L6-v2` — runs locally, no API needed, <10ms inference.

---

## 4. Lookahead / Simulation

### What It Is
Before executing an action, predict what the screen will look like afterward. Use this prediction to evaluate action quality and potentially search over multiple action sequences.

### Key Papers & Implementations
- **ProAct (2026)** — MCTS-based lookahead distilled into natural language reasoning chains. 4B model outperforms all open-source baselines. Key insight: compress search trees into causal reasoning during training, avoid expensive MCTS at inference. Addresses "simulation drift" where internal world models diverge from reality.
- **MobileDreamer (2026)** — Textual Sketch World Model (TSWM): predicts task-relevant GUI elements as structured text, not full screenshots. Tree-of-prediction: depth 2, 3 candidates per node. +5.25% success on Android World. F1=0.76 vs 0.44 baselines. Uses order-invariant learning with optimal transport matching.
- **WebSynthesis (2025)** — World-model-guided MCTS for web trajectory synthesis. Combines world models with tree search for planning.
- **ExACT (2025)** — Reflective-MCTS (R-MCTS) for autonomous agents. Test-time exploration of decision space with reflection.

### Trade-offs

| Pro | Con |
|-----|-----|
| Catches errors before they happen (preview bad clicks) | 2-10x latency increase per action for simulation |
| Survey cites 50% improvement at search depth 5 | Requires fine-tuned world model or expensive VLM calls |
| Reduces irreversible error rate | Simulation drift: predictions diverge from reality after 2+ steps |
| Can rank multiple action candidates | Training data: need thousands of state-transition pairs |

### Implementation Complexity
**High.** Full MCTS requires a world model (fine-tuned LLM or VLM). MobileDreamer needs ~110K state-transition samples + H100 GPU training. ProAct's distillation approach is more practical but still requires offline training.

**Lightweight alternative:** 1-step text-based lookahead. Ask the planner: "If I click X, what will happen?" Compare prediction against desired postcondition. No fine-tuning, just an extra LLM call per action. Adds ~2-5s latency.

### Failure Modes
- Simulation drift beyond depth 2 (predictions compound errors)
- Counterfactual hallucination (predicting UI elements that won't appear)
- Computational cost makes real-time interaction sluggish
- Training data bias toward common UI patterns

### Recommended Approach
**1-step text-based lookahead for high-risk actions only.** Before destructive actions (delete, submit, purchase), ask the planner to predict the outcome. Compare against the postcondition. If mismatch, flag for user confirmation. Skip lookahead for routine navigation. This avoids the need for a fine-tuned world model while capturing most of the safety benefit.

---

## 5. Infeasibility Detection

### What It Is
Agent recognizes a task is impossible or blocked and reports "cannot complete" with an explanation, instead of exhausting retries or looping indefinitely.

### Key Papers & Implementations
- **GAIA Benchmark (Meta, 2023-2024)** — General AI Assistants benchmark. Tasks include ones that are intentionally infeasible. Top agents still struggle with knowing when to stop.
- **ImpossibleBench (2025)** — Creates "impossible" variants of tasks from existing benchmarks. Measures agents' "cheating rate" (passing impossible tasks via shortcuts). Introduces `flag_for_human_intervention` abort mechanism — agent submits this flag when task is recognized as impossible.
- **AgentBench (ICLR 2024)** — Tracks failure modes: "Invalid Format," "Invalid Action," "Context Limit Exceeded," "Task Limit Exceeded." Enables fine-grained failure attribution.
- **AGENTRX (2026)** — Diagnoses agent failures from execution trajectories. Defines "critical failure" as first unrecoverable error. Best automated attribution: 53.5% accuracy for failure-responsible agent, only 14.2% for pinpointing failure step.
- **Multi-Agent Failure Analysis (ICML 2025)** — Formalizes failure attribution. Key failure mode: "premature termination" (6.2%), "no/incomplete verification" (8.2%), "incorrect verification" (9.1%).

### Trade-offs

| Pro | Con |
|-----|-----|
| Saves time and tokens on truly impossible tasks | False negatives: agent quits too early on hard-but-possible tasks |
| Better user experience ("I can't do X because Y") | Hard to distinguish "stuck" from "genuinely impossible" |
| Reduces cascading failures from retrying wrong paths | Current SOTA only 53.5% accuracy at attributing failures |
| Prevents destructive retry loops | Requires calibrated confidence — most LLMs are overconfident |

### Implementation Complexity
**Low-Medium.** We already have a replan loop with retry limits. The addition: (1) track a frustration signal (same-state-after-action count, repeated failures), (2) after N same-state iterations, ask planner: "Is this task still achievable? Explain why or why not," (3) if planner says infeasible, abort with explanation.

### Failure Modes
- Premature abort on tasks that need more creative approaches
- LLM confidently declares infeasible when it's just stuck (overconfidence)
- Missing context: agent may not know what's possible on the current screen
- Infinite loops if detection threshold is set too high

### Recommended Approach
**Frustration-based detection with planner verification.** Track: (1) consecutive same-state-after-action count, (2) repeated identical action attempts, (3) replan count. When any threshold exceeds limit (e.g., 3 same-state, 2 identical replans), invoke a dedicated infeasibility check prompt. Return `ExecutionResult` with `success=False` and a structured explanation. Threshold should be configurable.

---

## 6. User Confirmation for Destructive Actions

### What It Is
Pause execution and ask the user for confirmation before irreversible actions (delete, purchase, submit form, send message).

### Key Papers & Implementations
- **EU AI Act Article 14** — High-risk AI systems must allow qualified people to interpret outputs and effectively intervene, stop, or override.
- **LangGraph (2024-2025)** — Native `interrupt()` for pausing graph execution. Human input collected (yes/no, select from options), then workflow resumes.
- **CrewAI** — `human_input` flag and HumanTool support for agent confirmation gates.
- **HumanLayer** — `@require_approval()` decorators for async approval workflows.
- **Permit.io + MCP** — Policy-driven access requests with full audit trail.
- **Lies-in-the-Loop (LITL) Attack (2025)** — Shows HITL can be subverted: malicious instructions embedded in prompts mislead users reviewing approval dialogs. Caution: confirmation UIs must show the raw action, not LLM-generated summaries.

### Implementation Patterns

| Pattern | Use Case | Latency |
|---------|----------|---------|
| **Interrupt & Resume** | Tool call approvals, long workflows | User response time |
| **Human-as-a-Tool** | Ambiguous prompts, fact-checking | User response time |
| **Approval Flows** | Role-based authorization | Async (minutes-hours) |
| **Fallback Escalation** | Failed/denied tasks → human | Async |

### Trade-offs

| Pro | Con |
|-----|-----|
| Prevents costly irreversible errors | Breaks automation flow — user must be present |
| Builds user trust in agent | Approval fatigue: users rubber-stamp after too many prompts |
| Regulatory compliance (EU AI Act) | LITL attack: malicious prompts can forge approval dialogs |
| Clear audit trail | Latency penalty for every confirmed action |

### Implementation Complexity
**Low.** Action classification is straightforward: maintain a list of destructive action verbs/patterns (delete, remove, purchase, submit, send, confirm payment). Before executing a matching action, pause and prompt the user. Our orchestrator already has a step-by-step execution loop where this gate can be inserted.

### Failure Modes
- Over-prompting leading to approval fatigue
- Under-classification missing novel destructive patterns
- LITL-style attacks if confirmation shows LLM summary instead of raw action
- Blocking automation in unattended mode

### Recommended Approach
**Action-keyword classification + configurable gate.** (1) Classify actions as destructive based on verb + context (delete, purchase, submit, send, pay, confirm). (2) Before executing, display the raw action details to user and await confirmation. (3) Support three modes: `always_confirm` (all destructive), `smart_confirm` (only high-confidence destructive), `never_confirm` (unattended). (4) Always show raw action parameters, never LLM-generated summaries (mitigates LITL). (5) Log all confirmations for audit trail.

---

## 7. Dual-Resolution Screenshots

### What It Is
Send both a low-resolution full screenshot (for global context) and a high-resolution crop around the action target (for precise grounding).

### Key Papers & Implementations
- **CogAgent (CVPR 2024)** — 18B parameter VLM with dual encoder architecture. Low-res: EVA2-CLIP-E at 224x224 (4.4B params). High-res: EVA2-CLIP-L at 1120x1120 (0.30B params). Cross-attention module fuses streams — 25x reduction in compute vs. naive concatenation. SOTA on 5 text-rich and 4 general VQA benchmarks.
- **SeeClick (2024)** — Built on Qwen-VL. GUI grounding pre-training on ScreenSpot benchmark. 53.4% average accuracy (vs 5.2% Qwen-VL baseline). Point-based (x,y) and bounding box output. Demonstrates that grounding pre-training is more important than raw resolution.
- **UGround (2024)** — Universal GUI visual grounding model with large-scale web synthetic data. 32.8 action score on OmniACT.
- **SE-GUI (2025)** — Self-evolving GUI grounding via bootstrapped visual grounding data.

### CogAgent Architecture Detail
```
Low-res stream:  EVA2-CLIP-E (4.4B) → 224x224 → 256 tokens → MLP adapter
High-res stream: EVA2-CLIP-L (0.3B) → 1120x1120 → 6400 tokens → cross-attention
                                                                    ↓
Decoder: Vicuna-7B (hidden=4096) ← cross-attention (hidden=1024) ← high-res features
```

### Trade-offs

| Pro | Con |
|-----|-----|
| Global context + local precision in one pass | Two screenshots per step (2x capture overhead) |
| CogAgent: 25x compute savings vs. naive high-res | Requires knowing WHERE to crop before cropping |
| Critical for small text/icons on high-DPI displays | Crop region selection is itself a grounding problem |
| Our codebase already has `_maybe_crop_screenshot()` | Doubles VLM token usage if sending both images |

### Implementation Complexity
**Low.** We already have `_maybe_crop_screenshot()` in `orchestrator/agent.py` that crops 512x512 above 1440px threshold, and `_validate_candidate()` that crops 200x200 pre-click. The gap is: (1) we don't send BOTH resolutions to the VLM simultaneously, and (2) the crop is post-grounding (validation), not pre-grounding.

### Failure Modes
- Wrong crop region misses the actual target
- Double-image prompt confuses models not trained for it
- Token budget exceeded when sending two images
- Crop-before-grounding is circular (need location to crop, need crop to find location)

### Recommended Approach
**Two-pass grounding with confidence gating (extends existing architecture).** (1) First pass: full screenshot at default resolution → get candidate coordinates + confidence. (2) If confidence < 0.7, crop 512x512 around candidate → second pass with crop only. (3) If confidence >= 0.7, skip crop and proceed. This extends our existing `_validate_candidate()` pattern but moves the crop into the grounding path instead of just validation. Avoids the circular problem by using the first-pass estimate for crop center.

---

## Summary: Priority Matrix

| Gap | Impact | Complexity | Recommended Priority |
|-----|--------|------------|---------------------|
| **6. User Confirmation** | High (safety) | Low | P0 — ship first |
| **5. Infeasibility Detection** | High (UX) | Low-Medium | P0 — ship first |
| **1. Set-of-Mark Prompting** | High (accuracy) | Medium | P1 — next sprint |
| **3. Embedding Skill Retrieval** | Medium (scale) | Low-Medium | P1 — next sprint |
| **7. Dual-Resolution Screenshots** | Medium (accuracy) | Low | P1 — extends existing code |
| **2. World-State Document** | Medium (planning) | Medium-High | P2 — after P1 items |
| **4. Lookahead/Simulation** | High (but costly) | High | P2 — lightweight version only |

### Rationale
- **P0**: User confirmation and infeasibility detection are safety/UX features with low implementation cost and immediate user-visible impact.
- **P1**: SoM, embedding retrieval, and dual-resolution build on existing infrastructure (AX elements, skill registry, crop logic) and improve core accuracy.
- **P2**: World-state tracking and lookahead require more infrastructure. Start with lightweight versions (diff-based state, 1-step text lookahead for destructive actions only).
