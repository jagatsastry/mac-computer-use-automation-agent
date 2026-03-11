# Adaptive Skill System: Product Requirements Document

## 1. Problem Statement

The macOS automation agent uses markdown skill files as procedural priors for planning. Today, the skill system has four limitations that degrade task success rate and prevent the agent from improving over time:

### 1.1 Single-Skill Matching Hides Useful Priors

The router picks ONE skill and discards all other candidates. When a user says "return my Walmart order," the system either finds an exact match or returns nothing. It cannot surface `amazon_return` as an analogical prior -- even though the procedural shape (login -> orders -> item -> return -> reason -> confirm) is nearly identical.

### 1.2 No Analogical Transfer

There is no distinction between "this skill IS the right procedure" (direct match) and "this skill has a SIMILAR structure you can adapt" (analogical match). The planner receives a single skill context with no guidance on how literally to follow it. This causes failures when the skill's site-specific labels (e.g., "Orders" vs. "Purchase History") don't match the target site.

### 1.3 Stateless Replanning Loses Corrections

When a step fails and replanning occurs, the replan starts from the same original skill context. Corrections discovered during the run -- such as "Walmart uses 'Purchase History' not 'Orders'" -- are not captured in a structured way. If a second replan were to occur, it would repeat the same mistakes.

### 1.4 No Run-Local Adaptation

There is no mechanism to create a working hypothesis for the current task that evolves as execution proceeds. The agent cannot build up a run-local understanding of the target site/app and carry it forward through subsequent steps.

### 1.5 User Impact

These limitations mean:
- Tasks on unsupported sites fail even when a structurally analogous skill exists
- The agent makes the same mistakes repeatedly within a single run
- The skill library's value plateaus because each skill only helps its exact use case
- Users must write site-specific skills for every variant of a common task pattern

## 2. Acceptance Criteria

Each criterion is independently testable. All must pass for the MVP to be considered complete.

### Core Routing (top-k with match types)

**AC-1**: The router must return up to 3 candidate skills per prompt, each labeled with a `match_type` of `direct`, `analogical`, or `fallback`, plus a `confidence` score (0.0-1.0) and `reason` string.

**AC-2a** (unit-testable): The router must be able to parse and return candidates with `match_type: "analogical"`. Test: given a mock LLM response containing an analogical candidate, the router returns it with the correct `match_type` label, `confidence`, and `reason`.

**AC-2b** (integration-testable, requires live LLM): When no exact skill exists but a structurally similar skill does, the LLM must label it as analogical. Concretely: a prompt like "return my Walmart order" with only an `amazon_return` skill in the library must return `amazon_return` as an analogical candidate. This test runs against a live LLM and is NOT part of CI unit tests.

**AC-3**: When no relevant skill exists at all, `match()` must return `None` (same as today). The threshold is `MIN_USEFUL_CONFIDENCE = 0.5`: if all candidates returned by the LLM score below 0.5, `match()` returns `None` and the planner produces a generic plan with no skill priors. This constant must be defined in `skills/router.py` and referenced in tests.

### Multi-Skill Planner Context

**AC-4**: The planner must receive all top-k candidate skills (up to 3) in its prompt context, each labeled with its match type and the router's rationale for selecting it.

**AC-5**: The planner prompt must explicitly instruct the LLM: direct matches may be followed closely; analogical matches are structural priors only -- do not assume site-specific labels, buttons, or navigation are identical.

### Run-Local Derived Procedure

**AC-6**: When `match()` returns a non-None result (i.e., at least one candidate above `MIN_USEFUL_CONFIDENCE`), the orchestrator must create a run-local `DerivedSkillSession` object seeded from the best matching parent skill(s). This object exists only in memory for the duration of the run. When `match()` returns `None`, no `DerivedSkillSession` is created -- the run proceeds without a derived procedure, and the replan patch mechanism is skipped. This is correct for MVP: runs without skill matches get no within-run procedural learning.

**AC-7**: The derived procedure must contain at minimum: parent skill ID(s), current best step sequence, replaced labels/landmarks, discovered verification text, and notes about failed/successful assumptions.

### Stateful Replanning

**AC-8**: When replanning occurs, the replan prompt must include the current derived procedure state.

**AC-9**: The replan prompt must request both (a) revised next steps and (b) a `derived_skill_patch` object (label replacements, new landmarks, verification improvements). The `derived_skill_patch` key is **optional** in the LLM response: if the LLM omits it or returns malformed JSON for the patch, the orchestrator must still execute the revised steps and skip the derived procedure update. The planner's `_parse_plan_response()` must treat a missing or unparseable `derived_skill_patch` as `None`, never as an error. Test: a replan response with only `{"steps": [...]}` must succeed without raising.

**AC-10**: After a replan, the orchestrator must apply the replan patch to the `DerivedSkillSession` before execution continues. This is a forward-looking invariant: in the current single-replan architecture, the update still must happen so that (a) the derived procedure is correct when passed to the post-run distiller, and (b) the wiring is in place for when multi-replan is added. Test: assert `DerivedSkillSession` state differs after `_replan_and_continue()` returns.

### Safety Invariants

**AC-11**: Canonical markdown skill files in `src/automation_agent/skills/library/` must never be modified during a live run. This is a hard invariant.

**AC-12**: Post-run observation distillation via `SkillDistiller` and `SkillExperienceStore` must continue to work. The derived procedure must be serialized into the `skill_context: str` parameter already passed to `SkillDistiller.distill()` -- no new parameters added to the distiller interface. Concretely: `_maybe_learn_skill_run()` must append the derived procedure summary to the existing `skill_context` string before calling `learn_from_run()`.

**AC-13**: All existing tests (500+) must continue passing after implementation. No protocol signature changes: `ActionPlanner.plan()`, `ActionPlanner.replan()` signatures unchanged; `SkillRegistry.match()` Python type annotation stays `Optional[Dict[str, Any]]`. The dict retains the existing keys (`skill_name`, `expanded_steps`, `skill_context`, `params`) for backward compatibility and adds a new `candidates` key containing the list of top-k matches with `match_type`/`confidence`/`reason`. This is additive, not a breaking change: existing orchestrator code that reads `skill_match["skill_name"]` continues to work; new code reads `skill_match["candidates"]`. No dual-format handling or transition period is needed.

### Backward Compatibility

**AC-14**: New frontmatter fields on skill markdown files (`skill-id`, `tags`, `summary`) must be optional. Existing skills without these fields must load and function identically to today. **Implementation dependency**: `loader.py` (`parse_skill_file()`) currently drops unknown frontmatter fields. It must be updated to parse and pass through `skill-id`, `tags`, and `summary`. The `Skill` dataclass in `models.py` must gain these as optional fields with defaults (`skill_id: str = ""`, `tags: list[str] = field(default_factory=list)`, `summary: str = ""`). Both changes are part of Slice 1.

**AC-15**: The `skill_context` parameter to the planner remains `Optional[str]`. Multi-skill context and the derived procedure must be assembled into a single string by the registry/orchestrator.

**AC-16**: The keyword-based matcher fallback (`matcher.py`) must continue to work when no LLM is available. The keyword fallback returns the old single-match format (no `match_type` labels, no `candidates` list). The registry's `match()` method must normalize the keyword fallback result into the new dict shape by wrapping it as a single candidate in the `candidates` list with `match_type: "direct"` and `confidence: 1.0` (keyword matches are assumed direct). This normalization lives in `registry.py`, not in `matcher.py`.

### Skill Cards

**AC-17**: Each skill must have a compact routing card with at minimum: `skill_id`, `title`, `summary` (abstraction-first, not site-literal), and `tags`. Skill cards are computed on-the-fly by `SkillCardBuilder.build(skill) -> SkillCard` -- no persistent JSON cache. The builder derives `skill_id` from `skill.name`, `title` from `skill.description`, and `tags` from `skill.trigger_keywords`. The `summary` field is populated from the new optional `summary` frontmatter field if present, otherwise falls back to `skill.description`.

**AC-18**: Skill card summaries must describe the *procedural pattern* abstractly, not the site-specific steps. Example: "Navigate a retailer's order history, locate a purchased item, and complete a return or refund flow" -- not "Return an item on Amazon." Existing skills should be enriched with `summary` frontmatter during implementation, but must still load without it (AC-14).

### Implementation Notes for Spec Authors

These are not acceptance criteria but known integration points that must be addressed during implementation:

1. **`_build_skill_fallback_plan()`** (agent.py ~line 126): This method reads from `skill_context` to handle trivial done plans. When `skill_context` format changes (multi-skill sections, derived procedure), this method must be updated in Slice 4 (Orchestrator Integration). It is not called out as a separate AC because it is an internal implementation detail of the orchestrator, but it must not be overlooked.

2. **`_build_skills_summary()`** (router.py): The current inline skill summary builder should be replaced by `SkillCardBuilder` in Slice 2. The card builder produces the compact format the router prompt consumes.

## 3. Out of Scope

The following are explicitly NOT part of this MVP. They are planned for future phases.

1. **No automatic promotion into the canonical library.** The librarian/promotion pipeline (sibling skills, ancestor skills, parent patches) is post-MVP. Derived procedures are discarded after the run completes.

2. **No embedding-based retrieval.** The MVP uses LLM-only routing (all skill cards sent in one prompt). Embedding infrastructure (vector databases, BM25 shortlisting) is deferred until the library exceeds ~100 skills.

3. **No SkillLibrarian.** The post-run role that decides whether to promote a derived procedure to a sibling skill, ancestor skill, or parent patch does not exist in MVP. Only observation distillation runs post-run.

4. **No persistent derived skills between runs.** `DerivedSkillSession` is in-memory only, created at run start and discarded at run end. No derived procedures are saved to disk.

5. **No prompt card extraction.** The spec describes an LLM-extracted "prompt card" (intent, domain, site, entities, subgoals) as a retrieval input. For MVP, the raw user prompt is sent directly to the router.

6. **No skill honing / test-case generation.** SkillWeaver-style automated test generation and re-execution before promotion is post-MVP.

7. **No trust tiers on skills.** The four-tier trust model (untrusted -> verified -> trusted -> core) from the Agent Skills survey is post-MVP.

8. **No multi-replan recursion.** The orchestrator currently replans once per run. The derived procedure update happens in that single replan. Recursive replanning is not added.

## 4. Success Metrics

### 4.1 Functional Metrics (measurable in tests)

| Metric | Target | How to Measure |
|--------|--------|---------------|
| Router returns multiple candidates | 100% of prompts with relevant skills | Unit test: mock LLM returns top-k format |
| Analogical match parsed (AC-2a) | Router correctly parses analogical match_type | Unit test: mock LLM response with analogical candidate |
| Analogical match produced (AC-2b) | LLM labels cross-site skill as analogical | Integration test: live LLM with Walmart/Amazon scenario |
| Derived procedure created | 100% of runs with skill match | Unit test: DerivedSkillSession exists after match |
| Replan updates derived procedure | 100% of replans | Unit test: derived procedure state changes after replan |
| Existing tests pass | 500+ tests green | CI: `pytest tests/unit/` |

### 4.2 Quality Metrics (measurable in integration/e2e)

| Metric | Target | How to Measure |
|--------|--------|---------------|
| Cross-site task completion | Higher than baseline (no analogical priors) | Integration test: Walmart return with amazon_return prior vs. no prior |
| Within-run correction persistence | Corrections from replan 1 visible in replan 2 context | Integration test: derived procedure state after sequential replans |
| Post-run observation quality | Observations include derived procedure insights | Integration test: distiller receives derived procedure context |

### 4.3 Non-Regression Metrics

| Metric | Target | How to Measure |
|--------|--------|---------------|
| No protocol signature changes | Zero changes to `protocols.py` method signatures | Code review |
| Backward-compatible frontmatter | Old skills load without new fields | Unit test: skill loading without `skill-id`, `tags`, `summary` |
| No canonical skill mutation | Zero writes to `skills/library/` at runtime | Code review + assertion in DerivedSkillSession |

## 5. Research References

The following SOTA findings directly shaped the acceptance criteria and design decisions.

### Abstraction-First Skill Cards (AC-17, AC-18)

All successful transfer systems index skills by abstract procedural descriptions, not literal site-specific steps.
- **Voyager** (Wang et al., NeurIPS 2023): Skills indexed by natural language descriptions enable top-5 retrieval for new tasks.
- **SkillWeaver** (OSU NLP, Apr 2025): API docstrings serve as analogical retrieval keys; 31-54% success rate improvement via cross-site transfer.
- **AgentTrek** (Dec 2024): Structured procedure templates transfer across sites on WebArena.
- *Source: SOTA doc, Section 1 "Key Pattern: Abstraction-First Descriptions Enable Transfer"*

### LLM-Heavy Top-K Routing (AC-1, AC-2, AC-3)

For small-to-medium libraries, sending all compact cards to one LLM call outperforms embedding-only retrieval for procedural applicability.
- **RankRAG** (NeurIPS 2024): LLMs can rank and reason about applicability in a single call; optimal k ~10 retrieved contexts.
- **Agent Skills Survey** (arXiv 2602.12430, Feb 2026): LLM-mediated routing with deterministic pre-filters (OS, required apps) is the recommended pattern.
- *Source: SOTA doc, Section 4 "Key Pattern: LLM-Heavy Routing is Correct for Small Libraries"*

### Run-Local Derived Procedures (AC-6 through AC-10)

Corrections must persist within the run. Stateless replanning loses learned corrections and causes repeated failures.
- **ReCAP** (Stanford/MIT, Oct 2025): Corrections must flow back up to the procedure level, not just fix the immediate next step. Re-injecting subtask outcomes into parent context is the key mechanism.
- **Reflexion** (Shinn et al., NeurIPS 2023): Verbal reinforcement through stored reflections. Known failure mode: degeneration-of-thought when relying solely on LLM self-assessment (mitigated by our 3-tier external verifier).
- *Source: SOTA doc, Section 2 "Key Pattern: Corrections Must Persist Within the Run"*

### Conservative Promotion / Safety Invariants (AC-11, Out of Scope items)

All successful systems gate promotion by evidence strength. Premature promotion pollutes the library.
- **SkillWeaver**: Test-case-based verification before library entry. 26.1% of community-contributed skills contain vulnerabilities.
- **Voyager**: Only verified skills enter the library (LLM-as-judge).
- **Agent Skills Survey**: Four-tier trust model (untrusted -> verified -> trusted -> core). Curated skills provide quantifiable improvement over self-generated ones.
- *Source: SOTA doc, Section 3 "Key Pattern: Conservative Promotion with Evidence Thresholds"*

### Separation of Tactical and Strategic Correction (AC-8-10 vs AC-12)

Mixing within-run tactical correction and post-run strategic learning risks either corrupting the canonical library or losing within-run corrections.
- **ExpeL** (AAAI 2024): Dual learning modes -- episodic (raw trajectories) vs. semantic (generalized insights from success/failure pairs). Insight extraction should receive both failed and successful trajectory segments.
- *Source: SOTA doc, Section "Cross-Cutting Patterns", Pattern 2*

### Codebase Constraints That Shaped ACs

The following constraints from the codebase analysis directly shaped specific acceptance criteria:
- **`skill_context` is `Optional[str]`** (AC-15): The planner protocol cannot change signatures. Multi-skill context must be serialized into a single string. *Source: Codebase doc, Section 6.3 constraint 1.*
- **`match()` returns `Optional[Dict]`** (AC-13): The return type stays the same; only the dict shape changes. *Source: Codebase doc, Section 6.3 constraint 2.*
- **Single replan per run** (Out of Scope #8): The orchestrator's `_replan_and_continue` does not recurse. The derived procedure update happens in that one replan call. *Source: Codebase doc, Section 9 constraint 6.*
- **`.env` gotcha** (AC-13 testing): Tests must use `_env_file=None` to prevent `AGENT_MODEL_PROVIDER=anthropic` from leaking into pydantic-settings. *Source: Codebase doc, Section 8.1 pattern 6.*
