"""Unit tests for orchestrator adaptive skill integration (Slice 4).

Tests DerivedSkillSession lifecycle, context assembly, replan patch
integration, fallback plan isolation, and distiller context scoping.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    MatchType,
    ReplanPatch,
    SkillMatchResult,
    SkillRouteCandidate,
    StepResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    defaults = {"_env_file": None, "anthropic_api_key": "test-key-not-real"}
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _done_plan(goal="Test goal"):
    return ActionPlan(
        steps=[ActionStep(action="done", params={}, verify="")],
        goal=goal,
    )


def _simple_plan(goal="Test goal"):
    return ActionPlan(
        steps=[
            ActionStep(
                action="activate_app",
                params={"app_name": "Safari"},
                verify="Safari is frontmost app",
            ),
            ActionStep(action="done", params={}, verify=""),
        ],
        goal=goal,
    )


def _make_skill_match(
    skill_name="return-amazon-order",
    expanded_steps="1. Open Amazon\n   - verify: Amazon page visible",
    skill_context=None,
    candidates=None,
    params=None,
):
    """Create a SkillMatchResult for testing."""
    if candidates is None:
        candidates = [
            SkillRouteCandidate(
                skill_id=skill_name,
                match_type=MatchType.DIRECT,
                confidence=0.92,
                reason="Exact match",
            )
        ]
    if skill_context is None:
        skill_context = (
            f"### [direct] {skill_name} (confidence: 0.92)\n"
            f"Reason: Exact match.\n\n"
            f"## Steps\n{expanded_steps}\n\n"
            f"## Recovery Heuristics\n- Retry if page not loaded"
        )
    return SkillMatchResult(
        skill_name=skill_name,
        expanded_steps=expanded_steps,
        skill_context=skill_context,
        params=params or {},
        candidates=candidates,
    )


def _make_multi_skill_context():
    """Create a multi-skill context string with --- separators."""
    return (
        "## Skill Priors\n\n"
        "### [direct] return-amazon-order (confidence: 0.92)\n"
        "Reason: Exact match for Amazon return flow.\n\n"
        "## Steps\n"
        "1. Use open_url to navigate to https://www.amazon.com/orders\n"
        "   - verify: Amazon orders page visible\n"
        "2. Click the Return button\n"
        "   - verify: Return form visible\n\n"
        "## Recovery Heuristics\n"
        "- Retry if page not loaded\n\n"
        "---\n\n"
        "### [analogical] return-walmart-order (confidence: 0.41)\n"
        "Reason: Similar return flow.\n\n"
        "## Steps\n"
        "1. Navigate to https://www.walmart.com/orders\n"
        "   - verify: Walmart orders page visible\n"
        "2. Click Return item\n"
        "   - verify: Return form shows\n\n"
        "---\n\n"
        "## Derived Procedure (run-local, current best hypothesis)\n"
        "Parent skill(s): return-amazon-order\n"
        "### Label Replacements\n"
        '- "Orders" -> "Purchase History"\n'
    )


def _make_agent(
    planner=None,
    skill_registry=None,
    coordinator=None,
    actuator=None,
    logger=None,
    config=None,
):
    if planner is None:
        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_simple_plan())
        planner.replan = AsyncMock(return_value=_simple_plan("Replanned"))
    if skill_registry is None:
        skill_registry = MagicMock()
        skill_registry.match = AsyncMock(return_value=None)
    if coordinator is None:
        coordinator = AsyncMock()
        coordinator.describe_screen = AsyncMock(return_value="Desktop")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value="ZmFrZQ==")
    if actuator is None:
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.activate_app = MagicMock(return_value={"success": True})
        actuator.type_text = MagicMock(return_value={"success": True})
        actuator.press_key = MagicMock(return_value={"success": True})
        actuator.open_url = MagicMock(return_value={"success": True})
        actuator.quit_app = MagicMock(return_value={"success": True})
    if config is None:
        config = _make_config()
    if logger is None:
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp())
        logger = EventLogger(tmp / "logs")
    return AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDerivedSessionLifecycle:
    """Tests for DerivedSkillSession creation and threading in execute()."""

    async def test_execute_creates_derived_session(self):
        """After match with candidates, derived_session is created and
        skill_context passed to planner includes derived procedure."""
        skill_match = _make_skill_match()
        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_simple_plan())
        registry = MagicMock()
        registry.match = AsyncMock(return_value=skill_match)

        agent = _make_agent(planner=planner, skill_registry=registry)

        # Patch StepVerifier.verify to auto-pass
        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = StepResult(
                step=ActionStep(action="activate_app", params={}, verify="ok"),
                success=True,
                verification_method="actuator_state",
                evidence="Passed",
            )
            await agent.execute("Return my Amazon order")

        # planner.plan should have been called with skill_context containing
        # both multi-skill context AND derived procedure
        call_kwargs = planner.plan.call_args
        ctx = call_kwargs.kwargs.get("skill_context") or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs.kwargs.get("skill_context")
        assert ctx is not None
        assert "Derived Procedure" in ctx

    async def test_execute_no_match_no_session(self):
        """No skill match means no derived session; planner gets None."""
        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_simple_plan())
        registry = MagicMock()
        registry.match = AsyncMock(return_value=None)

        agent = _make_agent(planner=planner, skill_registry=registry)

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = StepResult(
                step=ActionStep(action="activate_app", params={}, verify="ok"),
                success=True,
                verification_method="actuator_state",
                evidence="Passed",
            )
            await agent.execute("Do something without skills")

        call_kwargs = planner.plan.call_args
        assert call_kwargs.kwargs.get("skill_context") is None

    async def test_execute_with_analogical_match(self):
        """Analogical match creates session with correct match_type."""
        candidates = [
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.ANALOGICAL,
                confidence=0.65,
                reason="Similar flow",
            )
        ]
        skill_match = _make_skill_match(candidates=candidates)
        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_simple_plan())
        registry = MagicMock()
        registry.match = AsyncMock(return_value=skill_match)

        agent = _make_agent(planner=planner, skill_registry=registry)

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = StepResult(
                step=ActionStep(action="activate_app", params={}, verify="ok"),
                success=True,
                verification_method="actuator_state",
                evidence="Passed",
            )
            await agent.execute("Return my Walmart order")

        call_kwargs = planner.plan.call_args
        ctx = call_kwargs.kwargs.get("skill_context")
        assert ctx is not None
        assert "Derived Procedure" in ctx

    async def test_skill_context_includes_derived_procedure(self):
        """Planner receives derived procedure text in skill_context."""
        skill_match = _make_skill_match()
        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_simple_plan())
        registry = MagicMock()
        registry.match = AsyncMock(return_value=skill_match)

        agent = _make_agent(planner=planner, skill_registry=registry)

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = StepResult(
                step=ActionStep(action="activate_app", params={}, verify="ok"),
                success=True,
                verification_method="actuator_state",
                evidence="Passed",
            )
            await agent.execute("Return my order")

        call_kwargs = planner.plan.call_args
        ctx = call_kwargs.kwargs.get("skill_context")
        assert "Derived Procedure" in ctx
        assert "Parent skill(s):" in ctx

    async def test_existing_execute_flow_unchanged(self):
        """Simple execute without skills works as before."""
        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_simple_plan())
        registry = MagicMock()
        registry.match = AsyncMock(return_value=None)

        agent = _make_agent(planner=planner, skill_registry=registry)

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = StepResult(
                step=ActionStep(action="activate_app", params={}, verify="ok"),
                success=True,
                verification_method="actuator_state",
                evidence="Passed",
            )
            result = await agent.execute("Open Calculator")

        assert result.success
        planner.plan.assert_awaited_once()

    async def test_concurrent_execute_no_clobber(self):
        """Two concurrent execute() calls on same agent don't share state."""
        skill_match_a = _make_skill_match(
            skill_name="skill-a",
            expanded_steps="1. Step A\n   - verify: A done",
        )
        skill_match_b = _make_skill_match(
            skill_name="skill-b",
            expanded_steps="1. Step B\n   - verify: B done",
        )

        call_count = 0

        async def alternating_match(goal):
            nonlocal call_count
            call_count += 1
            if "task-a" in goal:
                return skill_match_a
            return skill_match_b

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_simple_plan())
        registry = MagicMock()
        registry.match = AsyncMock(side_effect=alternating_match)

        agent = _make_agent(planner=planner, skill_registry=registry)

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = StepResult(
                step=ActionStep(action="activate_app", params={}, verify="ok"),
                success=True,
                verification_method="actuator_state",
                evidence="Passed",
            )
            # Run two execute() calls concurrently
            results = await asyncio.gather(
                agent.execute("Do task-a"),
                agent.execute("Do task-b"),
            )

        # Both should succeed without clobbering each other's sessions
        assert results[0].success
        assert results[1].success
        # Planner should have been called twice
        assert planner.plan.await_count == 2

        # Verify each call got different skill_context with distinct parent IDs
        calls = planner.plan.call_args_list
        ctx_a = calls[0].kwargs.get("skill_context", "")
        ctx_b = calls[1].kwargs.get("skill_context", "")
        # Both should have derived procedure
        assert "Derived Procedure" in ctx_a
        assert "Derived Procedure" in ctx_b
        # Each context should reference its own parent skill, not the other's
        assert "skill-a" in ctx_a, "task-a context should reference skill-a"
        assert "skill-b" in ctx_b, "task-b context should reference skill-b"


class TestReplanPatchIntegration:
    """Tests for replan patch application to derived session."""

    async def test_replan_applies_patch_to_session(self):
        """Replan with patch updates session state via argument."""
        skill_match = _make_skill_match()
        patch_data = ReplanPatch(
            replace_labels=[{"old": "Orders", "new": "Purchase History"}],
            add_landmarks=["sidebar nav"],
        )
        replan_result = ActionPlan(
            steps=[
                ActionStep(
                    action="activate_app",
                    params={"app_name": "Safari"},
                    verify="Safari visible",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Replanned",
            replan_patch=patch_data,
        )

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_simple_plan())
        planner.replan = AsyncMock(return_value=replan_result)
        registry = MagicMock()
        registry.match = AsyncMock(return_value=skill_match)
        registry.learn_from_run = AsyncMock(return_value=[])

        agent = _make_agent(planner=planner, skill_registry=registry)

        # Make the first step fail so replan is triggered
        fail_plan = ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"x": 100, "y": 100},
                    verify="Button clicked",
                    on_fail="replan",
                ),
            ],
            goal="Test",
        )
        planner.plan = AsyncMock(return_value=fail_plan)

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            # First step fails, then replan steps pass
            mock_verify.side_effect = [
                StepResult(
                    step=fail_plan.steps[0],
                    success=False,
                    verification_method="vision",
                    evidence="Button not found",
                ),
                StepResult(
                    step=replan_result.steps[0],
                    success=True,
                    verification_method="actuator_state",
                    evidence="Safari visible",
                ),
            ]
            await agent.execute("Return my order")

        # Replan should have been called with skill_context
        planner.replan.assert_awaited_once()
        replan_kwargs = planner.replan.call_args
        replan_ctx = replan_kwargs.kwargs.get("skill_context") or (
            replan_kwargs[0][1] if len(replan_kwargs[0]) > 1 else None
        )
        assert replan_ctx is not None

        # Verify the patch was actually applied: distiller context should
        # contain the patched labels and landmarks
        registry.learn_from_run.assert_awaited_once()
        learn_kwargs = registry.learn_from_run.call_args
        distiller_ctx = learn_kwargs.kwargs.get("skill_context", "")
        assert "Purchase History" in distiller_ctx, (
            "Patched label 'Purchase History' should appear in distiller context"
        )
        assert "sidebar nav" in distiller_ctx, (
            "Patched landmark 'sidebar nav' should appear in distiller context"
        )

    async def test_replan_without_patch_session_unchanged(self):
        """Replan without patch leaves session as-is."""
        skill_match = _make_skill_match()
        replan_result = ActionPlan(
            steps=[
                ActionStep(
                    action="activate_app",
                    params={"app_name": "Safari"},
                    verify="Safari visible",
                ),
                ActionStep(action="done", params={}, verify=""),
            ],
            goal="Replanned",
            replan_patch=None,  # No patch
        )

        planner = AsyncMock()
        fail_plan = ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"x": 100, "y": 100},
                    verify="Button clicked",
                    on_fail="replan",
                ),
            ],
            goal="Test",
        )
        planner.plan = AsyncMock(return_value=fail_plan)
        planner.replan = AsyncMock(return_value=replan_result)
        registry = MagicMock()
        registry.match = AsyncMock(return_value=skill_match)

        agent = _make_agent(planner=planner, skill_registry=registry)

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.side_effect = [
                StepResult(
                    step=fail_plan.steps[0],
                    success=False,
                    verification_method="vision",
                    evidence="Failed",
                ),
                StepResult(
                    step=replan_result.steps[0],
                    success=True,
                    verification_method="actuator_state",
                    evidence="Passed",
                ),
            ]
            await agent.execute("Return my order")

        # Should still succeed; no patch means session unchanged
        planner.replan.assert_awaited_once()

    async def test_replan_receives_session_as_argument(self):
        """_replan_and_continue receives derived_session as parameter, not from self."""
        skill_match = _make_skill_match()

        planner = AsyncMock()
        fail_plan = ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"x": 100, "y": 100},
                    verify="Button clicked",
                    on_fail="replan",
                ),
            ],
            goal="Test",
        )
        planner.plan = AsyncMock(return_value=fail_plan)
        replan_result = _simple_plan("Replanned")
        planner.replan = AsyncMock(return_value=replan_result)
        registry = MagicMock()
        registry.match = AsyncMock(return_value=skill_match)

        agent = _make_agent(planner=planner, skill_registry=registry)

        # Verify that agent has no _derived_session attribute
        assert not hasattr(agent, "_derived_session")

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.side_effect = [
                StepResult(
                    step=fail_plan.steps[0],
                    success=False,
                    verification_method="vision",
                    evidence="Failed",
                ),
                StepResult(
                    step=replan_result.steps[0],
                    success=True,
                    verification_method="actuator_state",
                    evidence="Passed",
                ),
            ]
            await agent.execute("Return order")

        # After execution, agent should still have no _derived_session
        assert not hasattr(agent, "_derived_session")


class TestDistillerContext:
    """Tests for distiller context isolation in _maybe_learn_skill_run."""

    async def test_learn_from_run_includes_derived_context(self):
        """Distiller receives derived procedure in context via argument."""
        skill_match = _make_skill_match()

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_simple_plan())
        registry = MagicMock()
        registry.match = AsyncMock(return_value=skill_match)
        registry.learn_from_run = AsyncMock(return_value=[])

        agent = _make_agent(planner=planner, skill_registry=registry)

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = StepResult(
                step=ActionStep(action="activate_app", params={}, verify="ok"),
                success=True,
                verification_method="actuator_state",
                evidence="Passed",
            )
            await agent.execute("Return my order")

        # learn_from_run should have been called
        registry.learn_from_run.assert_awaited_once()
        call_kwargs = registry.learn_from_run.call_args
        learn_ctx = call_kwargs.kwargs.get("skill_context", "")
        # Should include derived procedure
        assert "Derived Procedure" in learn_ctx
        # Should include parent skill's expanded steps
        assert "Open Amazon" in learn_ctx

    async def test_learn_from_run_excludes_other_candidates(self):
        """Distiller context does NOT contain analogical/generic skill sections."""
        candidates = [
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.DIRECT,
                confidence=0.92,
                reason="Exact match",
            ),
            SkillRouteCandidate(
                skill_id="open-app-navigate",
                match_type=MatchType.ANALOGICAL,
                confidence=0.41,
                reason="Generic nav",
            ),
        ]
        multi_ctx = _make_multi_skill_context()
        skill_match = _make_skill_match(
            candidates=candidates,
            skill_context=multi_ctx,
        )

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_simple_plan())
        registry = MagicMock()
        registry.match = AsyncMock(return_value=skill_match)
        registry.learn_from_run = AsyncMock(return_value=[])

        agent = _make_agent(planner=planner, skill_registry=registry)

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = StepResult(
                step=ActionStep(action="activate_app", params={}, verify="ok"),
                success=True,
                verification_method="actuator_state",
                evidence="Passed",
            )
            await agent.execute("Return my order")

        registry.learn_from_run.assert_awaited_once()
        call_kwargs = registry.learn_from_run.call_args
        learn_ctx = call_kwargs.kwargs.get("skill_context", "")
        # Should NOT contain the analogical skill's steps or context
        # (parent_skill_ids in derived procedure metadata is OK)
        assert "[analogical]" not in learn_ctx
        assert "return-walmart-order" not in learn_ctx
        assert "Navigate to https://www.walmart.com" not in learn_ctx

    async def test_learn_from_run_primary_skill_only(self):
        """Distiller's skill_context contains only the primary skill's
        expanded_steps, not multi-skill assembly."""
        candidates = [
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.DIRECT,
                confidence=0.92,
                reason="Exact match",
            ),
            SkillRouteCandidate(
                skill_id="generic-browser",
                match_type=MatchType.GENERIC,
                confidence=0.30,
                reason="Generic",
            ),
        ]
        multi_ctx = _make_multi_skill_context()
        skill_match = _make_skill_match(
            candidates=candidates,
            skill_context=multi_ctx,
            expanded_steps="1. Open Amazon orders page\n   - verify: Orders visible",
        )

        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_simple_plan())
        registry = MagicMock()
        registry.match = AsyncMock(return_value=skill_match)
        registry.learn_from_run = AsyncMock(return_value=[])

        agent = _make_agent(planner=planner, skill_registry=registry)

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = StepResult(
                step=ActionStep(action="activate_app", params={}, verify="ok"),
                success=True,
                verification_method="actuator_state",
                evidence="Passed",
            )
            await agent.execute("Return my order")

        registry.learn_from_run.assert_awaited_once()
        call_kwargs = registry.learn_from_run.call_args
        learn_ctx = call_kwargs.kwargs.get("skill_context", "")
        # Primary skill's expanded_steps should be present
        assert "Open Amazon orders page" in learn_ctx
        # Generic candidate's steps/context should NOT be in distiller context
        # (parent_skill_ids metadata in derived procedure is acceptable)
        assert "[generic]" not in learn_ctx
        assert "Generic browser" not in learn_ctx


    async def test_learn_from_run_with_empty_expanded_steps(self):
        """When expanded_steps is empty, distiller still receives derived procedure."""
        skill_match = _make_skill_match(expanded_steps="")
        planner = AsyncMock()
        planner.plan = AsyncMock(return_value=_simple_plan())
        registry = MagicMock()
        registry.match = AsyncMock(return_value=skill_match)
        registry.learn_from_run = AsyncMock(return_value=[])

        agent = _make_agent(planner=planner, skill_registry=registry)

        with patch.object(agent.verifier, "verify", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = StepResult(
                step=ActionStep(action="activate_app", params={}, verify="ok"),
                success=True,
                verification_method="actuator_state",
                evidence="Passed",
            )
            await agent.execute("Return my order")

        registry.learn_from_run.assert_awaited_once()
        call_kwargs = registry.learn_from_run.call_args
        learn_ctx = call_kwargs.kwargs.get("skill_context", "")
        # Derived procedure should still be present even with empty steps
        assert "Derived Procedure" in learn_ctx
        assert "Parent skill(s):" in learn_ctx


class TestMaybeLearnEdgeCases:
    """Edge cases for _maybe_learn_skill_run not covered by TestDistillerContext."""

    async def test_learn_skipped_when_no_skill_name(self):
        """_maybe_learn_skill_run returns early when skill_name is None."""
        agent = _make_agent()
        registry = agent.skill_registry
        registry.learn_from_run = AsyncMock(return_value=[])

        await agent._maybe_learn_skill_run(
            goal="test",
            skill_name=None,
            skill_context="context",
            step_results=[StepResult(
                step=ActionStep(action="click", params={}, verify="ok"),
                success=True, evidence="ok",
            )],
            had_replan=False,
        )

        registry.learn_from_run.assert_not_awaited()

    async def test_learn_skipped_when_empty_step_results(self):
        """_maybe_learn_skill_run returns early when step_results is empty."""
        agent = _make_agent()
        registry = agent.skill_registry
        registry.learn_from_run = AsyncMock(return_value=[])

        await agent._maybe_learn_skill_run(
            goal="test",
            skill_name="test-skill",
            skill_context="context",
            step_results=[],
            had_replan=False,
        )

        registry.learn_from_run.assert_not_awaited()

    async def test_learn_skipped_when_registry_lacks_method(self):
        """_maybe_learn_skill_run returns early when registry has no learn_from_run."""
        agent = _make_agent()
        # Remove learn_from_run from registry mock
        if hasattr(agent.skill_registry, "learn_from_run"):
            del agent.skill_registry.learn_from_run

        # Should not raise — just returns early
        await agent._maybe_learn_skill_run(
            goal="test",
            skill_name="test-skill",
            skill_context="context",
            step_results=[StepResult(
                step=ActionStep(action="click", params={}, verify="ok"),
                success=True, evidence="ok",
            )],
            had_replan=False,
        )

    async def test_learn_exception_does_not_propagate(self):
        """_maybe_learn_skill_run swallows exceptions from learn_from_run."""
        agent = _make_agent()
        registry = agent.skill_registry
        registry.learn_from_run = AsyncMock(side_effect=RuntimeError("LLM down"))

        # Should not raise
        await agent._maybe_learn_skill_run(
            goal="test",
            skill_name="test-skill",
            skill_context="context",
            step_results=[StepResult(
                step=ActionStep(action="click", params={}, verify="ok"),
                success=True, evidence="ok",
            )],
            had_replan=False,
        )

        registry.learn_from_run.assert_awaited_once()


class TestRecordContext:
    """Tests for _record_context method."""

    @staticmethod
    def _agent_with_context_monitor():
        agent = _make_agent()
        cm = MagicMock()
        cm.record_click = MagicMock()
        cm.record_type = MagicMock()
        cm.record_navigation = MagicMock()
        agent.context_monitor = cm
        return agent

    def test_record_click_context(self):
        """_record_context records click actions with element name."""
        agent = self._agent_with_context_monitor()
        step = ActionStep(action="click", params={"element": "Submit"}, verify="ok")
        agent._record_context(step)
        agent.context_monitor.record_click.assert_called_once_with("Submit")

    def test_record_type_context(self):
        """_record_context records type_text actions with text and field."""
        agent = self._agent_with_context_monitor()
        step = ActionStep(
            action="type_text",
            params={"text": "hello", "field_name": "search"},
            verify="ok",
        )
        agent._record_context(step)
        agent.context_monitor.record_type.assert_called_once_with("hello", "search")

    def test_record_open_url_context(self):
        """_record_context records open_url actions with URL."""
        agent = self._agent_with_context_monitor()
        step = ActionStep(
            action="open_url", params={"url": "https://example.com"}, verify="ok"
        )
        agent._record_context(step)
        agent.context_monitor.record_navigation.assert_called_once_with("https://example.com")

    def test_record_context_noop_for_other_actions(self):
        """_record_context does nothing for scroll, press_key, etc."""
        agent = self._agent_with_context_monitor()
        step = ActionStep(action="scroll", params={"direction": "down"}, verify="ok")
        agent._record_context(step)
        agent.context_monitor.record_click.assert_not_called()
        agent.context_monitor.record_type.assert_not_called()
        agent.context_monitor.record_navigation.assert_not_called()


class TestFallbackPlanIsolation:
    """Tests for _build_skill_fallback_plan with multi-skill context."""

    def test_fallback_plan_extracts_primary_skill_only(self):
        """_build_skill_fallback_plan with multi-skill context extracts
        steps only from primary/direct-match section."""
        agent = _make_agent()
        multi_ctx = _make_multi_skill_context()
        plan = agent._build_skill_fallback_plan("Return order", multi_ctx)

        if plan is not None:
            # Steps should only be from Amazon, not Walmart
            step_descriptions = [
                s.params.get("url", s.params.get("element", ""))
                for s in plan.steps
                if s.action != "done"
            ]
            combined = " ".join(str(d) for d in step_descriptions)
            assert "walmart" not in combined.lower()

    def test_fallback_plan_ignores_derived_procedure(self):
        """Derived procedure section headers and content are not parsed
        as steps."""
        agent = _make_agent()
        # Context with only derived procedure, no real steps
        derived_only = (
            "## Derived Procedure (run-local, current best hypothesis)\n"
            "Parent skill(s): return-amazon-order\n"
            "### Label Replacements\n"
            '- "Orders" -> "Purchase History"\n'
        )
        plan = agent._build_skill_fallback_plan("Test", derived_only)
        # Should return None since there are no numbered steps
        assert plan is None

    def test_fallback_plan_multi_skill_no_cross_contamination(self):
        """Steps from analogical/generic candidates do NOT appear in
        fallback plan."""
        agent = _make_agent()
        multi_ctx = _make_multi_skill_context()
        plan = agent._build_skill_fallback_plan("Return order", multi_ctx)

        if plan is not None:
            for step in plan.steps:
                if step.action == "done":
                    continue
                desc = str(step.params)
                assert "walmart" not in desc.lower(), (
                    f"Walmart step leaked into fallback plan: {step}"
                )

    def test_fallback_plan_single_skill_unchanged(self):
        """Single-skill context (backward compat) still works."""
        agent = _make_agent()
        single_ctx = (
            "1. Use open_url to navigate to https://www.amazon.com/orders\n"
            "   - verify: Amazon orders page visible\n"
            "2. Click the Return button\n"
            "   - verify: Return form visible\n"
        )
        plan = agent._build_skill_fallback_plan("Return order", single_ctx)
        assert plan is not None
        # Should have compiled steps
        non_done = [s for s in plan.steps if s.action != "done"]
        assert len(non_done) >= 1
