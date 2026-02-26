# Competitive Landscape: Desktop Automation Agents & UI Grounding Models

*February 2026*

---

## 1. Executive Summary

The desktop AI agent space has exploded since mid-2025. Vercept's Vy (powered by VyUI) set the bar with a purpose-built UI grounding model achieving 92% accuracy on ScreenSpot v1, but Anthropic's acquisition of Vercept in February 2026 removed it from the market. Meanwhile, a rich open-source ecosystem has emerged, spanning full agent frameworks, specialized grounding models, and infrastructure platforms.

This report maps the competitive landscape, compares architectural approaches, and identifies what our `macos-automation-agent` should adopt.

---

## 2. VyUI / Vy by Vercept (Acquired by Anthropic)

### What It Was

Vy was a native macOS desktop agent that could control any GUI application through natural language commands. VyUI was the proprietary AI model powering it — specifically trained for UI element grounding (locating elements by pixel coordinates from screenshots).

### Architecture

Vy used a four-component architecture:

| Component | Purpose | How It Worked |
|-----------|---------|---------------|
| **Intent Parser** | NLU layer | LLM-based; converts natural language to structured action specs |
| **Frontier Agents** | Task execution | Modular routines per domain with conditional logic and branching |
| **Context Monitor** | State tracking | Continuously tracks window states, selected elements, screen regions |
| **Execution Engine** | Action dispatch | Pixel-level clicks/keystrokes with real-time error detection + retry |

### VyUI Model

- **Custom-trained vision model** for UI element grounding
- Built by AI2 alumni who created Molmo (vision model with native pointing/coordinate prediction)
- Trained specifically on UI screenshot data with spatial annotations
- Deployed on Together AI's dedicated endpoints (achieved 5x inference speedup)
- **Not open-source** — proprietary, now folded into Anthropic

### Benchmark Performance

| Benchmark | VyUI | OpenAI CUA | Claude | Best Open-Source |
|-----------|------|------------|--------|------------------|
| ScreenSpot v1 | **92.0%** | 18.3% | — | UGround ~85% |
| ScreenSpot v2 | **94.7%** | 87.9% | — | — |
| ScreenSpot Pro | **63.0%** | — | — | GUI-Actor-7B |
| GroundUI Web | **84.8%** | 82.3% | — | — |
| Showdown Click | **78.5%** | — | — | — |

### Key Differentiators

1. **Purpose-built grounding model** — not a prompted general VLM
2. **Continuous context tracking** — maintained state across observations
3. **Domain-specialized agents** — different Frontier Agents for different task types
4. **On-device execution** — ran locally on Apple Silicon, macOS 14+
5. **Hybrid accessibility + vision** — used macOS accessibility protocols alongside vision

### Current Status

Acquired by Anthropic (February 2026). Vy product discontinued. Technology being integrated into Claude's computer-use capabilities.

**Sources:**
- https://www.anthropic.com/news/acquires-vercept
- https://www.geekwire.com/2026/anthropic-acquires-vercept-in-early-exit-for-one-of-seattles-standout-ai-startups/
- https://www.together.ai/customers/vercept

---

## 3. Open-Source Agent Frameworks

### 3.1 Agent S2 / S3 — Simular AI

**GitHub:** https://github.com/simular-ai/Agent-S
**Stars:** 15k+ | **Funding:** $21.5M (Felicis)
**License:** Apache 2.0

**Architecture:**

Agent S2 uses a **Generalist-Specialist compositional framework**:

```
User Instruction
      |
      v
+-----------+
|  Manager  | <-- Generalist LLM (high-level reasoning)
| (Planner) |    Decomposes tasks into subgoals
+-----+-----+    Proactive Hierarchical Planning
      |
      v
+-----------+
|  Worker   | <-- Tactical routing
| (Router)  |    Maps subgoals to grounding specialists
+-----+-----+
      |
      v
+----------------------------------+
|  Mixture-of-Grounding (MoG)     |
| +------+ +------+ +-----------+ |
| |Visual| | Text | |Structural | |
| |Expert| |Expert| |  Expert   | |
| +------+ +------+ +-----------+ |
+----------------------------------+
```

**Key Innovations:**

1. **Mixture-of-Grounding (MoG):** Uses multiple grounding experts (visual, textual, structural) — the insight that no single grounding approach works for all UI elements
2. **Proactive Hierarchical Planning:** Dynamically refines plans at multiple timescales as observations change
3. **Continual Learning Memory:** Remembers past task completions, learns from successes/failures
4. **Manager-Worker Split:** Strategic reasoning separated from tactical execution

**Benchmarks:**
- OSWorld (50 steps): **34.5%** — #1, beating Claude Computer Use and UI-TARS
- 18.9% and 32.7% relative improvements over leading baselines

**Relevance to Us:**
- Highest — their MoG approach directly addresses our biggest weakness (single-model grounding)
- Their memory system is what our agent lacks
- Manager-Worker pattern maps to our Planner + Agent split, but more sophisticated

---

### 3.2 UI-TARS Desktop — ByteDance

**GitHub:** https://github.com/bytedance/UI-TARS-desktop
**Stars:** 12k+ | **License:** Apache 2.0
**Model:** https://huggingface.co/ByteDance-Seed/UI-TARS-1.5-7B

**Architecture:**

Electron desktop app with a purpose-built VLM:

```
Natural Language Command
        |
        v
+---------------+
|  UI-TARS VLM  | <-- Custom model (2B/7B/72B)
| (Screenshot   |    Trained for screen understanding
|  --> Action)  |    + action prediction
+-------+-------+
        |
        v
+---------------+
|  Execution    | <-- Mouse/keyboard control
|   Engine      |    Screenshot feedback loop
+---------------+
```

**Key Features:**
- **Purpose-built model family** (2B, 7B, 72B) trained specifically for computer control
- **End-to-end:** Screenshot in, action out (no separate grounding step)
- **Cross-platform:** macOS + Windows
- **Remote operator:** Can control remote computers and browsers
- **Polished UX:** Most production-ready open-source desktop agent

**Model Details (UI-TARS 1.5-7B):**
- Based on Qwen2-VL backbone
- Fine-tuned on UI interaction data
- Predicts both element location AND action type
- Available on HuggingFace with Apache 2.0 license

**Relevance to Us:**
- Their model (UI-TARS 1.5-7B) is a drop-in replacement for our Qwen2-VL observer
- Their Electron app approach is more polished than our CLI
- Cross-platform design could inform future Windows support

---

### 3.3 Cua — Computer Use Agent Platform (YC W25)

**GitHub:** https://github.com/trycua/cua
**Stars:** 11.1k | **License:** Apache 2.0
**Website:** https://cua.ai

**Architecture:**

Infrastructure layer, not an agent — provides sandboxed environments:

```
+-------------------------------------+
|         Agent SDK (Python)          |
|  (Model-agnostic via LiteLLM)      |
+-------------------------------------+
|         Computer SDK                |
|  (Keyboard, mouse, screen capture)  |
+-------------------------------------+
|       Sandbox Environment           |
|  +----------+  +----------------+  |
|  | macOS VM |  | Linux Container|  |
|  | (Apple   |  | (Docker)       |  |
|  | Silicon) |  |                |  |
|  +----------+  +----------------+  |
+-------------------------------------+
```

**Key Features:**
- **Sandboxed execution:** Agent runs in isolated VM, can't damage host
- **97% native speed** on Apple Silicon (macOS virtualization)
- **Model-agnostic:** Plugs into any LLM via LiteLLM
- **Docker-like UX:** `cua run` to spin up sandboxed agent environments
- **Benchmarking infrastructure:** Built-in eval framework

**Relevance to Us:**
- Perfect testing infrastructure — run our agent in isolated macOS VMs
- Sandboxing addresses safety concerns
- Their Computer SDK could replace our PyAutoGUI/AppleScript layer

---

### 3.4 macOS-use — Browser Use Team

**GitHub:** https://github.com/browser-use/macOS-use
**Stars:** 8k+ | **License:** MIT

**Architecture:**

Lightweight, accessibility-API-first approach:

```
Natural Language Command
        |
        v
+---------------+
|  VLM (any)    | <-- Supports OAI, Anthropic, Gemini, local MLX
+-------+-------+
        |
        v
+---------------+
| Accessibility | <-- macOS AX APIs
|   Bridge      |    Reads UI element tree
+-------+-------+
        |
        v
+---------------+
| Action Layer  | <-- Mouse/keyboard/AppleScript
+---------------+
```

**Key Differentiator:**
- Uses **macOS Accessibility API** (AXUIElement) to make apps readable
- Designed to expose app structure to AI agents, not just pixels
- Built by the browser-use team (60k+ stars on their browser agent)
- Local inference via Apple's MLX framework

**Relevance to Us:**
- Their accessibility bridge is exactly what we're missing
- Lightweight, Python-based — easy to study and integrate
- MLX support for fast local inference on Apple Silicon

---

### 3.5 ShowUI (CVPR 2025)

**GitHub:** https://github.com/showlab/ShowUI

**Architecture:**
- **Vision-Language-Action (VLA) model** — end-to-end
- Takes screenshot, understands UI, predicts action (single model)
- Academic research, open-source with weights

**Relevance to Us:** Research reference for end-to-end VLA approaches.

---

### 3.6 OpenCUA — XLANG Lab

**GitHub:** https://github.com/xlang-ai/OpenCUA

**Architecture:**
- Open foundation models for computer-use agents
- Training framework for custom CUA models
- Benchmark suite

**Relevance to Us:** If we want to fine-tune our own grounding model.

---

## 4. Open-Source Grounding Models (VyUI Alternatives)

These are the "eyes" — models that take a screenshot + text description and output pixel coordinates of the target element. This is what VyUI does, and where our agent is weakest.

### Comparison Table

| Model | Source | Sizes | ScreenSpot v1 | ScreenSpot Pro | Open Weights | Key Innovation |
|-------|--------|-------|---------------|----------------|-------------|----------------|
| **VyUI** | Vercept | ? | 92.0% | 63.0% | No | Custom UI-specific model |
| **UGround-V1** | OSU NLP | 2B/7B/72B | ~85% | SOTA | Yes | 10M elements, 1.3M screenshots |
| **GUI-Actor** | Microsoft | 7B | High | Beats UI-TARS-72B | Yes | Coordinate-free grounding |
| **UI-TARS** | ByteDance | 2B/7B/72B | High | High | Yes | End-to-end action prediction |
| **SeeClick** | NJU | 7B | Created benchmark | — | Yes | First visual-only GUI agent |
| **Molmo 2** | AI2 | 1B-72B | Good pointing | — | Yes | Native pointing capability |
| **Qwen2-VL** | Alibaba | 2B/7B/72B | Moderate | Moderate | Yes | General VLM (not UI-specific) |

### Detailed Model Analysis

#### UGround-V1 (OSU NLP Group) — ICLR 2025 Oral

**The strongest open-source grounding model.**

- Trained on **10 million UI elements** from **1.3 million screenshots**
- Outperforms SeeClick by up to **20% absolute** on ScreenSpot
- Based on Qwen2-VL backbone (same as what we use, but fine-tuned for UI)
- Available in 2B, 7B, 72B sizes
- ICLR 2025 Oral paper — top-tier academic validation

**Why this matters for us:** UGround-7B uses the same Qwen2-VL backbone we already use, but fine-tuned on massive UI grounding data. Swapping from vanilla Qwen2-VL to UGround-7B should dramatically improve element finding with minimal code changes.

#### GUI-Actor (Microsoft)

**Coordinate-free visual grounding — a different approach.**

- Instead of predicting raw pixel coordinates, predicts element identity
- Then maps identity to coordinates via UI structure
- 7B model beats UI-TARS-72B on ScreenSpot-Pro (10x smaller!)
- Shows that architecture matters more than model size

**Why this matters for us:** Demonstrates that we don't need massive models — a smart 7B model with the right training can beat 72B general models.

#### UI-TARS 1.5-7B (ByteDance)

**End-to-end: screenshot to action prediction.**

- Doesn't just find elements — predicts the action too
- Fine-tuned for computer control, screen detection, action prediction
- Available in 2B/7B/72B sizes
- Integrated into UI-TARS Desktop app

**Why this matters for us:** Could replace both our observer AND planning step in one model call, simplifying the pipeline.

---

## 5. Architectural Comparison: Our Agent vs. The Field

### Gap Analysis

| Capability | Our Agent | VyUI | Agent S2 | UI-TARS | macOS-use |
|-----------|-----------|------|----------|---------|-----------|
| **Grounding Model** | Generic VLM (prompted) | Custom trained | Mixture-of-Grounding | Purpose-built VLM | Generic VLM |
| **Grounding Accuracy** | ~40-60% | ~92% | ~80% | ~85% | ~40-60% |
| **Accessibility API** | None | Hybrid | Structural expert | None | Primary |
| **Context Persistence** | Per-plan | Continuous | Continual memory | Per-session | None |
| **Error Recovery** | retry_different + replan | Real-time retry | Hierarchical replan | Built-in retry | None |
| **Agent Architecture** | Planner + Verifier | Frontier Agents | Manager-Worker | Single model | Single loop |
| **Action Verification** | Vision-based verify step | Immediate | Multi-scale | Screenshot diff | None |
| **Learning** | Skill library | Unknown | Past task memory | None | None |
| **Sandboxing** | None (runs on host) | None | None | None | None |

### Critical Gaps (Priority Order)

1. **Grounding accuracy** — Our biggest problem. Going from ~40-60% to ~85% would transform reliability.
2. **No accessibility API** — We're vision-only, missing free structural data.
3. **No persistent context** — Each observation starts from scratch (though our verifier helps).
4. **Single grounding strategy** — We use one model; Agent S2 proves multiple experts are better.

---

## 6. Recommendations

### Immediate (Week 1-2)

1. **Swap Qwen2-VL for UGround-7B** as the grounding model
   - Same backbone, but fine-tuned for UI elements
   - Expected improvement: 40-60% to ~80-85% accuracy
   - Minimal code changes (same API interface)

2. **Add macOS Accessibility API bridge**
   - Use AXUIElement to get UI element tree
   - Provides free, accurate element positions without vision
   - Fall back to vision only when accessibility data is unavailable

### Short-term (Week 3-4)

3. **Add persistent context tracking**
   - Maintain a state model: frontmost app, window title, known elements
   - Update incrementally instead of full re-observation
   - Use accessibility API for cheap state polling

4. **Implement Mixture-of-Grounding** (inspired by Agent S2)
   - Visual expert: UGround/GUI-Actor for pixel grounding
   - Structural expert: Accessibility API for element tree
   - Text expert: OCR for text-based element finding
   - Router: Choose expert based on element type

### Medium-term (Month 2)

5. **Add continual learning memory**
   - Store successful task completions
   - Recall similar past tasks during planning
   - Build a library of reusable action sequences

### Long-term (Month 3+)

6. **Explore fine-tuning** our own grounding model on macOS-specific data
7. **Add Cua-based sandboxing** for safe development and testing
8. **Consider UI-TARS-style end-to-end model** to simplify the pipeline

---

## 7. Sources

### VyUI / Vercept
- [Anthropic acquires Vercept](https://www.anthropic.com/news/acquires-vercept)
- [GeekWire: Anthropic acquires Vercept](https://www.geekwire.com/2026/anthropic-acquires-vercept-in-early-exit-for-one-of-seattles-standout-ai-startups/)
- [TechCrunch: Anthropic acquires Vercept](https://techcrunch.com/2026/02/25/anthropic-acquires-vercept-ai-startup-agents-computer-use-founders-investors/)
- [Together AI + Vercept case study](https://www.together.ai/customers/vercept)
- [Vercept Vy details](https://adviceofai.blogspot.com/2025/05/vercepts-vy.html)
- [Vy by Vercept — SuperbCrew](https://www.superbcrew.com/vy-by-vercept-uses-advanced-ui-understanding-to-complete-tasks-on-your-mac-just-like-you-would/)

### Open-Source Frameworks
- [Agent S / S2 — Simular AI](https://github.com/simular-ai/Agent-S)
- [UI-TARS Desktop — ByteDance](https://github.com/bytedance/UI-TARS-desktop)
- [UI-TARS model — HuggingFace](https://huggingface.co/ByteDance-Seed/UI-TARS-1.5-7B)
- [Cua — Computer Use Agent Platform](https://github.com/trycua/cua)
- [macOS-use — Browser Use](https://github.com/browser-use/macOS-use)
- [ShowUI — CVPR 2025](https://github.com/showlab/ShowUI)
- [OpenCUA — XLANG Lab](https://github.com/xlang-ai/OpenCUA)

### Grounding Models
- [UGround — OSU NLP](https://github.com/OSU-NLP-Group/UGround)
- [GUI-Actor — Microsoft](https://microsoft.github.io/GUI-Actor/)
- [SeeClick](https://github.com/njucckevin/SeeClick)
- [Molmo 2 — AI2](https://allenai.org/blog/molmo2)
- [ScreenSpot-Pro benchmark](https://arxiv.org/html/2504.07981v1)

### Benchmarks
- [Awesome GUI Agent list](https://github.com/showlab/Awesome-GUI-Agent)
- [GUI Agents Paper List](https://github.com/OSU-NLP-Group/GUI-Agents-Paper-List)
