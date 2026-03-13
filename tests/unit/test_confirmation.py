"""Unit tests for Gap 6: User Confirmation for Destructive Actions (AC-6 through AC-10)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from automation_agent.config import AgentConfig, ConfirmMode
from automation_agent.logging.event_logger import EventLogger
from automation_agent.logging.models import EventType
from automation_agent.orchestrator.agent import (
    AutomationAgent,
    DestructiveClassification,
    FrustrationScore,
    Phase1Decision,
    _redact_params_for_log,
)
from automation_agent.orchestrator.confirmation import (
    AutoDenyConfirmationHandler,
    ConsoleConfirmationHandler,
    _sanitize_for_display,
)
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    ExecutionResult,
    StepResult,
)


def _make_config(**overrides) -> AgentConfig:
    """Create an AgentConfig for tests with sensible defaults."""
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


class MockConfirmationHandler:
    """Test mock for ConfirmationHandler protocol."""

    def __init__(self, response: bool = True):
        self._response = response
        self.calls: list[ActionStep] = []

    async def confirm(self, step: ActionStep) -> bool:
        self.calls.append(step)
        return self._response


def _make_agent(
    planner=None,
    skill_registry=None,
    coordinator=None,
    actuator=None,
    logger=None,
    config=None,
    confirmation_handler=None,
):
    """Create an AutomationAgent with mocked dependencies."""
    if planner is None:
        planner = AsyncMock()
    if skill_registry is None:
        skill_registry = AsyncMock()
        skill_registry.match = AsyncMock(return_value=None)
    if coordinator is None:
        coordinator = AsyncMock()
        coordinator.describe_screen = AsyncMock(return_value="Screen desc")
        coordinator.capture_screenshot = AsyncMock(return_value="base64img")
    if actuator is None:
        actuator = MagicMock()
        actuator.get_state.return_value = {"frontmost_app": "TestApp"}
    if config is None:
        config = _make_config()
    if logger is None:
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
    if confirmation_handler is None:
        confirmation_handler = AutoDenyConfirmationHandler()

    return AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
        confirmation_handler=confirmation_handler,
    )


# ---------------------------------------------------------------------------
# Destructive classification tests (AC-6)
# ---------------------------------------------------------------------------


class TestDestructiveClassification:
    """Tests for _is_destructive_step()."""

    def test_destructive_classification_keyword(self):
        """AC-6a: Keywords in verify/element trigger classification."""
        agent = _make_agent()

        # Keyword in verify
        step = ActionStep(
            action="click",
            params={"element": "some button"},
            verify="The delete confirmation dialog appears",
        )
        result = agent._is_destructive_step(step)
        assert result.is_destructive
        assert result.matched_keyword == "delete"
        assert result.classification_path == "keyword_match"

        # Keyword in element description
        step2 = ActionStep(
            action="click",
            params={"element": "Submit Order button"},
            verify="Order placed",
        )
        result2 = agent._is_destructive_step(step2)
        assert result2.is_destructive
        assert result2.matched_keyword == "submit"

        # No keyword — not destructive
        step3 = ActionStep(
            action="click",
            params={"element": "Next Page button"},
            verify="The next page is visible",
        )
        result3 = agent._is_destructive_step(step3)
        assert not result3.is_destructive

    def test_destructive_classification_planner_flag(self):
        """AC-6b: destructive: true flag detected."""
        agent = _make_agent()

        step = ActionStep(
            action="click",
            params={"element": "some button"},
            verify="Something happens",
            destructive=True,
        )
        result = agent._is_destructive_step(step)
        assert result.is_destructive
        assert result.classification_path == "planner_flag"
        # matched_keyword is None for planner flag
        assert result.matched_keyword is None

    def test_destructive_classification_type_text_via_verify(self):
        """AC-6: type_text with critical keyword in verify text detected via path (a).

        Path (a) scans verify for ALL step types including type_text.
        AC-6c (type_text-specific verify scan) was removed as dead code
        since path (a) already covers it (short-seller issue 4).
        """
        agent = _make_agent()

        # Keyword in verify — matched by general scan (a)
        step = ActionStep(
            action="type_text",
            params={"text": "my password"},
            verify="The payment amount shows $50 to submit",
        )
        result = agent._is_destructive_step(step)
        assert result.is_destructive
        assert result.matched_keyword == "submit"
        # Path (a) fires first since it scans verify for all step types
        assert result.classification_path == "keyword_match"

        # Also verify that type_text without keywords is NOT destructive
        step2 = ActionStep(
            action="type_text",
            params={"text": "hello world"},
            verify="The text field shows hello world",
        )
        result2 = agent._is_destructive_step(step2)
        assert not result2.is_destructive

    def test_not_destructive_classification(self):
        """Non-destructive step returns NOT_DESTRUCTIVE."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Settings icon"},
            verify="Settings page opens",
        )
        result = agent._is_destructive_step(step)
        assert not result.is_destructive
        assert result == DestructiveClassification.NOT_DESTRUCTIVE

    def test_word_boundary_matching(self):
        """Word boundary prevents false positives like 'payment' triggering 'pay'."""
        agent = _make_agent()

        # "payment" should NOT trigger "pay" because of word boundary
        step = ActionStep(
            action="click",
            params={"element": "payment method section"},
            verify="payment section is visible",
        )
        result = agent._is_destructive_step(step)
        # "payment" does not match \bpay\b
        assert not result.is_destructive

    def test_unicode_confusable_normalization(self):
        """NFKC normalization catches fullwidth/confusable variants of keywords."""
        from automation_agent.orchestrator.agent import AutomationAgent

        agent = _make_agent()

        # Fullwidth "pay" (U+FF50 U+FF41 U+FF59) normalizes to "pay" via NFKC
        fullwidth_pay = "\uff50\uff41\uff59"
        normalized = AutomationAgent._normalize_for_matching(fullwidth_pay)
        assert normalized == "pay"

        # Step with fullwidth "delete" in verify
        fullwidth_delete = "\uff44\uff45\uff4c\uff45\uff54\uff45"
        step = ActionStep(
            action="click",
            params={"element": "some button"},
            verify=f"The {fullwidth_delete} confirmation appears",
        )
        result = agent._is_destructive_step(step)
        assert result.is_destructive
        assert result.matched_keyword == "delete"


# ---------------------------------------------------------------------------
# Confirmation phase tests (AC-7, AC-8)
# ---------------------------------------------------------------------------


class TestConfirmationPhases:
    """Tests for _should_confirm_phase1 and _should_confirm_phase2."""

    def test_smart_mode_planner_flag_always_confirms(self):
        """AC-8: Planner-flagged = phase1 CONFIRM."""
        config = _make_config(confirm_destructive=ConfirmMode.SMART)
        agent = _make_agent(config=config)

        step = ActionStep(
            action="click",
            params={"element": "Buy"},
            verify="Order placed",
            destructive=True,
        )
        decision = agent._should_confirm_phase1(step)
        assert decision == Phase1Decision.CONFIRM

    def test_smart_mode_keyword_defers_to_phase2(self):
        """AC-8: Keyword match on click returns DEFER from phase1."""
        config = _make_config(confirm_destructive=ConfirmMode.SMART)
        agent = _make_agent(config=config)

        # Non-flagged click step — keyword match defers to phase2
        step = ActionStep(
            action="click",
            params={"element": "Delete button"},
            verify="Item deleted",
        )
        decision = agent._should_confirm_phase1(step)
        assert decision == Phase1Decision.DEFER

    def test_smart_mode_non_click_confirms_immediately(self):
        """AC-8: Non-click destructive steps confirm in phase1."""
        config = _make_config(confirm_destructive=ConfirmMode.SMART)
        agent = _make_agent(config=config)

        step = ActionStep(
            action="type_text",
            params={"text": "yes"},
            verify="Confirm delete dialog accepted",
        )
        decision = agent._should_confirm_phase1(step)
        assert decision == Phase1Decision.CONFIRM

    def test_always_mode_always_confirms(self):
        """ConfirmMode.ALWAYS always returns CONFIRM."""
        config = _make_config(confirm_destructive=ConfirmMode.ALWAYS)
        agent = _make_agent(config=config)

        step = ActionStep(
            action="click",
            params={"element": "Harmless button"},
            verify="Nothing bad",
        )
        decision = agent._should_confirm_phase1(step)
        assert decision == Phase1Decision.CONFIRM

    def test_never_mode_always_skips(self):
        """ConfirmMode.NEVER always returns SKIP."""
        # Set env var to allow NEVER mode
        with patch.dict("os.environ", {"AGENT_CONFIRM_DESTRUCTIVE": "never"}):
            config = _make_config(confirm_destructive=ConfirmMode.NEVER)
        agent = _make_agent(config=config)

        step = ActionStep(
            action="click",
            params={"element": "Delete All"},
            verify="All deleted",
            destructive=True,
        )
        decision = agent._should_confirm_phase1(step)
        assert decision == Phase1Decision.SKIP

    def test_never_mode_without_env_var_falls_back_to_smart(self):
        """ConfirmMode.NEVER without env var falls back to SMART with warning."""
        import warnings

        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("AGENT_CONFIRM_DESTRUCTIVE", None)
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                config = _make_config(confirm_destructive=ConfirmMode.NEVER)
            assert config.confirm_destructive == ConfirmMode.SMART
            assert len(w) >= 1
            assert "Falling back to SMART" in str(w[0].message)

    def test_never_mode_wrong_env_value_falls_back_to_smart(self):
        """ConfirmMode.NEVER with env var != 'never' falls back to SMART."""
        import warnings

        with patch.dict("os.environ", {"AGENT_CONFIRM_DESTRUCTIVE": "always"}):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                config = _make_config(confirm_destructive=ConfirmMode.NEVER)
            assert config.confirm_destructive == ConfirmMode.SMART
            assert len(w) >= 1

    def test_never_mode_with_env_var_succeeds(self):
        """ConfirmMode.NEVER with env var='never' succeeds."""
        with patch.dict("os.environ", {"AGENT_CONFIRM_DESTRUCTIVE": "never"}):
            config = _make_config(confirm_destructive=ConfirmMode.NEVER)
        assert config.confirm_destructive == ConfirmMode.NEVER

    def test_smart_mode_phase2_low_confidence_confirms(self):
        """AC-8: Phase2 with conf < 0.9 -> confirm."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Confirm button"},
            verify="Confirmed",
        )
        assert agent._should_confirm_phase2(step, confidence=0.7, matched_keyword="confirm")

    def test_smart_mode_phase2_high_confidence_skips(self):
        """AC-8: Phase2 with conf >= 0.9 + soft keyword -> skip."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Reserve button"},
            verify="Reserved",
        )
        # "reserve" is a soft keyword (not in _HARD_DESTRUCTIVE_KEYWORDS)
        assert not agent._should_confirm_phase2(
            step, confidence=0.95, matched_keyword="reserve"
        )

    def test_hard_destructive_keyword_always_confirms_phase2(self):
        """Hard-destructive keywords always confirm regardless of confidence."""
        agent = _make_agent()
        step = ActionStep(
            action="click",
            params={"element": "Pay Now"},
            verify="Payment processed",
        )
        # "pay" is hard-destructive — confidence doesn't matter
        assert agent._should_confirm_phase2(
            step, confidence=0.99, matched_keyword="pay"
        )


# ---------------------------------------------------------------------------
# Confirmation display / handler tests (AC-7)
# ---------------------------------------------------------------------------


class TestConfirmationDisplay:
    """Tests for confirmation display and sanitization."""

    def test_confirmation_displays_raw_fields(self):
        """AC-7: Shows action, params, verify verbatim (after sanitization)."""
        handler = ConsoleConfirmationHandler(timeout_s=0.01)
        step = ActionStep(
            action="click",
            params={"element": "Delete button"},
            verify="Item deleted",
        )
        # Test sanitization works on each field
        assert _sanitize_for_display(step.action) == "click"
        assert "Delete button" in _sanitize_for_display(step.params)
        assert _sanitize_for_display(step.verify) == "Item deleted"

    def test_sanitize_strips_ansi(self):
        """Sanitization strips ANSI escape codes."""
        text = "\x1b[2JHello\x1b[0m World"
        assert _sanitize_for_display(text) == "Hello World"

    def test_sanitize_strips_directional_overrides(self):
        """Sanitization strips Unicode directional overrides."""
        text = "Pay \u202eNow"
        sanitized = _sanitize_for_display(text)
        assert "\u202e" not in sanitized

    @pytest.mark.asyncio
    async def test_auto_deny_handler(self):
        """AutoDenyConfirmationHandler always returns False."""
        handler = AutoDenyConfirmationHandler()
        step = ActionStep(
            action="click",
            params={"element": "Pay"},
            verify="Paid",
        )
        assert await handler.confirm(step) is False

    @pytest.mark.asyncio
    async def test_mock_handler_captures_calls(self):
        """MockConfirmationHandler records calls and returns configured response."""
        handler = MockConfirmationHandler(response=True)
        step = ActionStep(
            action="click",
            params={"element": "Submit"},
            verify="Submitted",
        )
        result = await handler.confirm(step)
        assert result is True
        assert len(handler.calls) == 1
        assert handler.calls[0].action == "click"


# ---------------------------------------------------------------------------
# Dry-run and logging tests (AC-9, AC-10)
# ---------------------------------------------------------------------------


class TestConfirmationLogging:
    """Tests for confirmation logging and dry-run behavior."""

    def test_confirmation_logging(self):
        """AC-10: JSONL log entry with decision field."""
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(logger=logger)

        step = ActionStep(
            action="click",
            params={"element": "Pay Now"},
            verify="Payment processed",
        )
        agent._log_confirmation(
            step,
            decision=True,
            classification_path="keyword_match",
            matched_keyword="pay",
            phase=1,
        )

        # Verify log_event was called with DESTRUCTIVE_CONFIRM
        logger.log_event.assert_called()
        log_call = None
        for c in logger.log_event.call_args_list:
            if c[0][0] == EventType.DESTRUCTIVE_CONFIRM:
                log_call = c
                break
        assert log_call is not None
        data = log_call[1].get("data") or log_call[0][2] if len(log_call[0]) > 2 else log_call[1]["data"]
        assert data["decision"] == "approved"
        assert data["classification_path"] == "keyword_match"
        assert data["matched_keyword"] == "pay"
        assert data["phase"] == 1

    def test_dry_run_logs_skipped(self):
        """AC-9+AC-10: Log entry with decision='skipped_dry_run'."""
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(logger=logger)

        step = ActionStep(
            action="click",
            params={"element": "Delete All"},
            verify="All deleted",
        )
        agent._log_confirmation(
            step,
            decision="skipped_dry_run",
            classification_path="keyword_match",
            matched_keyword="delete",
            phase=1,
        )

        logger.log_event.assert_called()
        log_call = None
        for c in logger.log_event.call_args_list:
            if c[0][0] == EventType.DESTRUCTIVE_CONFIRM:
                log_call = c
                break
        assert log_call is not None
        data = log_call[1].get("data") or log_call[0][2] if len(log_call[0]) > 2 else log_call[1]["data"]
        assert data["decision"] == "skipped_dry_run"

    @pytest.mark.asyncio
    async def test_dry_run_skips_confirmation(self):
        """AC-9: No blocking prompt in dry-run.

        Actually calls _execute_step with dry_run=True and verifies the
        confirmation handler is never invoked for a destructive step.
        """
        handler = MockConfirmationHandler(response=True)
        config = _make_config(dry_run=True)
        coordinator = AsyncMock()
        coordinator.describe_screen = AsyncMock(return_value="Screen")
        coordinator.capture_screenshot = AsyncMock(return_value="b64")
        coordinator.capabilities = MagicMock(return_value=frozenset())
        actuator = MagicMock()
        actuator.get_state.return_value = {"frontmost_app": "TestApp"}
        actuator.execute = AsyncMock(return_value={"success": True})
        agent = _make_agent(
            config=config,
            confirmation_handler=handler,
            coordinator=coordinator,
            actuator=actuator,
        )

        step = ActionStep(
            action="click",
            params={"element": "Pay Now"},
            verify="Payment processed",
            destructive=True,
        )
        plan = ActionPlan(goal="Pay", steps=[step])

        try:
            await agent._execute_step(
                index=0, step=step, history=[], goal="Pay", plan=plan
            )
        except Exception:
            pass  # May fail at dispatch/verify — we only care about handler

        # Handler should NOT have been called in dry-run mode
        assert len(handler.calls) == 0

    @pytest.mark.asyncio
    async def test_prompt_user_confirmation_error_denies(self):
        """Confirmation handler error -> deny (fail-safe)."""

        class BrokenHandler:
            async def confirm(self, step):
                raise RuntimeError("Handler crashed")

        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"
        agent = _make_agent(
            logger=logger, confirmation_handler=BrokenHandler()
        )

        step = ActionStep(
            action="click",
            params={"element": "Pay"},
            verify="Paid",
        )
        result = await agent._prompt_user_confirmation(step)
        assert result is False
        # Should have logged the error
        logger.log_event.assert_called()


# ---------------------------------------------------------------------------
# PII redaction tests
# ---------------------------------------------------------------------------


class TestPIIRedaction:
    """Tests for _redact_params_for_log()."""

    def test_sensitive_params_redacted(self):
        """Sensitive param values are replaced with [REDACTED]."""
        params = {
            "text": "my secret password",
            "password": "hunter2",
            "element": "Password field",
        }
        redacted = _redact_params_for_log(params)
        assert redacted["text"] == "[REDACTED]"
        assert redacted["password"] == "[REDACTED]"
        assert redacted["element"] == "Password field"

    def test_url_query_redacted(self):
        """URL query params are redacted but path is preserved."""
        params = {"url": "https://example.com/search?q=my+secret&token=abc123"}
        redacted = _redact_params_for_log(params)
        assert "?[REDACTED]" in redacted["url"]
        assert "/search" in redacted["url"]

    def test_url_without_query_preserved(self):
        """URLs without query params are preserved."""
        params = {"url": "https://example.com/orders"}
        redacted = _redact_params_for_log(params)
        assert redacted["url"] == "https://example.com/orders"

    def test_unknown_params_truncated(self):
        """Unknown params are truncated to 50 chars."""
        params = {"custom_field": "x" * 100}
        redacted = _redact_params_for_log(params)
        assert redacted["custom_field"].endswith("[truncated]")
        assert len(redacted["custom_field"]) < 100


# ---------------------------------------------------------------------------
# Phase 2 confirmation integration tests (AC-8)
# ---------------------------------------------------------------------------


class TestPhase2Integration:
    """Tests that phase 2 confirmation is wired into _execute_step()."""

    @pytest.mark.asyncio
    async def test_phase2_destructive_click_prompts_user(self):
        """AC-8: Destructive click deferred to phase 2 prompts user.

        Uses a step where the keyword is in the ELEMENT (not verify), so the
        existing confidence gate uses the default 0.5 threshold. Confidence 0.7
        passes the gate but is < 0.9 so phase 2 triggers confirmation.
        """
        from automation_agent.shared_models import FindElementResult

        handler = MockConfirmationHandler(response=False)
        config = _make_config(confirm_destructive=ConfirmMode.SMART)
        coordinator = AsyncMock()
        coordinator.describe_screen = AsyncMock(return_value="Screen desc")
        coordinator.capture_screenshot = AsyncMock(return_value="base64img")
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(
                x=100, y=200, confidence=0.7, source="vision",
            )
        )

        actuator = MagicMock()
        actuator.get_state.return_value = {"frontmost_app": "TestApp"}
        actuator.click.return_value = {"success": True}

        agent = _make_agent(
            config=config,
            coordinator=coordinator,
            actuator=actuator,
            confirmation_handler=handler,
        )
        agent.screenshot_diff = None

        # Keyword "delete" in element only — verify has no critical keywords
        # so confidence threshold = 0.5 (passes with 0.7)
        step = ActionStep(
            action="click",
            params={"element": "Delete button"},
            verify="The item is gone from the list",
        )
        plan = ActionPlan(steps=[step], goal="delete item")

        result, _ = await agent._execute_step(0, step, [], "delete item", plan)
        # Handler denied -> step should fail
        assert not result.success
        assert "User denied" in result.error
        assert len(handler.calls) == 1
        # CRITICAL: click must NOT have been dispatched when user denied
        actuator.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_phase2_high_confidence_soft_keyword_auto_approves(self):
        """AC-8: High confidence + soft keyword -> auto-approve (no prompt).

        "confirm" is a soft keyword (not in _HARD_DESTRUCTIVE_KEYWORDS).
        With confidence >= 0.9, phase 2 should auto-approve.
        """
        from automation_agent.shared_models import FindElementResult

        handler = MockConfirmationHandler(response=True)
        config = _make_config(confirm_destructive=ConfirmMode.SMART)
        coordinator = AsyncMock()
        coordinator.describe_screen = AsyncMock(return_value="Screen desc")
        coordinator.capture_screenshot = AsyncMock(return_value="base64img")
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(
                x=100, y=200, confidence=0.95, source="vision",
            )
        )
        coordinator.verify_condition = AsyncMock(return_value=True)

        actuator = MagicMock()
        actuator.get_state.return_value = {"frontmost_app": "TestApp"}
        actuator.click.return_value = {"success": True}

        agent = _make_agent(
            config=config,
            coordinator=coordinator,
            actuator=actuator,
            confirmation_handler=handler,
        )
        agent.screenshot_diff = None

        # "confirm" in element only; verify has no critical keywords
        step = ActionStep(
            action="click",
            params={"element": "Confirm button"},
            verify="Dialog closed and action completed",
        )
        plan = ActionPlan(steps=[step], goal="confirm action")

        result, _ = await agent._execute_step(0, step, [], "confirm action", plan)
        # "confirm" is a soft keyword + high confidence -> auto-approved, no prompt
        assert len(handler.calls) == 0

    @pytest.mark.asyncio
    async def test_phase2_hard_keyword_always_prompts(self):
        """AC-8: Hard-destructive keyword always prompts regardless of confidence.

        "pay" is a hard keyword. Even at confidence=0.99, phase 2 must prompt.
        Verify text avoids critical keywords so confidence gate uses 0.5 threshold.
        """
        from automation_agent.shared_models import FindElementResult

        handler = MockConfirmationHandler(response=True)
        config = _make_config(confirm_destructive=ConfirmMode.SMART)
        coordinator = AsyncMock()
        coordinator.describe_screen = AsyncMock(return_value="Screen desc")
        coordinator.capture_screenshot = AsyncMock(return_value="base64img")
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(
                x=100, y=200, confidence=0.99, source="vision",
            )
        )
        coordinator.verify_condition = AsyncMock(return_value=True)

        actuator = MagicMock()
        actuator.get_state.return_value = {"frontmost_app": "TestApp"}
        actuator.click.return_value = {"success": True}

        agent = _make_agent(
            config=config,
            coordinator=coordinator,
            actuator=actuator,
            confirmation_handler=handler,
        )
        agent.screenshot_diff = None

        # "pay" in element only — verify has no critical keywords
        step = ActionStep(
            action="click",
            params={"element": "Pay Now button"},
            verify="Transaction was processed successfully",
        )
        plan = ActionPlan(steps=[step], goal="complete checkout")

        result, _ = await agent._execute_step(0, step, [], "complete checkout", plan)
        # "pay" is a hard keyword — must prompt even at 0.99 confidence
        assert len(handler.calls) == 1

    @pytest.mark.asyncio
    async def test_dry_run_skips_phase2_prompt(self):
        """AC-9: dry_run mode logs but doesn't prompt in phase 2."""
        from automation_agent.shared_models import FindElementResult

        handler = MockConfirmationHandler(response=True)
        config = _make_config(confirm_destructive=ConfirmMode.SMART, dry_run=True)
        coordinator = AsyncMock()
        coordinator.describe_screen = AsyncMock(return_value="Screen desc")
        coordinator.capture_screenshot = AsyncMock(return_value="base64img")
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(
                x=100, y=200, confidence=0.7, source="vision",
            )
        )
        coordinator.verify_condition = AsyncMock(return_value=True)

        actuator = MagicMock()
        actuator.get_state.return_value = {"frontmost_app": "TestApp"}
        actuator.click.return_value = {"success": True}

        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"

        agent = _make_agent(
            config=config,
            coordinator=coordinator,
            actuator=actuator,
            confirmation_handler=handler,
            logger=logger,
        )
        agent.screenshot_diff = None

        # Keyword in element only — verify avoids critical keywords
        step = ActionStep(
            action="click",
            params={"element": "Delete button"},
            verify="The item is gone from the list",
        )
        plan = ActionPlan(steps=[step], goal="delete item")

        result, _ = await agent._execute_step(0, step, [], "delete item", plan)
        # Handler should NOT have been called (dry_run)
        assert len(handler.calls) == 0


class TestTOCTOUMitigation:
    """Tests for TOCTOU mitigation: UI change detection between grounding and dispatch."""

    @pytest.mark.asyncio
    async def test_toctou_aborts_when_ui_changed(self):
        """TOCTOU: step is aborted when UI changes during confirmation."""
        import base64
        import io

        from PIL import Image

        # Create two visually different screenshots (base64)
        img_a = Image.new("RGB", (100, 100), color="red")
        buf_a = io.BytesIO()
        img_a.save(buf_a, format="PNG")
        b64_a = base64.b64encode(buf_a.getvalue()).decode()

        img_b = Image.new("RGB", (100, 100), color="blue")
        buf_b = io.BytesIO()
        img_b.save(buf_b, format="PNG")
        b64_b = base64.b64encode(buf_b.getvalue()).decode()

        from automation_agent.shared_models import FindElementResult

        # First call returns pre-confirmation screenshot,
        # second call returns different post-confirmation screenshot
        coordinator = AsyncMock()
        coordinator.capabilities = MagicMock(return_value=frozenset())
        coordinator.capture_screenshot = AsyncMock(side_effect=[b64_a, b64_b, b64_b])
        coordinator.describe_screen = AsyncMock(return_value="Screen")
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=50, y=50, confidence=0.95, source="mock")
        )

        class MockHandler:
            def __init__(self):
                self.calls = []

            async def confirm(self, step):
                self.calls.append(step)
                return True  # User approves

        handler = MockHandler()
        config = _make_config(
            confirm_destructive="smart",
            infeasibility_same_state_threshold=0.05,
        )
        logger = MagicMock(spec=EventLogger)
        logger.run_id = "test-run"

        actuator = MagicMock()
        actuator.get_state.return_value = {"frontmost_app": "TestApp"}

        agent = _make_agent(
            config=config,
            coordinator=coordinator,
            actuator=actuator,
            confirmation_handler=handler,
            logger=logger,
        )
        agent.screenshot_diff = None

        step = ActionStep(
            action="click",
            params={"element": "Delete Account"},
            verify="Account is deleted",
        )
        plan = ActionPlan(steps=[step], goal="delete account")

        result, _ = await agent._execute_step(0, step, [], "delete account", plan)
        # Step should fail due to TOCTOU detection
        assert not result.success
        assert "UI changed" in result.error
