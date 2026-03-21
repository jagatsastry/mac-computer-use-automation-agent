# Testing Patterns

**Analysis Date:** 2026-03-20

## Test Framework

**Runner:**
- pytest 8.0.0+
- Config: `pyproject.toml` under `[tool.pytest.ini_options]`
- Test discovery: `test_*.py` files in `tests/` directory

**Assertion Library:**
- pytest native assertions: `assert condition`, `assert len(list) == 2`
- pytest.raises for exception testing: `with pytest.raises(ValueError, match="Plan validation failed")`

**Run Commands:**
```bash
pytest                              # Run all tests
pytest tests/unit/                  # Unit tests only (fast, all mocked)
pytest tests/unit/test_planner.py   # Single test file
pytest -k "test_plan_basic"         # Single test by name
pytest -m unit                      # By marker (unit, integration, e2e, manual, legacy)
pytest -m "not e2e"                 # Skip e2e (requires live macOS desktop)
pytest --cov=src/                   # Coverage report
```

**Async Support:**
- pytest-asyncio 0.23.0+
- Config: `asyncio_mode = "auto"` in `pyproject.toml`
- Tests use `async def test_*()` directly; no `@pytest.mark.asyncio` needed

## Test File Organization

**Location:**
- Co-located in `tests/unit/` — separate from source code (not in `src/`)
- Large components have dedicated test file: `test_planner.py` for `planner/`, `test_coordinator.py` for `vision/`

**Naming:**
- `test_<component>.py`: `test_planner.py`, `test_vision_arch_improvements.py`, `test_grounding_pipeline.py`
- Nested test classes for organization: `TestPlanBasic`, `TestYAMLFrontmatter`, `TestBodySections`

**Structure:**
```
tests/
├── conftest.py                      # Shared fixtures for all tests
└── unit/
    ├── __init__.py
    ├── test_planner.py              # Tests for ActionPlannerImpl
    ├── test_vision_arch_improvements.py  # 79 tests, 4 recommendation areas
    ├── test_skill_registry.py        # Skill loading, parsing, expansion
    └── ... (27 test files total)
```

## Test Structure

**Suite Organization:**

Fixtures at module level with descriptive names:
```python
@pytest.fixture
def config():
    """Create a test AgentConfig."""
    return AgentConfig(_env_file=None, anthropic_api_key="test-key-not-real")

@pytest.fixture
def planner(config):
    """Create an ActionPlannerImpl with test config."""
    return ActionPlannerImpl(config)
```

Grouped into test classes with semantic names:
```python
class TestPlanBasic:
    """Tests for the plan() method — basic happy-path scenarios."""

    async def test_plan_returns_action_plan_with_verify_fields(self, planner):
        """1. plan() returns ActionPlan with all steps having verify fields."""
        planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

        plan = await planner.plan("Open Calculator")

        assert isinstance(plan, ActionPlan)
        assert len(plan.steps) == 2
        assert plan.steps[0].verify == "Calculator is the frontmost application"
```

**Patterns:**

Module-level helpers for test data:
```python
def _make_llm_response(steps_data: list, wrap_in_markdown: bool = False) -> dict:
    """Helper: build a mock LLM response dict from steps data."""
    payload = json.dumps({"steps": steps_data})
    if wrap_in_markdown:
        content = f"Here is the plan:\n```json\n{payload}\n```\n"
    else:
        content = payload
    return {
        "content": content,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }

VALID_STEPS = [
    {
        "action": "activate_app",
        "params": {"app_name": "Calculator"},
        "verify": "Calculator is the frontmost application",
        "on_fail": "retry_different",
    },
    # ...
]
```

Setup/teardown: Minimal use of fixtures; most state created via factory helpers
- No `@pytest.fixture` with side effects (cleanup handled by context managers or auto-cleanup via tmp_path)
- Example: `tmp_path` fixture (pytest built-in) for temporary directories

## Mocking

**Framework:** unittest.mock (Python stdlib)

**Patterns:**

AsyncMock for async functions:
```python
from unittest.mock import AsyncMock, MagicMock

planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))
coordinator.find_element = AsyncMock(
    return_value=FindElementResult(x=500, y=300, confidence=0.9, source="vision")
)
coordinator.describe_screen = AsyncMock(return_value="Desktop with Safari open")
```

MagicMock for sync methods:
```python
actuator = MagicMock()
actuator.click = MagicMock(return_value={"success": True, "output": ""})
actuator.get_state = MagicMock(
    return_value={"app_name": "Safari", "window_title": "Google"}
)
```

Spec-based mocking for interface safety:
```python
mock_capture = MagicMock(spec=ScreenCapture)
fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 6
mock_capture.capture_b64.return_value = base64.b64encode(fake_jpeg).decode()
```

Patching external calls:
```python
with patch("subprocess.run", return_value=mock_result):
    result = actuator.get_accessibility_elements()

with patch("automation_agent.vision.coordinator.ScreenCoordinatorImpl._call_vision_model") as mock:
    # Test code
```

**What to Mock:**
- External service calls: LLM endpoints, vision servers, subprocess calls
- I/O operations: file system (use `tmp_path` instead), network calls
- Cross-component boundaries: always mock dependencies (planner, coordinator, actuator)
- Time-dependent operations: patch `time.monotonic()` for duration testing

**What NOT to Mock:**
- Dataclass construction: always use real `ActionStep`, `ActionPlan`, etc.
- Validation logic: test real validation paths (e.g., `ActionPlan.validate()`)
- Local filesystem operations: use `tmp_path` fixture instead
- Configuration objects: use real `AgentConfig` (may override specific fields)

Example fixture factory in `conftest.py`:
```python
@pytest.fixture
def mock_planner():
    """Mock ActionPlanner for testing."""
    planner = AsyncMock()
    planner.plan = AsyncMock(
        return_value=ActionPlan(
            steps=[
                ActionStep(
                    action="activate_app",
                    params={"app_name": "Safari"},
                    verify="Safari is frontmost app",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Open Safari",
        )
    )
    planner.replan = AsyncMock(...)
    planner.check_infeasibility = AsyncMock(
        return_value={"infeasible": False, "reason": "Still achievable"}
    )
    return planner
```

## Fixtures and Factories

**Test Data:**

Central `conftest.py` defines reusable fixtures:
```python
@pytest.fixture
def sample_action_step():
    """A sample ActionStep with all fields populated."""
    return ActionStep(
        action="click",
        params={"x": 500, "y": 300},
        verify="Button state changes to 'pressed'",
        on_fail="retry_different",
        max_retries=3,
    )

@pytest.fixture
def sample_action_plan():
    """A sample ActionPlan with multiple steps."""
    return ActionPlan(
        steps=[
            ActionStep(
                action="activate_app",
                params={"app_name": "Calculator"},
                verify="Calculator app is in foreground",
            ),
            # ... more steps
            ActionStep(action="done", params={}, verify=""),
        ],
        goal="Calculate 2 + 2",
    )
```

Local helper factories in individual test files:
```python
def _make_config(**overrides):
    """Return an AgentConfig for tests, no real log dir needed."""
    defaults = {
        "vision_model": "molmo",
        "model_provider": "local",
        "log_dir": "/tmp/test_arch_improvement_logs",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)

def _make_jpeg_b64(width: int = 100, height: int = 100) -> str:
    """Return a minimal valid JPEG as base64."""
    from PIL import Image
    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()
```

**Location:**
- Global fixtures: `tests/conftest.py`
- Component-specific fixtures: top of individual test files (e.g., `test_vision_arch_improvements.py` defines `_make_agent()`)
- No external fixture plugins; all fixtures defined inline or in conftest.py

## Coverage

**Requirements:** Not enforced by tests (no minimum coverage threshold configured)

**View Coverage:**
```bash
pytest --cov=src/automation_agent tests/unit/ --cov-report=html
# Open htmlcov/index.html
```

**Test Markers:**

Defined in `pyproject.toml`:
```python
markers = [
    "unit: Unit tests (fast, all dependencies mocked)",
    "integration: Integration tests (may use real local services)",
    "e2e: End-to-end tests (real everything, requires macOS desktop)",
    "manual: Manual/interactive tests (require human observation)",
    "legacy: Tests for the old actions/ package (retained until removal)",
]
```

Usage:
```python
@pytest.mark.integration
async def test_planner_with_real_ollama():
    # Requires AGENT_TEXT_SERVER_URL pointing to running Ollama

@pytest.mark.e2e
async def test_full_automation_flow():
    # Requires macOS desktop, real apps, real focus
```

## Test Types

**Unit Tests:**
- Scope: Single component in isolation
- Approach: All external dependencies mocked (AsyncMock, MagicMock, patch)
- Location: `tests/unit/`
- Example: `test_planner.py` tests `ActionPlannerImpl.plan()` with mocked `_call_llm`
- Execution time: < 1s per test (< 30s full suite)

**Integration Tests:**
- Scope: Multiple components + local services (e.g., real Ollama server)
- Approach: Real service calls (not mocked), internal components integrated
- Marker: `@pytest.mark.integration`
- Example: Grounding pipeline tests that call real vision model

**E2E Tests:**
- Scope: Full system with real macOS desktop, real apps, real focus
- Approach: No mocking; agent actually performs desktop actions
- Marker: `@pytest.mark.e2e`
- Warning: User must step away from keyboard during test (`--status-ui overlay` required)
- Example: "Complete a full purchase flow on target.com"
- Execution: `pytest -m e2e` (skipped by default in CI)

## Common Patterns

**Async Testing:**

Test async functions directly with `async def`:
```python
async def test_plan_returns_action_plan_with_verify_fields(self, planner):
    """1. plan() returns ActionPlan with all steps having verify fields."""
    planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

    plan = await planner.plan("Open Calculator")  # Awaited call

    assert isinstance(plan, ActionPlan)
```

Multiple async calls in sequence:
```python
async def test_replan_with_skill_context(self, planner):
    planner._call_llm = AsyncMock(return_value=_make_llm_response(VALID_STEPS))

    # First plan
    plan1 = await planner.plan("Open Safari")
    assert plan1.goal == "Open Safari"

    # Then replan
    plan2 = await planner.replan(
        goal="Open Safari",
        screen_description="Still on desktop",
        history=[],
        retry_strategies_used=[],
    )
    assert plan2.goal == "Open Safari"
```

Awaiting mocked async calls:
```python
coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())
result = await coordinator.capture_screenshot()
assert isinstance(result, str)  # base64
```

**Error Testing:**

Validation errors raised by dataclasses:
```python
async def test_plan_rejects_empty_verify(self, planner):
    """4. plan() rejects LLM response where any step has empty verify."""
    steps_missing_verify = [
        {
            "action": "activate_app",
            "params": {"app_name": "Calculator"},
            "verify": "",  # Invalid — non-terminal step with empty verify
            "on_fail": "retry_different",
        },
    ]
    planner._call_llm = AsyncMock(
        return_value=_make_llm_response(steps_missing_verify)
    )

    with pytest.raises(ValueError, match="Plan validation failed"):
        await planner.plan("Open Calculator")
```

Exception matching:
```python
with pytest.raises(ValueError, match="frontmatter"):
    parse_skill_file("No frontmatter here\nJust text")

with pytest.raises(ValueError, match="Unknown action"):
    ActionStep(action="invalid_action", params={}, verify="test")
```

Testing retry logic:
```python
async def test_click_retries_on_confidence_failure(self, tmp_path):
    agent = _make_agent(tmp_path)

    # First call: low confidence (retry)
    # Second call: higher confidence (success)
    coordinator.find_element = AsyncMock(
        side_effect=[
            FindElementResult(x=100, y=100, confidence=0.4, source="vision"),
            FindElementResult(x=100, y=100, confidence=0.85, source="vision"),
        ]
    )

    await agent.execute("Click the button")
    # Verify click was called once (after second find_element)
    actuator.click.assert_called_once()
```

**Capture/Assertion with side_effect:**
```python
coordinator.find_element = AsyncMock(
    side_effect=[
        FindElementResult(x=400, y=300, confidence=0.8, source="vision"),
        FindElementResult(x=400, y=300, confidence=0.9, source="vision"),
    ]
)
# First call returns 0.8, second returns 0.9
```

## Gotchas & Best Practices

**Config in Tests:**
- Pin `model_provider="local"` in test configs to avoid API key validation errors
- `.env` may leak `AGENT_MODEL_PROVIDER=anthropic` from development environment
- Use `_env_file=None` in AgentConfig constructor to avoid loading `.env`

Example:
```python
@pytest.fixture
def config():
    return AgentConfig(_env_file=None, model_provider="local", log_dir="/tmp/test")
```

**Fixture Scope:**
- Default scope is `"function"` (fixture recreated per test)
- Use `scope="module"` only for expensive setup (e.g., shared mock server)
- `tmp_path` fixture (pytest built-in) is function-scoped and auto-cleaned

**Keyword Arguments in Mock Verification:**
- Always use keyword args when calling verifier or coordinator methods to avoid swapping positional args
- Example: `verifier.verify(step=step, coordinator=coord, actuator=act)` not `verifier.verify(coord, act, step)`

**Error Message Precision:**
- Use `match=` parameter in `pytest.raises` to verify specific error messages
- Helps catch overly broad exception handling

Example:
```python
with pytest.raises(ValueError, match="Unknown action 'foo'"):
    ActionStep(action="foo", params={}, verify="test")
```

**Screenshot/Image Test Data:**
- Create minimal valid JPEGs: `_make_jpeg_b64(width=100, height=100)`
- For hi-res tests: `_make_wide_jpeg_b64(width=2560, height=1440)`
- Store in memory as base64 strings (no fixtures file dependency)

---

*Testing analysis: 2026-03-20*
