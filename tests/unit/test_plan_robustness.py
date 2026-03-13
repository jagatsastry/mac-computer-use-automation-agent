"""Unit tests for Plan Robustness fixes (P0-1, P0-2, P2-1).

Engineer 1 — Fixes 1-4 and Fix 8:
  Fix 1: Expand action aliases in shared_models.py
  Fix 2: Smart fallback for unknown actions in planner.py
  Fix 3: Planner prompt update (navigation instruction)
  Fix 4: Navigation prepend safety net (_ensure_skill_navigation)
  Fix 8: Duplicate skill cleanup
"""

import logging
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.planner.planner import ActionPlannerImpl
from automation_agent.shared_models import ActionPlan, ActionStep


def _make_config(**overrides):
    return AgentConfig(
        grounding_model="",
        grounding_server_url="",
        model_provider="local",
        **overrides,
    )


# ---------------------------------------------------------------------------
# Fix 1: Expanded action aliases
# ---------------------------------------------------------------------------


class TestActionAliases:
    """Fix 1 — new aliases map to canonical action names."""

    def test_select_maps_to_click(self):
        step = ActionStep.from_dict({
            "action": "select",
            "params": {"element": "OK button"},
            "verify": "clicked",
        })
        assert step.action == "click"

    def test_choose_maps_to_click(self):
        step = ActionStep.from_dict({
            "action": "choose",
            "params": {"element": "option A"},
            "verify": "selected",
        })
        assert step.action == "click"

    def test_tap_maps_to_click(self):
        step = ActionStep.from_dict({
            "action": "tap",
            "params": {"element": "link"},
            "verify": "tapped",
        })
        assert step.action == "click"

    def test_pick_maps_to_click(self):
        step = ActionStep.from_dict({
            "action": "pick",
            "params": {"element": "item"},
            "verify": "picked",
        })
        assert step.action == "click"

    def test_submit_maps_to_click(self):
        step = ActionStep.from_dict({
            "action": "submit",
            "params": {"element": "form"},
            "verify": "submitted",
        })
        assert step.action == "click"

    def test_submit_form_maps_to_click(self):
        step = ActionStep.from_dict({
            "action": "submit_form",
            "params": {"element": "form"},
            "verify": "submitted",
        })
        assert step.action == "click"

    def test_fill_maps_to_type_text(self):
        step = ActionStep.from_dict({
            "action": "fill",
            "params": {"text": "hello"},
            "verify": "filled",
        })
        assert step.action == "type_text"

    def test_fill_in_maps_to_type_text(self):
        step = ActionStep.from_dict({
            "action": "fill_in",
            "params": {"text": "world"},
            "verify": "filled",
        })
        assert step.action == "type_text"

    def test_input_maps_to_type_text(self):
        step = ActionStep.from_dict({
            "action": "input",
            "params": {"text": "data"},
            "verify": "typed",
        })
        assert step.action == "type_text"

    def test_write_maps_to_type_text(self):
        step = ActionStep.from_dict({
            "action": "write",
            "params": {"text": "words"},
            "verify": "typed",
        })
        assert step.action == "type_text"

    def test_go_to_maps_to_open_url(self):
        step = ActionStep.from_dict({
            "action": "go_to",
            "params": {"url": "https://example.com"},
            "verify": "page loaded",
        })
        assert step.action == "open_url"

    def test_browse_maps_to_open_url(self):
        step = ActionStep.from_dict({
            "action": "browse",
            "params": {"url": "https://example.com"},
            "verify": "page loaded",
        })
        assert step.action == "open_url"

    def test_visit_maps_to_open_url(self):
        step = ActionStep.from_dict({
            "action": "visit",
            "params": {"url": "https://example.com"},
            "verify": "page loaded",
        })
        assert step.action == "open_url"

    def test_look_maps_to_observe(self):
        step = ActionStep.from_dict({
            "action": "look",
            "params": {},
            "verify": "",
        })
        assert step.action == "observe"

    def test_check_maps_to_observe(self):
        step = ActionStep.from_dict({
            "action": "check",
            "params": {},
            "verify": "",
        })
        assert step.action == "observe"

    def test_inspect_maps_to_observe(self):
        step = ActionStep.from_dict({
            "action": "inspect",
            "params": {},
            "verify": "",
        })
        assert step.action == "observe"

    def test_end_maps_to_done(self):
        step = ActionStep.from_dict({
            "action": "end",
            "params": {},
            "verify": "",
        })
        assert step.action == "done"

    def test_stop_maps_to_done(self):
        step = ActionStep.from_dict({
            "action": "stop",
            "params": {},
            "verify": "",
        })
        assert step.action == "done"

    def test_press_maps_to_press_key(self):
        step = ActionStep.from_dict({
            "action": "press",
            "params": {"keys": ["enter"]},
            "verify": "pressed",
        })
        assert step.action == "press_key"


# ---------------------------------------------------------------------------
# Fix 2: Smart fallback for unknown actions
# ---------------------------------------------------------------------------


class TestSmartFallback:
    """Fix 2 — unknown actions fall back instead of being dropped."""

    def _make_llm_response(self, steps: list[dict]) -> dict:
        """Build a fake LLM response with given steps."""
        import json
        return {"content": json.dumps({"steps": steps}), "usage": {}}

    def test_parse_plan_no_steps_dropped(self):
        """All steps should survive: observe, select->click, fill->type_text, click, done."""
        planner = ActionPlannerImpl(_make_config())
        response = self._make_llm_response([
            {"action": "observe", "params": {}, "verify": ""},
            {"action": "select", "params": {"element": "btn"}, "verify": "clicked"},
            {"action": "fill", "params": {"text": "hello"}, "verify": "filled"},
            {"action": "click", "params": {"element": "ok"}, "verify": "clicked"},
            {"action": "done", "params": {}, "verify": ""},
        ])
        plan = planner._parse_plan_response(response, "test goal")
        assert len(plan.steps) == 5
        assert plan.steps[1].action == "click"   # select -> click
        assert plan.steps[2].action == "type_text"  # fill -> type_text

    def test_unknown_action_falls_back_to_click(self):
        """Truly unknown action without text param -> click fallback."""
        planner = ActionPlannerImpl(_make_config())
        response = self._make_llm_response([
            {"action": "wiggle", "params": {"element": "thing"}, "verify": "wiggled"},
        ])
        plan = planner._parse_plan_response(response, "test goal")
        assert len(plan.steps) == 1
        assert plan.steps[0].action == "click"

    def test_unknown_action_with_text_falls_back_to_type_text(self):
        """Unknown action with text param -> type_text fallback."""
        planner = ActionPlannerImpl(_make_config())
        response = self._make_llm_response([
            {"action": "scribble", "params": {"text": "hi"}, "verify": "scribbled"},
        ])
        plan = planner._parse_plan_response(response, "test goal")
        assert len(plan.steps) == 1
        assert plan.steps[0].action == "type_text"

    def test_wait_scroll_actions_not_mapped_to_click(self):
        """Actions containing wait/scroll/delay/sleep/pause should be skipped, not mapped."""
        planner = ActionPlannerImpl(_make_config())
        response = self._make_llm_response([
            {"action": "click", "params": {"element": "btn"}, "verify": "clicked"},
            {"action": "wait_for_load", "params": {}, "verify": "loaded"},
            {"action": "scroll_to_bottom", "params": {}, "verify": "scrolled"},
            {"action": "delay_action", "params": {}, "verify": "delayed"},
        ])
        plan = planner._parse_plan_response(response, "test goal")
        # Only the click should survive; wait/scroll/delay are skipped
        assert len(plan.steps) == 1
        assert plan.steps[0].action == "click"

    def test_step_count_mismatch_warning(self):
        """When steps are dropped, a warning should be logged with mismatch info."""
        import structlog

        planner = ActionPlannerImpl(_make_config())
        response = self._make_llm_response([
            {"action": "click", "params": {"element": "btn"}, "verify": "ok"},
            {"action": "wait_for_load", "params": {}, "verify": "loaded"},
        ])
        # Capture structlog warnings by mocking the bound logger
        with patch("automation_agent.planner.planner.logger") as mock_logger:
            plan = planner._parse_plan_response(response, "test goal")
        assert len(plan.steps) == 1
        # Verify step count mismatch warning was logged
        warning_calls = [
            call for call in mock_logger.warning.call_args_list
            if "step count mismatch" in str(call).lower()
        ]
        assert len(warning_calls) == 1, (
            f"Expected 1 mismatch warning, got {len(warning_calls)}. "
            f"All warning calls: {mock_logger.warning.call_args_list}"
        )

    def test_all_skippable_actions_raise_value_error(self):
        """Plan where every step is wait/scroll/delay → all skipped → ValueError."""
        planner = ActionPlannerImpl(_make_config())
        response = self._make_llm_response([
            {"action": "wait_for_animation", "params": {}, "verify": "animated"},
            {"action": "scroll_to_element", "params": {}, "verify": "scrolled"},
            {"action": "sleep_briefly", "params": {}, "verify": "slept"},
        ])
        with pytest.raises(ValueError, match="only invalid steps"):
            planner._parse_plan_response(response, "test goal")

    def test_single_unknown_action_fallback(self):
        """Single-step plan with unknown action survives via fallback."""
        planner = ActionPlannerImpl(_make_config())
        response = self._make_llm_response([
            {"action": "poke", "params": {"element": "button"}, "verify": "poked"},
        ])
        plan = planner._parse_plan_response(response, "test goal")
        assert len(plan.steps) == 1
        assert plan.steps[0].action == "click"
        assert plan.steps[0].params["element"] == "button"

    def test_double_fallback_failure_skips_step(self):
        """If the fallback action itself fails validation, the step is truly skipped."""
        planner = ActionPlannerImpl(_make_config())
        # Create a step where even the click fallback will fail: provide an
        # invalid on_fail value that ActionStep.__post_init__ rejects.
        response = self._make_llm_response([
            {
                "action": "zap",
                "params": {"element": "x"},
                "verify": "zapped",
                "on_fail": "invalid_on_fail_value",
            },
            {"action": "done", "params": {}, "verify": ""},
        ])
        plan = planner._parse_plan_response(response, "test goal")
        # "zap" fallback to click will fail because on_fail is invalid
        # Only "done" survives
        assert len(plan.steps) == 1
        assert plan.steps[0].action == "done"

    def test_fallback_preserves_params(self):
        """Original params survive through the fallback mapping."""
        planner = ActionPlannerImpl(_make_config())
        response = self._make_llm_response([
            {
                "action": "wiggle",
                "params": {"element": "btn", "custom_key": "val"},
                "verify": "wiggled",
            },
        ])
        plan = planner._parse_plan_response(response, "test goal")
        assert len(plan.steps) == 1
        assert plan.steps[0].action == "click"
        assert plan.steps[0].params["element"] == "btn"
        assert plan.steps[0].params["custom_key"] == "val"


# ---------------------------------------------------------------------------
# Fix 3: Prompt navigation instruction
# ---------------------------------------------------------------------------


class TestPromptNavigationInstruction:
    """Fix 3 — planner prompts include skill-navigation mandate."""

    def test_plan_from_prompt_contains_instruction(self):
        planner = ActionPlannerImpl(_make_config())
        content = (planner._prompts_dir / "plan_from_prompt.md").read_text()
        assert "contract, not a suggestion" in content

    def test_replan_from_state_contains_instruction(self):
        planner = ActionPlannerImpl(_make_config())
        content = (planner._prompts_dir / "replan_from_state.md").read_text()
        assert "contract, not a suggestion" in content


# ---------------------------------------------------------------------------
# Fix 4: Navigation prepend safety net
# ---------------------------------------------------------------------------


class TestSkillNavigationPrepend:
    """Fix 4 — _ensure_skill_navigation prepends nav step when missing."""

    def _make_plan(self, steps: list[ActionStep], **kwargs) -> ActionPlan:
        return ActionPlan(steps=steps, goal="test", **kwargs)

    def test_prepend_when_missing(self):
        """Plan without nav in first 3 steps gets fallback nav prepended."""
        from automation_agent.orchestrator.agent import AutomationAgent

        plan = self._make_plan([
            ActionStep(action="observe", params={}, verify=""),
            ActionStep(action="click", params={"element": "btn"}, verify="clicked"),
            ActionStep(action="done", params={}, verify=""),
        ])
        fallback = self._make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://walmart.com/orders"},
                verify="orders page visible",
            ),
            ActionStep(action="click", params={"element": "item"}, verify="clicked"),
        ])
        result = AutomationAgent._ensure_skill_navigation(plan, fallback)
        assert result.steps[0].action == "open_url"
        assert len(result.steps) == 4  # prepended + original 3

    def test_no_prepend_when_present(self):
        """Plan with nav already in first 3 steps is returned unchanged."""
        from automation_agent.orchestrator.agent import AutomationAgent

        plan = self._make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://walmart.com/orders"},
                verify="orders page visible",
            ),
            ActionStep(action="click", params={"element": "btn"}, verify="clicked"),
            ActionStep(action="done", params={}, verify=""),
        ])
        fallback = self._make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://walmart.com/orders"},
                verify="orders page",
            ),
        ])
        result = AutomationAgent._ensure_skill_navigation(plan, fallback)
        assert len(result.steps) == 3  # unchanged
        assert result is plan  # same object

    def test_no_prepend_when_activate_app_present(self):
        """activate_app also counts as navigation."""
        from automation_agent.orchestrator.agent import AutomationAgent

        plan = self._make_plan([
            ActionStep(action="activate_app", params={"app_name": "Safari"}, verify="active"),
            ActionStep(action="click", params={"element": "btn"}, verify="clicked"),
        ])
        fallback = self._make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://example.com"},
                verify="page loaded",
            ),
        ])
        result = AutomationAgent._ensure_skill_navigation(plan, fallback)
        assert result is plan  # unchanged

    def test_no_prepend_when_fallback_has_no_nav(self):
        """If fallback has no nav step, plan is returned unchanged."""
        from automation_agent.orchestrator.agent import AutomationAgent

        plan = self._make_plan([
            ActionStep(action="click", params={"element": "btn"}, verify="clicked"),
        ])
        fallback = self._make_plan([
            ActionStep(action="click", params={"element": "other"}, verify="clicked"),
        ])
        result = AutomationAgent._ensure_skill_navigation(plan, fallback)
        assert result is plan

    def test_trivial_done_plan_not_renavigated(self):
        """A trivial done-only plan should NOT get nav prepended.

        The guard at execute() line 342 prevents _ensure_skill_navigation
        from being called for trivial done plans. Verify the method itself
        would prepend (to confirm the guard is necessary).
        """
        from automation_agent.orchestrator.agent import AutomationAgent

        done_plan = self._make_plan([
            ActionStep(action="done", params={}, verify=""),
        ])
        fallback = self._make_plan([
            ActionStep(
                action="open_url",
                params={"url": "https://walmart.com/orders"},
                verify="orders page visible",
            ),
            ActionStep(action="click", params={"element": "item"}, verify="clicked"),
        ])
        # The method itself WOULD prepend nav (no guard at this level)
        result = AutomationAgent._ensure_skill_navigation(done_plan, fallback)
        assert result.steps[0].action == "open_url"
        assert len(result.steps) == 2  # open_url + done

        # Verify that _is_trivial_done_plan correctly identifies done-only plans
        assert AutomationAgent._is_trivial_done_plan(done_plan) is True
        # And that a non-trivial plan is not identified as trivial
        assert AutomationAgent._is_trivial_done_plan(result) is False


# ---------------------------------------------------------------------------
# Fix 8: Duplicate skill cleanup
# ---------------------------------------------------------------------------


class TestDuplicateSkillCleanup:
    """Fix 8 — duplicate skill names are rejected during loading."""

    def test_duplicate_skill_names_skipped(self, caplog):
        """Loading two skills with the same name: second is skipped, error logged."""
        from automation_agent.skills.registry import SkillRegistryImpl

        skill_template = """\
---
name: {name}
description: Test skill
trigger-keywords: [test]
parameters: {{}}
requires:
  apps: [Safari]
  os: darwin
success-condition: Done
---

## Steps
1. Do thing
   - verify: Done
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "skill_a.md").write_text(skill_template.format(name="my-skill"))
            (p / "skill_b.md").write_text(skill_template.format(name="my-skill"))

            # __init__ calls load_from_directory automatically
            with caplog.at_level(logging.ERROR):
                registry = SkillRegistryImpl(skill_dir=p)

            # Only one instance should be loaded
            assert "my-skill" in registry._skills
            # Error should have been logged about duplicate
            has_duplicate_log = any(
                "Duplicate skill name" in rec.message and "my-skill" in rec.message
                for rec in caplog.records
            )
            assert has_duplicate_log, (
                f"Expected 'Duplicate skill name' error log. Got: "
                f"{[r.message for r in caplog.records]}"
            )

    def test_single_skill_loads_clean(self, caplog):
        """A single skill loads without error/warning."""
        from automation_agent.skills.registry import SkillRegistryImpl

        skill_content = """\
---
name: unique-skill
description: Unique test skill
trigger-keywords: [unique]
parameters: {}
requires:
  apps: [Safari]
  os: darwin
success-condition: Done
---

## Steps
1. Do thing
   - verify: Done
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "unique.md").write_text(skill_content)

            # __init__ calls load_from_directory automatically
            with caplog.at_level(logging.ERROR):
                registry = SkillRegistryImpl(skill_dir=p)

            assert "unique-skill" in registry._skills
            dup_logs = [
                r for r in caplog.records
                if "Duplicate" in r.message
            ]
            assert len(dup_logs) == 0
