"""Tests for P0-1, P0-2, P1-5 consultant review fixes.

P0-1: DerivedSkillSession.serialize_for_context() drops current_steps
P0-2: Librarian groups by (category, category) instead of (category, recommendation)
P1-5: Malformed derived_skill_patch creates empty ReplanPatch instead of None
"""

from __future__ import annotations

import json
import textwrap
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation_agent.config import AgentConfig
from automation_agent.shared_models import ReplanPatch
from automation_agent.skills.derived_skill import DerivedSkillSession
from automation_agent.skills.experience import SkillExperienceStore, _normalize_key
from automation_agent.skills.models import SkillObservation, SkillRequirements, Skill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path, **overrides) -> AgentConfig:
    defaults = dict(
        _env_file=None,
        anthropic_api_key="test-key-not-real",
        model_provider="local",
        skill_learning_dir=tmp_path / "learning",
        skill_librarian_enabled=True,
        grounding_model="",
        grounding_server_url="",
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)


SAMPLE_SKILL_MD = textwrap.dedent("""\
---
name: return-amazon-order
skill-id: return-amazon-order
description: Return an item on Amazon
summary: Automates the Amazon return flow
tags: [ecommerce, return]
trigger-keywords: [return, amazon, refund]
parameters:
  item:
    type: string
    required: true
    description: What to return
requires:
  apps: [Safari]
  os: darwin
success-condition: Return confirmation visible
max-retries: 3
---

## Steps
1. Open orders page
   - verify: Orders page visible
2. Search for "{{item}}"
   - verify: Matching order visible

## Error Recovery
- If return button is absent: look for order details first

## Notes
- Treat labels as likely affordances, not guarantees
""")


def _make_skill(**overrides) -> Skill:
    defaults = dict(
        name="return-amazon-order",
        description="Return an item on Amazon",
        trigger_keywords=["return", "amazon", "refund"],
        parameters={},
        requires=SkillRequirements(apps=["Safari"], os="darwin"),
        success_condition="Return confirmation visible",
        steps_text="1. Open orders\n   - verify: Orders visible",
        error_recovery_text="- If return absent: view details first",
        notes_text="- Treat labels as affordances",
        raw_content=SAMPLE_SKILL_MD,
        skill_id="return-amazon-order",
        tags=["ecommerce", "return"],
        summary="Automates Amazon return flow",
    )
    defaults.update(overrides)
    return Skill(**defaults)


# ===========================================================================
# P0-1: DerivedSkillSession.serialize_for_context() drops current_steps
# ===========================================================================


class TestP0_1_SerializeCurrentSteps:
    """serialize_for_context() must include current_steps so patched steps
    appear in replan and distiller context."""

    def test_serialize_includes_current_steps_from_seed(self):
        """Even before any patch, the seeded steps should appear in output."""
        session = DerivedSkillSession.seed(
            parent_skill_ids=["return-amazon-order"],
            match_types=["direct"],
            steps_text="1. Open orders page\n2. Click return button",
        )
        output = session.serialize_for_context()
        assert "1. Open orders page" in output
        assert "2. Click return button" in output

    def test_serialize_includes_revised_steps_after_patch(self):
        """After apply_patch with revised_steps, serialize must show the new steps."""
        session = DerivedSkillSession.seed(
            parent_skill_ids=["return-amazon-order"],
            match_types=["direct"],
            steps_text="1. Open orders page\n2. Click return button",
        )
        patch = ReplanPatch(
            revised_steps="1. Open orders page\n2. Click View item\n3. Click return",
            replace_labels=[{"old": "Return", "new": "View item", "reason": "label changed"}],
        )
        session.apply_patch(patch)
        output = session.serialize_for_context()
        assert "Click View item" in output
        assert "3. Click return" in output

    def test_serialize_has_current_steps_section_header(self):
        """The serialized output should have a recognizable section for steps."""
        session = DerivedSkillSession.seed(
            parent_skill_ids=["return-amazon-order"],
            match_types=["direct"],
            steps_text="1. Open orders page",
        )
        output = session.serialize_for_context()
        assert "### Current Steps" in output

    def test_serialize_empty_steps_omits_section(self):
        """If current_steps is empty string, the section should be omitted."""
        session = DerivedSkillSession(
            parent_skill_ids=["return-amazon-order"],
            match_types=["direct"],
            current_steps="",
        )
        output = session.serialize_for_context()
        assert "### Current Steps" not in output


# ===========================================================================
# P0-2: Librarian groups by (cat, cat) instead of (cat, recommendation)
# ===========================================================================


class TestP0_2_GroupByRecommendation:
    """_group_observations must group by (category, recommendation), and
    mark_promoted must match the same key space."""

    @pytest.fixture
    def librarian(self, tmp_path):
        from automation_agent.skills.librarian import SkillLibrarian

        config = _make_config(tmp_path)
        experience_store = SkillExperienceStore(tmp_path / "learning")
        mock_registry = MagicMock()
        skill = _make_skill()
        mock_registry.get_skill.return_value = skill
        mock_registry._skills = {"return-amazon-order": skill}
        return SkillLibrarian(
            config=config,
            experience_store=experience_store,
            registry=mock_registry,
        )

    @pytest.fixture
    def experience_store(self, tmp_path):
        return SkillExperienceStore(tmp_path / "learning")

    def test_same_category_different_recommendations_separate_groups(self, librarian):
        """Two observations in same category but different recommendations
        must land in separate groups."""
        obs = [
            SkillObservation(
                category="alternative_path",
                condition="Return absent",
                recommendation="click View item first",
                confidence=0.9,
                run_id="r1",
            ),
            SkillObservation(
                category="alternative_path",
                condition="Return absent",
                recommendation="scroll down to find the button",
                confidence=0.8,
                run_id="r2",
            ),
        ]
        groups = librarian._group_observations(obs)
        assert len(groups) == 2, (
            f"Expected 2 groups for 2 different recommendations in same category, "
            f"got {len(groups)}: {list(groups.keys())}"
        )

    def test_multiple_recommendations_same_category_all_present(self, librarian):
        """Three distinct recommendations in same category -> 3 groups."""
        obs = [
            SkillObservation(
                category="checkpoint",
                condition="c",
                recommendation="verify order title",
                confidence=0.7,
                run_id="r1",
            ),
            SkillObservation(
                category="checkpoint",
                condition="c",
                recommendation="wait for page load",
                confidence=0.8,
                run_id="r2",
            ),
            SkillObservation(
                category="checkpoint",
                condition="c",
                recommendation="check total amount",
                confidence=0.9,
                run_id="r3",
            ),
        ]
        groups = librarian._group_observations(obs)
        assert len(groups) == 3

    def test_group_key_matches_mark_promoted_key(self, librarian, experience_store):
        """The group key from _group_observations must match what mark_promoted
        uses, so that promoted observations are correctly filtered."""
        obs = [
            SkillObservation(
                category="alternative_path",
                condition="Return absent",
                recommendation="click View item first",
                confidence=0.9,
                run_id="r1",
            ),
        ]
        groups = librarian._group_observations(obs)
        group_key = list(groups.keys())[0]

        # The key should be (normalized_category, normalized_recommendation)
        # mark_promoted uses (category, recommendation) normalized the same way
        expected_cat = _normalize_key("alternative_path")
        expected_rec = _normalize_key("click View item first")
        assert group_key == (expected_cat, expected_rec), (
            f"Group key {group_key} doesn't match expected "
            f"({expected_cat}, {expected_rec})"
        )

    def test_mark_promoted_matches_group_keys(self, tmp_path):
        """After grouping and promoting, mark_promoted must actually mark
        the observations as promoted (end-to-end key alignment)."""
        store = SkillExperienceStore(tmp_path / "learning")
        obs = [
            SkillObservation(
                category="alternative_path",
                condition="Return absent",
                recommendation="click View item first",
                confidence=0.9,
                run_id="r1",
            ),
            SkillObservation(
                category="alternative_path",
                condition="Different condition",
                recommendation="scroll down to find button",
                confidence=0.8,
                run_id="r2",
            ),
        ]
        store.append("test-skill", obs)

        from automation_agent.skills.librarian import SkillLibrarian

        config = _make_config(tmp_path)
        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = _make_skill()
        mock_registry._skills = {"test-skill": _make_skill()}
        lib = SkillLibrarian(config=config, experience_store=store, registry=mock_registry)

        groups = lib._group_observations(obs)
        # Pick the first group key and try to mark it promoted
        first_key = list(groups.keys())[0]
        marked = store.mark_promoted("test-skill", {first_key})
        # Should mark exactly 1 observation (only one matches first_key)
        assert marked == 1, (
            f"Expected 1 observation marked for key {first_key}, got {marked}"
        )

    def test_duplicate_recommendations_same_group(self, librarian):
        """Same category + same recommendation (normalized) -> single group."""
        obs = [
            SkillObservation(
                category="Alternative_Path",
                condition="c",
                recommendation="Click VIEW item",
                confidence=0.8,
                run_id="r1",
            ),
            SkillObservation(
                category="alternative_path",
                condition="c",
                recommendation="click view item",
                confidence=0.9,
                run_id="r2",
            ),
        ]
        groups = librarian._group_observations(obs)
        assert len(groups) == 1


# ===========================================================================
# P1-5: Malformed derived_skill_patch creates empty ReplanPatch instead of None
# ===========================================================================


class TestP1_5_MalformedPatchReturnsNone:
    """When the LLM returns a malformed/unusable derived_skill_patch,
    the planner must set replan_patch=None (not an empty ReplanPatch)."""

    def test_malformed_patch_dict_returns_none(self):
        """Non-dict patch_dict should produce replan_patch=None."""
        from automation_agent.planner.planner import ActionPlannerImpl

        planner = ActionPlannerImpl(
            config=AgentConfig(
                _env_file=None,
                anthropic_api_key="test-key-not-real",
                model_provider="local",
            )
        )
        # Simulate LLM response with malformed patch (string instead of dict)
        response = {
            "content": json.dumps({
                "steps": [
                    {"action": "click", "params": {"element": "X"}, "verify": "Y"}
                ],
                "derived_skill_patch": "this is not a dict",
            }),
        }
        plan = planner._parse_plan_response(response, "test goal")
        assert plan.replan_patch is None, (
            f"Expected None for malformed patch, got {plan.replan_patch}"
        )

    def test_empty_dict_patch_returns_none(self):
        """An empty dict patch ({}) should produce replan_patch=None since
        it has no usable content."""
        from automation_agent.planner.planner import ActionPlannerImpl

        planner = ActionPlannerImpl(
            config=AgentConfig(
                _env_file=None,
                anthropic_api_key="test-key-not-real",
                model_provider="local",
            )
        )
        response = {
            "content": json.dumps({
                "steps": [
                    {"action": "click", "params": {"element": "X"}, "verify": "Y"}
                ],
                "derived_skill_patch": {},
            }),
        }
        plan = planner._parse_plan_response(response, "test goal")
        assert plan.replan_patch is None, (
            f"Expected None for empty patch dict, got {plan.replan_patch}"
        )

    def test_patch_with_only_empty_lists_returns_none(self):
        """A patch with all empty fields should be treated as None."""
        from automation_agent.planner.planner import ActionPlannerImpl

        planner = ActionPlannerImpl(
            config=AgentConfig(
                _env_file=None,
                anthropic_api_key="test-key-not-real",
                model_provider="local",
            )
        )
        response = {
            "content": json.dumps({
                "steps": [
                    {"action": "click", "params": {"element": "X"}, "verify": "Y"}
                ],
                "derived_skill_patch": {
                    "replace_labels": [],
                    "add_landmarks": [],
                    "verify_improvements": [],
                    "failed_assumptions": [],
                    "successful_adaptations": [],
                    "revised_steps": "",
                },
            }),
        }
        plan = planner._parse_plan_response(response, "test goal")
        assert plan.replan_patch is None, (
            f"Expected None for all-empty patch, got {plan.replan_patch}"
        )

    def test_valid_patch_returns_replan_patch(self):
        """A patch with actual content should return a proper ReplanPatch."""
        from automation_agent.planner.planner import ActionPlannerImpl

        planner = ActionPlannerImpl(
            config=AgentConfig(
                _env_file=None,
                anthropic_api_key="test-key-not-real",
                model_provider="local",
            )
        )
        response = {
            "content": json.dumps({
                "steps": [
                    {"action": "click", "params": {"element": "X"}, "verify": "Y"}
                ],
                "derived_skill_patch": {
                    "replace_labels": [
                        {"old": "Return", "new": "View item", "reason": "changed"}
                    ],
                    "add_landmarks": ["order details page"],
                },
            }),
        }
        plan = planner._parse_plan_response(response, "test goal")
        assert plan.replan_patch is not None
        assert len(plan.replan_patch.replace_labels) == 1
        assert len(plan.replan_patch.add_landmarks) == 1

    def test_no_patch_key_returns_none(self):
        """When derived_skill_patch key is absent, replan_patch should be None."""
        from automation_agent.planner.planner import ActionPlannerImpl

        planner = ActionPlannerImpl(
            config=AgentConfig(
                _env_file=None,
                anthropic_api_key="test-key-not-real",
                model_provider="local",
            )
        )
        response = {
            "content": json.dumps({
                "steps": [
                    {"action": "click", "params": {"element": "X"}, "verify": "Y"}
                ],
            }),
        }
        plan = planner._parse_plan_response(response, "test goal")
        assert plan.replan_patch is None

    def test_replan_patch_is_empty_helper(self):
        """ReplanPatch.is_empty() should return True for all-default patches."""
        empty = ReplanPatch()
        assert empty.is_empty()

        non_empty = ReplanPatch(add_landmarks=["something"])
        assert not non_empty.is_empty()

        non_empty2 = ReplanPatch(revised_steps="1. Do something")
        assert not non_empty2.is_empty()

    def test_from_dict_returns_none_for_non_dict(self):
        """from_dict() returns None directly for non-dict input."""
        assert ReplanPatch.from_dict("not a dict") is None
        assert ReplanPatch.from_dict(42) is None
        assert ReplanPatch.from_dict(None) is None
        assert ReplanPatch.from_dict([1, 2, 3]) is None

    def test_from_dict_returns_none_for_empty_dict(self):
        """from_dict() returns None for empty dict (no usable content)."""
        assert ReplanPatch.from_dict({}) is None

    def test_from_dict_returns_none_for_all_empty_fields(self):
        """from_dict() returns None when all fields are empty/default."""
        result = ReplanPatch.from_dict({
            "replace_labels": [],
            "add_landmarks": [],
            "verify_improvements": [],
            "failed_assumptions": [],
            "successful_adaptations": [],
            "revised_steps": "",
        })
        assert result is None

    def test_from_dict_returns_patch_for_valid_content(self):
        """from_dict() returns a ReplanPatch when content is present."""
        result = ReplanPatch.from_dict({
            "add_landmarks": ["sidebar"],
        })
        assert result is not None
        assert result.add_landmarks == ["sidebar"]

    def test_from_dict_revised_steps_none_returns_none(self):
        """revised_steps=None must not produce string 'None'."""
        result = ReplanPatch.from_dict({"revised_steps": None})
        assert result is None

    def test_from_dict_revised_steps_whitespace_returns_none(self):
        """revised_steps with only whitespace must be treated as empty."""
        result = ReplanPatch.from_dict({"revised_steps": "   \n  "})
        assert result is None

    def test_from_dict_revised_steps_int_returns_none(self):
        """revised_steps as non-string (int) must not crash."""
        result = ReplanPatch.from_dict({"revised_steps": 42})
        assert result is None

    def test_apply_patch_whitespace_revised_steps_no_overwrite(self):
        """Whitespace-only revised_steps must not overwrite current_steps."""
        session = DerivedSkillSession.seed(
            "test-skill", "test goal",
            "1. Open app\n2. Click button"
        )
        original = session.current_steps
        patch = ReplanPatch(revised_steps="   ")
        session.apply_patch(patch)
        assert session.current_steps == original


# ===========================================================================
# Finding 2: E2E test for P0-1 — current_steps reaching planner prompt
# ===========================================================================


class TestP0_1_CurrentStepsReachPlanner:
    """Verify that current_steps goes through the full
    agent.py -> serialize_for_context() -> planner prompt path.
    """

    async def test_current_steps_in_planner_plan_context(self):
        """After skill match, current_steps from DerivedSkillSession
        appears in the skill_context kwarg passed to planner.plan()."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from automation_agent.orchestrator.agent import AutomationAgent
        from automation_agent.shared_models import (
            ActionPlan,
            ActionStep,
            MatchType,
            SkillMatchResult,
            SkillRouteCandidate,
            StepResult,
        )

        steps_text = "1. Navigate to orders page\n2. Click the return button"
        skill_match = SkillMatchResult(
            skill_name="return-amazon-order",
            expanded_steps=steps_text,
            skill_context=(
                "### [direct] return-amazon-order (confidence: 0.92)\n"
                "Reason: Exact match.\n\n"
                f"## Steps\n{steps_text}\n\n"
                "## Recovery Heuristics\n- Retry if page not loaded"
            ),
            params={},
            candidates=[
                SkillRouteCandidate(
                    skill_id="return-amazon-order",
                    match_type=MatchType.DIRECT,
                    confidence=0.92,
                    reason="Exact match",
                )
            ],
        )

        two_step_plan = ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"element": "Return button"},
                    verify="Return dialog is visible",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Test",
        )
        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=two_step_plan)

        registry = MagicMock()
        registry.match = AsyncMock(return_value=skill_match)

        coordinator = AsyncMock()
        coordinator.describe_screen = AsyncMock(return_value="Desktop")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value="ZmFrZQ==")

        actuator = MagicMock()

        import tempfile
        from pathlib import Path
        from automation_agent.config import AgentConfig
        from automation_agent.logging.event_logger import EventLogger

        tmp = Path(tempfile.mkdtemp())
        config = AgentConfig(
            _env_file=None,
            anthropic_api_key="test-key-not-real",
            model_provider="local",
        )
        logger = EventLogger(tmp / "logs")

        agent = AutomationAgent(
            planner=planner,
            skill_registry=registry,
            coordinator=coordinator,
            actuator=actuator,
            config=config,
            logger=logger,
        )

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = StepResult(
                step=ActionStep(
                    action="click",
                    params={"element": "Return button"},
                    verify="Return dialog is visible",
                ),
                success=True,
                verification_method="actuator_state",
                evidence="Return dialog is visible",
            )
            await agent.execute("Return my Amazon order")

        # Verify planner.plan was called with skill_context containing
        # the current_steps from the derived session
        call_kwargs = planner.plan.call_args
        ctx = call_kwargs.kwargs.get("skill_context", "")
        assert "### Current Steps" in ctx, (
            f"Expected '### Current Steps' in planner skill_context, got:\n{ctx}"
        )
        assert "Navigate to orders page" in ctx, (
            f"Expected current_steps content in planner skill_context, got:\n{ctx}"
        )
        assert "Click the return button" in ctx, (
            f"Expected current_steps content in planner skill_context, got:\n{ctx}"
        )

    async def test_revised_steps_in_replan_context(self):
        """After a replan with revised_steps, the updated current_steps
        appear in the distiller context (via learn_from_run).

        Follows the pattern from test_orchestrator_adaptive.py's
        test_replan_applies_patch_to_session.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from automation_agent.orchestrator.agent import AutomationAgent
        from automation_agent.shared_models import (
            ActionPlan,
            ActionStep,
            FindElementResult,
            MatchType,
            ReplanPatch,
            SkillMatchResult,
            SkillRouteCandidate,
            StepResult,
        )

        steps_text = "1. Open orders page\n2. Click return button"
        skill_match = SkillMatchResult(
            skill_name="return-amazon-order",
            expanded_steps=steps_text,
            skill_context=(
                "### [direct] return-amazon-order (confidence: 0.92)\n"
                "Reason: Exact match.\n\n"
                f"## Steps\n{steps_text}\n\n"
                "## Recovery Heuristics\n- Retry if page not loaded"
            ),
            params={},
            candidates=[
                SkillRouteCandidate(
                    skill_id="return-amazon-order",
                    match_type=MatchType.DIRECT,
                    confidence=0.92,
                    reason="Exact match",
                )
            ],
        )

        # First plan: open_url (to avoid nav injection) then click that fails
        fail_plan = ActionPlan(
            steps=[
                ActionStep(
                    action="open_url",
                    params={"url": "https://www.amazon.com/orders"},
                    verify="Amazon orders page visible",
                ),
                ActionStep(
                    action="click",
                    params={"element": "Return"},
                    verify="Return form visible",
                    on_fail="replan",
                ),
            ],
            goal="Return order",
        )

        # Replan returns revised_steps in its patch.
        # Include open_url (avoids nav enforcement) + interaction steps
        # (avoids truncation detection by _harden_plan).
        replan_result = ActionPlan(
            steps=[
                ActionStep(
                    action="open_url",
                    params={"url": "https://www.amazon.com/orders"},
                    verify="Orders page loaded",
                ),
                ActionStep(
                    action="click",
                    params={"element": "View Details"},
                    verify="Details page visible",
                ),
                ActionStep(
                    action="click",
                    params={"element": "Return"},
                    verify="Return form visible",
                ),
                ActionStep(
                    action="click",
                    params={"element": "Submit"},
                    verify="Return submitted",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Replanned",
            replan_patch=ReplanPatch(
                revised_steps="1. Open orders\n2. Click View Details\n3. Click Return",
            ),
        )

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=fail_plan)
        planner.replan = AsyncMock(return_value=replan_result)

        registry = MagicMock()
        registry.match = AsyncMock(return_value=skill_match)
        registry.learn_from_run = AsyncMock(return_value=[])

        coordinator = AsyncMock()
        coordinator.describe_screen = AsyncMock(return_value="Desktop")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value="ZmFrZQ==")
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=500, y=300, confidence=0.9, source="vision")
        )

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.activate_app = MagicMock(return_value={"success": True})
        actuator.open_url = MagicMock(return_value={"success": True})

        import tempfile
        from pathlib import Path
        from automation_agent.config import AgentConfig
        from automation_agent.logging.event_logger import EventLogger

        tmp = Path(tempfile.mkdtemp())
        config = AgentConfig(
            _env_file=None,
            anthropic_api_key="test-key-not-real",
            model_provider="local",
        )
        logger = EventLogger(tmp / "logs")

        agent = AutomationAgent(
            planner=planner,
            skill_registry=registry,
            coordinator=coordinator,
            actuator=actuator,
            config=config,
            logger=logger,
        )

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            _click_call_count = 0

            def _verify_side_effect(step, actuator_result, **kwargs):
                """Fail the first click (initial plan) to trigger replan,
                succeed for everything in the replan."""
                nonlocal _click_call_count
                if step.action == "open_url":
                    return StepResult(
                        step=step, success=True,
                        verification_method="actuator_state",
                        evidence="Amazon orders page visible",
                    )
                if step.action == "activate_app":
                    return StepResult(
                        step=step, success=True,
                        verification_method="actuator_state",
                        evidence="Safari visible",
                    )
                if step.action == "done":
                    return StepResult(
                        step=step, success=True,
                        verification_method="",
                        evidence="Done",
                    )
                if step.action == "click":
                    _click_call_count += 1
                    if _click_call_count == 1:
                        # First click (initial plan) fails -> triggers replan
                        return StepResult(
                            step=step, success=False,
                            verification_method="vision",
                            evidence="Return button not found",
                        )
                    # Subsequent clicks (replan) succeed
                    return StepResult(
                        step=step, success=True,
                        verification_method="actuator_state",
                        evidence="Step completed",
                    )
                # All other steps (press_key) succeed
                return StepResult(
                    step=step, success=True,
                    verification_method="actuator_state",
                    evidence="Step completed",
                )
            mock_verify.side_effect = _verify_side_effect
            await agent.execute("Return my order")

        # Verify the distiller context includes the revised steps
        registry.learn_from_run.assert_awaited_once()
        learn_kwargs = registry.learn_from_run.call_args
        distiller_ctx = learn_kwargs.kwargs.get("skill_context", "")
        assert "Click View Details" in distiller_ctx, (
            "Revised steps from replan patch should appear in distiller context"
        )


# ===========================================================================
# Finding 4: _normalize_key() divergence test
# ===========================================================================


class TestNormalizeKeyAlignment:
    """Verify that _normalize_key() is a single shared function used by both
    librarian.py and experience.py (no duplication)."""

    def test_librarian_imports_from_experience(self):
        """librarian._normalize_key IS experience._normalize_key (same object)."""
        from automation_agent.skills.experience import (
            _normalize_key as experience_normalize,
        )
        from automation_agent.skills.librarian import (
            _normalize_key as librarian_normalize,
        )

        assert librarian_normalize is experience_normalize, (
            "_normalize_key should be imported from experience.py into librarian.py, "
            "not duplicated"
        )

    def test_librarian_has_no_instance_normalize_key(self):
        """SkillLibrarian should not define its own _normalize_key method."""
        from automation_agent.skills.librarian import SkillLibrarian

        assert not hasattr(SkillLibrarian, "_normalize_key"), (
            "SkillLibrarian should not have its own _normalize_key; "
            "it should use the shared function from experience.py"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "simple text",
            "  UPPER CASE with SPACES  ",
            "dashes-in-text",
            "underscores_in_text",
            "Mixed-Case_With-Dashes_And_Underscores",
            "punctuation! here? yes.",
            "   lots   of   whitespace   ",
            "",
            "click View item first",
            "alternative_path",
            "Click VIEW item",
            "a",
            "123 numbers 456",
            "emoji 🎉 text",
            "newlines\nin\ntext",
            "tabs\there\ttoo",
            "CamelCaseNoSpaces",
            "already normalized",
            "ALLCAPS",
            "special chars: @#$%^&*()",
        ],
    )
    def test_normalize_key_behavior(self, text):
        """_normalize_key produces expected normalization (extra safety)."""
        result = _normalize_key(text)
        # Basic contracts: lowercase, no leading/trailing whitespace
        assert result == result.lower()
        assert result == result.strip()


# ===========================================================================
# Finding 5: Whitespace-only current_steps
# ===========================================================================


class TestP0_1_WhitespaceOnlyCurrentSteps:
    """Whitespace-only current_steps should NOT produce a ### Current Steps
    section in serialize_for_context()."""

    @pytest.mark.parametrize(
        "whitespace_value",
        [
            " ",
            "  ",
            "\n",
            "\n\n",
            "\t",
            " \n \t ",
        ],
    )
    def test_whitespace_only_steps_omits_section(self, whitespace_value):
        """Whitespace-only current_steps should be treated as empty."""
        session = DerivedSkillSession(
            parent_skill_ids=["test-skill"],
            match_types=["direct"],
            current_steps=whitespace_value,
        )
        output = session.serialize_for_context()
        assert "### Current Steps" not in output, (
            f"Whitespace-only current_steps {whitespace_value!r} should not "
            f"produce a Current Steps section"
        )

    def test_non_whitespace_steps_includes_section(self):
        """Non-whitespace current_steps should produce the section."""
        session = DerivedSkillSession(
            parent_skill_ids=["test-skill"],
            match_types=["direct"],
            current_steps="1. Do something",
        )
        output = session.serialize_for_context()
        assert "### Current Steps" in output
