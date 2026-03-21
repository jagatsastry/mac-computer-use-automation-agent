# Coding Conventions

**Analysis Date:** 2026-03-20

## Naming Patterns

**Files:**
- Lowercase with underscores: `shared_models.py`, `applescript_actuator.py`
- Test files: `test_<component>.py` (e.g., `test_planner.py`)
- Implementation classes often have `Impl` suffix: `ActionPlannerImpl`, `ScreenCoordinatorImpl`, `SkillRegistryImpl`

**Functions:**
- Lowercase with underscores: `describe_screen()`, `find_element()`, `get_state()`
- Private/internal functions: leading underscore `_run_osascript()`, `_escape_for_applescript()`, `_verify_tier0()`
- Helper functions in tests: `_make_config()`, `_make_llm_response()`, `_make_agent()`
- Async functions: `async def` with same naming conventions (e.g., `async def plan()`)

**Variables:**
- Lowercase with underscores: `config`, `step_result`, `screenshot_b64`
- Constants: UPPERCASE: `TIMEOUT_SECONDS`, `TEXT_INPUT_AX_ROLES`
- Frozen/immutable collections: `_SCROLL_DIRS_INCREASING`, `_ACTION_ALIASES`
- Shorthand in internal code: `slog` for structlog logger, `coord` for coordinator, `act` for actuator

**Types:**
- Dataclasses: PascalCase: `ActionStep`, `ActionPlan`, `StepResult`, `FindElementResult`
- Enums: PascalCase: `ModelProvider`, `LogLevel`, `ConfirmMode`, `MatchType`
- Protocols: PascalCase with suffix: `ActionPlanner`, `ScreenCoordinator`, `Actuator`

## Code Style

**Formatting:**
- Tool: Black (configured in `pyproject.toml`)
- Line length: 100 characters (Black enforces)
- Target version: Python 3.11

**Linting:**
- Tool: Ruff
- Selected rules: E, W, F, I, B, C4, UP
- Ignored rules: E501 (line too long, enforced by Black), B008 (function call default argument)

**Type Hints:**
- Required for public functions and method signatures
- Used throughout: `def plan(self, goal: str, ...) -> ActionPlan`
- Optional imports handled: `Optional[str]`, `List[Dict[str, Any]]`
- Python 3.9 compatible: StrEnum polyfill in `shared_models.py` for pre-3.11

## Import Organization

**Order:**
1. Standard library: `import json`, `import asyncio`, `from pathlib import Path`
2. Third-party: `import structlog`, `from pydantic import Field`, `import pytest`
3. Local application: `from automation_agent.config import AgentConfig`, `from .shared_models import ActionStep`

**Path Aliases:**
- Absolute imports preferred: `from automation_agent.config import AgentConfig`
- No @ aliases detected; flat structure relies on package imports
- Relative imports used within packages: `from .shared_models import ActionPlan` in `protocols.py`

**Ruff sorting:** Configured in `pyproject.toml` under `tool.ruff.lint` (rule "I" enforces import sorting)

## Error Handling

**Patterns:**
- Exceptions raised for validation failures: `raise ValueError(f"Unknown action '{self.action}'...")`
- Descriptive error messages with context: "Unknown action 'foo'. Valid actions: {...}"
- Custom exception mapping in dataclass factories: `ActionStep.from_dict()` maps common LLM misspellings to valid actions
- None returns for graceful degradation: `find_element()` returns `Optional[FindElementResult]` (None if not found)
- Three-tier fallback in verification: Accessibility API → Actuator state → Vision screenshot

**Try/Except:**
- Used sparingly; most functions validate inputs before execution
- `_run_osascript()` wraps subprocess calls: catches `TimeoutExpired` → returns `ActuatorResult(success=False, error=...)`
- Browser activation in `_activate_browser()` has broad `except Exception: pass` (non-critical pre-action setup)
- AppleScript availability check: `is_available()` returns boolean, wraps subprocess call

**Preconditions/Postconditions:**
- **Mandatory postconditions:** Every `ActionStep` must have non-empty `verify` field (enforced in `ActionPlan.validate()`)
- **Optional preconditions:** `ActionStep.precondition` field chains conditions: step N's `verify` should match step N+1's `precondition`
- Plans fail validation if non-terminal step has empty `verify`

## Logging

**Framework:** structlog (configured in `src/automation_agent/logging/`)

**Pattern:**
```python
import structlog
logger = structlog.get_logger(__name__)
logger.info("📋 Planning started", goal=goal)
logger.debug("verify_tier0_inconclusive", condition=step.verify[:80], duration_ms=_t0_dur)
logger.warning("Element not found, fallback to vision", element=description[:50])
```

**Conventions:**
- First argument is human-readable message (emoji prefixed for visibility)
- Subsequent arguments are structured key-value pairs
- Sensitive data truncated: `condition=step.verify[:80]` (first 80 chars)
- Timing included: `duration_ms=int((time.monotonic() - start) * 1000)`
- Log levels: info (major milestones), debug (detailed traces), warning (recoverable failures)

## Comments

**When to Comment:**
- Complex algorithm explanations: 3-tier verification flow in `verifier.py` (line 4-6)
- Non-obvious design decisions: "Ensure browser is frontmost before clicking — other apps may have stolen focus"
- Browser-specific quirks: "Some web UIs need the hover state to register before the link becomes clickable"
- Workarounds for external system limitations: JS injection, SafariJavaScript prerequisites, float16 overflow in MLX

**JSDoc/TSDoc:**
- Not used; Python docstrings follow PEP 257
- Triple-quoted docstrings on functions, classes, modules
- Format: Brief description on first line, blank line, detailed explanation (if needed)

Example from `planner.py`:
```python
async def plan(
    self,
    goal: str,
    screen_description: str = "",
    skill_context: Optional[str] = None,
) -> ActionPlan:
    """Generate action plan. ALL steps must have non-empty 'verify' fields.

    Args:
        goal: Natural language description of what to accomplish.
        screen_description: Current screen state description.
        skill_context: Optional expanded skill template for context.

    Returns:
        ActionPlan with validated steps.

    Raises:
        ValueError: If the LLM response cannot be parsed or validation fails.
    """
```

## Function Design

**Size:** Generally 20-100 lines; internal helpers keep public functions focused
- Example: `_verify_tier0()`, `_verify_tier1()`, `_verify_tier2()` break down `verify()` into testable phases

**Parameters:**
- Positional args for required inputs: `verify(self, step: ActionStep, actuator_result: Dict[str, Any])`
- Keyword-only args (`*`) for optional inputs: `async def _call_llm(self, prompt: str, *, image_b64: Optional[str] = None)`
- Dataclass parameters passed as objects, not unpacked: `step: ActionStep` not `action, params, verify, ...`

**Return Values:**
- Dataclass returns for complex results: `FindElementResult(x=int, y=int, confidence=float, source=str, ...)`
- Dict returns for backwards compatibility: `actuator.click()` returns `Dict[str, Any]` (converted from `ActuatorResult.to_dict()`)
- Tuples for internal helpers: `_verify_tier0()` returns `Tuple[bool, str] | None` (success, evidence)
- Optional returns for failure cases: `find_element()` returns `Optional[FindElementResult]`

## Module Design

**Exports:**
- Main implementation classes exported via `__init__.py` in each component package
- Example: `src/automation_agent/planner/__init__.py` exports `ActionPlannerImpl`
- Protocols defined in central `protocols.py` for dependency injection

**Barrel Files:**
- Used: `src/automation_agent/__init__.py` exports `AgentConfig`, `get_logger`, `__version__`
- Pattern: Explicit imports, no `from .module import *`

**Protocol-based Design:**
- All components implement `@runtime_checkable` protocols
- No inheritance; composition via duck typing
- Example: `ActionPlanner` protocol in `protocols.py` defines interface; `ActionPlannerImpl` satisfies it
- Capabilities advertised via `capabilities()` method: `CoordinatorCapability` enum

## Configuration & Constants

**Configuration:**
- Pydantic BaseSettings in `config.py`
- Environment prefix: `AGENT_` (e.g., `AGENT_MODEL_PROVIDER`, `AGENT_VISION_SERVER_URL`)
- Per-step model routing: `config.resolve_step_model("planning")` returns `(provider, model)` tuple
- Field validation: `@field_validator` decorators for Anthropic/Gemini API key fallback

**Aliasing & Mappings:**
- LLM action misspellings mapped in `shared_models.py`: `_ACTION_ALIASES` dict
- On-fail aliases: `_ON_FAIL_ALIASES` for common LLM mistakes (e.g., "skip" → "abort")
- Browser key codes in `applescript_actuator.py`: `key_codes` dict with lowercase keys

---

*Convention analysis: 2026-03-20*
