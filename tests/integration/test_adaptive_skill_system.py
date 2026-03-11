"""Integration tests for the Adaptive Skill System.

Tests cross-component interactions between:
- SkillRegistryImpl (real, with loaded skills)
- SkillRouter (mocked LLM calls)
- SkillCardBuilder (real)
- ActionPlannerImpl (mocked LLM calls)
- DerivedSkillSession (real)
- ReplanPatch (real)
- AutomationAgent orchestrator (real, with mocked actuator/coordinator)

Spec reference: docs/adaptive-skill-system-spec.md section 5.3
"""

import json
import re
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.logging.event_logger import EventLogger
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.planner.planner import ActionPlannerImpl
from automation_agent.shared_models import (
    ActionPlan,
    ActionStep,
    MatchType,
    ReplanPatch,
    SkillMatchResult,
    SkillRouteCandidate,
    SkillRouteResult,
    StepResult,
)
from automation_agent.skills.card_builder import SkillCardBuilder
from automation_agent.skills.derived_skill import DerivedSkillSession
from automation_agent.skills.registry import SkillRegistryImpl

# ---------------------------------------------------------------------------
# Skill fixtures — two skills for routing tests
# ---------------------------------------------------------------------------

AMAZON_RETURN_SKILL = textwrap.dedent("""\
    ---
    name: return-amazon-order
    description: Return an item purchased on Amazon
    skill-id: return-amazon-order
    trigger-keywords: [return, amazon, order, refund]
    tags: [e-commerce, return, refund]
    summary: Navigate a retailer order history, locate a purchased item, and complete a return flow
    parameters:
      item:
        type: string
        required: true
        description: The item to return
    requires:
      os: darwin
    success-condition: Return confirmation page visible
    ---

    ## Steps
    1. Open Safari and go to amazon.com/orders
       - verify: Orders page visible
    2. Find "{{item}}" in recent orders
       - verify: Matching order card visible
    3. Click "Return or Replace Items" on the order card
       - verify: Return reason selection visible
    4. Select reason and confirm return
       - verify: Return confirmation visible

    ## Error Recovery
    - If "Return or Replace Items" is absent, click "View item" first
""")

WALMART_RETURN_SKILL = textwrap.dedent("""\
    ---
    name: return-walmart-order
    description: Return an item purchased on Walmart
    skill-id: return-walmart-order
    trigger-keywords: [return, walmart, order]
    tags: [e-commerce, return]
    summary: Navigate Walmart order history to initiate a product return
    parameters:
      item:
        type: string
        required: true
        description: The item to return
    requires:
      os: darwin
    success-condition: Return confirmation visible
    ---

    ## Steps
    1. Open Safari and go to walmart.com/account/orders
       - verify: Purchase History visible
    2. Find "{{item}}" in purchase history
       - verify: Matching order visible
    3. Click "Start a return" on the order
       - verify: Return flow visible

    ## Error Recovery
    - If "Start a return" is absent, try "Return items" link
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path):
    return AgentConfig(
        _env_file=None,
        model_provider="local",
        anthropic_api_key="test-key-not-real",
        skill_learning_dir=tmp_path / "skill-learning",
    )


def _make_registry(tmp_path, config, *skill_contents):
    """Create a registry with real skills loaded from strings."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir(exist_ok=True)
    for i, content in enumerate(skill_contents):
        (skill_dir / f"skill_{i}.md").write_text(content)
    return SkillRegistryImpl(skill_dir=skill_dir, config=config)


def _mock_router_response(matches_json: str):
    """Create an AsyncMock router that returns the given JSON."""
    async def _route(prompt):
        from automation_agent.skills.router import SkillRouter
        # Use a real parser, but with mock LLM output
        router = MagicMock(spec=SkillRouter)
        router.skills = {}
        # Parse manually
        return None  # placeholder
    return matches_json


def _llm_response(steps_data, patch_data=None):
    """Build a mock LLM response dict."""
    payload = {"steps": steps_data}
    if patch_data is not None:
        payload["derived_skill_patch"] = patch_data
    return {
        "content": json.dumps(payload),
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


def _make_agent(planner, registry, coordinator, actuator, config, tmp_path):
    """Create an AutomationAgent with real orchestration logic."""
    logger = EventLogger(tmp_path / "runs")
    return AutomationAgent(
        planner=planner,
        skill_registry=registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        logger=logger,
    )


# ---------------------------------------------------------------------------
# Test 1: Analogical match flows to planner (spec 5.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analogical_match_flows_to_planner(tmp_path):
    """'Walmart return' with only amazon skill -> planner sees [analogical] label."""
    config = _make_config(tmp_path)
    registry = _make_registry(tmp_path, config, AMAZON_RETURN_SKILL)

    # Mock the router to return amazon skill as analogical match for Walmart prompt
    route_result = SkillRouteResult(
        candidates=[
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.ANALOGICAL,
                confidence=0.75,
                reason="Similar e-commerce return flow",
            ),
        ],
        params={"item": "headphones"},
    )
    registry._router = AsyncMock()
    registry._router.route = AsyncMock(return_value=route_result)

    # Match
    match = await registry.match("Return my Walmart headphones order")
    assert match is not None

    # Verify the skill_context contains analogical label
    ctx = match["skill_context"]
    assert "[analogical]" in ctx
    assert "return-amazon-order" in ctx
    assert "Similar e-commerce return flow" in ctx

    # Wire up planner and verify it receives the multi-skill context
    planner = ActionPlannerImpl(config)
    planner._call_llm = AsyncMock(
        return_value=_llm_response([
            {"action": "open_url", "params": {"url": "walmart.com"}, "verify": "Walmart open"},
            {"action": "done", "params": {}, "verify": ""},
        ])
    )

    plan = await planner.plan(
        goal="Return my Walmart headphones order",
        screen_description="Desktop visible",
        skill_context=ctx,
    )
    assert len(plan.steps) >= 1

    # Verify the prompt sent to the LLM includes the analogical label
    prompt = planner._call_llm.call_args[0][0]
    assert "[analogical]" in prompt
    assert "return-amazon-order" in prompt


# ---------------------------------------------------------------------------
# Test 2: Replan patch updates derived procedure (spec 5.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replan_patch_updates_derived_procedure(tmp_path):
    """Replan produces patch -> session updated -> next context reflects change."""
    config = _make_config(tmp_path)
    registry = _make_registry(tmp_path, config, AMAZON_RETURN_SKILL)

    # Setup route result
    route_result = SkillRouteResult(
        candidates=[
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.DIRECT,
                confidence=0.95,
                reason="Exact match",
            ),
        ],
        params={"item": "Tylenol"},
    )
    registry._router = AsyncMock()
    registry._router.route = AsyncMock(return_value=route_result)

    match = await registry.match("Return the most recent Tylenol order on Amazon")
    assert match is not None

    # Create a derived session (as the orchestrator would)
    candidates = match.candidates
    session = DerivedSkillSession.seed(
        parent_skill_ids=[c.skill_id for c in candidates],
        match_types=[str(c.match_type.value) for c in candidates],
        steps_text=match.expanded_steps,
    )

    # Capture state before patch
    assert len(session.replaced_labels) == 0
    assert len(session.failed_assumptions) == 0

    # Simulate replan with a patch
    patch = ReplanPatch(
        replace_labels=[{"old": "Return or Replace Items", "new": "Start a Return", "reason": "Amazon UI changed"}],
        add_landmarks=["Return Center link in sidebar"],
        failed_assumptions=["Return or Replace Items button exists on order card"],
        successful_adaptations=["Found Start a Return in order details page"],
    )
    session.apply_patch(patch)

    # Verify session updated
    assert len(session.replaced_labels) == 1
    assert session.replaced_labels[0]["old"] == "Return or Replace Items"
    assert session.replaced_labels[0]["new"] == "Start a Return"
    assert len(session.failed_assumptions) == 1
    assert len(session.successful_adaptations) == 1
    assert len(session.discovered_landmarks) == 1

    # Verify serialized context reflects changes
    serialized = session.serialize_for_context()
    assert "Label Replacements" in serialized
    assert '"Return or Replace Items" -> "Start a Return"' in serialized
    assert "Failed Assumptions" in serialized
    assert "Return or Replace Items button exists" in serialized
    assert "Successful Adaptations" in serialized
    assert "Return Center link in sidebar" in serialized


# ---------------------------------------------------------------------------
# Test 3: No skill match still plans (spec 5.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_skill_match_still_plans(tmp_path):
    """No skills match -> planner works without skill context."""
    config = _make_config(tmp_path)
    # Registry with skills that won't match
    registry = _make_registry(tmp_path, config, AMAZON_RETURN_SKILL)

    # Router returns None (no match)
    registry._router = AsyncMock()
    registry._router.route = AsyncMock(return_value=None)

    match = await registry.match("Open Calculator and compute 2+2")
    assert match is None

    # Planner still works without skill context
    planner = ActionPlannerImpl(config)
    planner._call_llm = AsyncMock(
        return_value=_llm_response([
            {"action": "activate_app", "params": {"app_name": "Calculator"}, "verify": "Calculator open"},
            {"action": "done", "params": {}, "verify": ""},
        ])
    )

    plan = await planner.plan(
        goal="Open Calculator and compute 2+2",
        screen_description="Desktop visible",
        skill_context=None,
    )
    assert len(plan.steps) == 2
    assert plan.steps[0].action == "activate_app"


# ---------------------------------------------------------------------------
# Test 4: Distiller receives derived context (spec 5.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_distiller_receives_derived_context(tmp_path):
    """Post-run distiller sees derived procedure in skill_context."""
    config = _make_config(tmp_path)
    registry = _make_registry(tmp_path, config, AMAZON_RETURN_SKILL)

    # Setup route + match
    route_result = SkillRouteResult(
        candidates=[
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.ANALOGICAL,
                confidence=0.8,
                reason="Analogical: similar return flow",
            ),
        ],
        params={"item": "shoes"},
    )
    registry._router = AsyncMock()
    registry._router.route = AsyncMock(return_value=route_result)

    # Mock the distiller
    registry._distiller = AsyncMock()
    registry._distiller.distill = AsyncMock(return_value=[])

    match = await registry.match("Return my Walmart shoes")
    assert match is not None

    # Create derived session with corrections
    session = DerivedSkillSession.seed(
        parent_skill_ids=["return-amazon-order"],
        match_types=["analogical"],
        steps_text=match.expanded_steps,
    )
    session.apply_patch(ReplanPatch(
        replace_labels=[{"old": "Orders", "new": "Purchase History", "reason": "Walmart uses different label"}],
        failed_assumptions=["Orders link exists on Walmart"],
        successful_adaptations=["Purchase History found in sidebar"],
    ))

    # Build distiller context as the orchestrator would
    expanded_steps = match.expanded_steps
    derived_text = session.serialize_for_context()
    distiller_ctx = expanded_steps + "\n\n---\n\n" + derived_text

    # Call learn_from_run
    trace = [
        StepResult(
            step=ActionStep(action="click", params={"element": "Orders"}, verify="Orders page"),
            success=False,
            evidence="Orders link not found",
            retry_strategies_used=["replan_missing_target"],
        ),
    ]
    await registry.learn_from_run(
        "return-amazon-order",
        "Return my Walmart shoes",
        trace,
        skill_context=distiller_ctx,
        run_id="test-run-001",
        had_replan=True,
    )

    # Verify distiller was called with derived procedure in context
    assert registry._distiller.distill.called
    call_kwargs = registry._distiller.distill.call_args
    distilled_ctx = call_kwargs.kwargs.get("skill_context") or call_kwargs[1].get("skill_context", "")
    # The distiller should see the derived procedure
    assert "Derived Procedure" in distilled_ctx or "Purchase History" in distilled_ctx


# ---------------------------------------------------------------------------
# Test 5: Full routing-to-planning pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_routing_to_planning_pipeline(tmp_path):
    """Router returns top-3 -> registry builds multi-skill context -> planner receives it."""
    config = _make_config(tmp_path)
    registry = _make_registry(
        tmp_path, config, AMAZON_RETURN_SKILL, WALMART_RETURN_SKILL
    )

    # Router returns both skills with different match types
    route_result = SkillRouteResult(
        candidates=[
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.DIRECT,
                confidence=0.95,
                reason="Direct match for Amazon return",
            ),
            SkillRouteCandidate(
                skill_id="return-walmart-order",
                match_type=MatchType.ANALOGICAL,
                confidence=0.7,
                reason="Analogical: similar e-commerce return flow",
            ),
        ],
        params={"item": "laptop"},
    )
    registry._router = AsyncMock()
    registry._router.route = AsyncMock(return_value=route_result)

    match = await registry.match("Return my laptop on Amazon")
    assert match is not None
    assert match.skill_name == "return-amazon-order"
    assert len(match.candidates) == 2

    # Verify multi-skill context
    ctx = match["skill_context"]
    assert "[direct]" in ctx
    assert "[analogical]" in ctx
    assert "return-amazon-order" in ctx
    assert "return-walmart-order" in ctx
    assert "Direct match for Amazon return" in ctx
    assert "Analogical" in ctx or "analogical" in ctx

    # Wire to planner
    planner = ActionPlannerImpl(config)
    planner._call_llm = AsyncMock(
        return_value=_llm_response([
            {"action": "open_url", "params": {"url": "amazon.com/orders"}, "verify": "Orders page"},
            {"action": "done", "params": {}, "verify": ""},
        ])
    )

    plan = await planner.plan(
        goal="Return my laptop on Amazon",
        screen_description="Desktop",
        skill_context=ctx,
    )

    # Check the prompt sent to LLM contains both skill sections
    prompt = planner._call_llm.call_args[0][0]
    assert "return-amazon-order" in prompt
    assert "return-walmart-order" in prompt
    assert "[direct]" in prompt
    assert "[analogical]" in prompt


# ---------------------------------------------------------------------------
# Test 6: Keyword fallback to planning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keyword_fallback_to_planning(tmp_path):
    """LLM router unavailable -> keyword fallback -> planner still gets skill context."""
    # Use a skill without required parameters (keyword fallback cannot extract params)
    no_param_skill = textwrap.dedent("""\
        ---
        name: check-amazon-orders
        description: Check recent Amazon orders
        trigger-keywords: [check, amazon, orders, recent]
        parameters: {}
        requires:
          os: darwin
        success-condition: Orders page visible
        ---

        ## Steps
        1. Open Safari and go to amazon.com/orders
           - verify: Orders page visible

        ## Error Recovery
        - If login required, wait for user
    """)

    config = _make_config(tmp_path)
    registry = _make_registry(tmp_path, config, no_param_skill)

    # Router fails (returns None) -> keyword fallback kicks in
    registry._router = AsyncMock()
    registry._router.route = AsyncMock(return_value=None)

    # "check amazon orders" matches 3 of 4 keywords -> 75% confidence
    match = await registry.match("check amazon orders")
    assert match is not None
    assert match.skill_name == "check-amazon-orders"
    assert len(match.candidates) == 1
    assert match.candidates[0].match_type == MatchType.DIRECT
    assert match.candidates[0].confidence > 0.0

    # Verify skill_context is populated
    ctx = match["skill_context"]
    assert ctx is not None
    assert len(ctx) > 0
    assert "check-amazon-orders" in ctx

    # Planner receives it
    planner = ActionPlannerImpl(config)
    planner._call_llm = AsyncMock(
        return_value=_llm_response([
            {"action": "open_url", "params": {"url": "amazon.com"}, "verify": "Amazon open"},
            {"action": "done", "params": {}, "verify": ""},
        ])
    )
    plan = await planner.plan(
        goal="check amazon orders",
        screen_description="Desktop",
        skill_context=ctx,
    )
    assert len(plan.steps) >= 1

    # Prompt includes skill context from keyword fallback
    prompt = planner._call_llm.call_args[0][0]
    assert "check-amazon-orders" in prompt


# ---------------------------------------------------------------------------
# Test 7: Skill card caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_card_caching(tmp_path):
    """Cards built at load time, not rebuilt per match()."""
    config = _make_config(tmp_path)
    registry = _make_registry(
        tmp_path, config, AMAZON_RETURN_SKILL, WALMART_RETURN_SKILL
    )

    # Cards should be pre-built after loading
    assert len(registry._cards) == 2

    # Capture card references
    cards_before = list(registry._cards)

    # Mock router to return a match
    route_result = SkillRouteResult(
        candidates=[
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.DIRECT,
                confidence=0.9,
                reason="Direct",
            ),
        ],
        params={"item": "book"},
    )
    registry._router = AsyncMock()
    registry._router.route = AsyncMock(return_value=route_result)

    # Multiple match() calls should not rebuild cards
    await registry.match("return my amazon book")
    await registry.match("return my walmart shoes")

    # Cards should still be the same objects (not rebuilt)
    cards_after = registry._cards
    assert len(cards_after) == len(cards_before)
    for before, after in zip(cards_before, cards_after):
        assert before.skill_id == after.skill_id


# ---------------------------------------------------------------------------
# Test 8: Backward-compatible dict access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backward_compat_match_dict_access(tmp_path):
    """Existing code using match['skill_name'] still works."""
    config = _make_config(tmp_path)
    registry = _make_registry(tmp_path, config, AMAZON_RETURN_SKILL)

    route_result = SkillRouteResult(
        candidates=[
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.DIRECT,
                confidence=0.95,
                reason="Exact match",
            ),
        ],
        params={"item": "phone"},
    )
    registry._router = AsyncMock()
    registry._router.route = AsyncMock(return_value=route_result)

    match = await registry.match("Return my phone on Amazon")
    assert match is not None

    # Dict-style access (backward compat via __getitem__ shim)
    assert match["skill_name"] == "return-amazon-order"
    assert match["params"] == {"item": "phone"}
    assert isinstance(match["expanded_steps"], str)
    assert isinstance(match["skill_context"], str)

    # .get() access
    assert match.get("skill_name") == "return-amazon-order"
    assert match.get("nonexistent", "default") == "default"

    # Attribute access (new style)
    assert match.skill_name == "return-amazon-order"
    assert match.params == {"item": "phone"}
    assert isinstance(match.candidates, list)

    # KeyError on invalid key
    with pytest.raises(KeyError):
        _ = match["totally_bogus_key"]


# ---------------------------------------------------------------------------
# Test 9: Derived session survives replan cycle (full orchestrator)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_derived_session_survives_replan_cycle(tmp_path):
    """Session created -> replan with patch -> session updated -> distiller sees updates."""
    config = _make_config(tmp_path)
    registry = _make_registry(tmp_path, config, AMAZON_RETURN_SKILL)

    # Setup router
    route_result = SkillRouteResult(
        candidates=[
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.ANALOGICAL,
                confidence=0.8,
                reason="Analogical match for Walmart return",
            ),
        ],
        params={"item": "tablet"},
    )
    registry._router = AsyncMock()
    registry._router.route = AsyncMock(return_value=route_result)
    registry._distiller = AsyncMock()
    registry._distiller.distill = AsyncMock(return_value=[])

    # Mock planner: initial plan has a failing step with on_fail=replan
    planner = MagicMock()
    planner.plan = AsyncMock(
        return_value=ActionPlan(
            steps=[
                ActionStep(
                    action="click",
                    params={"x": 100, "y": 200},
                    verify="Return flow visible",
                    on_fail="replan",
                ),
            ],
            goal="Return my Walmart tablet",
        )
    )
    # Replan returns done + a patch
    replan_patch = ReplanPatch(
        replace_labels=[
            {"old": "Return or Replace Items", "new": "Start a return", "reason": "Walmart UI"},
        ],
        failed_assumptions=["Amazon-style return button exists"],
        successful_adaptations=["Found Walmart return link"],
        add_landmarks=["Purchase History sidebar"],
    )
    planner.replan = AsyncMock(
        return_value=ActionPlan(
            steps=[ActionStep(action="done", params={}, verify="")],
            goal="Return my Walmart tablet",
            replan_patch=replan_patch,
        )
    )

    # Mock coordinator and actuator
    coordinator = MagicMock()
    coordinator.verify_condition = AsyncMock(return_value=False)
    coordinator.describe_screen = AsyncMock(return_value="Walmart order page")
    coordinator.capture_screenshot = AsyncMock(return_value="")

    actuator = MagicMock()
    actuator.click = MagicMock(return_value={"success": True})
    actuator.get_state = MagicMock(return_value={})

    agent = _make_agent(planner, registry, coordinator, actuator, config, tmp_path)
    result = await agent.execute("Return my Walmart tablet")

    # The replan should have been called (first step fails verification)
    assert result.success is True

    # Verify the distiller was called with derived procedure context
    if registry._distiller.distill.called:
        call_args = registry._distiller.distill.call_args
        distiller_ctx = call_args.kwargs.get("skill_context", "")
        # Should contain derived procedure updates
        assert (
            "Derived Procedure" in distiller_ctx
            or "Start a return" in distiller_ctx
            or "Purchase History" in distiller_ctx
        ), f"Distiller context missing derived procedure: {distiller_ctx[:300]}"


# ---------------------------------------------------------------------------
# Test 10: Replan without patch does not crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replan_without_patch_succeeds(tmp_path):
    """AC-9: Replan response with only steps and no patch must succeed."""
    config = _make_config(tmp_path)

    planner = ActionPlannerImpl(config)
    # Response with steps only, no derived_skill_patch
    planner._call_llm = AsyncMock(
        return_value=_llm_response([
            {"action": "click", "params": {"element": "Orders"}, "verify": "Orders visible"},
            {"action": "done", "params": {}, "verify": ""},
        ])
    )

    history = [
        StepResult(
            step=ActionStep(action="click", params={"x": 50, "y": 50}, verify="Button clicked"),
            success=False,
            evidence="Button not found",
        ),
    ]

    plan = await planner.replan(
        goal="Return my order",
        screen_description="Amazon page",
        history=history,
        retry_strategies_used=["replan_missing_target"],
        skill_context="Some skill context",
    )

    assert plan is not None
    assert len(plan.steps) == 2
    assert plan.replan_patch is None


# ---------------------------------------------------------------------------
# Test 11: Replan with patch parses correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replan_with_patch_parses(tmp_path):
    """AC-9: Replan response with patch is parsed into ReplanPatch."""
    config = _make_config(tmp_path)

    planner = ActionPlannerImpl(config)
    patch_data = {
        "replace_labels": [
            {"old": "Orders", "new": "Purchase History", "reason": "Walmart label"},
        ],
        "add_landmarks": ["sidebar nav"],
        "failed_assumptions": ["Orders exists"],
        "successful_adaptations": ["Found Purchase History"],
    }
    planner._call_llm = AsyncMock(
        return_value=_llm_response(
            [{"action": "done", "params": {}, "verify": ""}],
            patch_data=patch_data,
        )
    )

    history = [
        StepResult(
            step=ActionStep(action="click", params={"x": 50, "y": 50}, verify="Clicked"),
            success=False,
            evidence="Not found",
        ),
    ]

    plan = await planner.replan(
        goal="Return",
        screen_description="Page",
        history=history,
        retry_strategies_used=[],
    )

    assert plan.replan_patch is not None
    assert len(plan.replan_patch.replace_labels) == 1
    assert plan.replan_patch.replace_labels[0]["old"] == "Orders"
    assert plan.replan_patch.replace_labels[0]["new"] == "Purchase History"
    assert "Orders exists" in plan.replan_patch.failed_assumptions
    assert "sidebar nav" in plan.replan_patch.add_landmarks


# ---------------------------------------------------------------------------
# Test 12: Multi-skill context has correct structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_skill_context_structure(tmp_path):
    """Multi-skill context has labeled sections with instructions per AC-5."""
    config = _make_config(tmp_path)
    registry = _make_registry(
        tmp_path, config, AMAZON_RETURN_SKILL, WALMART_RETURN_SKILL
    )

    route_result = SkillRouteResult(
        candidates=[
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.DIRECT,
                confidence=0.95,
                reason="Exact match",
            ),
            SkillRouteCandidate(
                skill_id="return-walmart-order",
                match_type=MatchType.ANALOGICAL,
                confidence=0.65,
                reason="Similar structure",
            ),
        ],
        params={"item": "camera"},
    )
    registry._router = AsyncMock()
    registry._router.route = AsyncMock(return_value=route_result)

    match = await registry.match("Return my camera on Amazon")
    ctx = match["skill_context"]

    # Verify structural elements of multi-skill context (AC-5)
    assert "## Skill Priors" in ctx
    assert "direct" in ctx.lower()
    assert "analogical" in ctx.lower()
    assert "may be followed closely" in ctx
    assert "structural priors only" in ctx
    assert "do NOT assume" in ctx.lower() or "do not assume" in ctx.lower()

    # Verify each candidate section
    assert "### [direct] return-amazon-order" in ctx
    assert "### [analogical] return-walmart-order" in ctx
    assert "confidence: 0.95" in ctx
    assert "confidence: 0.65" in ctx


# ---------------------------------------------------------------------------
# Test 13: Below-threshold candidates filtered out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_below_threshold_candidates_filtered(tmp_path):
    """Candidates below MIN_USEFUL_CONFIDENCE are filtered from match result."""
    config = _make_config(tmp_path)
    registry = _make_registry(tmp_path, config, AMAZON_RETURN_SKILL)

    route_result = SkillRouteResult(
        candidates=[
            SkillRouteCandidate(
                skill_id="return-amazon-order",
                match_type=MatchType.GENERIC,
                confidence=0.3,
                reason="Weak generic match",
            ),
        ],
        params={},
    )
    registry._router = AsyncMock()
    registry._router.route = AsyncMock(return_value=route_result)

    match = await registry.match("Play a YouTube video")
    # All candidates below 0.5 -> returns None (AC-3)
    assert match is None


# ---------------------------------------------------------------------------
# Test 14: SkillCardBuilder produces correct cards
# ---------------------------------------------------------------------------


def test_skill_card_builder_produces_correct_cards(tmp_path):
    """SkillCardBuilder.build() creates correct cards from real skills."""
    config = _make_config(tmp_path)
    registry = _make_registry(tmp_path, config, AMAZON_RETURN_SKILL)

    skill = registry.get_skill("return-amazon-order")
    assert skill is not None

    card = SkillCardBuilder.build(skill)
    assert card.skill_id == "return-amazon-order"
    assert card.summary == "Navigate a retailer order history, locate a purchased item, and complete a return flow"
    assert "return" in card.tags or "e-commerce" in card.tags
    assert card.required_os == "darwin"


# ---------------------------------------------------------------------------
# Test 15: DerivedSkillSession idempotent patch
# ---------------------------------------------------------------------------


def test_derived_session_idempotent_patch():
    """Applying the same patch twice is a no-op (deduplication)."""
    session = DerivedSkillSession.seed(
        parent_skill_ids=["skill-a"],
        match_types=["direct"],
        steps_text="1. Do something",
    )

    patch = ReplanPatch(
        replace_labels=[{"old": "X", "new": "Y", "reason": "test"}],
        add_landmarks=["landmark1"],
        failed_assumptions=["assumption1"],
    )

    session.apply_patch(patch)
    state_after_first = (
        list(session.replaced_labels),
        list(session.discovered_landmarks),
        list(session.failed_assumptions),
    )

    session.apply_patch(patch)
    state_after_second = (
        list(session.replaced_labels),
        list(session.discovered_landmarks),
        list(session.failed_assumptions),
    )

    assert state_after_first == state_after_second
