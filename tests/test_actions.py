"""Tests for action system."""

import pytest
import asyncio
from unittest.mock import patch, MagicMock
from automation_agent.actions.base import ActionType, ActionResult
from automation_agent.actions.simple import (
    ClickAction, TypeAction, HotkeyAction, WaitAction
)


class TestActionBase:
    """Test base action classes."""

    def test_action_type_enum(self):
        """Test ActionType enum."""
        assert ActionType.CLICK.value == "click"
        assert ActionType.TYPE.value == "type"
        assert ActionType.HOTKEY.value == "hotkey"
        assert ActionType.WAIT.value == "wait"
        print("✓ ActionType enum works")

    def test_action_result(self):
        """Test ActionResult dataclass."""
        result = ActionResult(
            success=True,
            action_type=ActionType.CLICK,
            timestamp=1234567890.0,
            metadata={"x": 100, "y": 200}
        )

        assert result.success is True
        assert result.action_type == ActionType.CLICK
        assert result.timestamp == 1234567890.0
        assert result.metadata == {"x": 100, "y": 200}
        assert result.error is None
        print("✓ ActionResult dataclass works")


class TestClickAction:
    """Test ClickAction."""

    @pytest.mark.asyncio
    async def test_click_validation_valid(self):
        """Test click validation with valid coordinates."""
        with patch('pyautogui.size', return_value=(1920, 1080)):
            action = ClickAction(100, 200)
            valid = await action.validate()
            assert valid is True
            print("✓ Click validation (valid) works")

    @pytest.mark.asyncio
    async def test_click_validation_invalid(self):
        """Test click validation with invalid coordinates."""
        with patch('pyautogui.size', return_value=(1920, 1080)):
            action = ClickAction(3000, 200)  # Out of bounds
            valid = await action.validate()
            assert valid is False
            print("✓ Click validation (invalid) works")

    @pytest.mark.asyncio
    async def test_click_execution(self):
        """Test click execution."""
        with patch('pyautogui.click') as mock_click:
            action = ClickAction(100, 200)
            result = await action.execute()

            assert result.success is True
            assert result.action_type == ActionType.CLICK
            assert result.metadata["x"] == 100
            assert result.metadata["y"] == 200
            mock_click.assert_called_once_with(100, 200)
            print("✓ Click execution works")

    @pytest.mark.asyncio
    async def test_click_execution_error(self):
        """Test click execution with error."""
        with patch('pyautogui.click', side_effect=Exception("Click error")):
            action = ClickAction(100, 200)
            result = await action.execute()

            assert result.success is False
            assert result.error == "Click error"
            print("✓ Click error handling works")


class TestTypeAction:
    """Test TypeAction."""

    @pytest.mark.asyncio
    async def test_type_validation_valid(self):
        """Test type validation with valid text."""
        action = TypeAction("Hello World")
        valid = await action.validate()
        assert valid is True
        print("✓ Type validation (valid) works")

    @pytest.mark.asyncio
    async def test_type_validation_empty(self):
        """Test type validation with empty text."""
        action = TypeAction("")
        valid = await action.validate()
        assert valid is False
        print("✓ Type validation (empty) works")

    @pytest.mark.asyncio
    async def test_type_execution(self):
        """Test type execution."""
        with patch('pyautogui.write') as mock_write:
            action = TypeAction("Hello")
            result = await action.execute()

            assert result.success is True
            assert result.action_type == ActionType.TYPE
            assert result.metadata["text_length"] == 5
            mock_write.assert_called_once_with("Hello", interval=0.05)
            print("✓ Type execution works")


class TestHotkeyAction:
    """Test HotkeyAction."""

    @pytest.mark.asyncio
    async def test_hotkey_validation_valid(self):
        """Test hotkey validation with valid keys."""
        action = HotkeyAction("cmd", "c")
        valid = await action.validate()
        assert valid is True
        print("✓ Hotkey validation (valid) works")

    @pytest.mark.asyncio
    async def test_hotkey_validation_empty(self):
        """Test hotkey validation with empty keys."""
        action = HotkeyAction()
        valid = await action.validate()
        assert valid is False
        print("✓ Hotkey validation (empty) works")

    @pytest.mark.asyncio
    async def test_hotkey_execution(self):
        """Test hotkey execution."""
        with patch('pyautogui.hotkey') as mock_hotkey:
            action = HotkeyAction("cmd", "c")
            result = await action.execute()

            assert result.success is True
            assert result.action_type == ActionType.HOTKEY
            assert result.metadata["keys"] == ("cmd", "c")
            mock_hotkey.assert_called_once_with("cmd", "c")
            print("✓ Hotkey execution works")


class TestWaitAction:
    """Test WaitAction."""

    @pytest.mark.asyncio
    async def test_wait_validation_valid(self):
        """Test wait validation with valid duration."""
        action = WaitAction(1.0)
        valid = await action.validate()
        assert valid is True
        print("✓ Wait validation (valid) works")

    @pytest.mark.asyncio
    async def test_wait_validation_invalid(self):
        """Test wait validation with invalid duration."""
        action = WaitAction(0)
        valid = await action.validate()
        assert valid is False

        action2 = WaitAction(-1)
        valid2 = await action2.validate()
        assert valid2 is False
        print("✓ Wait validation (invalid) works")

    @pytest.mark.asyncio
    async def test_wait_execution(self):
        """Test wait execution."""
        action = WaitAction(0.01)  # Very short wait for test
        result = await action.execute()

        assert result.success is True
        assert result.action_type == ActionType.WAIT
        assert result.metadata["duration"] == 0.01
        print("✓ Wait execution works")

    @pytest.mark.asyncio
    async def test_wait_duration_accuracy(self):
        """Test that wait actually waits."""
        import time
        start = time.time()
        action = WaitAction(0.1)
        await action.execute()
        elapsed = time.time() - start

        assert elapsed >= 0.1
        assert elapsed < 0.2  # Allow some overhead
        print("✓ Wait duration accurate")
