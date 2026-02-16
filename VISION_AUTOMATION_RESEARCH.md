# Vision-Based Desktop Automation Research (2026-02-16)

This document summarizes recent opportunities for improving pure vision desktop automation, with a focus on practical upgrades for this repository.

## Sources Reviewed

- Microsoft Research: OmniParser V2 article and project links
- arXiv: UI-Vision (2025), desktop-centric GUI benchmark
- arXiv: UI-TARS (2025), native end-to-end GUI agent model
- Benchmark references surfaced in those materials (OSWorld, ScreenSpot-Pro)

## Key Findings

1. Pure screenshot+LLM pipelines remain fragile on small targets and dense UIs.
2. Best-reported systems use a dedicated GUI parsing/grounding stage before planning.
3. High-resolution desktop tasks are still hard: benchmark scores remain far from human-level.
4. Robust agents combine:
   - element detector/grounder
   - action planner
   - reflection/error recovery
   - optional human-in-the-loop checkpoints

## Missed Opportunity in Current Stack

The current architecture relies on direct "describe/find/click" from a general vision model.
That skips a specialized GUI-tokenization layer that converts screenshots into structured interactable elements.

This can be improved by adding an intermediate parser layer:

1. Screenshot capture
2. GUI parser extracts candidate clickable elements + text + bounds
3. Planner chooses target element from this set
4. Click executes on chosen bound
5. Reflection checks if post-state matches expectation

This is the pattern used by stronger "computer use" pipelines in 2025.

## Recommended Near-Term Upgrades

### 1) Add parser-assisted grounding

- Integrate an optional parser module (OmniParser-like component or local equivalent).
- Feed planner a ranked list of elements rather than raw screenshot-only prompts.
- Keep fallback to raw vision when parser confidence is low.

### 2) Add confidence-gated actions

- Require confidence threshold for clicks.
- If confidence is below threshold, ask clarification or run secondary check.
- For critical steps (reservation submit/payment), require explicit user confirmation.

### 3) Two-pass target validation

- Before click: ask model "what is this candidate element?"
- After click: verify expected transition ("did reservation details panel open?")
- Retry with second candidate if mismatch.

### 4) Resolution-aware candidate narrowing

- For high-DPI screens, crop and process relevant regions at higher effective scale.
- Use temporal consistency: if same target requested repeatedly, bias around previous successful regions.

### 5) Task-specific policy (restaurant vertical)

- For OpenTable/Yelp/Google flows, maintain provider-specific target lexicon:
  - party size selector
  - date picker
  - time slot
  - reserve button
- This reduces search ambiguity and increases grounding precision.

## Practical Benchmarking Plan for This Repo

Track these metrics on the restaurant workflow:

- Element-grounding success rate (first click correct)
- End-to-end reservation-flow completion rate (excluding login/payment)
- Average actions per successful run
- Human interventions per run
- Time-to-complete

Target progression:

1. Baseline (current pure-vision flow)
2. Add parser-assisted candidate list
3. Add confidence gates + verification
4. Tune provider-specific prompts

## Bottom Line

Pure vision is viable for scoped verticals, but reliability jumps when adding a dedicated GUI-grounding stage and explicit verification loops.
The biggest opportunity was not model size; it was architecture: parser-assisted grounding + confidence/verification control.
