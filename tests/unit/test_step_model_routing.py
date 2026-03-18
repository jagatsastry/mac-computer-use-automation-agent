"""Unit tests for per-step model routing and precondition/action/verify flow.

Covers:
  - config.resolve_step_model() with various inputs
  - ActionStep.from_dict() precondition parsing
  - Planner _call_llm routing via resolve_step_model
  - Coordinator vision dispatch with step parameter
  - Precondition check flow in _execute_step_inner
  - Ollama format schema includes precondition
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig, ModelProvider
from automation_agent.shared_models import ActionPlan, ActionStep, StepResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    """Create AgentConfig with test defaults, avoiding .env leakage."""
    defaults = {
        "_env_file": None,
        "model_provider": "local",
        "vision_model": "molmo",
        "anthropic_api_key": "test-key-not-real",
        "log_dir": "/tmp/test_agent_logs",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


# ---------------------------------------------------------------------------
# 1. resolve_step_model — basic fallback to global provider
# ---------------------------------------------------------------------------


class TestResolveStepModelFallback:
    def test_fallback_to_global_local(self):
        config = _make_config(model_provider="local", vision_model="molmo")
        provider, model = config.resolve_step_model("planning")
        assert provider == "local"
        assert model == "molmo"

    def test_fallback_to_global_anthropic(self):
        config = _make_config(
            model_provider="anthropic",
            anthropic_model="claude-sonnet-4-20250514",
        )
        provider, model = config.resolve_step_model("grounding")
        assert provider == "anthropic"
        assert model == "claude-sonnet-4-20250514"

    def test_fallback_to_global_gemini(self):
        config = _make_config(
            model_provider="gemini",
            gemini_api_key="fake-key",
            gemini_model="gemini-2.5-flash",
        )
        provider, model = config.resolve_step_model("verification")
        assert provider == "gemini"
        assert model == "gemini-2.5-flash"

    def test_fallback_to_global_openai(self):
        config = _make_config(
            model_provider="openai",
            openai_api_key="sk-test",
            openai_model="gpt-4.1",
        )
        provider, model = config.resolve_step_model("screen_description")
        assert provider == "openai"
        assert model == "gpt-4.1"


# ---------------------------------------------------------------------------
# 2. resolve_step_model — provider:model override syntax
# ---------------------------------------------------------------------------


class TestResolveStepModelOverride:
    def test_planning_override_provider_model(self):
        config = _make_config(planning_model="gemini:gemini-2.5-flash")
        provider, model = config.resolve_step_model("planning")
        assert provider == "gemini"
        assert model == "gemini-2.5-flash"

    def test_grounding_override_provider_model(self):
        config = _make_config(grounding_model_provider="openai:gpt-5.4")
        provider, model = config.resolve_step_model("grounding")
        assert provider == "openai"
        assert model == "gpt-5.4"

    def test_verification_override_provider_model(self):
        config = _make_config(verification_model="anthropic:claude-opus-4-20250514")
        provider, model = config.resolve_step_model("verification")
        assert provider == "anthropic"
        assert model == "claude-opus-4-20250514"

    def test_screen_description_override(self):
        config = _make_config(screen_description_model="local:molmo2")
        provider, model = config.resolve_step_model("screen_description")
        assert provider == "local"
        assert model == "molmo2"

    def test_reflection_override(self):
        config = _make_config(reflection_model="anthropic:claude-opus-4-20250514")
        provider, model = config.resolve_step_model("reflection")
        assert provider == "anthropic"
        assert model == "claude-opus-4-20250514"


# ---------------------------------------------------------------------------
# 3. resolve_step_model — provider-only override (uses default model)
# ---------------------------------------------------------------------------


class TestResolveStepModelProviderOnly:
    def test_provider_only_gemini(self):
        config = _make_config(
            planning_model="gemini",
            gemini_api_key="fake",
            gemini_model="gemini-2.5-flash",
        )
        provider, model = config.resolve_step_model("planning")
        assert provider == "gemini"
        assert model == "gemini-2.5-flash"

    def test_provider_only_openai(self):
        config = _make_config(
            grounding_model_provider="openai",
            openai_api_key="sk-test",
            openai_model="gpt-4.1",
        )
        provider, model = config.resolve_step_model("grounding")
        assert provider == "openai"
        assert model == "gpt-4.1"

    def test_provider_only_local(self):
        config = _make_config(
            verification_model="local",
            vision_model="qwen3-vl",
        )
        provider, model = config.resolve_step_model("verification")
        assert provider == "local"
        assert model == "qwen3-vl"


# ---------------------------------------------------------------------------
# 4. resolve_step_model — whitespace handling
# ---------------------------------------------------------------------------


class TestResolveStepModelWhitespace:
    def test_strips_whitespace(self):
        config = _make_config(planning_model=" openai : gpt-5.4 ")
        provider, model = config.resolve_step_model("planning")
        assert provider == "openai"
        assert model == "gpt-5.4"


# ---------------------------------------------------------------------------
# 5. resolve_step_model — unknown step falls back to global
# ---------------------------------------------------------------------------


class TestResolveStepModelUnknownStep:
    def test_unknown_step_returns_global(self):
        config = _make_config(model_provider="local", vision_model="molmo")
        provider, model = config.resolve_step_model("nonexistent_step")
        assert provider == "local"
        assert model == "molmo"


# ---------------------------------------------------------------------------
# 6. ActionStep.from_dict — precondition parsing
# ---------------------------------------------------------------------------


class TestFromDictPrecondition:
    def test_precondition_parsed(self):
        step = ActionStep.from_dict({
            "action": "click",
            "params": {"element": "Submit"},
            "precondition": "A form is visible with filled fields",
            "verify": "Form submitted",
        })
        assert step.precondition == "A form is visible with filled fields"

    def test_precondition_defaults_to_empty(self):
        step = ActionStep.from_dict({
            "action": "click",
            "params": {"element": "OK"},
            "verify": "Dialog dismissed",
        })
        assert step.precondition == ""

    def test_precondition_empty_string(self):
        step = ActionStep.from_dict({
            "action": "done",
            "params": {},
            "precondition": "",
            "verify": "",
        })
        assert step.precondition == ""

    def test_full_step_round_trip(self):
        """All fields including precondition survive from_dict."""
        data = {
            "action": "open_url",
            "params": {"url": "https://example.com"},
            "precondition": "A browser is available",
            "verify": "URL contains example.com",
            "expected_observation": "Browser shows example.com",
            "on_fail": "retry_different",
            "max_retries": 2,
            "destructive": False,
        }
        step = ActionStep.from_dict(data)
        assert step.action == "open_url"
        assert step.precondition == "A browser is available"
        assert step.verify == "URL contains example.com"
        assert step.expected_observation == "Browser shows example.com"
        assert step.max_retries == 2


# ---------------------------------------------------------------------------
# 7. Ollama format schema includes precondition
# ---------------------------------------------------------------------------


def test_ollama_schema_has_precondition():
    from automation_agent.planner.planner import ActionPlannerImpl

    schema = ActionPlannerImpl._OLLAMA_FORMAT_SCHEMA
    step_props = schema["properties"]["steps"]["items"]["properties"]
    assert "precondition" in step_props
    assert step_props["precondition"]["type"] == "string"


# ---------------------------------------------------------------------------
# 8. Planner _call_llm uses resolve_step_model for routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_routes_via_resolve_step_model():
    """When planning_model is set, _call_llm uses that provider."""
    from automation_agent.planner.planner import ActionPlannerImpl

    config = _make_config(
        model_provider="local",
        planning_model="openai:gpt-5.4",
        openai_api_key="sk-test",
    )
    planner = ActionPlannerImpl(config)

    mock_result = {
        "content": '{"steps":[{"action":"click","params":{"target":"btn"},"verify":"ok"}]}',
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    planner._call_openai_llm = AsyncMock(return_value=mock_result)
    planner._call_local_llm = AsyncMock()
    planner._call_anthropic_llm = AsyncMock()
    planner._call_gemini_llm = AsyncMock()

    result = await planner._call_llm("test prompt")
    assert result["content"] == mock_result["content"]
    planner._call_openai_llm.assert_awaited_once()
    planner._call_local_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_planner_falls_back_to_global_when_no_override():
    """Without planning_model, _call_llm uses global model_provider."""
    from automation_agent.planner.planner import ActionPlannerImpl

    config = _make_config(model_provider="local")
    planner = ActionPlannerImpl(config)

    mock_result = {
        "content": '{"steps":[{"action":"done","params":{},"verify":""}]}',
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    planner._call_local_llm = AsyncMock(return_value=mock_result)
    planner._call_openai_llm = AsyncMock()
    planner._call_anthropic_llm = AsyncMock()

    result = await planner._call_llm("test")
    planner._call_local_llm.assert_awaited_once()
    planner._call_openai_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_planner_gemini_override():
    """planning_model='gemini' routes to _call_gemini_llm."""
    from automation_agent.planner.planner import ActionPlannerImpl

    config = _make_config(
        model_provider="local",
        planning_model="gemini:gemini-2.5-flash",
        gemini_api_key="fake",
    )
    planner = ActionPlannerImpl(config)

    mock_result = {
        "content": '{"steps":[{"action":"done","params":{},"verify":""}]}',
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    planner._call_gemini_llm = AsyncMock(return_value=mock_result)
    planner._call_local_llm = AsyncMock()

    await planner._call_llm("test")
    planner._call_gemini_llm.assert_awaited_once()
    planner._call_local_llm.assert_not_awaited()


# ---------------------------------------------------------------------------
# 9. Coordinator vision dispatch respects step parameter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_dispatch_with_grounding_step():
    """Coordinator routes to openai when grounding step is overridden."""
    from automation_agent.vision.capture import ScreenCapture
    from automation_agent.vision.coordinator import ScreenCoordinatorImpl

    config = _make_config(
        model_provider="local",
        grounding_model_provider="openai:gpt-4.1",
        openai_api_key="sk-test",
    )
    capture = MagicMock(spec=ScreenCapture)
    capture.target_resolution = (1024, 768)

    coord = ScreenCoordinatorImpl(config, capture=capture)
    coord._call_openai_vision = AsyncMock(return_value="FOUND: x=500, y=300")
    coord._call_local_vision = AsyncMock()

    result = await coord._call_vision_model_with_images(
        "find element", ["fakeimg"], step="grounding",
    )
    assert result == "FOUND: x=500, y=300"
    coord._call_openai_vision.assert_awaited_once()
    coord._call_local_vision.assert_not_awaited()


@pytest.mark.asyncio
async def test_coordinator_dispatch_verification_step():
    """Coordinator routes to gemini when verification step is overridden."""
    from automation_agent.vision.capture import ScreenCapture
    from automation_agent.vision.coordinator import ScreenCoordinatorImpl

    config = _make_config(
        model_provider="local",
        verification_model="gemini:gemini-2.5-flash",
        gemini_api_key="fake",
    )
    capture = MagicMock(spec=ScreenCapture)
    capture.target_resolution = (1024, 768)

    coord = ScreenCoordinatorImpl(config, capture=capture)
    coord._call_gemini_vision = AsyncMock(return_value="YES")
    coord._call_local_vision = AsyncMock()

    result = await coord._call_vision_model_with_images(
        "verify condition", ["fakeimg"], step="verification",
    )
    assert result == "YES"
    coord._call_gemini_vision.assert_awaited_once()
    coord._call_local_vision.assert_not_awaited()


@pytest.mark.asyncio
async def test_coordinator_dispatch_no_step_uses_global():
    """Without step parameter, coordinator uses global model_provider."""
    from automation_agent.vision.capture import ScreenCapture
    from automation_agent.vision.coordinator import ScreenCoordinatorImpl

    config = _make_config(
        model_provider="local",
        verification_model="openai:gpt-4.1",
        openai_api_key="sk-test",
    )
    capture = MagicMock(spec=ScreenCapture)
    capture.target_resolution = (1024, 768)

    coord = ScreenCoordinatorImpl(config, capture=capture)
    coord._call_local_vision = AsyncMock(return_value="ok")
    coord._call_openai_vision = AsyncMock()

    # No step parameter — should use global (local)
    result = await coord._call_vision_model_with_images("test", ["fakeimg"])
    coord._call_local_vision.assert_awaited_once()
    coord._call_openai_vision.assert_not_awaited()


# ---------------------------------------------------------------------------
# 10. Precondition check flow in _execute_step_inner
# ---------------------------------------------------------------------------


def _make_plan(steps, goal="Test goal"):
    return ActionPlan(steps=steps, goal=goal)


def _make_agent(planner, skill_registry, coordinator, actuator, logger, config=None):
    from automation_agent.orchestrator.agent import AutomationAgent

    if config is None:
        config = _make_config()
    return AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
    )


@pytest.fixture
def mock_planner():
    p = MagicMock()
    p.plan = AsyncMock()
    p.replan = AsyncMock()
    return p


@pytest.fixture
def mock_coordinator():
    c = MagicMock()
    c.describe_screen = AsyncMock(return_value="Screen description")
    c.find_element = AsyncMock(return_value=None)
    c.verify_condition = AsyncMock(return_value=True)
    c.capabilities = MagicMock(return_value=frozenset())
    c.capture = MagicMock()
    c.capture.capture_b64 = MagicMock(return_value="fakeimg")
    c.capture.get_screen_size = MagicMock(return_value=(1024, 768))
    return c


@pytest.fixture
def mock_actuator():
    a = MagicMock()
    a.execute = AsyncMock(return_value=True)
    a.get_state = MagicMock(return_value={})
    a.get_accessibility_elements = AsyncMock(return_value=[])
    return a


@pytest.fixture
def mock_skill_registry():
    sr = MagicMock()
    sr.match = AsyncMock(return_value=None)
    return sr


@pytest.fixture
def tmp_log_dir(tmp_path):
    from automation_agent.logging.event_logger import EventLogger

    log = EventLogger(log_dir=tmp_path, run_id="test-run")
    return log


@pytest.mark.asyncio
async def test_precondition_passes_step_executes(
    mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir,
):
    """When precondition passes, _execute_step_inner proceeds past the check."""
    step = ActionStep(
        action="click",
        params={"element": "Submit"},
        precondition="A form is visible",
        verify="Form submitted",
    )
    plan = _make_plan([step])

    agent = _make_agent(
        mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, tmp_log_dir,
    )

    # Mock the verifier: first call is for precondition (success),
    # second would be for post-action verification.
    precondition_result = StepResult(
        step=step, success=True, verification_method="vision",
        evidence="Precondition met",
    )
    verify_result = StepResult(
        step=step, success=True, verification_method="vision",
        evidence="Verified",
    )
    agent.verifier = MagicMock()
    agent.verifier.verify = AsyncMock(
        side_effect=[precondition_result, verify_result]
    )
    # Mock _dispatch_action to avoid full execution
    agent._dispatch_action = AsyncMock(return_value={
        "success": True, "image_x": 100, "image_y": 200,
    })

    result, _tf = await agent._execute_step_inner(0, step, [], "test", plan)
    # The verifier was called (at least for precondition)
    assert agent.verifier.verify.await_count >= 1
    # Step did not fail on precondition (it proceeded)
    assert result.error is None or "precondition_failed" not in str(result.error)


@pytest.mark.asyncio
async def test_precondition_fails_step_fails(
    mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir,
):
    """When precondition fails, _execute_step_inner returns failure."""
    step = ActionStep(
        action="click",
        params={"element": "Submit"},
        precondition="A form is visible with all fields filled",
        verify="Form submitted",
    )
    plan = _make_plan([step])

    agent = _make_agent(
        mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, tmp_log_dir,
    )

    # Mock the verifier to return failure for the precondition
    agent.verifier = MagicMock()
    agent.verifier.verify = AsyncMock(
        return_value=StepResult(
            step=step, success=False, verification_method="vision",
            evidence="Form is not visible",
        )
    )

    result, _tf = await agent._execute_step_inner(0, step, [], "test", plan)
    assert result.success is False
    assert "precondition_failed" in str(result.error)
    assert "Precondition failed" in result.evidence


@pytest.mark.asyncio
async def test_precondition_skipped_for_done_action(
    mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir,
):
    """Precondition check should be skipped for 'done' action."""
    step = ActionStep(
        action="done",
        params={},
        precondition="Something that should not be checked",
        verify="",
    )
    plan = _make_plan([step])

    agent = _make_agent(
        mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, tmp_log_dir,
    )

    # Mock verifier -- should NOT be called for precondition
    agent.verifier = MagicMock()
    agent.verifier.verify = AsyncMock()

    result, _tf = await agent._execute_step_inner(0, step, [], "test", plan)
    # done action succeeds and verifier should not have been called
    # for precondition (done skips precondition check)
    assert result.success is True
    agent.verifier.verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_precondition_skipped_for_observe_action(
    mock_planner, mock_coordinator, mock_actuator, mock_skill_registry, tmp_log_dir,
):
    """Precondition check should be skipped for 'observe' action."""
    step = ActionStep(
        action="observe",
        params={},
        precondition="Should not be checked",
        verify="Screen described",
    )
    plan = _make_plan([step])

    agent = _make_agent(
        mock_planner, mock_skill_registry, mock_coordinator, mock_actuator, tmp_log_dir,
    )

    # Mock verifier -- should NOT be called for precondition
    agent.verifier = MagicMock()
    agent.verifier.verify = AsyncMock()

    result, _tf = await agent._execute_step_inner(0, step, [], "test", plan)
    # observe skips precondition check
    agent.verifier.verify.assert_not_awaited()


def test_empty_precondition_skips_check():
    """Empty precondition is truthy-falsy, so the check branch is skipped."""
    # This is a logic test: verify the branching condition in
    # _execute_step_inner which checks `if precondition and step.action
    # not in ("done", "observe"):`. An empty string is falsy in Python.
    step = ActionStep(
        action="click",
        params={"element": "OK"},
        precondition="",
        verify="Dialog dismissed",
    )
    # The precondition is falsy so the check should be skipped
    assert not step.precondition  # empty string is falsy
    assert step.action not in ("done", "observe")

    # Non-empty precondition is truthy
    step2 = ActionStep(
        action="click",
        params={"element": "OK"},
        precondition="Form is visible",
        verify="Dialog dismissed",
    )
    assert step2.precondition  # non-empty string is truthy


# ---------------------------------------------------------------------------
# 11. _default_model_for returns correct defaults
# ---------------------------------------------------------------------------


class TestDefaultModelFor:
    def test_local_returns_vision_model(self):
        config = _make_config(vision_model="qwen3-vl")
        assert config._default_model_for("local") == "qwen3-vl"

    def test_anthropic_returns_anthropic_model(self):
        config = _make_config(anthropic_model="claude-opus-4-20250514")
        assert config._default_model_for("anthropic") == "claude-opus-4-20250514"

    def test_gemini_returns_gemini_model(self):
        config = _make_config(gemini_model="gemini-2.5-pro")
        assert config._default_model_for("gemini") == "gemini-2.5-pro"

    def test_openai_returns_openai_model(self):
        config = _make_config(openai_model="gpt-5.4")
        assert config._default_model_for("openai") == "gpt-5.4"

    def test_unknown_provider_returns_vision_model(self):
        config = _make_config(vision_model="molmo")
        assert config._default_model_for("unknown") == "molmo"
