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

    def setup_method(self):
        """Reset scale factor cache before each test."""
        ClickAction._scale_factor = None

    @pytest.mark.asyncio
    async def test_click_validation_valid(self):
        """Test click validation with valid coordinates."""
        # Mock non-Retina display (1:1 scaling)
        mock_screenshot = MagicMock()
        mock_screenshot.size = (1920, 1080)
        with patch('pyautogui.size', return_value=(1920, 1080)), \
             patch('pyautogui.screenshot', return_value=mock_screenshot):
            ClickAction._scale_factor = None  # Reset cache
            action = ClickAction(100, 200)
            valid = await action.validate()
            assert valid is True
            print("✓ Click validation (valid) works")

    @pytest.mark.asyncio
    async def test_click_validation_invalid(self):
        """Test click validation with invalid coordinates (after scaling)."""
        # Mock Retina display (2x scaling) - coordinates in screenshot space
        mock_screenshot = MagicMock()
        mock_screenshot.size = (3840, 2160)  # 2x logical
        with patch('pyautogui.size', return_value=(1920, 1080)), \
             patch('pyautogui.screenshot', return_value=mock_screenshot):
            ClickAction._scale_factor = None  # Reset cache
            # 5000 in screenshot space / 2 = 2500 logical, which is > 1920
            action = ClickAction(5000, 200)
            valid = await action.validate()
            assert valid is False
            print("✓ Click validation (invalid) works")

    @pytest.mark.asyncio
    async def test_click_execution_no_scaling(self):
        """Test click execution on non-Retina display (1:1 scale)."""
        mock_screenshot = MagicMock()
        mock_screenshot.size = (1920, 1080)  # Same as logical = 1x scaling
        with patch('pyautogui.size', return_value=(1920, 1080)), \
             patch('pyautogui.screenshot', return_value=mock_screenshot), \
             patch('pyautogui.click') as mock_click:
            ClickAction._scale_factor = None
            action = ClickAction(100, 200)
            result = await action.execute()

            assert result.success is True
            assert result.action_type == ActionType.CLICK
            assert result.metadata["original_x"] == 100
            assert result.metadata["original_y"] == 200
            assert result.metadata["scaled_x"] == 100  # No scaling
            assert result.metadata["scaled_y"] == 200
            assert result.metadata["scale_factor"] == 1.0
            mock_click.assert_called_once_with(100, 200)
            print("✓ Click execution (no scaling) works")

    @pytest.mark.asyncio
    async def test_click_execution_retina_scaling(self):
        """Test click execution on Retina display (2x scale)."""
        mock_screenshot = MagicMock()
        mock_screenshot.size = (3024, 1964)  # 2x logical = Retina
        with patch('pyautogui.size', return_value=(1512, 982)), \
             patch('pyautogui.screenshot', return_value=mock_screenshot), \
             patch('pyautogui.click') as mock_click:
            ClickAction._scale_factor = None
            # Claude reports (1806, 612) in screenshot space
            action = ClickAction(1806, 612)
            result = await action.execute()

            assert result.success is True
            assert result.action_type == ActionType.CLICK
            # Original coordinates preserved
            assert result.metadata["original_x"] == 1806
            assert result.metadata["original_y"] == 612
            # Scaled coordinates (divided by 2)
            assert result.metadata["scaled_x"] == 903
            assert result.metadata["scaled_y"] == 306
            assert result.metadata["scale_factor"] == 2.0
            # Click called with SCALED coordinates
            mock_click.assert_called_once_with(903, 306)
            print("✓ Click execution (Retina 2x scaling) works")

    @pytest.mark.asyncio
    async def test_click_execution_error(self):
        """Test click execution with error."""
        mock_screenshot = MagicMock()
        mock_screenshot.size = (1920, 1080)
        with patch('pyautogui.size', return_value=(1920, 1080)), \
             patch('pyautogui.screenshot', return_value=mock_screenshot), \
             patch('pyautogui.click', side_effect=Exception("Click error")):
            ClickAction._scale_factor = None
            action = ClickAction(100, 200)
            result = await action.execute()

            assert result.success is False
            assert result.error == "Click error"
            print("✓ Click error handling works")

    @pytest.mark.asyncio
    async def test_scale_factor_caching(self):
        """Test that scale factor is cached after first calculation."""
        mock_screenshot = MagicMock()
        mock_screenshot.size = (3024, 1964)
        with patch('pyautogui.size', return_value=(1512, 982)), \
             patch('pyautogui.screenshot', return_value=mock_screenshot) as mock_ss, \
             patch('pyautogui.click'):
            ClickAction._scale_factor = None

            # First click should calculate scale factor
            action1 = ClickAction(100, 100)
            await action1.execute()
            assert mock_ss.call_count == 1

            # Second click should use cached value
            action2 = ClickAction(200, 200)
            await action2.execute()
            assert mock_ss.call_count == 1  # No additional screenshot call
            print("✓ Scale factor caching works")

    @pytest.mark.asyncio
    async def test_scale_factor_calculation(self):
        """Test scale factor calculation for various display configurations."""
        test_cases = [
            # (logical_size, screenshot_size, expected_scale)
            ((1920, 1080), (1920, 1080), 1.0),   # Non-Retina 1080p
            ((1512, 982), (3024, 1964), 2.0),    # Retina MacBook Pro
            ((2560, 1440), (5120, 2880), 2.0),   # Retina 5K display
            ((1440, 900), (2880, 1800), 2.0),    # Retina MacBook Air
            ((1920, 1080), (2880, 1620), 1.5),   # 1.5x scaling
        ]

        for logical_size, screenshot_size, expected_scale in test_cases:
            mock_screenshot = MagicMock()
            mock_screenshot.size = screenshot_size
            with patch('pyautogui.size', return_value=logical_size), \
                 patch('pyautogui.screenshot', return_value=mock_screenshot):
                ClickAction._scale_factor = None
                scale = ClickAction._get_scale_factor()
                assert scale == expected_scale, f"Expected {expected_scale} for {logical_size} -> {screenshot_size}"
        print("✓ Scale factor calculation works for various displays")


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
