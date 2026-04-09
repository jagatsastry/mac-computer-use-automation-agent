"""Unit tests for overlay narration events and pre/post state observation features.

Tests StepResult state fields, _snapshot_state helper, _execute_step state stamping,
narration events (NARRATE_INTENT / NARRATE_OBSERVE), status overlay formatting,
and action summary output formatting in __main__.py.
"""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.shared_models import ActionPlan, ActionStep, StepResult
from automation_agent.status import StatusSnapshot, format_status_event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    defaults = dict(
        _env_file=None,
        model_provider="local",
        anthropic_api_key="test-key-not-real",
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)


class _AutoApproveHandler:
    async def confirm(self, step):
        return True


def _make_agent(
    planner=None,
    skill_registry=None,
    coordinator=None,
    actuator=None,
    logger=None,
    config=None,
    **kwargs,
) -> AutomationAgent:
    """Create an AutomationAgent with the given mocks."""
    if config is None:
        config = _make_config()
    if planner is None:
        planner = MagicMock()
    if skill_registry is None:
        skill_registry = MagicMock()
    if coordinator is None:
        coordinator = MagicMock()
        coordinator.capabilities = MagicMock(return_value=frozenset())
    if actuator is None:
        actuator = MagicMock()
    if logger is None:
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        logger.log_event = MagicMock()
    return AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
        confirmation_handler=_AutoApproveHandler(),
        **kwargs,
    )


def _make_step(action="click", params=None, verify="element visible", **kw) -> ActionStep:
    """Shortcut for an ActionStep with sensible defaults."""
    return ActionStep(
        action=action,
        params=params or {},
        verify=verify,
        **kw,
    )


# ===========================================================================
# 1. StepResult pre/post state fields
# ===========================================================================


@pytest.mark.unit
class TestStepResultStateFields:
    """StepResult has pre_state_app, post_state_app, pre_state_url, post_state_url."""

    def test_default_values_are_empty_strings(self):
        step = _make_step(action="done", verify="")
        result = StepResult(step=step, success=True)
        assert result.pre_state_app == ""
        assert result.post_state_app == ""
        assert result.pre_state_url == ""
        assert result.post_state_url == ""

    def test_fields_can_be_set_on_creation(self):
        step = _make_step(action="done", verify="")
        result = StepResult(
            step=step,
            success=True,
            pre_state_app="Safari",
            post_state_app="Chrome",
            pre_state_url="https://a.com",
            post_state_url="https://b.com",
        )
        assert result.pre_state_app == "Safari"
        assert result.post_state_app == "Chrome"
        assert result.pre_state_url == "https://a.com"
        assert result.post_state_url == "https://b.com"

    def test_fields_can_be_set_after_creation(self):
        step = _make_step(action="done", verify="")
        result = StepResult(step=step, success=True)
        result.pre_state_app = "Finder"
        result.post_state_app = "Terminal"
        result.pre_state_url = "https://old.com"
        result.post_state_url = "https://new.com"
        assert result.pre_state_app == "Finder"
        assert result.post_state_app == "Terminal"
        assert result.pre_state_url == "https://old.com"
        assert result.post_state_url == "https://new.com"

    def test_all_four_fields_independent(self):
        step = _make_step(action="done", verify="")
        result = StepResult(step=step, success=False, pre_state_app="A")
        assert result.pre_state_app == "A"
        assert result.post_state_app == ""
        assert result.pre_state_url == ""
        assert result.post_state_url == ""


# ===========================================================================
# 2. _snapshot_state helper
# ===========================================================================


@pytest.mark.unit
class TestSnapshotState:
    """_snapshot_state returns (app_name, browser_url) from actuator.get_state()."""

    def test_returns_app_and_url(self):
        actuator = MagicMock()
        actuator.get_state.return_value = {
            "app_name": "Chrome",
            "browser_url": "https://google.com",
        }
        agent = _make_agent(actuator=actuator)
        app, url = agent._snapshot_state()
        assert app == "Chrome"
        assert url == "https://google.com"

    def test_returns_empty_tuple_on_exception(self):
        actuator = MagicMock()
        actuator.get_state.side_effect = RuntimeError("no accessibility")
        agent = _make_agent(actuator=actuator)
        app, url = agent._snapshot_state()
        assert app == ""
        assert url == ""

    def test_returns_app_with_empty_url_when_url_missing(self):
        actuator = MagicMock()
        actuator.get_state.return_value = {
            "app_name": "Safari",
        }
        agent = _make_agent(actuator=actuator)
        app, url = agent._snapshot_state()
        assert app == "Safari"
        assert url == ""

    def test_returns_app_with_empty_url_when_url_none(self):
        actuator = MagicMock()
        actuator.get_state.return_value = {
            "app_name": "Safari",
            "browser_url": None,
        }
        agent = _make_agent(actuator=actuator)
        app, url = agent._snapshot_state()
        assert app == "Safari"
        assert url == ""

    def test_returns_empty_app_when_app_name_missing(self):
        actuator = MagicMock()
        actuator.get_state.return_value = {"browser_url": "https://example.com"}
        agent = _make_agent(actuator=actuator)
        app, url = agent._snapshot_state()
        assert app == ""
        assert url == "https://example.com"

    def test_empty_state_dict(self):
        actuator = MagicMock()
        actuator.get_state.return_value = {}
        agent = _make_agent(actuator=actuator)
        app, url = agent._snapshot_state()
        assert app == ""
        assert url == ""

    def test_handles_non_string_values(self):
        """get_state might return non-string values; _snapshot_state coerces to str."""
        actuator = MagicMock()
        actuator.get_state.return_value = {
            "app_name": 12345,
            "browser_url": "https://example.com",
        }
        agent = _make_agent(actuator=actuator)
        app, url = agent._snapshot_state()
        assert app == "12345"
        assert url == "https://example.com"

    def test_falsy_browser_url_returns_empty(self):
        """Falsy browser_url (False, 0, None) returns empty string due to `or` guard."""
        actuator = MagicMock()
        actuator.get_state.return_value = {
            "app_name": "Safari",
            "browser_url": False,
        }
        agent = _make_agent(actuator=actuator)
        app, url = agent._snapshot_state()
        assert app == "Safari"
        assert url == ""


# ===========================================================================
# 3. _execute_step stamps pre/post state on StepResult
# ===========================================================================


@pytest.mark.unit
class TestExecuteStepStateStamping:
    """_execute_step calls _snapshot_state before and after _execute_step_inner."""

    @pytest.mark.asyncio
    async def test_done_step_pre_post_populated(self):
        """For a 'done' step, pre_state_app and post_state_app are populated."""
        actuator = MagicMock()
        actuator.get_state.return_value = {
            "app_name": "Finder",
            "browser_url": "",
        }
        agent = _make_agent(actuator=actuator)
        step = _make_step(action="done", verify="")
        inner_result = StepResult(step=step, success=True, evidence="done")

        with patch.object(
            agent, "_execute_step_inner", new_callable=AsyncMock, return_value=(inner_result, False)
        ):
            result, _ = await agent._execute_step(
                index=0,
                step=step,
                history=[],
                goal="test",
                plan=ActionPlan(steps=[step], goal="test"),
            )

        assert result.pre_state_app == "Finder"
        assert result.post_state_app == "Finder"

    @pytest.mark.asyncio
    async def test_click_step_state_captured_before_and_after(self):
        """For a 'click' step, state captured before and after action."""
        call_count = 0

        def changing_state():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return {"app_name": "Safari", "browser_url": "https://before.com"}
            return {"app_name": "Safari", "browser_url": "https://after.com"}

        actuator = MagicMock()
        actuator.get_state.side_effect = changing_state
        agent = _make_agent(actuator=actuator)
        step = _make_step(action="click", params={"element": "Submit"})
        inner_result = StepResult(step=step, success=True, evidence="clicked")

        with patch.object(
            agent, "_execute_step_inner", new_callable=AsyncMock, return_value=(inner_result, False)
        ):
            result, _ = await agent._execute_step(
                index=0,
                step=step,
                history=[],
                goal="test",
                plan=ActionPlan(steps=[step], goal="test"),
            )

        assert result.pre_state_url == "https://before.com"
        assert result.post_state_url == "https://after.com"

    @pytest.mark.asyncio
    async def test_different_apps_before_after(self):
        """When get_state() returns different apps before/after: both captured correctly."""
        call_count = 0

        def changing_state():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return {"app_name": "Finder", "browser_url": ""}
            return {"app_name": "Safari", "browser_url": "https://apple.com"}

        actuator = MagicMock()
        actuator.get_state.side_effect = changing_state
        agent = _make_agent(actuator=actuator)
        step = _make_step(action="click", params={"element": "Safari icon"})
        inner_result = StepResult(step=step, success=True, evidence="opened")

        with patch.object(
            agent, "_execute_step_inner", new_callable=AsyncMock, return_value=(inner_result, False)
        ):
            result, _ = await agent._execute_step(
                index=0,
                step=step,
                history=[],
                goal="test",
                plan=ActionPlan(steps=[step], goal="test"),
            )

        assert result.pre_state_app == "Finder"
        assert result.post_state_app == "Safari"
        assert result.pre_state_url == ""
        assert result.post_state_url == "https://apple.com"

    @pytest.mark.asyncio
    async def test_execute_step_calls_snapshot_twice(self):
        """_execute_step calls _snapshot_state exactly twice (pre + post)."""
        actuator = MagicMock()
        actuator.get_state.return_value = {"app_name": "X", "browser_url": ""}
        agent = _make_agent(actuator=actuator)
        step = _make_step(action="done", verify="")
        inner_result = StepResult(step=step, success=True, evidence="ok")

        with patch.object(
            agent, "_execute_step_inner", new_callable=AsyncMock, return_value=(inner_result, False)
        ), patch.object(agent, "_snapshot_state", wraps=agent._snapshot_state) as spy:
            await agent._execute_step(
                index=0,
                step=step,
                history=[],
                goal="test",
                plan=ActionPlan(steps=[step], goal="test"),
            )

        assert spy.call_count == 2


# ===========================================================================
# 4. Narration events (NARRATE_INTENT and NARRATE_OBSERVE)
# ===========================================================================


@pytest.mark.unit
class TestNarrationEvents:
    """NARRATE_INTENT and NARRATE_OBSERVE are logged by _dispatch_action."""

    def _get_logged_events(self, logger_mock, event_type: EventType):
        """Extract all log_event calls that match a given EventType."""
        results = []
        for call in logger_mock.log_event.call_args_list:
            args = call[0] if call[0] else []
            kwargs = call[1] if call[1] else {}
            et = args[0] if len(args) > 0 else kwargs.get("event_type")
            if et == event_type:
                msg = args[1] if len(args) > 1 else kwargs.get("message", "")
                data = kwargs.get("data") or (args[2] if len(args) > 2 else {})
                results.append({"message": msg, "data": data})
        return results

    @pytest.mark.asyncio
    async def test_narrate_intent_logged_before_action(self):
        """NARRATE_INTENT is logged before the action executes."""
        actuator = MagicMock()
        actuator.get_state.return_value = {"app_name": "Chrome", "browser_url": ""}
        actuator.click.return_value = {"success": True}
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(actuator=actuator, logger=logger)
        step = _make_step(action="click", params={"element": "Submit", "x": 100, "y": 200})

        await agent._dispatch_action(step)

        intents = self._get_logged_events(logger, EventType.NARRATE_INTENT)
        assert len(intents) >= 1, "NARRATE_INTENT should be logged"
        # It should be logged before ACTION_COMPLETE
        event_types = [
            call[0][0] for call in logger.log_event.call_args_list if call[0]
        ]
        intent_idx = event_types.index(EventType.NARRATE_INTENT)
        assert EventType.ACTION_START in event_types
        action_start_idx = event_types.index(EventType.ACTION_START)
        assert intent_idx < action_start_idx, "NARRATE_INTENT should come before ACTION_START"

    @pytest.mark.asyncio
    async def test_click_intent_message(self):
        """For click action: intent says 'I will click <element_name>'."""
        actuator = MagicMock()
        actuator.get_state.return_value = {"app_name": "Safari", "browser_url": ""}
        actuator.click.return_value = {"success": True}
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(actuator=actuator, logger=logger)
        step = _make_step(action="click", params={"element": "Add to Cart", "x": 50, "y": 60})

        await agent._dispatch_action(step)

        intents = self._get_logged_events(logger, EventType.NARRATE_INTENT)
        assert len(intents) >= 1
        msg = intents[0]["message"]
        assert "I will click" in msg
        assert "Add to Cart" in msg

    @pytest.mark.asyncio
    async def test_type_text_intent_message(self):
        """For type_text action: intent says 'I will type <masked_text>'."""
        actuator = MagicMock()
        actuator.get_state.return_value = {"app_name": "Chrome", "browser_url": ""}
        actuator.type_text.return_value = {"success": True}
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(actuator=actuator, logger=logger)
        step = _make_step(action="type_text", params={"text": "hello world"})

        await agent._dispatch_action(step)

        intents = self._get_logged_events(logger, EventType.NARRATE_INTENT)
        assert len(intents) >= 1
        msg = intents[0]["message"]
        assert "I will type" in msg

    @pytest.mark.asyncio
    async def test_open_url_intent_message(self):
        """For open_url action: intent says 'I will open <url>'."""
        actuator = MagicMock()
        actuator.get_state.return_value = {"app_name": "Safari", "browser_url": ""}
        actuator.open_url.return_value = {"success": True}
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(actuator=actuator, logger=logger)
        step = _make_step(action="open_url", params={"url": "https://example.com"})

        await agent._dispatch_action(step)

        intents = self._get_logged_events(logger, EventType.NARRATE_INTENT)
        assert len(intents) >= 1
        msg = intents[0]["message"]
        assert "I will open" in msg
        assert "example.com" in msg

    @pytest.mark.asyncio
    async def test_narrate_observe_logged_after_action(self):
        """NARRATE_OBSERVE is logged after the action with observation."""
        actuator = MagicMock()
        actuator.get_state.return_value = {"app_name": "Chrome", "browser_url": "https://x.com"}
        actuator.press_key.return_value = {"success": True}
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(actuator=actuator, logger=logger)
        step = _make_step(action="press_key", params={"keys": "Return"})

        await agent._dispatch_action(step)

        observes = self._get_logged_events(logger, EventType.NARRATE_OBSERVE)
        assert len(observes) >= 1, "NARRATE_OBSERVE should be logged"
        # It should be logged after ACTION_COMPLETE
        event_types = [
            call[0][0] for call in logger.log_event.call_args_list if call[0]
        ]
        observe_idx = event_types.index(EventType.NARRATE_OBSERVE)
        action_complete_idx = event_types.index(EventType.ACTION_COMPLETE)
        assert observe_idx > action_complete_idx, (
            "NARRATE_OBSERVE should come after ACTION_COMPLETE"
        )

    @pytest.mark.asyncio
    async def test_observe_includes_focus_changed(self):
        """Observation includes 'Focus changed: X -> Y' when app changes."""
        call_count = 0

        def changing_state():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return {"app_name": "Finder", "browser_url": ""}
            return {"app_name": "Safari", "browser_url": "https://apple.com"}

        actuator = MagicMock()
        actuator.get_state.side_effect = changing_state
        actuator.activate_app.return_value = {"success": True}
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(actuator=actuator, logger=logger)
        step = _make_step(action="activate_app", params={"app_name": "Safari"})

        await agent._dispatch_action(step)

        observes = self._get_logged_events(logger, EventType.NARRATE_OBSERVE)
        assert len(observes) >= 1
        msg = observes[0]["message"]
        assert "Focus changed" in msg
        assert "Finder" in msg
        assert "Safari" in msg

    @pytest.mark.asyncio
    async def test_observe_includes_current_app_and_url(self):
        """Observation includes current app and URL."""
        actuator = MagicMock()
        actuator.get_state.return_value = {
            "app_name": "Chrome",
            "browser_url": "https://google.com",
        }
        actuator.open_url.return_value = {"success": True}
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(actuator=actuator, logger=logger)
        step = _make_step(action="open_url", params={"url": "https://google.com"})

        await agent._dispatch_action(step)

        observes = self._get_logged_events(logger, EventType.NARRATE_OBSERVE)
        assert len(observes) >= 1
        msg = observes[0]["message"]
        assert "Chrome" in msg
        assert "google.com" in msg

    @pytest.mark.asyncio
    async def test_observe_data_contains_app_and_url(self):
        """NARRATE_OBSERVE event data dict has app and url keys."""
        actuator = MagicMock()
        actuator.get_state.return_value = {
            "app_name": "Safari",
            "browser_url": "https://example.com",
        }
        actuator.press_key.return_value = {"success": True}
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(actuator=actuator, logger=logger)
        step = _make_step(action="press_key", params={"keys": "Return"})

        await agent._dispatch_action(step)

        observes = self._get_logged_events(logger, EventType.NARRATE_OBSERVE)
        assert len(observes) >= 1
        data = observes[0]["data"]
        assert data["app"] == "Safari"
        assert "example.com" in data["url"]

    @pytest.mark.asyncio
    async def test_intent_data_contains_app_and_url(self):
        """NARRATE_INTENT event data dict has app and url keys."""
        actuator = MagicMock()
        actuator.get_state.return_value = {
            "app_name": "Firefox",
            "browser_url": "https://mozilla.org",
        }
        actuator.press_key.return_value = {"success": True}
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(actuator=actuator, logger=logger)
        step = _make_step(action="press_key", params={"keys": "cmd+t"})

        await agent._dispatch_action(step)

        intents = self._get_logged_events(logger, EventType.NARRATE_INTENT)
        assert len(intents) >= 1
        data = intents[0]["data"]
        assert data["app"] == "Firefox"
        assert "mozilla.org" in data["url"]

    @pytest.mark.asyncio
    async def test_no_focus_changed_when_same_app(self):
        """No 'Focus changed' in observation when app stays the same."""
        actuator = MagicMock()
        actuator.get_state.return_value = {"app_name": "Chrome", "browser_url": ""}
        actuator.press_key.return_value = {"success": True}
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(actuator=actuator, logger=logger)
        step = _make_step(action="press_key", params={"keys": "Tab"})

        await agent._dispatch_action(step)

        observes = self._get_logged_events(logger, EventType.NARRATE_OBSERVE)
        assert len(observes) >= 1
        msg = observes[0]["message"]
        assert "Focus changed" not in msg

    @pytest.mark.asyncio
    async def test_press_key_intent_message(self):
        """For press_key action: intent says 'I will press <keys>'."""
        actuator = MagicMock()
        actuator.get_state.return_value = {"app_name": "Terminal", "browser_url": ""}
        actuator.press_key.return_value = {"success": True}
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(actuator=actuator, logger=logger)
        step = _make_step(action="press_key", params={"keys": "cmd+c"})

        await agent._dispatch_action(step)

        intents = self._get_logged_events(logger, EventType.NARRATE_INTENT)
        assert len(intents) >= 1
        msg = intents[0]["message"]
        assert "I will press" in msg
        assert "cmd+c" in msg

    @pytest.mark.asyncio
    async def test_activate_app_intent_message(self):
        """For activate_app action: intent says 'I will activate <app>'."""
        actuator = MagicMock()
        actuator.get_state.return_value = {"app_name": "Finder", "browser_url": ""}
        actuator.activate_app.return_value = {"success": True}
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(actuator=actuator, logger=logger)
        step = _make_step(action="activate_app", params={"app_name": "Safari"})

        await agent._dispatch_action(step)

        intents = self._get_logged_events(logger, EventType.NARRATE_INTENT)
        assert len(intents) >= 1
        msg = intents[0]["message"]
        assert "I will activate" in msg
        assert "Safari" in msg


# ===========================================================================
# 5. Status overlay formatting
# ===========================================================================


@pytest.mark.unit
class TestStatusOverlayFormatting:
    """format_status_event correctly formats narration and plan events."""

    def test_narrate_intent_title_starts_with_thought_emoji(self):
        event = {
            "event_type": "narrate_intent",
            "message": "I will click Submit",
            "data": {"app": "Chrome", "url": "https://x.com"},
        }
        snap = format_status_event(event)
        assert snap is not None
        assert snap.title.startswith("\U0001f4ad")  # thought balloon

    def test_narrate_observe_title_starts_with_eye_emoji(self):
        event = {
            "event_type": "narrate_observe",
            "message": "Done: click succeeded | Now focused: Chrome",
            "data": {"app": "Chrome", "url": "https://x.com"},
        }
        snap = format_status_event(event)
        assert snap is not None
        assert snap.title.startswith("\U0001f441")  # eye

    def test_narrate_intent_verbose_includes_app_url(self):
        event = {
            "event_type": "narrate_intent",
            "message": "I will click Submit",
            "data": {"app": "Safari", "url": "https://example.com"},
        }
        snap = format_status_event(event, verbose=True)
        assert snap is not None
        assert "Safari" in snap.line
        assert "example.com" in snap.line

    def test_narrate_observe_verbose_includes_app_url(self):
        event = {
            "event_type": "narrate_observe",
            "message": "Done: click succeeded",
            "data": {"app": "Chrome", "url": "https://google.com"},
        }
        snap = format_status_event(event, verbose=True)
        assert snap is not None
        assert "Chrome" in snap.line
        assert "google.com" in snap.line

    def test_plan_complete_includes_step_summary_in_line(self):
        """plan_complete event includes step summary in line text (always, not just verbose)."""
        event = {
            "event_type": "plan_complete",
            "message": "Plan generated",
            "data": {
                "step_count": 3,
                "steps_summary": [
                    "open_url: https://x.com",
                    "click: Sign In",
                    "done",
                ],
            },
        }
        snap = format_status_event(event, verbose=False)
        assert snap is not None
        assert "PLAN" in snap.line
        assert "open_url" in snap.line
        assert "click: Sign In" in snap.line
        assert "done" in snap.line

    def test_plan_complete_snapshot_has_is_plan_true(self):
        event = {
            "event_type": "plan_complete",
            "message": "Plan generated",
            "data": {"step_count": 2, "steps_summary": ["click: A", "done"]},
        }
        snap = format_status_event(event)
        assert snap is not None
        assert snap.is_plan is True

    def test_replan_complete_snapshot_has_is_plan_true(self):
        event = {
            "event_type": "replan_complete",
            "message": "Replan generated",
            "data": {"step_count": 1, "steps_summary": ["scroll: down"]},
        }
        snap = format_status_event(event)
        assert snap is not None
        assert snap.is_plan is True

    def test_non_plan_event_has_is_plan_false(self):
        event = {
            "event_type": "step_start",
            "message": "Step 0: click",
            "data": {"action": "click"},
        }
        snap = format_status_event(event)
        assert snap is not None
        assert snap.is_plan is False

    def test_narrate_intent_not_terminal(self):
        event = {
            "event_type": "narrate_intent",
            "message": "I will click X",
            "data": {},
        }
        snap = format_status_event(event)
        assert snap is not None
        assert snap.terminal is False

    def test_narrate_observe_not_terminal(self):
        event = {
            "event_type": "narrate_observe",
            "message": "Done: click succeeded",
            "data": {},
        }
        snap = format_status_event(event)
        assert snap is not None
        assert snap.terminal is False

    def test_plan_complete_title_includes_step_count(self):
        event = {
            "event_type": "plan_complete",
            "message": "Plan generated",
            "data": {"step_count": 5},
        }
        snap = format_status_event(event)
        assert snap is not None
        assert "5 steps" in snap.title

    def test_narrate_intent_title_truncates_long_message(self):
        long_msg = "I will click on a very long element name " * 5
        event = {
            "event_type": "narrate_intent",
            "message": long_msg,
            "data": {},
        }
        snap = format_status_event(event)
        assert snap is not None
        # Title should truncate; the emoji prefix + message[:60]
        assert len(snap.title) <= 70  # emoji (2-4 chars) + space + 60 chars

    def test_empty_message_returns_none(self):
        """format_status_event returns None for empty message."""
        event = {"event_type": "narrate_intent", "message": ""}
        snap = format_status_event(event)
        assert snap is None

    def test_empty_event_type_returns_none(self):
        """format_status_event returns None for empty event_type."""
        event = {"event_type": "", "message": "something"}
        snap = format_status_event(event)
        assert snap is None


# ===========================================================================
# 6. Action summary output formatting (__main__.py)
# ===========================================================================


@pytest.mark.unit
class TestActionSummaryFormatting:
    """Tests the action summary output lines produced by __main__.py.

    We test the formatting logic by building StepResults and verifying
    that the output lines match the expected patterns.
    """

    def _format_step_line(self, sr: StepResult, index: int = 1) -> str:
        """Replicate the formatting logic from __main__.py for a single step."""
        status = "OK" if sr.success else "FAIL"
        pre = f"[{sr.pre_state_app}]" if sr.pre_state_app else "[?]"
        post = f"[{sr.post_state_app}]" if sr.post_state_app else "[?]"
        return f"  {index}. [{status}] {pre} \u2192 {sr.step.action}: {sr.step.params} \u2192 {post}"

    def _format_url_line(self, sr: StepResult) -> str:
        """Replicate URL change formatting from __main__.py."""
        url_pre = sr.pre_state_url[:60] if sr.pre_state_url else ""
        url_post = sr.post_state_url[:60] if sr.post_state_url else ""
        if url_pre != url_post:
            return f"      URL: {url_pre} \u2192 {url_post}"
        return ""

    def test_success_case_format(self):
        step = _make_step(action="open_url", params={"url": "https://x.com"})
        sr = StepResult(
            step=step,
            success=True,
            pre_state_app="Chrome",
            post_state_app="Chrome",
        )
        line = self._format_step_line(sr)
        assert "[OK]" in line
        assert "[Chrome]" in line
        assert "open_url" in line
        assert line.count("[Chrome]") == 2  # pre and post

    def test_failure_case_format(self):
        step = _make_step(action="type_text", params={"text": "hello"})
        sr = StepResult(
            step=step,
            success=False,
            pre_state_app="Chrome",
            post_state_app="Safari",
        )
        line = self._format_step_line(sr)
        assert "[FAIL]" in line
        assert "[Chrome]" in line
        assert "[Safari]" in line
        assert "type_text" in line

    def test_url_change_shows_url_line(self):
        step = _make_step(action="click", params={"element": "link"})
        sr = StepResult(
            step=step,
            success=True,
            pre_state_url="https://old.com",
            post_state_url="https://new.com",
        )
        url_line = self._format_url_line(sr)
        assert url_line != ""
        assert "URL:" in url_line
        assert "old.com" in url_line
        assert "new.com" in url_line

    def test_no_url_change_no_url_line(self):
        step = _make_step(action="click", params={"element": "btn"})
        sr = StepResult(
            step=step,
            success=True,
            pre_state_url="https://same.com",
            post_state_url="https://same.com",
        )
        url_line = self._format_url_line(sr)
        assert url_line == ""

    def test_missing_pre_state_shows_question_mark(self):
        step = _make_step(action="click", params={"element": "X"})
        sr = StepResult(
            step=step,
            success=True,
            pre_state_app="",
            post_state_app="Chrome",
        )
        line = self._format_step_line(sr)
        assert "[?]" in line  # pre is unknown
        assert "[Chrome]" in line  # post is known

    def test_missing_post_state_shows_question_mark(self):
        step = _make_step(action="click", params={"element": "Y"})
        sr = StepResult(
            step=step,
            success=True,
            pre_state_app="Safari",
            post_state_app="",
        )
        line = self._format_step_line(sr)
        assert "[Safari]" in line  # pre is known
        assert "[?]" in line  # post is unknown

    def test_both_missing_shows_two_question_marks(self):
        step = _make_step(action="done", verify="")
        sr = StepResult(step=step, success=True)
        line = self._format_step_line(sr)
        assert line.count("[?]") == 2

    def test_url_line_empty_when_both_urls_empty(self):
        step = _make_step(action="click", params={"element": "Z"})
        sr = StepResult(
            step=step,
            success=True,
            pre_state_url="",
            post_state_url="",
        )
        url_line = self._format_url_line(sr)
        assert url_line == ""

    def test_url_line_shown_when_pre_empty_post_has_value(self):
        """URL line shown when pre is empty but post has a value (they differ)."""
        step = _make_step(action="open_url", params={"url": "https://new.com"})
        sr = StepResult(
            step=step,
            success=True,
            pre_state_url="",
            post_state_url="https://new.com",
        )
        url_line = self._format_url_line(sr)
        assert "URL:" in url_line
        assert "new.com" in url_line

    def test_step_index_in_output(self):
        step = _make_step(action="click", params={"element": "OK"})
        sr = StepResult(step=step, success=True, pre_state_app="A", post_state_app="B")
        line3 = self._format_step_line(sr, index=3)
        assert "  3." in line3
        line1 = self._format_step_line(sr, index=1)
        assert "  1." in line1

    def test_params_shown_in_output(self):
        step = _make_step(action="type_text", params={"text": "search query"})
        sr = StepResult(
            step=step,
            success=True,
            pre_state_app="Chrome",
            post_state_app="Chrome",
        )
        line = self._format_step_line(sr)
        assert "search query" in line
