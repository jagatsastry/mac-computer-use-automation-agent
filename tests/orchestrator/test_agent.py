"""Tests for automation agent component."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.orchestrator.models import (
    Intent,
    ActionStep,
    Observation,
    ActionResult,
    ExecutionResult,
    NextAction,
)
from automation_agent.orchestrator.intent_parser import IntentParser
from automation_agent.orchestrator.action_registry import ActionRegistry
from automation_agent.orchestrator.observer import ScreenObserver


@pytest.fixture
def mock_parser(mock_ollama_client):
    """Create a mock IntentParser."""
    parser = MagicMock(spec=IntentParser)
    parser.parse = AsyncMock()
    return parser


@pytest.fixture
def mock_observer(mock_ollama_client, mock_screen_capturer):
    """Create a mock ScreenObserver."""
    observer = MagicMock(spec=ScreenObserver)
    observer.observe = AsyncMock()
    observer.find_element = AsyncMock()
    observer.check_condition = AsyncMock()
    return observer


@pytest.fixture
def mock_registry():
    """Create a mock ActionRegistry."""
    registry = MagicMock(spec=ActionRegistry)
    return registry


@pytest.fixture
def mock_action():
    """Create a mock action with execute method."""
    action = MagicMock()
    action.execute = AsyncMock()
    return action


class TestAutomationAgentInit:
    """Test AutomationAgent initialization."""

    def test_init_with_defaults(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test agent initialization with default settings."""
        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
        )

        assert agent.parser == mock_parser
        assert agent.observer == mock_observer
        assert agent.registry == mock_registry
        assert agent.text_model == "gemma2:9b"
        assert agent.max_iterations == 35
        assert agent.action_delay == 1.0

    def test_init_with_custom_settings(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test agent initialization with custom settings."""
        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            text_model="llama3:8b",
            max_iterations=10,
            action_delay=0.5,
        )

        assert agent.text_model == "llama3:8b"
        assert agent.max_iterations == 10
        assert agent.action_delay == 0.5


class TestAutomationAgentExecute:
    """Test AutomationAgent.execute() method."""

    @pytest.mark.asyncio
    async def test_execute_calls_parser(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test that execute calls the parser."""
        intent = Intent(
            steps=[ActionStep(action="activate_app", params={"app_name": "Safari"})],
            raw_prompt="Open Safari",
            requires_observation=False
        )
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_action = MagicMock()
        mock_action.execute = AsyncMock(return_value=MagicMock(success=True, output=""))
        mock_registry.get_action = MagicMock(return_value=mock_action)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        await agent.execute("Open Safari")

        mock_parser.parse.assert_called_once_with("Open Safari")

    @pytest.mark.asyncio
    async def test_execute_simple_task_sequential(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test simple task uses sequential execution."""
        intent = Intent(
            steps=[ActionStep(action="activate_app", params={"app_name": "Safari"})],
            raw_prompt="Open Safari",
            requires_observation=False
        )
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_action = MagicMock()
        mock_action.execute = AsyncMock(return_value=MagicMock(success=True, output="Safari opened"))
        mock_registry.get_action = MagicMock(return_value=mock_action)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        result = await agent.execute("Open Safari")

        assert result.success is True
        # Observer should NOT be called for simple tasks
        mock_observer.observe.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_complex_task_agentic(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test complex task uses agentic execution."""
        intent = Intent(
            steps=[ActionStep(action="click_element", params={"description": "button"})],
            raw_prompt="Click the button",
            requires_observation=True
        )
        mock_parser.parse = AsyncMock(return_value=intent)

        # Mock observer
        mock_observer.observe = AsyncMock(return_value=Observation(
            screenshot_b64="data",
            description="Button visible on screen"
        ))

        # Mock LLM to return completion
        mock_ollama_client.generate = AsyncMock(
            return_value=json.dumps({"complete": True, "reasoning": "Button clicked"})
        )

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        result = await agent.execute("Click the button")

        # Observer should be called for complex tasks
        mock_observer.observe.assert_called()


class TestAutomationAgentSequentialExecution:
    """Test sequential execution mode."""

    @pytest.mark.asyncio
    async def test_sequential_single_step_success(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test sequential execution with single successful step."""
        intent = Intent(
            steps=[ActionStep(action="activate_app", params={"app_name": "Safari"})],
            raw_prompt="Open Safari",
            requires_observation=False
        )
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_action = MagicMock()
        mock_action.execute = AsyncMock(return_value=MagicMock(success=True, output="Done"))
        mock_registry.get_action = MagicMock(return_value=mock_action)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        result = await agent.execute("Open Safari")

        assert result.success is True
        assert len(result.steps) == 1
        assert result.steps[0].success is True

    @pytest.mark.asyncio
    async def test_sequential_multiple_steps_success(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test sequential execution with multiple successful steps."""
        intent = Intent(
            steps=[
                ActionStep(action="activate_app", params={"app_name": "Safari"}),
                ActionStep(action="open_url", params={"url": "https://google.com"}),
            ],
            raw_prompt="Open Safari and go to Google",
            requires_observation=False
        )
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_action = MagicMock()
        mock_action.execute = AsyncMock(return_value=MagicMock(success=True, output="Done"))
        mock_registry.get_action = MagicMock(return_value=mock_action)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        result = await agent.execute("Open Safari and go to Google")

        assert result.success is True
        assert len(result.steps) == 2

    @pytest.mark.asyncio
    async def test_sequential_step_failure_stops_execution(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test that failure in a step stops further execution."""
        intent = Intent(
            steps=[
                ActionStep(action="activate_app", params={"app_name": "Safari"}),
                ActionStep(action="open_url", params={"url": "https://google.com"}),
                ActionStep(action="type_text", params={"text": "hello"}),
            ],
            raw_prompt="Multi-step command",
            requires_observation=False
        )
        mock_parser.parse = AsyncMock(return_value=intent)

        # First action succeeds, second fails
        mock_action1 = MagicMock()
        mock_action1.execute = AsyncMock(return_value=MagicMock(success=True, output="OK"))
        mock_action2 = MagicMock()
        mock_action2.execute = AsyncMock(return_value=MagicMock(success=False, error="Failed"))

        mock_registry.get_action = MagicMock(side_effect=[mock_action1, mock_action2])

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        result = await agent.execute("Multi-step command")

        assert result.success is False
        assert len(result.steps) == 2  # Third step not executed
        assert result.steps[0].success is True
        assert result.steps[1].success is False

    @pytest.mark.asyncio
    async def test_sequential_unknown_action_type(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test handling of unknown action type."""
        intent = Intent(
            steps=[ActionStep(action="fly_to_moon", params={})],
            raw_prompt="Do something impossible",
            requires_observation=False
        )
        mock_parser.parse = AsyncMock(return_value=intent)
        mock_registry.get_action = MagicMock(return_value=None)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        result = await agent.execute("Do something impossible")

        assert result.success is False
        assert "Unknown action type" in result.steps[0].error

    @pytest.mark.asyncio
    async def test_sequential_action_exception(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test handling of action execution exception."""
        intent = Intent(
            steps=[ActionStep(action="activate_app", params={"app_name": "Safari"})],
            raw_prompt="Open Safari",
            requires_observation=False
        )
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_action = MagicMock()
        mock_action.execute = AsyncMock(side_effect=Exception("Unexpected error"))
        mock_registry.get_action = MagicMock(return_value=mock_action)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        result = await agent.execute("Open Safari")

        assert result.success is False
        assert "Unexpected error" in result.steps[0].error


class TestAutomationAgentAgenticExecution:
    """Test agentic execution mode."""

    @pytest.mark.asyncio
    async def test_agentic_completes_on_goal_achieved(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test agentic loop completes when goal is achieved."""
        intent = Intent(
            steps=[],
            raw_prompt="Complex task",
            requires_observation=True
        )
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_observer.observe = AsyncMock(return_value=Observation(
            screenshot_b64="data",
            description="Goal achieved - task complete"
        ))

        # LLM says task is complete
        mock_ollama_client.generate = AsyncMock(
            return_value=json.dumps({"complete": True, "reasoning": "Goal achieved"})
        )

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        result = await agent.execute("Complex task")

        assert result.success is True
        assert "Goal achieved" in result.message

    @pytest.mark.asyncio
    async def test_agentic_max_iterations_reached(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test agentic loop stops at max iterations."""
        intent = Intent(
            steps=[],
            raw_prompt="Impossible task",
            requires_observation=True
        )
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_observer.observe = AsyncMock(return_value=Observation(
            screenshot_b64="data",
            description="Still working"
        ))

        # LLM never says complete, always wants to do more
        mock_ollama_client.generate = AsyncMock(
            return_value=json.dumps({
                "action": "type_text",
                "params": {"text": "test"},
                "reasoning": "Keep trying"
            })
        )

        mock_action = MagicMock()
        mock_action.execute = AsyncMock(return_value=MagicMock(success=True, output="OK"))
        mock_registry.get_action = MagicMock(return_value=mock_action)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            max_iterations=3,
            action_delay=0,
        )

        result = await agent.execute("Impossible task")

        assert result.success is False
        assert "Max iterations" in result.message
        assert result.iterations == 3

    @pytest.mark.asyncio
    async def test_agentic_executes_initial_steps(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test that initial non-observation steps are executed first."""
        intent = Intent(
            steps=[
                ActionStep(action="open_url", params={"url": "https://youtube.com"}),
                ActionStep(action="click_element", params={"description": "video"}),
            ],
            raw_prompt="Open YouTube and click video",
            requires_observation=True
        )
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_observer.observe = AsyncMock(return_value=Observation(
            screenshot_b64="data",
            description="YouTube loaded"
        ))

        mock_ollama_client.generate = AsyncMock(
            return_value=json.dumps({"complete": True, "reasoning": "Done"})
        )

        mock_action = MagicMock()
        mock_action.execute = AsyncMock(return_value=MagicMock(success=True, output="OK"))
        mock_registry.get_action = MagicMock(return_value=mock_action)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        result = await agent.execute("Open YouTube and click video")

        # open_url should have been called (initial step)
        mock_registry.get_action.assert_called()


class TestAutomationAgentActionExecution:
    """Test individual action execution."""

    @pytest.mark.asyncio
    async def test_execute_click_element_finds_coordinates(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test click_element action finds coordinates first."""
        from automation_agent.orchestrator.models import Coordinates

        intent = Intent(
            steps=[],
            raw_prompt="Click button",
            requires_observation=True
        )
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_observer.observe = AsyncMock(return_value=Observation(
            screenshot_b64="data",
            description="Button visible"
        ))
        mock_observer.find_element = AsyncMock(return_value=Coordinates(x=100, y=200, width=50, height=30))

        # First call returns click action, then complete
        call_count = [0]
        def generate_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return json.dumps({
                    "action": "click_element",
                    "params": {"description": "the button"},
                    "reasoning": "Need to click"
                })
            return json.dumps({"complete": True, "reasoning": "Clicked"})

        mock_ollama_client.generate = AsyncMock(side_effect=generate_side_effect)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        with patch('automation_agent.orchestrator.agent.ClickAction') as MockClickAction:
            mock_click = MagicMock()
            mock_click.execute = AsyncMock(return_value=MagicMock(success=True))
            MockClickAction.return_value = mock_click

            result = await agent.execute("Click button")

            # Should have called find_element
            mock_observer.find_element.assert_called()

    @pytest.mark.asyncio
    async def test_execute_click_element_not_found(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test click_element when element is not found."""
        intent = Intent(
            steps=[],
            raw_prompt="Click nonexistent",
            requires_observation=True
        )
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_observer.observe = AsyncMock(return_value=Observation(
            screenshot_b64="data",
            description="Screen visible"
        ))
        mock_observer.find_element = AsyncMock(return_value=None)  # Element not found

        # LLM wants to click, then gives up
        call_count = [0]
        def generate_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                return json.dumps({
                    "action": "click_element",
                    "params": {"description": "nonexistent"},
                    "reasoning": "Try to click"
                })
            return json.dumps({"complete": True, "reasoning": "Given up"})

        mock_ollama_client.generate = AsyncMock(side_effect=generate_side_effect)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            max_iterations=3,
            action_delay=0,
        )

        result = await agent.execute("Click nonexistent")

        # Should have failed action results
        failed_actions = [s for s in result.steps if not s.success]
        assert len(failed_actions) >= 1


class TestAutomationAgentHistoryFormatting:
    """Test history formatting for LLM prompts."""

    @pytest.mark.asyncio
    async def test_history_includes_observations(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test that history includes observations."""
        intent = Intent(steps=[], raw_prompt="Task", requires_observation=True)
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_observer.observe = AsyncMock(return_value=Observation(
            screenshot_b64="data",
            description="Test observation"
        ))

        mock_ollama_client.generate = AsyncMock(
            return_value=json.dumps({"complete": True, "reasoning": "Done"})
        )

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        await agent.execute("Task")

        # Check that LLM was called with history
        call_kwargs = mock_ollama_client.generate.call_args.kwargs
        assert "History" in call_kwargs["prompt"] or "history" in call_kwargs["prompt"].lower()

    @pytest.mark.asyncio
    async def test_history_limits_entries(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test that history is limited to prevent context overflow."""
        intent = Intent(steps=[], raw_prompt="Long task", requires_observation=True)
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_observer.observe = AsyncMock(return_value=Observation(
            screenshot_b64="data",
            description="Observation"
        ))

        # Keep returning actions to generate many history entries
        iteration = [0]
        def generate_side_effect(*args, **kwargs):
            iteration[0] += 1
            if iteration[0] > 15:
                return json.dumps({"complete": True, "reasoning": "Done"})
            return json.dumps({
                "action": "type_text",
                "params": {"text": f"text{iteration[0]}"},
                "reasoning": "Keep going"
            })

        mock_ollama_client.generate = AsyncMock(side_effect=generate_side_effect)

        mock_action = MagicMock()
        mock_action.execute = AsyncMock(return_value=MagicMock(success=True, output="OK"))
        mock_registry.get_action = MagicMock(return_value=mock_action)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            max_iterations=20,
            action_delay=0,
        )

        result = await agent.execute("Long task")

        # Agent uses last 10 entries in _format_history
        # This test just ensures it doesn't crash with many entries
        assert result is not None


class TestAutomationAgentEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_empty_intent_steps(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test handling of empty intent steps for simple task."""
        intent = Intent(steps=[], raw_prompt="Empty", requires_observation=False)
        mock_parser.parse = AsyncMock(return_value=intent)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        result = await agent.execute("Empty")

        assert result.success is True
        assert len(result.steps) == 0

    @pytest.mark.asyncio
    async def test_llm_returns_invalid_json(self, mock_parser, mock_observer, mock_registry, mock_ollama_client):
        """Test handling of invalid JSON from LLM in agentic mode."""
        intent = Intent(steps=[], raw_prompt="Task", requires_observation=True)
        mock_parser.parse = AsyncMock(return_value=intent)

        mock_observer.observe = AsyncMock(return_value=Observation(
            screenshot_b64="data",
            description="Screen"
        ))

        # Return invalid JSON first, then valid completion
        call_count = [0]
        def generate_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return "This is not valid JSON at all"
            return json.dumps({"complete": True, "reasoning": "Done"})

        mock_ollama_client.generate = AsyncMock(side_effect=generate_side_effect)

        agent = AutomationAgent(
            parser=mock_parser,
            observer=mock_observer,
            registry=mock_registry,
            llm_client=mock_ollama_client,
            action_delay=0,
        )

        # Should not crash, should handle gracefully
        result = await agent.execute("Task")
        assert result is not None
