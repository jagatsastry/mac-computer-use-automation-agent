# Codebase Structure

**Analysis Date:** 2026-03-20

## Directory Layout

```
src/automation_agent/
├── __init__.py                      # Package initialization
├── __main__.py                      # CLI entry point: parse args, load config, run agent
├── cli.py                           # Argument parser and CLI validation
├── config.py                        # Pydantic Settings: AgentConfig with env var support
├── protocols.py                     # @runtime_checkable protocol classes for components
├── shared_models.py                 # Dataclasses: ActionStep, ActionPlan, StepResult, FindElementResult, etc.
├── status.py                        # Status display controller
├── status_overlay.py                # Live status overlay UI (for terminal or tkinter)
├── version.py                       # Version string
│
├── actuator/                        # Desktop action execution (AppleScript backend)
│   ├── __init__.py                  # create_actuator() factory
│   ├── __main__.py                  # CLI for testing actuator
│   ├── applescript_actuator.py      # AppleScriptActuator: click, type, press_key, open_url, scroll, etc.
│   ├── models.py                    # ActuatorResult dataclass
│   └── self_test.py                 # Self-test routine
│
├── agent/                           # Empty placeholder (legacy?)
│   └── __init__.py
│
├── app/                             # Empty (legacy?)
│   └── __init__.py
│
├── integration/                     # Integration helpers (legacy?)
│   └── __init__.py
│
├── llm/                             # LLM client implementations (provider abstraction)
│   ├── __init__.py                  # LLMClient factory
│   ├── client.py                    # BaseClient abstract class
│   ├── anthropic_client.py          # AnthropicClient (Claude API)
│   ├── gemini_client.py             # GeminiClient (Google Gemini API)
│   ├── molmo_client.py              # MolmoClient (Molmo via OpenRouter)
│   ├── molmo_local_client.py        # MolmoLocalClient (Molmo via local MLX server)
│   ├── openai_client.py             # OpenAIClient (GPT/gpt-4o)
│   └── exceptions.py                # LLM-specific exceptions
│
├── logging/                         # Structured event logging
│   ├── __init__.py                  # configure_logging() setup
│   ├── event_logger.py              # EventLogger class (JSONL output)
│   ├── models.py                    # EventType enum and event models
│   └── structured.py                # structlog configuration
│
├── orchestrator/                    # Main orchestration: execute→verify→retry loop
│   ├── __init__.py                  # AutomationAgent export
│   ├── __main__.py                  # CLI for orchestrator
│   ├── agent.py                     # AutomationAgent (223KB!): full execution flow, replanning, infeasibility
│   ├── confirmation.py              # ConsoleConfirmationHandler for user confirmations
│   ├── context_monitor.py           # ContextMonitor: maintains evolving world-state document
│   ├── grounding_router.py          # GroundingRouter: routes grounding to vision or accessibility
│   ├── models.py                    # DestructiveClassification, other orchestrator-specific models
│   ├── screenshot_diff.py           # ScreenshotDiffVerifier: diff-based verification
│   ├── verifier.py                  # StepVerifier: 3-tier post-action verification
│   └── self_test.py                 # Self-test for orchestrator
│
├── perception/                      # Low-level desktop perception
│   ├── __init__.py
│   ├── accessibility.py             # AccessibilityBridge: JXA queries via osascript
│   └── capture.py                   # ScreenCapture: framebuffer capture
│
├── planner/                         # Action planning via LLM
│   ├── __init__.py                  # ActionPlannerImpl export
│   ├── __main__.py                  # CLI for planner testing
│   ├── planner.py                   # ActionPlannerImpl: plan(), replan(), per-step model routing
│   ├── models.py                    # Planner-specific models
│   ├── prompts/                     # Prompt templates (Markdown)
│   │   ├── plan_from_prompt.md      # Planning prompt template
│   │   ├── replan_from_failure.md   # Replanning prompt template
│   │   └── ...                      # Additional prompts
│   └── self_test.py                 # Self-test for planner
│
├── skills/                          # Skill templates: matching, loading, expansion, learning
│   ├── __init__.py                  # SkillRegistryImpl export
│   ├── __main__.py                  # CLI for skill testing
│   ├── card_builder.py              # SkillCard construction
│   ├── derived_skill.py             # DerivedSkillSession: in-memory skill with runtime adaptations
│   ├── distiller.py                 # SkillDistiller: extracts learning observations from execution traces
│   ├── embeddings.py                # EmbeddingIndex: semantic skill retrieval (fastembed)
│   ├── experience.py                # SkillExperienceStore: JSONL sidecar persistence
│   ├── librarian.py                 # SkillLibrarian: evaluates observations, promotes skills (34KB!)
│   ├── llm_utils.py                 # LLM utilities for skill routing
│   ├── loader.py                    # load_skill_from_file(): parses YAML + Markdown
│   ├── matcher.py                   # match_skill(): simple keyword/embedding matching
│   ├── models.py                    # Skill, SkillCard, SkillObservation dataclasses
│   ├── registry.py                  # SkillRegistryImpl: loads, matches, expands skills (30KB!)
│   ├── router.py                    # SkillRouter: LLM-based ranking with keyword fallback
│   ├── library/                     # Skill template files (YAML + Markdown)
│   │   ├── amazon_search.md
│   │   ├── buy_on_target.md
│   │   ├── google_search.md
│   │   ├── restaurant_google.md
│   │   ├── restaurant_opentable.md
│   │   ├── restaurant_yelp.md
│   │   ├── return_amazon_order.md
│   │   ├── return_target_order.md
│   │   ├── return_walmart_order.md
│   │   ├── send_imessage.md
│   │   └── ...                      # More skills
│   ├── prompts/                     # Skill-related prompt templates
│   │   ├── route_skill.md           # Skill routing prompt
│   │   └── ...                      # Additional prompts
│   └── self_test.py                 # Self-test for skills
│
└── vision/                          # Vision-based screen understanding
    ├── __init__.py                  # ScreenCoordinatorImpl export
    ├── __main__.py                  # CLI for vision testing
    ├── annotator.py                 # Screenshot annotation (e.g., SOM labels)
    ├── capture.py                   # ScreenCapture (low-level framebuffer access)
    ├── coordinator.py               # ScreenCoordinatorImpl (53KB!): find_element, describe_screen, verify_condition
    ├── geometry.py                  # Coordinate conversion utilities
    ├── models.py                    # Vision-specific models
    ├── prompts/                     # Vision prompt templates
    │   ├── find_element.md          # Element grounding prompt
    │   ├── describe_screen.md       # Screen description prompt
    │   ├── verify_condition.md      # Condition verification prompt
    │   └── ...                      # Additional prompts
    └── self_test.py                 # Self-test for vision

tests/                              # Full test suite
├── unit/                           # Unit tests (fast, all mocked)
│   ├── test_*.py                   # 40+ test files
│   └── conftest.py                 # pytest fixtures, _make_config() helpers
├── integration/                    # Integration tests (slower, real components)
├── e2e/                            # End-to-end tests (live macOS desktop)
├── orchestrator/                   # Orchestrator-specific tests
├── perception/                     # Perception-specific tests
└── workflows/                      # Workflow scenario tests
```

## Directory Purposes

**`src/automation_agent/`:**
- Purpose: Main source directory containing all components
- Contains: Five core components (Planner, Vision, Actuator, Skills, Orchestrator) plus supporting modules
- Key files: `protocols.py` (component contracts), `shared_models.py` (data structures), `__main__.py` (CLI)

**`orchestrator/`:**
- Purpose: Orchestrates all components; runs the execute→verify→retry loop
- Contains: `AutomationAgent` (main orchestrator), `StepVerifier` (post-action verification), `ContextMonitor` (world-state tracking), `GroundingRouter` (grounding strategy selection)
- Key files: `agent.py` (223KB, contains most of orchestration logic), `verifier.py` (3-tier verification)

**`planner/`:**
- Purpose: Generates multi-step action sequences from natural language
- Contains: `ActionPlannerImpl` (implements ActionPlanner protocol), prompt templates, LLM routing
- Key files: `planner.py` (plan generation), `prompts/` (prompt templates)

**`vision/`:**
- Purpose: Screen understanding via vision models; element locating; condition verification
- Contains: `ScreenCoordinatorImpl` (implements ScreenCoordinator protocol), coordinate conversion, multi-model support
- Key files: `coordinator.py` (53KB, main vision logic), `capture.py` (screenshot capture), `geometry.py` (coordinate conversion)

**`skills/`:**
- Purpose: Reusable automation templates; skill matching and expansion; adaptive learning
- Contains: `SkillRegistryImpl` (skill loader/matcher), `SkillRouter` (LLM-based routing), `SkillLibrarian` (observation evaluation and promotion), skill templates (`.md` files)
- Key files: `registry.py` (30KB, main skill logic), `librarian.py` (34KB, skill learning), `library/` (skill templates)

**`actuator/`:**
- Purpose: Execute desktop actions via AppleScript
- Contains: `AppleScriptActuator` (implements Actuator protocol), action methods (click, type, press_key, etc.)
- Key files: `applescript_actuator.py` (action execution via osascript/JXA)

**`perception/`:**
- Purpose: Low-level desktop perception (Accessibility API, screen capture)
- Contains: `AccessibilityBridge` (JXA queries), `ScreenCapture` (framebuffer capture)
- Key files: `accessibility.py` (Accessibility API wrapper), `capture.py` (screenshot capture)

**`llm/`:**
- Purpose: Provider abstraction for LLM backends (Anthropic, Gemini, OpenAI, Ollama, Molmo)
- Contains: LLM client implementations per provider
- Key files: `client.py` (base class), individual provider clients

**`logging/`:**
- Purpose: Structured event logging to JSONL files; configuration
- Contains: `EventLogger` (JSONL output), `EventType` enum, structlog setup
- Key files: `event_logger.py` (main logging), `models.py` (event types)

**`tests/`:**
- Purpose: Full test suite with unit, integration, and e2e coverage
- Contains: 40+ test files covering all components
- Key patterns: Unit tests use `_make_config()` fixtures; mocks for all external dependencies; e2e tests require live desktop

## Key File Locations

**Entry Points:**
- `src/automation_agent/__main__.py`: CLI entry point (parse args, load config, initialize components, call run_agent)
- `src/automation_agent/orchestrator/agent.py`: AutomationAgent.execute() — main orchestration loop
- `src/automation_agent/orchestrator/__main__.py`: Alternative CLI for orchestrator-only mode

**Configuration:**
- `src/automation_agent/config.py`: AgentConfig (Pydantic Settings with env var support)

**Core Logic:**
- `src/automation_agent/protocols.py`: Protocol classes (ActionPlanner, ScreenCoordinator, Actuator, SkillRegistry)
- `src/automation_agent/shared_models.py`: Shared dataclasses (ActionStep, ActionPlan, StepResult, FindElementResult, ReplanPatch)
- `src/automation_agent/orchestrator/agent.py`: Full execution flow, step verification, replanning, infeasibility detection (223KB — very large)
- `src/automation_agent/orchestrator/verifier.py`: 3-tier verification (Accessibility → Actuator state → Vision)

**Testing:**
- `tests/unit/conftest.py`: pytest fixtures, _make_config() helpers, common mocks
- `tests/unit/test_*.py`: 40+ test files (unit tests — fast, mocked)

## Naming Conventions

**Files:**
- Component implementation: `{component_name}.py` (e.g., `planner.py`, `coordinator.py`, `actuator.py`, `registry.py`)
- Configuration: `config.py`, `models.py`
- Tests: `test_{component}.py` or `test_{feature}.py`
- Prompts: `{purpose}.md` (e.g., `plan_from_prompt.md`, `find_element.md`)
- Skill templates: `{skill_name}.md` (e.g., `amazon_search.md`, `return_target_order.md`)

**Directories:**
- Components: lowercase, plural or singular (e.g., `planner`, `vision`, `skills`, `orchestrator`)
- Support: lowercase descriptive (e.g., `prompts`, `library`, `perception`, `logging`)

**Classes:**
- Implementations: `{Component}Impl` (e.g., `ActionPlannerImpl`, `ScreenCoordinatorImpl`, `SkillRegistryImpl`)
- Protocols: plain name (e.g., `ActionPlanner`, `ScreenCoordinator`, `Actuator`)
- Utility: descriptive (e.g., `StepVerifier`, `ContextMonitor`, `SkillLibrarian`, `EventLogger`)

**Functions & Methods:**
- Public: camelCase starting lowercase (e.g., `plan()`, `find_element()`, `verify()`, `match()`)
- Private: _camelCase with leading underscore (e.g., `_verify_tier0()`, `_parse_plan_response()`)
- Async functions: same naming, always declared as `async def`

**Variables & Constants:**
- Local variables: camelCase (e.g., `stepResult`, `coordinatorError`)
- Constants: UPPER_CASE (e.g., `TEXT_INPUT_AX_ROLES`, `COORDINATE_SPACES`)
- Dataclass fields: camelCase (e.g., `step.verify`, `result.success`)

**Enums:**
- Class name: PascalCase (e.g., `ModelProvider`, `LogLevel`, `ConfirmMode`, `EventType`)
- Values: UPPER_CASE (e.g., `LOCAL`, `ANTHROPIC`, `DEBUG`, `PLAN`)

## Where to Add New Code

**New Feature (Multi-Step Automation):**
- Primary code: `src/automation_agent/planner/planner.py` (extend ActionPlannerImpl.plan())
- Tests: `tests/unit/test_planner.py` or new `tests/unit/test_{feature}.py`
- If vision-related: add prompt template to `src/automation_agent/planner/prompts/`

**New Component/Module:**
- Implementation: Create new directory `src/automation_agent/{component}/` with `__init__.py`, `{component}.py`
- Protocol definition: Add to `src/automation_agent/protocols.py` if needed
- Factory: Add `create_{component}()` to `src/automation_agent/{component}/__init__.py`
- Integration: Wire into orchestrator at `src/automation_agent/__main__.py` (run_agent function)
- Tests: Create `tests/unit/test_{component}.py` with fixtures in `tests/unit/conftest.py`

**Utilities (Shared Helpers):**
- General: `src/automation_agent/shared_models.py` (if data structures) or new `src/automation_agent/utils.py`
- Vision-specific: `src/automation_agent/vision/geometry.py` or new `src/automation_agent/vision/utils.py`
- Orchestration-specific: `src/automation_agent/orchestrator/{utility}.py`

**New Skill Template:**
- Location: `src/automation_agent/skills/library/{skill_name}.md`
- Format: YAML frontmatter (name, trigger-keywords, parameters, requires) + Markdown steps with `{{param}}` placeholders and `verify` conditions
- Example: `src/automation_agent/skills/library/amazon_search.md` (simple search skill) or `return_amazon_order.md` (complex multi-step skill)

**New Prompt Template:**
- Location: Component-specific `src/automation_agent/{component}/prompts/{purpose}.md`
- Examples: `src/automation_agent/planner/prompts/plan_from_prompt.md`, `src/automation_agent/vision/prompts/find_element.md`

**New LLM Backend:**
- Implementation: `src/automation_agent/llm/{provider}_client.py`
- Inherit from: `src/automation_agent/llm/client.py` BaseClient
- Register in: `src/automation_agent/llm/__init__.py` factory
- Config: Add provider enum and settings to `src/automation_agent/config.py`

**Tests:**
- Unit tests: `tests/unit/test_*.py` (fast, all mocked)
- Integration tests: `tests/integration/test_*.py` (slower, real components)
- E2E tests: `tests/e2e/test_*.py` (live macOS desktop, requires manual intervention warning)

## Special Directories

**`prompts/`:**
- Purpose: Prompt templates used by components (Markdown files with Jinja2 or simple string interpolation)
- Generated: No (checked into version control)
- Committed: Yes (essential for component behavior)
- Locations: `src/automation_agent/planner/prompts/`, `src/automation_agent/vision/prompts/`, `src/automation_agent/skills/prompts/`

**`library/`:**
- Purpose: Skill template library (`.md` files with YAML frontmatter + Markdown steps)
- Generated: No (authored manually; skills may be created by SkillLibrarian at runtime)
- Committed: Yes (base skills checked in; generated/learned skills stored in logs)
- Location: `src/automation_agent/skills/library/`

**`logs/`:**
- Purpose: Runtime logs and event tracking
- Generated: Yes (created at runtime)
- Committed: No (git-ignored)
- Contents: `{run_id}/events.jsonl` (structured event logs), `skill_learning/` (observation sidecars), `benchmark_grounding_*.json` (grounding benchmarks)

**`tests/`:**
- Purpose: Full test suite
- Generated: No (test code checked in)
- Committed: Yes
- Structure: `unit/` (fast, mocked), `integration/` (medium, real components), `e2e/` (slow, live desktop)

---

*Structure analysis: 2026-03-20*
