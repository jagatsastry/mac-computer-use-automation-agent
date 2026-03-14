"""AC-6: Horizontal scroll verification tests + PB4/PB5/PB7 review tests.

Tests cover:
- AC-6a: get_scroll_position(axis=) in applescript_actuator.py
- AC-6b: Axis-parametric S1 scroll verification in verifier.py
- AC-6c: Structured _scroll_before metadata in agent.py dispatch
- PB4: Word-boundary in required-keywords review
- PB7: Domain injection scoped to matching-domain steps review
"""

from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from automation_agent.actuator.applescript_actuator import AppleScriptActuator
from automation_agent.config import AgentConfig
from automation_agent.orchestrator.verifier import StepVerifier
from automation_agent.shared_models import ActionStep


def _make_config(**overrides) -> AgentConfig:
    """Standard test config factory. model_provider='local' is MANDATORY to
    prevent .env leaking AGENT_MODEL_PROVIDER=anthropic into pydantic-settings."""
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",
        "grounding_model": "",
        "grounding_server_url": "",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


# ===========================================================================
# AC-6a: Actuator get_scroll_position(axis=)
# ===========================================================================


@pytest.mark.unit
class TestScrollPositionAxis:
    """AC-6: get_scroll_position(axis=) in applescript_actuator.py."""

    def test_get_scroll_position_axis_y_default_unchanged(self):
        """get_scroll_position() (no axis) returns scrollY -- backward compat."""
        actuator = AppleScriptActuator()
        with patch.object(actuator, "get_state", return_value={"app_name": "Safari"}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="500\n", stderr=""
                )
                result = actuator.get_scroll_position()
        assert result == 500
        # Verify it used scrollY
        call_args = mock_run.call_args[0][0]
        script = call_args[2]  # osascript -e <script>
        assert "scrollY" in script

    def test_get_scroll_position_axis_x_safari(self):
        """get_scroll_position(axis='x') returns int for Safari (scrollX)."""
        actuator = AppleScriptActuator()
        with patch.object(actuator, "get_state", return_value={"app_name": "Safari"}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="300\n", stderr=""
                )
                result = actuator.get_scroll_position(axis="x")
        assert result == 300
        call_args = mock_run.call_args[0][0]
        script = call_args[2]
        assert "scrollX" in script

    def test_get_scroll_position_axis_x_chrome(self):
        """get_scroll_position(axis='x') returns int for Chrome (scrollX)."""
        actuator = AppleScriptActuator()
        with patch.object(
            actuator, "get_state", return_value={"app_name": "Google Chrome"}
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="150\n", stderr=""
                )
                result = actuator.get_scroll_position(axis="x")
        assert result == 150
        call_args = mock_run.call_args[0][0]
        script = call_args[2]
        assert "scrollX" in script

    def test_get_scroll_position_non_browser_returns_none(self):
        """get_scroll_position(axis='x') returns None for Finder."""
        actuator = AppleScriptActuator()
        with patch.object(
            actuator, "get_state", return_value={"app_name": "Finder"}
        ):
            result = actuator.get_scroll_position(axis="x")
        assert result is None

    def test_get_scroll_position_invalid_axis_raises(self):
        """get_scroll_position(axis='z') raises ValueError."""
        actuator = AppleScriptActuator()
        with pytest.raises(ValueError, match="axis must be 'x' or 'y'"):
            actuator.get_scroll_position(axis="z")

    def test_get_scroll_position_timeout_returns_none(self):
        """get_scroll_position(axis='x') returns None on subprocess timeout."""
        actuator = AppleScriptActuator()
        with patch.object(actuator, "get_state", return_value={"app_name": "Safari"}):
            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired("cmd", 3),
            ):
                result = actuator.get_scroll_position(axis="x")
        assert result is None

    def test_get_scroll_position_oserror_returns_none(self):
        """get_scroll_position catches OSError (e.g., missing osascript)."""
        actuator = AppleScriptActuator()
        with patch.object(actuator, "get_state", return_value={"app_name": "Safari"}):
            with patch(
                "subprocess.run",
                side_effect=OSError("No such file or directory"),
            ):
                result = actuator.get_scroll_position(axis="y")
        assert result is None

    def test_get_scroll_position_float_truncation(self):
        """Browser may return '500.7' — int(float()) truncates to 500.
        This is safe: the verifier checks delta > 0 / delta < 0, so a
        truncated delta of 0 (from 500.0 -> 500.7) falls through to S2/S3.
        Sub-pixel scrolls are inherently unreliable and should fall through.
        """
        actuator = AppleScriptActuator()
        with patch.object(actuator, "get_state", return_value={"app_name": "Safari"}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="500.7\n", stderr=""
                )
                result = actuator.get_scroll_position(axis="y")
        assert result == 500  # int(float("500.7")) == 500
        assert isinstance(result, int)


# ===========================================================================
# AC-6b: Verifier axis-parametric S1 scroll verification
# ===========================================================================


def _make_scroll_step(direction="down"):
    return ActionStep(
        action="scroll",
        params={"direction": direction, "amount": 3},
        verify="Page scrolled " + direction,
    )


@pytest.mark.unit
class TestVerifierScrollS1Horizontal:
    """AC-6: Axis-parametric S1 scroll verification for horizontal scrolls."""

    def test_scroll_right_js_confirmed(self):
        """scrollX 0->300 -> S1 pass for scroll right."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=300)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("right")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "x", "value": 0},
            },
        )
        assert result is not None
        assert result[0] is True
        assert "scrollX" in result[1]

    def test_scroll_left_js_confirmed(self):
        """scrollX 300->100 -> S1 pass for scroll left."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=100)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("left")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "x", "value": 300},
            },
        )
        assert result is not None
        assert result[0] is True
        assert "scrollX" in result[1]

    def test_scroll_right_no_horizontal_scrollbar_falls_through(self):
        """Page has no horizontal scrollbar: scrollX=0 before AND after.
        Delta=0 -> S1 inconclusive, falls to S2/S3."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=0)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("right")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "x", "value": 0},
            },
        )
        # delta=0 -> S1 inconclusive, falls through to S3 actuator success
        assert result is not None
        assert result[0] is True
        assert "actuator" in result[1].lower()

    def test_scroll_left_at_boundary_falls_through(self):
        """Already at left boundary: scrollX=0 before and after scroll left.
        Delta=0 -> falls to S3."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=0)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("left")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "x", "value": 0},
            },
        )
        assert result is not None
        assert result[0] is True
        assert "actuator" in result[1].lower()

    def test_scroll_right_zero_delta_falls_through(self):
        """scrollX stays same -> delta=0 -> inconclusive, falls to S2/S3."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=500)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("right")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "x", "value": 500},
            },
        )
        # S1 inconclusive -> S3 actuator success
        assert result is not None
        assert result[0] is True
        assert "actuator" in result[1].lower()

    def test_scroll_right_wrong_direction_falls_through(self):
        """scrollX 500->300 (decreasing) for direction='right' -> S1 inconclusive.
        Mirrors test_scroll_wrong_direction for vertical."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=300)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("right")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "x", "value": 500},
                "_scroll_pixel_changed": True,
            },
        )
        # S1 inconclusive (wrong direction), falls to S2 pixel diff
        assert result is not None
        assert result[0] is True
        assert "pixel" in result[1].lower() or "screenshot" in result[1].lower()

    def test_scroll_horizontal_delta_zero_inconclusive(self):
        """scrollX delta=0 -> S1 does not confirm, even with pixel diff."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=100)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("right")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "x", "value": 100},
                "_scroll_pixel_changed": True,
            },
        )
        # S1 inconclusive (delta=0) -> falls to S2 pixel diff
        assert result is not None
        assert result[0] is True
        assert "pixel" in result[1].lower() or "screenshot" in result[1].lower()


@pytest.mark.unit
class TestVerifierScrollS1Vertical:
    """AC-6: Vertical scroll with new _scroll_before structured format."""

    def test_scroll_down_js_confirmed_new_format(self):
        """scrollY 0->500 with new _scroll_before format -> S1 pass for down."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=500)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "y", "value": 0},
            },
        )
        assert result is not None
        assert result[0] is True
        assert "scrollY" in result[1]

    def test_scroll_up_js_confirmed_new_format(self):
        """scrollY 500->200 with new _scroll_before format -> S1 pass for up."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=200)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("up")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "y", "value": 500},
            },
        )
        assert result is not None
        assert result[0] is True
        assert "scrollY" in result[1]

    def test_scroll_no_scroll_before_metadata_falls_through(self):
        """No _scroll_before in actuator_result -> S1 skipped entirely."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=500)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={"success": True},
        )
        # No scroll metadata -> S1 skipped -> S3 actuator success
        assert result is not None
        assert result[0] is True
        assert "actuator" in result[1].lower()

    def test_scroll_no_get_scroll_position_falls_through(self):
        """Actuator without get_scroll_position -> S1 skipped."""
        actuator = MagicMock(spec=["get_state", "scroll"])
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "y", "value": 0},
            },
        )
        # No get_scroll_position -> S1 skipped -> S3
        assert result is not None
        assert result[0] is True
        assert "actuator" in result[1].lower()

    def test_malformed_scroll_before_missing_axis(self):
        """_scroll_before={"value": 100} (no axis) -> S1 skipped, falls through."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=500)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"value": 100},
            },
        )
        # axis is None -> guard fails -> S3
        assert result is not None
        assert result[0] is True
        assert "actuator" in result[1].lower()

    def test_malformed_scroll_before_missing_value(self):
        """_scroll_before={"axis": "y"} (no value) -> S1 skipped, falls through."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=500)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "y"},
            },
        )
        # value is None -> guard fails -> S3
        assert result is not None
        assert result[0] is True
        assert "actuator" in result[1].lower()

    def test_unknown_direction_falls_through(self):
        """direction='diagonal' (unknown) -> not in INCREASING or DECREASING ->
        S1 inconclusive, falls through to S3."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=600)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("diagonal")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "y", "value": 500},
            },
        )
        # "diagonal" not in INCREASING or DECREASING -> S1 inconclusive -> S3
        assert result is not None
        assert result[0] is True
        assert "actuator" in result[1].lower()

    def test_scroll_before_bare_int_falls_through(self):
        """_scroll_before=500 (old format, bare int) -> isinstance guard catches,
        no AttributeError, graceful fallthrough to S3."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=600)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": 500,  # old format: bare int
            },
        )
        # isinstance(500, dict) is False -> S1 skipped -> S3
        assert result is not None
        assert result[0] is True
        assert "actuator" in result[1].lower()


@pytest.mark.unit
class TestScrollSignConvention:
    """Verify sign convention: browser JS scroll increases down/right."""

    def test_scroll_sign_convention_down_positive_delta(self):
        """scroll down -> scrollY increases -> delta > 0 -> S1 pass."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=400)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("down")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "y", "value": 100},
            },
        )
        assert result is not None
        assert result[0] is True
        assert "scrollY" in result[1]
        assert "100" in result[1] and "400" in result[1]

    def test_scroll_sign_convention_right_positive_delta(self):
        """scroll right -> scrollX increases -> delta > 0 -> S1 pass."""
        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "Safari"})
        actuator.get_scroll_position = MagicMock(return_value=200)

        verifier = StepVerifier(actuator=actuator)
        step = _make_scroll_step("right")
        result = verifier._verify_tier1(
            step,
            actuator,
            actuator_result={
                "success": True,
                "_scroll_before": {"axis": "x", "value": 0},
            },
        )
        assert result is not None
        assert result[0] is True
        assert "scrollX" in result[1]
        assert "0" in result[1] and "200" in result[1]


# ===========================================================================
# AC-6c: Dispatch scroll metadata
# ===========================================================================


def _make_scroll_agent(scroll_return="Scrolled down 3", scroll_pos=100,
                       actuator_spec=None, app_name="Safari"):
    """Build an AutomationAgent with mocked components for scroll dispatch tests.

    Args:
        scroll_return: Actuator scroll() output string.
        scroll_pos: get_scroll_position return value (int, list for side_effect, or None).
        actuator_spec: If set, passed to MagicMock(spec=...) to restrict attrs.
        app_name: App name for actuator.get_state.
    """
    from automation_agent.orchestrator.agent import AutomationAgent

    planner = AsyncMock()
    skill_registry = MagicMock()
    skill_registry.match = AsyncMock(return_value=None)
    skill_registry.learn_from_run = AsyncMock(return_value=[])
    skill_registry.promote_from_run = AsyncMock(return_value=None)
    coordinator = AsyncMock()
    coordinator.capabilities = MagicMock(return_value=frozenset())
    coordinator.capture_screenshot = AsyncMock(return_value="base64data")

    if actuator_spec is not None:
        actuator = MagicMock(spec=actuator_spec)
    else:
        actuator = MagicMock()
    actuator.scroll = MagicMock(
        return_value={"success": True, "output": scroll_return}
    )
    actuator.get_state = MagicMock(return_value={"app_name": app_name})
    if actuator_spec is None:
        if isinstance(scroll_pos, list):
            actuator.get_scroll_position = MagicMock(side_effect=scroll_pos)
        else:
            actuator.get_scroll_position = MagicMock(return_value=scroll_pos)

    config = _make_config()
    agent = AutomationAgent(planner, skill_registry, coordinator, actuator, config)
    return agent, actuator


@pytest.mark.unit
class TestScrollDispatchMetadata:
    """AC-6: Structured _scroll_before metadata in agent.py dispatch."""

    @pytest.mark.asyncio
    async def test_dispatch_horizontal_captures_scroll_before_axis_x(self):
        """_dispatch_action for direction='right' stores _scroll_before with axis='x'."""
        agent, _ = _make_scroll_agent(scroll_return="Scrolled right 3", scroll_pos=50)
        step = ActionStep(
            action="scroll",
            params={"direction": "right", "amount": 3},
            verify="Page scrolled right",
        )
        result = await agent._dispatch_action(step)
        assert "_scroll_before" in result
        assert result["_scroll_before"]["axis"] == "x"
        assert result["_scroll_before"]["value"] == 50

    @pytest.mark.asyncio
    async def test_dispatch_vertical_captures_scroll_before_axis_y(self):
        """_dispatch_action for direction='down' stores _scroll_before with axis='y'."""
        agent, _ = _make_scroll_agent(scroll_pos=100)
        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 3},
            verify="Page scrolled down",
        )
        result = await agent._dispatch_action(step)
        assert "_scroll_before" in result
        assert result["_scroll_before"]["axis"] == "y"
        assert result["_scroll_before"]["value"] == 100

    @pytest.mark.asyncio
    async def test_dispatch_scroll_before_is_structured_dict(self):
        """_scroll_before value is {"axis": str, "value": int}, not a bare int."""
        agent, _ = _make_scroll_agent(scroll_pos=42)
        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 3},
            verify="Page scrolled down",
        )
        result = await agent._dispatch_action(step)
        scroll_meta = result["_scroll_before"]
        assert isinstance(scroll_meta, dict)
        assert "axis" in scroll_meta
        assert "value" in scroll_meta
        assert scroll_meta["axis"] in ("x", "y")
        assert isinstance(scroll_meta["value"], int)

    @pytest.mark.asyncio
    async def test_dispatch_scroll_position_returns_none_no_metadata(self):
        """get_scroll_position exists but returns None (non-browser app) ->
        no _scroll_before in result. Different path than method not existing."""
        agent, _ = _make_scroll_agent(scroll_pos=None, app_name="Finder")
        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 3},
            verify="Page scrolled down",
        )
        result = await agent._dispatch_action(step)
        assert "_scroll_before" not in result

    @pytest.mark.asyncio
    async def test_dispatch_scroll_no_get_scroll_position_no_metadata(self):
        """Actuator without get_scroll_position -> no _scroll_before in result."""
        agent, _ = _make_scroll_agent(
            actuator_spec=["scroll", "get_state", "is_available"],
            app_name="Finder",
        )
        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 3},
            verify="Page scrolled down",
        )
        result = await agent._dispatch_action(step)
        assert "_scroll_before" not in result

    @pytest.mark.asyncio
    async def test_scroll_dispatch_to_verifier_roundtrip(self):
        """End-to-end: dispatch writes _scroll_before, verifier reads it.
        Validates metadata contract between agent.py and verifier.py."""
        agent, actuator = _make_scroll_agent(scroll_pos=[0, 500])
        step = ActionStep(
            action="scroll",
            params={"direction": "down", "amount": 3},
            verify="Page scrolled down",
        )

        # 1. Dispatch writes _scroll_before
        result = await agent._dispatch_action(step)
        assert "_scroll_before" in result
        assert result["_scroll_before"] == {"axis": "y", "value": 0}

        # 2. Verify dispatch called get_scroll_position with correct axis
        dispatch_axis = result["_scroll_before"]["axis"]
        first_call = actuator.get_scroll_position.call_args_list[0]
        assert first_call == call(axis=dispatch_axis), (
            f"Dispatch used axis={first_call}, expected axis={dispatch_axis}"
        )

        # 3. Feed dispatch result to verifier
        verifier = StepVerifier(actuator=actuator)
        tier1_result = verifier._verify_tier1(step, actuator, result)

        # 4. Verify verifier called get_scroll_position with same axis
        verifier_call = actuator.get_scroll_position.call_args_list[-1]
        assert verifier_call == call(axis=dispatch_axis), (
            f"Verifier used axis={verifier_call}, expected axis={dispatch_axis}"
        )

        # 5. S1 confirms (not falls through)
        assert tier1_result is not None
        assert tier1_result[0] is True
        assert "scrollY" in tier1_result[1]


# ===========================================================================
# PB4 Review: Word-boundary in required-keywords
# ===========================================================================


@pytest.mark.unit
class TestPB4WordBoundaryReview:
    """PB4 review: word-boundary in required-keywords."""

    def test_hyphenated_site_name_matches(self):
        """'best-buy' as required-keyword matches 'buy on best-buy'."""
        from automation_agent.skills.matcher import match_skill
        from automation_agent.skills.models import Skill, SkillRequirements

        skill = Skill(
            name="buy_on_bestbuy",
            description="Buy on Best Buy",
            trigger_keywords=["buy", "best-buy"],
            parameters={},
            requires=SkillRequirements(),
            success_condition="Item purchased",
            metadata={"required-keywords": ["best-buy"]},
        )
        result = match_skill("buy on best-buy", [skill])
        assert result is not None
        assert result[0].name == "buy_on_bestbuy"

    def test_possessive_form_matches(self):
        """'target' matches 'buy from target's website'."""
        from automation_agent.skills.matcher import match_skill
        from automation_agent.skills.models import Skill, SkillRequirements

        skill = Skill(
            name="buy_on_target",
            description="Buy on Target",
            trigger_keywords=["buy", "target"],
            parameters={},
            requires=SkillRequirements(),
            success_condition="Item purchased",
            metadata={"required-keywords": ["target"]},
        )
        # Python \b treats apostrophe as non-word character, so
        # "target" has word boundary before apostrophe
        result = match_skill("buy from target's website", [skill])
        assert result is not None
        assert result[0].name == "buy_on_target"


# ===========================================================================
# PB5 Review: Walmart stub deletion (no duplicate skill files)
# PB5 from commit 1c1535a: "Delete duplicate walmart stub files
# (return-walmart-order.md, return-walmart-order-2.md), convert xfail to
# cleanup+assertion". This is NOT about sequential keyword matching.
# ===========================================================================


@pytest.mark.unit
class TestPB5WalmartStubReview:
    """PB5 review: duplicate walmart skill stubs deleted, only canonical remains.

    PB5 (commit 1c1535a) fixed duplicate walmart stub files that were checked
    into the skills library. This test verifies the cleanup is still in place.
    """

    def test_no_duplicate_walmart_skill_files(self):
        """Only return_walmart_order.md is tracked by git — no hyphenated stubs.

        Uses git ls-files rather than filesystem glob because leaked test
        artifacts (from test_skill_librarian.py sibling-write) may leave
        untracked hyphenated files on disk.
        """
        import subprocess
        from pathlib import Path

        # Guard: skip if not in a git repo (e.g., packaged install in Docker)
        repo_root = Path(__file__).resolve().parent.parent.parent
        if not (repo_root / ".git").exists():
            pytest.skip("Not in a git repo — cannot use git ls-files")

        result = subprocess.run(
            ["git", "ls-files", "--", "src/automation_agent/skills/library/*walmart*"],
            capture_output=True, text=True, timeout=5,
            cwd=str(repo_root),
        )
        assert result.returncode == 0, f"git ls-files failed: {result.stderr}"
        tracked = [
            line.strip().rsplit("/", 1)[-1]
            for line in result.stdout.splitlines()
            if line.strip()
        ]
        # Guard: git ls-files must return something (not empty/broken)
        assert len(tracked) > 0, "git ls-files returned empty — git may not be available"
        # Only the canonical file should be tracked
        assert "return_walmart_order.md" in tracked
        # No hyphenated duplicates tracked
        assert "return-walmart-order.md" not in tracked
        assert "return-walmart-order-2.md" not in tracked


# ===========================================================================
# PB7 Review: Domain injection scoped to matching-domain steps
# ===========================================================================


@pytest.mark.unit
class TestPB7DomainScopeReview:
    """PB7 review: domain injection scoped to matching-domain steps."""

    def test_multi_domain_plan_only_target_injected(self):
        """Plan with target.com + google.com: only target.com steps get injection."""
        from automation_agent.orchestrator.agent import AutomationAgent
        from automation_agent.shared_models import ActionPlan

        planner = AsyncMock()
        skill_registry = MagicMock()
        coordinator = AsyncMock()
        actuator = MagicMock()
        config = _make_config()

        agent = AutomationAgent(
            planner, skill_registry, coordinator, actuator, config
        )

        target_step = ActionStep(
            action="open_url",
            params={"url": "https://www.target.com/s?q=test"},
            verify="Target page loaded",
        )
        google_step = ActionStep(
            action="open_url",
            params={"url": "https://www.google.com/search?q=test"},
            verify="Google search loaded",
        )
        plan = ActionPlan(steps=[target_step, google_step], goal="Test")

        agent._inject_domain_verification(plan, "target.com")

        # target.com step should get injection
        assert "browser domain is target.com" in target_step.verify
        # google.com step should NOT get injection
        assert "browser domain is" not in google_step.verify
