# Architecture

**Analysis Date:** 2026-03-20

## Pattern Overview

**Overall:** Protocol-based, component-decoupled architecture using duck typing via `@runtime_checkable` protocols. Five independent components communicate through Protocol classes and shared dataclasses. No inheritance, no global state, fully async-first with pytest-asyncio.

**Key Characteristics:**
- **Protocol-driven design**: All components depend on `protocols.py` abstractions, not concrete implementations
- **Data-centric**: Shared dataclasses (`ActionStep`, `ActionPlan`, `StepResult`) define contracts
- **Async-first**: All blocking operations are async-wrapped; tests use `asyncio_mode = "auto"`
- **Multi-backend vision**: Explicit coordinate space mapping per vision model (no guessing)
- **3-tier verification**: Post-action verification via Accessibility → Actuator state → Vision
- **Skill-driven automation**: Reusable `.md` templates with parameter substitution; adaptive learning via librarian

## Layers

**Orchestrator Layer:**
- Purpose: Coordinates all components; runs the execute→verify→retry loop; manages state transitions
- Location: `src/automation_agent/orchestrator/agent.py`
- Contains: `AutomationAgent` class with full execution flow, step verification, replanning, infeasibility detection
- Depends on: All other components (planner, coordinator, actuator, skill_registry, verifier)
- Used by: CLI entry point `src/automation_agent/__main__.py`

**Planning Layer:**
- Purpose: Generates multi-step action sequences from natural language + screen context
- Location: `src/automation_agent/planner/planner.py`
- Contains: `ActionPlannerImpl` implementing `ActionPlanner` protocol; prompt engineering; LLM routing
- Depends on: LLM clients (`llm/` directory); config; logging
- Used by: Orchestrator for initial planning and replanning

**Vision Layer (Grounding & Verification):**
- Purpose: Screen understanding, element locating, condition verification via vision models
- Location: `src/automation_agent/vision/coordinator.py`
- Contains: `ScreenCoordinatorImpl` implementing `ScreenCoordinator` protocol; coordinate conversion; multi-model support
- Depends on: Vision clients (vision server, Anthropic, Gemini, OpenAI); accessibility bridge; config
- Used by: Orchestrator for screenshots, element finding, and post-action verification

**Skills Layer (Template Matching & Expansion):**
- Purpose: Reusable automation templates; skill routing with three-stage matching (embedding → LLM → keyword fallback)
- Location: `src/automation_agent/skills/registry.py`, `router.py`, `librarian.py`
- Contains: `SkillRegistryImpl`, skill loader, matcher, router, librarian, experience store
- Depends on: Planner (for LLM-based routing); skill files (`.md` in `library/`); embedding index
- Used by: Orchestrator for skill context injection; librarian for post-run learning

**Actuator Layer:**
- Purpose: Executes desktop actions (click, type, press_key, etc.) via AppleScript
- Location: `src/automation_agent/actuator/applescript_actuator.py`
- Contains: `AppleScriptActuator` implementing `Actuator` protocol; action execution; Accessibility API queries
- Depends on: osascript (system binary); AppleScript/JXA; config
- Used by: Orchestrator for action execution; verifier for state queries

**Verification Layer:**
- Purpose: Three-tier post-action condition checking (Accessibility → Actuator state → Vision)
- Location: `src/automation_agent/orchestrator/verifier.py`
- Contains: `StepVerifier` class with `verify()` method; tier logic; context-aware checks
- Depends on: Actuator; Coordinator; Accessibility API
- Used by: Orchestrator after every step execution

**Perception Layer (Supporting):**
- Purpose: Low-level desktop state queries (Accessibility API, screenshot capture)
- Location: `src/automation_agent/perception/accessibility.py`, `capture.py`
- Contains: `AccessibilityBridge` (JXA queries via osascript); `ScreenCapture` (framebuffer capture)
- Used by: Coordinator, Actuator, Verifier for structured element discovery

## Data Flow

**Primary Execution Flow:**

1. **User Prompt** → `__main__.py` → CLI args parsed → Config loaded
2. **Initialize Components** → Planner, Skills, Coordinator, Actuator created
3. **AutomationAgent.execute(prompt)**
   - Extract site entity (deterministic pre-filter: "on amazon" → site=amazon)
   - SkillRegistry.match() → three-stage matching (embedding → LLM router → keyword fallback)
   - ContextMonitor.update_cheap() → evolving world-state document
   - ScreenCoordinator.describe() → natural language screen description
   - ActionPlanner.plan() → LLM generates steps with mandatory verify fields
   - _is_truncated_plan() → detect shallow plans; fall back to skill template if needed
   - _inject_domain_verification() → append "AND browser domain is X" to open_url verify
4. **For each ActionStep:**
   - Precondition check (if non-empty) → StepVerifier asserts via accessibility/vision
   - Lookahead prediction (destructive steps only, opt-in)
   - Confirmation gate (destructive action user confirmation)
   - Actuator.execute() → perform the action
   - StepVerifier.verify() → 3-tier verification (Tier 0 → Tier 1 → Tier 2)
   - Infeasibility detection → FrustrationScore tracking
   - ContextMonitor.record_step_outcome() → track milestones/obstacles
   - On failure → replan() with execution history
5. **ExecutionResult** → Return success/failure + step results

**Skill Learning Flow (Post-Run):**

1. _maybe_learn_skill_run() → fires only if a skill was matched
2. SkillDistiller extracts observations (alternative_path, checkpoint, user_gate, anti_pattern)
3. SkillExperienceStore persists to JSONL sidecar at `logs/skill_learning/{skill_name}.jsonl`
4. SkillLibrarian evaluates accumulated observations
5. Two promotion paths:
   - patch_parent: append `## Learned Tips` to existing skill `.md`
   - create_sibling: generate new `.md` with `parent-skill-id` link, `trusted: false`
6. Promotion history tracked at `logs/skill_learning/promotions/history.jsonl`

**State Management:**

- **Per-Run State**: `FrustrationScore` created FRESH per execute() call (never stored on self)
  - Tracks: same-state count, identical action retries, replan count, advisory checks
- **Derived Skill State**: `DerivedSkillSession` in-memory, seeded from parent skill's steps
  - Tracks adaptations during execution (label replacements, discovered landmarks, failed assumptions)
  - Updated by `ReplanPatch` during replanning; all lists capped at 20 items
- **Execution History**: Full `StepResult` list; replanning uses for context
- **Desktop Context**: `ContextMonitor` maintains evolving world-state document (app, URL, visited sites, etc.)

## Key Abstractions

**ActionPlan & ActionStep:**
- Purpose: Represent a multi-step automation sequence
- Examples: `src/automation_agent/shared_models.py` lines 114-187
- Pattern: Each step has mandatory postcondition (`verify` field). Every step includes optional `precondition` (asserted pre-action), `action` (what to do), and `verify` (what must be true after). LLM generates steps; planner validates all have verify fields.

**FindElementResult:**
- Purpose: Locating UI element on screen with confidence scoring
- Examples: `src/automation_agent/shared_models.py` lines 88-111
- Pattern: Dataclass with x, y pixel coords, confidence (0.0-1.0), source (accessibility/vision/grounding), and raw_response for debugging

**StepResult:**
- Purpose: Full outcome of executing and verifying a single step
- Examples: `src/automation_agent/shared_models.py` lines 289-300
- Pattern: Captures step, success flag, verification method (which tier passed), evidence (observation), error (if any), retry count, and retry strategies used

**Skill & SkillCard:**
- Purpose: Reusable automation templates; card for lightweight matching
- Examples: `src/automation_agent/skills/models.py`; skill files at `src/automation_agent/skills/library/*.md`
- Pattern: YAML frontmatter (name, trigger-keywords, parameters, OS requirements) + Markdown steps with `{{param}}` placeholders and `verify` conditions. Skills may include `site:` metadata for site-entity routing.

**ReplanPatch:**
- Purpose: Patch to apply to DerivedSkillSession after replanning
- Examples: `src/automation_agent/shared_models.py` lines 191-258
- Pattern: Dataclass with replace_labels, add_landmarks, verify_improvements, failed_assumptions, successful_adaptations. Parsed from LLM replan response; tolerant of missing fields.

**Protocols (Communication Contracts):**
- `ActionPlanner`: plan(), replan(), check_infeasibility()
- `ScreenCoordinator`: find_element(), describe_screen(), verify_condition(), capture_screenshot(), capabilities()
- `Actuator`: click(), type_text(), press_key(), open_url(), activate_app(), scroll(), etc.
- `SkillRegistry`: match(), expand(), etc.

## Entry Points

**CLI Entry Point:**
- Location: `src/automation_agent/__main__.py`
- Triggers: `automation-agent "Open Calculator"` command
- Responsibilities: Parse args, load config, initialize components, call `run_agent()` async function, display results

**Dry-Run Mode:**
- Location: `src/automation_agent/__main__.py` lines 139-170
- Triggers: `automation-agent --dry-run "Open Calculator"`
- Responsibilities: Parse intent, match skills, generate plan WITHOUT executing; print steps

**Orchestrator Entry Point:**
- Location: `src/automation_agent/orchestrator/agent.py`
- Triggers: `await agent.execute(prompt)` call
- Responsibilities: Full execute→verify→retry loop; runs all 5 components in orchestrated sequence

**Component-Level Entry Points:**
- Planner: `planner.plan(goal, screen_description, skill_context, desktop_context)`
- Coordinator: `coordinator.describe_screen()`, `find_element()`, `verify_condition()`
- Actuator: `actuator.click(x, y)`, `type_text(text)`, etc.
- Skills: `skill_registry.match(prompt)`, `expand(skill, params)`

## Error Handling

**Strategy:** Layered error recovery with escalation (retry strategies → replan → abort)

**Patterns:**

1. **Step-Level Failure**:
   - Actuator returns error → StepVerifier checks tiers
   - All tiers inconclusive → Step fails with `verification_inconclusive` error
   - on_fail field determines recovery: `retry_different` (try alternative approach), `replan` (regenerate plan), `abort` (stop), `wait_for_user` (pause for manual input)

2. **Retry Strategies** (orchestrator/agent.py):
   - Transient keys injected: `_pre_delay`, `_clear_first`, `_slow_type`, `_address_bar_fallback`, `_quit_first`, `_pre_keys`, `_spotlight`
   - Up to `max_retries` per step (default 3); exhausting retries triggers on_fail handler

3. **Infeasibility Detection**:
   - `FrustrationScore` tracks same-state count, retries, replan count, advisory checks
   - Thresholds: `same_state_limit` (default 3), `replan_limit` (default 2), `max_advisory_checks` (default 2)
   - Hard abort when limits exceeded; planner.check_infeasibility() called for advisory assessment

4. **Precondition Failures**:
   - Non-empty precondition is asserted pre-action via verifier
   - If precondition fails, step fails immediately with `precondition_failed` error
   - Precondition checks skipped for `done` and `observe` actions

5. **Accessibility/Vision Failures**:
   - Missing/null responses caught; graceful degradation (e.g., vision-only mode without accessibility)
   - Coordinate conversion failures raise ValueError with explicit model registry error

6. **LLM Call Failures**:
   - JSON parse errors caught; ValueError raised with context
   - Model provider validation at coordinator._validate_model() — unknown models rejected upfront

## Cross-Cutting Concerns

**Logging:** Structured logging via `structlog` + custom `EventLogger` for JSONL event logs

- Pattern: `slog.info("event_name", key1=val1, key2=val2)`
- Event logs: `logs/{run_id}/events.jsonl` with EventType enums (PLAN, GROUNDING, VERIFY_PASS, VERIFY_FAIL, REPLAN, etc.)
- Console logging: stderr via `structlog` formatter; log level configurable via `AGENT_LOG_LEVEL`

**Validation:** Mandatory postconditions; preconditions optional but recommended

- Pattern: Every ActionStep.verify must be non-empty for non-terminal actions
- ActionPlan.validate() checks all steps before execution; raises ValueError if validation fails
- LLM output auto-corrects via ActionStep.from_dict() with _ACTION_ALIASES and _ON_FAIL_ALIASES

**Authentication:** Per-provider API keys (Anthropic, Gemini, OpenAI) loaded from env vars

- Pattern: Config validators (field_validator) check env var fallbacks (ANTHROPIC_API_KEY, GEMINI_API_KEY, etc.)
- No secrets in logs; omit full responses in event logs (first ~300 chars only)

**Configuration:** Pydantic Settings with env prefix AGENT_ and .env file support

- Pattern: `AGENT_MODEL_PROVIDER=anthropic`, `AGENT_VISION_MODEL=molmo`, etc.
- Per-step model routing: `AGENT_PLANNING_MODEL`, `AGENT_GROUNDING_MODEL_PROVIDER`, etc.
- Config loaded: defaults → env vars → .env → config.json (later overrides earlier)

---

*Architecture analysis: 2026-03-20*
