# Desktop Automation Landscape Research

Date: 2026-02-25  
Scope: Similar attempts at desktop/web computer-use automation and the approaches they use

## Executive Summary

The strongest pattern across research and production systems is:

1. Selector/accessibility-first when available.
2. Vision grounding for what selectors cannot handle.
3. Typed action interfaces for execution safety.
4. Closed-loop verification before claiming success.
5. Full run traces for debugging and regression testing.

Pure end-to-end "single model does everything" approaches are improving, but compositional systems still provide better controllability, debuggability, and operational reliability for real workflows.

## Research Questions

1. What design patterns repeatedly appear in successful automation systems?
2. Where do systems fail in practice?
3. Which approaches best support modular testing and debugging?
4. What should this repository adopt now?

## Landscape by Era

### Era 1: Deterministic RPA and Accessibility Automation

These systems prioritize reliability and auditability over autonomy.

- Microsoft UI Automation API
  - Approach: structured UI tree + control patterns.
  - Strength: deterministic interactions and strong inspectability.
  - Source: [UI Automation overview](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-overview)
- Power Automate Desktop
  - Approach: UI elements, recorded flows, selector logic.
  - Strength: enterprise-grade repeatability for fixed workflows.
  - Sources:
    - [Desktop automation](https://learn.microsoft.com/en-us/power-automate/desktop-flows/desktop-automation)
    - [UI elements](https://learn.microsoft.com/en-us/power-automate/desktop-flows/ui-elements)
- UiPath selectors and Computer Vision fallback
  - Approach: selector-first with CV for inaccessible UIs.
  - Strength: practical fallback strategy for VDI/Citrix/canvas-like apps.
  - Sources:
    - [Selectors](https://docs.uipath.com/studio/standalone/2022.10/user-guide/about-selectors)
    - [Dynamic selectors](https://docs.uipath.com/studio/standalone/latest/user-guide/dynamic-selectors)
    - [Computer Vision activities](https://docs.uipath.com/activities/other/latest/ui-automation/computer-vision-activities)
- SikuliX
  - Approach: image-template matching + scripting.
  - Strength: works where accessibility metadata is unavailable.
  - Source: [SikuliX](https://www.sikulix.com/)

### Era 2: Web-Agent Benchmarks (LLM Planning + Browser Execution)

These efforts pushed autonomous planning but revealed large performance gaps.

- WebArena
  - Approach: realistic web tasks in controlled environments.
  - Observation: significant gap to human performance at publication time.
  - Sources:
    - [Paper](https://arxiv.org/abs/2307.13854)
    - [Project](https://webarena.dev/)
- VisualWebArena
  - Approach: multimodal web tasks requiring visual grounding.
  - Observation: DOM-only logic is insufficient for many tasks.
  - Sources:
    - [Paper](https://arxiv.org/abs/2401.13649)
    - [Code](https://github.com/web-arena-x/visualwebarena)
- WebVoyager
  - Approach: multimodal web navigation + automatic evaluation pipeline.
  - Observation: strong emphasis on evaluator quality and scalability.
  - Source: [ACL paper](https://aclanthology.org/2024.acl-long.371/)
- Mind2Web
  - Approach: large-scale web interaction traces and grounding tasks.
  - Observation: useful for training/evaluating action grounding and planning.
  - Source: [Repository](https://github.com/OSU-NLP-Group/Mind2Web)

### Era 3: Full OS Computer-Use Benchmarks

These projects moved beyond browser-only automation to full desktop operation.

- OSWorld
  - Approach: cross-OS real computer-use tasks and execution evaluation.
  - Observation: large gap between agents and humans under realistic settings.
  - Sources:
    - [Paper](https://arxiv.org/abs/2404.07972)
    - [Code](https://github.com/xlang-ai/OSWorld)
- WindowsAgentArena
  - Approach: scalable Windows task environments and evaluation.
  - Observation: emphasizes benchmark reproducibility and task breadth.
  - Source: [Repository](https://github.com/microsoft/WindowsAgentArena)
- AndroidWorld
  - Approach: dynamic mobile tasks with programmatic reward checks.
  - Observation: highlights value of objective, closed-loop success signals.
  - Sources:
    - [Paper](https://arxiv.org/abs/2405.14573)
    - [Code](https://github.com/google-research/android_world)
- OSWorld-Verified (2025 update)
  - Approach: benchmark quality and evaluation infrastructure refinements.
  - Observation: benchmark quality control materially affects conclusions.
  - Source: [Update post](https://xlang.ai/blog/osworld-verified)

### Era 4: Production "Computer Use" APIs

These systems expose controlled tool interfaces and safety boundaries.

- OpenAI CUA / Operator
  - Approach: model plans actions over screenshots and browser/OS controls.
  - Observation: strong emphasis on eval transparency and safety layering.
  - Sources:
    - [Computer-Using Agent](https://openai.com/index/computer-using-agent/)
    - [Operator](https://openai.com/index/introducing-operator/)
    - [Operator system card](https://openai.com/index/operator-system-card/)
- Anthropic computer-use tool
  - Approach: tool-use protocol where host executes actions in a controlled loop.
  - Observation: explicit host-side responsibility for execution and safety.
  - Source: [Computer use tool docs](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/computer-use-tool)

### Era 5: Open Multimodal GUI-Agent Stacks

These systems emphasize specialized grounding modules and compositionality.

- OmniParser
  - Approach: dedicated GUI parsing/grounding front-end for agents.
  - Observation: specialized grounding improves downstream action quality.
  - Sources:
    - [Microsoft Research page](https://www.microsoft.com/en-us/research/publication/omniparser-for-pure-vision-based-gui-agent/)
    - [Code](https://github.com/microsoft/OmniParser)
- UI-TARS
  - Approach: screenshot-native end-to-end GUI agent model.
  - Observation: promising generality with fewer hand-coded rules.
  - Sources:
    - [Paper](https://arxiv.org/abs/2501.12326)
    - [Code](https://github.com/bytedance/UI-TARS)
- Agent S2
  - Approach: compositional agent with specialist components and grounding mixtures.
  - Observation: compositional designs improve robustness and adaptability.
  - Source: [Paper](https://arxiv.org/abs/2504.00906)

## Comparative Matrix

| System/Family | Planning | Grounding | Action Layer | Verification | Main Tradeoff |
|---|---|---|---|---|---|
| RPA selector-first (UIA/PAD/UiPath) | Rule/workflow | Accessibility tree, selectors | Native deterministic actions | Deterministic state checks | High reliability, low adaptability |
| Vision fallback (SikuliX/UiPath CV) | Rule/workflow | Template/CV element matching | Click/type primitives | Usually weak unless custom checks | Better coverage, fragile to visual drift |
| Early web agents (WebArena generation) | LLM loop | DOM + sometimes screenshot | Browser actions | Task-specific evaluators | Better autonomy, unstable long-horizon behavior |
| OS benchmarks (OSWorld family) | LLM or hybrid | Screenshot + optional structured state | Full OS action space | Execution-based scoring | Realistic but hard; exposes reliability gaps |
| Production computer-use APIs | LLM policy + tool use | Screenshot + model grounding | Host-executed tool actions | Guardrails + host checks | Practical deployment, safety constraints |
| Compositional open stacks (OmniParser, Agent S2 style) | Hierarchical/typed planning | Specialized grounding modules | Modular executors | Module-specific + end-to-end checks | More engineering complexity, better debugability |

## Repeated Failure Modes

1. Grounding miss on dense UI (small hit targets, visually similar controls).
2. Planner loops repeating the same failing action.
3. Success reported without post-action verification.
4. Latency explosion from repeated high-cost vision calls.
5. Benchmark overfitting and weak transfer to real screens.
6. Safety gaps around login/payment/irreversible actions.

## What Consistently Works

1. Typed action contracts and strict JSON schemas.
2. Explicit separation of planning, grounding, actuation, and verification.
3. Retry with strategy change, not blind repetition.
4. Step-level postconditions plus final goal assertions.
5. Full traces (inputs, decisions, screenshots, outputs) for forensic debugging.

## Implications for This Repository

The proposed component split is aligned with the strongest pattern in the landscape:

1. Action planning client.
2. Skill/recipe registry with templated parameters.
3. Coordinate grounding service.
4. Actuator adapters.
5. Orchestrator loop.
6. Layered test strategy.
7. Manual real-screen scripts.

This is superior to a monolithic loop for maintainability and debugging.

## Closed-Loop Verification Standard (Required)

Every task should satisfy all of the following:

1. Each action step declares a postcondition.
2. After actuation, a fresh observation verifies the postcondition.
3. If verification fails, retry policy must alter strategy after threshold.
4. Final success requires explicit goal assertions (not just "no error").
5. Run artifact must store evidence fields:
   - `goal_verified: true/false`
   - `assertions_passed`
   - `screenshots`
   - `step_trace`
   - `verifier_outputs`

## Recommended Validation Plan

### Unit Tests (Per Component)

- Planner client:
  - emits valid schema
  - always includes postconditions
  - handles malformed model output
- Skill registry:
  - template variable validation
  - prompt overlay merge
  - assertion generation
- Grounding:
  - bbox parsing and coordinate-space detection
  - confidence thresholding
  - fallback model behavior
- Actuator:
  - adapter mapping correctness
  - error propagation
  - dry-run predictability
- Orchestrator:
  - loop transitions
  - retry strategy change
  - trace completeness

### Component E2E-in-Isolation

- Planner E2E with mocked LLM transport.
- Skill E2E with fixture recipes and parameterized user overlays.
- Grounder E2E against screenshot fixtures with expected targets.
- Actuator E2E with mock native runner and optional local smoke checks.
- Orchestrator E2E with deterministic mocked planner/grounder/actuator/verifier.

### Top-Level Integration

- Happy path: all assertions pass.
- Recovery path: one grounding failure, fallback succeeds.
- Failure path: repeated mismatch, task aborted with clear reason.
- Safety path: login/payment boundary triggers pause/confirmation.

### Manual Screen Validation

Create scripts that produce run artifacts and deterministic pass/fail checks:

- `scripts/manual/run_case_<name>.py`
- `scripts/manual/verify_case_<name>.py`
- `scripts/manual/report_runs.py`

Each run should write:

- `artifacts/<run_id>/steps.jsonl`
- `artifacts/<run_id>/summary.json`
- `artifacts/<run_id>/screenshots/*.jpg`

## Acceptance Criteria for "Success" Claims

A run can be marked successful only when:

1. Final goal assertions pass.
2. Verification evidence exists in artifacts.
3. No unresolved critical step failure appears in trace.
4. Scenario passes in both:
   - deterministic integration test
   - manual real-screen validation script

## Suggested Next Actions

1. Freeze component interfaces first.
2. Enforce schema and postcondition contracts across planner and skills.
3. Add a standalone verifier module used by both tests and production runs.
4. Promote artifact-based pass/fail gates into CI before feature expansion.

---

If you want, the next document can be a concrete implementation blueprint mapping this research to exact package paths, class interfaces, test files, and CI jobs for this repository.
