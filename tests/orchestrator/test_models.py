"""Tests for orchestrator data models."""

import pytest
from datetime import datetime
from automation_agent.orchestrator.models import (
    Coordinates,
    ActionStep,
    Intent,
    Observation,
    ActionResult,
    ExecutionResult,
    NextAction,
    HistoryEntry,
)


class TestCoordinates:
    """Test Coordinates dataclass."""

    def test_basic_coordinates(self):
        """Test basic coordinate creation."""
        coords = Coordinates(x=100, y=200)
        assert coords.x == 100
        assert coords.y == 200
        assert coords.width is None
        assert coords.height is None

    def test_coordinates_with_dimensions(self):
        """Test coordinates with width and height."""
        coords = Coordinates(x=100, y=200, width=50, height=30)
        assert coords.x == 100
        assert coords.y == 200
        assert coords.width == 50
        assert coords.height == 30

    def test_center_x_without_width(self):
        """Test center_x returns x when no width."""
        coords = Coordinates(x=100, y=200)
        assert coords.center_x == 100

    def test_center_x_with_width(self):
        """Test center_x calculates center when width present."""
        coords = Coordinates(x=100, y=200, width=50, height=30)
        assert coords.center_x == 125  # 100 + 50//2

    def test_center_y_without_height(self):
        """Test center_y returns y when no height."""
        coords = Coordinates(x=100, y=200)
        assert coords.center_y == 200

    def test_center_y_with_height(self):
        """Test center_y calculates center when height present."""
        coords = Coordinates(x=100, y=200, width=50, height=30)
        assert coords.center_y == 215  # 200 + 30//2

    def test_from_bbox(self):
        """Test creating coordinates from bounding box."""
        coords = Coordinates.from_bbox(x1=100, y1=200, x2=150, y2=230)
        assert coords.x == 100
        assert coords.y == 200
        assert coords.width == 50
        assert coords.height == 30
        assert coords.center_x == 125
        assert coords.center_y == 215


class TestActionStep:
    """Test ActionStep dataclass."""

    def test_basic_action_step(self):
        """Test basic action step creation."""
        step = ActionStep(action="activate_app", params={"app_name": "Safari"})
        assert step.action == "activate_app"
        assert step.params == {"app_name": "Safari"}

    def test_action_step_default_params(self):
        """Test action step with default empty params."""
        step = ActionStep(action="quit_app")
        assert step.action == "quit_app"
        assert step.params == {}

    def test_from_dict(self):
        """Test creating action step from dictionary."""
        data = {"action": "open_url", "params": {"url": "https://google.com"}}
        step = ActionStep.from_dict(data)
        assert step.action == "open_url"
        assert step.params == {"url": "https://google.com"}

    def test_from_dict_missing_params(self):
        """Test creating action step from dict without params."""
        data = {"action": "activate_app"}
        step = ActionStep.from_dict(data)
        assert step.action == "activate_app"
        assert step.params == {}

    def test_from_dict_empty(self):
        """Test creating action step from empty dict."""
        step = ActionStep.from_dict({})
        assert step.action == ""
        assert step.params == {}


class TestIntent:
    """Test Intent dataclass."""

    def test_basic_intent(self):
        """Test basic intent creation."""
        steps = [ActionStep(action="activate_app", params={"app_name": "Safari"})]
        intent = Intent(steps=steps, raw_prompt="Open Safari")
        assert len(intent.steps) == 1
        assert intent.raw_prompt == "Open Safari"
        assert intent.requires_observation is False

    def test_intent_with_observation(self):
        """Test intent requiring observation."""
        steps = [ActionStep(action="click_element", params={"description": "button"})]
        intent = Intent(steps=steps, raw_prompt="Click button", requires_observation=True)
        assert intent.requires_observation is True

    def test_from_dict_simple(self):
        """Test creating intent from simple dict."""
        data = {
            "steps": [{"action": "activate_app", "params": {"app_name": "Safari"}}],
            "requires_observation": False
        }
        intent = Intent.from_dict(data, raw_prompt="Open Safari")
        assert len(intent.steps) == 1
        assert intent.steps[0].action == "activate_app"
        assert intent.raw_prompt == "Open Safari"
        assert intent.requires_observation is False

    def test_from_dict_complex(self):
        """Test creating intent from complex dict."""
        data = {
            "steps": [
                {"action": "open_url", "params": {"url": "https://youtube.com"}},
                {"action": "type_text", "params": {"text": "cats"}},
                {"action": "click_element", "params": {"description": "first video"}}
            ],
            "requires_observation": True
        }
        intent = Intent.from_dict(data, raw_prompt="Search YouTube for cats")
        assert len(intent.steps) == 3
        assert intent.requires_observation is True

    def test_from_dict_missing_observation_flag(self):
        """Test creating intent with missing requires_observation."""
        data = {"steps": [{"action": "activate_app", "params": {"app_name": "Safari"}}]}
        intent = Intent.from_dict(data)
        assert intent.requires_observation is False  # Default


class TestObservation:
    """Test Observation dataclass."""

    def test_basic_observation(self):
        """Test basic observation creation."""
        obs = Observation(
            screenshot_b64="base64_data",
            description="Safari is open"
        )
        assert obs.screenshot_b64 == "base64_data"
        assert obs.description == "Safari is open"
        assert obs.elements is None
        assert isinstance(obs.timestamp, datetime)

    def test_observation_with_elements(self):
        """Test observation with elements list."""
        elements = [{"name": "button", "position": (100, 200)}]
        obs = Observation(
            screenshot_b64="data",
            description="Page loaded",
            elements=elements
        )
        assert obs.elements == elements

    def test_observation_str(self):
        """Test observation string representation."""
        obs = Observation(screenshot_b64="data", description="Screen state")
        str_repr = str(obs)
        assert "Observation" in str_repr
        assert "Screen state" in str_repr


class TestActionResult:
    """Test ActionResult dataclass."""

    def test_successful_result(self):
        """Test successful action result."""
        result = ActionResult(
            success=True,
            action="activate_app",
            params={"app_name": "Safari"},
            output="Safari activated"
        )
        assert result.success is True
        assert result.action == "activate_app"
        assert result.error is None

    def test_failed_result(self):
        """Test failed action result."""
        result = ActionResult(
            success=False,
            action="open_url",
            params={"url": "https://example.com"},
            error="Connection timeout"
        )
        assert result.success is False
        assert result.error == "Connection timeout"

    def test_result_str_success(self):
        """Test string representation of successful result."""
        result = ActionResult(success=True, action="click", params={"x": 100})
        str_repr = str(result)
        assert "SUCCESS" in str_repr
        assert "click" in str_repr

    def test_result_str_failed(self):
        """Test string representation of failed result."""
        result = ActionResult(success=False, action="type_text", params={})
        str_repr = str(result)
        assert "FAILED" in str_repr


class TestExecutionResult:
    """Test ExecutionResult dataclass."""

    def test_successful_execution(self):
        """Test successful execution result."""
        steps = [
            ActionResult(success=True, action="activate_app", params={}),
            ActionResult(success=True, action="open_url", params={})
        ]
        result = ExecutionResult(
            success=True,
            message="All steps completed",
            steps=steps
        )
        assert result.success is True
        assert len(result.steps) == 2
        assert result.error is None

    def test_failed_execution(self):
        """Test failed execution result."""
        result = ExecutionResult(
            success=False,
            message="Failed at step 2",
            error="Timeout error",
            iterations=5
        )
        assert result.success is False
        assert result.error == "Timeout error"
        assert result.iterations == 5

    def test_execution_default_values(self):
        """Test execution result default values."""
        result = ExecutionResult(success=True)
        assert result.message == ""
        assert result.steps == []
        assert result.error is None
        assert result.iterations == 0


class TestNextAction:
    """Test NextAction dataclass."""

    def test_action_to_execute(self):
        """Test next action with action to execute."""
        action = NextAction(
            action="click",
            params={"x": 100, "y": 200},
            reasoning="Need to click the button"
        )
        assert action.action == "click"
        assert action.params == {"x": 100, "y": 200}
        assert action.is_complete is False

    def test_completion_action(self):
        """Test completion action."""
        action = NextAction(action="", is_complete=True, reasoning="Goal achieved")
        assert action.is_complete is True
        assert action.action == ""

    def test_from_dict_action(self):
        """Test creating next action from dict."""
        data = {
            "action": "type_text",
            "params": {"text": "hello"},
            "reasoning": "Need to type"
        }
        action = NextAction.from_dict(data)
        assert action.action == "type_text"
        assert action.is_complete is False

    def test_from_dict_complete(self):
        """Test creating completion from dict."""
        data = {"complete": True, "reasoning": "Task done"}
        action = NextAction.from_dict(data)
        assert action.is_complete is True
        assert action.reasoning == "Task done"

    def test_complete_classmethod(self):
        """Test complete() classmethod."""
        action = NextAction.complete("All done")
        assert action.is_complete is True
        assert action.reasoning == "All done"
        assert action.action == ""


class TestHistoryEntry:
    """Test HistoryEntry dataclass."""

    def test_observation_entry(self):
        """Test creating history entry from observation."""
        obs = Observation(screenshot_b64="data", description="Screen visible")
        entry = HistoryEntry.from_observation(obs)
        assert entry.entry_type == "observation"
        assert "Screen visible" in entry.content
        assert entry.raw_data == obs

    def test_action_entry(self):
        """Test creating history entry from action result."""
        result = ActionResult(success=True, action="click", params={"x": 100})
        entry = HistoryEntry.from_action(result)
        assert entry.entry_type == "action"
        assert "click" in entry.content
        assert entry.raw_data == result

    def test_manual_entry(self):
        """Test creating manual history entry."""
        entry = HistoryEntry(
            entry_type="observation",
            content="Manual entry content",
        )
        assert entry.entry_type == "observation"
        assert entry.content == "Manual entry content"
        assert isinstance(entry.timestamp, datetime)
