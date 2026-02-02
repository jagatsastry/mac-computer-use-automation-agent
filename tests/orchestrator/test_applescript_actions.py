"""Tests for AppleScript actions (TypeTextAction, PressKeyAction, etc.)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
from automation_agent.actions.applescript import (
    AppleScriptAction,
    AppleScriptResult,
    ActivateAppAction,
    OpenURLAction,
    QuitAppAction,
    GetFrontmostAppAction,
    IsAppRunningAction,
    ClickUIElementAction,
    TypeTextAction,
    PressKeyAction,
)


class TestAppleScriptResult:
    """Test AppleScriptResult dataclass."""

    def test_successful_result(self):
        """Test successful result creation."""
        result = AppleScriptResult(success=True, output="Safari", error="")
        assert result.success is True
        assert result.output == "Safari"
        assert result.error == ""

    def test_failed_result(self):
        """Test failed result creation."""
        result = AppleScriptResult(success=False, output="", error="Script failed")
        assert result.success is False
        assert result.error == "Script failed"

    def test_result_defaults(self):
        """Test result with default values."""
        result = AppleScriptResult(success=True)
        assert result.output == ""
        assert result.error == ""


class TestAppleScriptActionBase:
    """Test base AppleScriptAction class."""

    @pytest.mark.asyncio
    async def test_run_script_success(self):
        """Test successful script execution."""
        action = AppleScriptAction()

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"output", b""))
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            result = await action._run_script('tell application "Finder" to activate')

            assert result.success is True
            assert result.output == "output"

    @pytest.mark.asyncio
    async def test_run_script_failure(self):
        """Test failed script execution."""
        action = AppleScriptAction()

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b"error message"))
            mock_process.returncode = 1
            mock_exec.return_value = mock_process

            result = await action._run_script('invalid script')

            assert result.success is False
            assert result.error == "error message"

    @pytest.mark.asyncio
    async def test_run_script_timeout(self):
        """Test script timeout handling."""
        action = AppleScriptAction()

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_process.kill = MagicMock()
            mock_exec.return_value = mock_process

            result = await action._run_script('slow script', timeout=0.1)

            assert result.success is False
            assert "timed out" in result.error.lower()


class TestActivateAppAction:
    """Test ActivateAppAction."""

    def test_init(self):
        """Test initialization."""
        action = ActivateAppAction("Safari")
        assert action.app_name == "Safari"

    @pytest.mark.asyncio
    async def test_execute(self):
        """Test execute method."""
        action = ActivateAppAction("Safari")

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True
            mock_run.assert_called_once()
            assert "Safari" in mock_run.call_args[0][0]
            assert "activate" in mock_run.call_args[0][0]


class TestOpenURLAction:
    """Test OpenURLAction."""

    def test_init_with_default_browser(self):
        """Test initialization with default browser."""
        action = OpenURLAction("https://google.com")
        assert action.url == "https://google.com"
        assert action.browser == "Safari"

    def test_init_with_custom_browser(self):
        """Test initialization with custom browser."""
        action = OpenURLAction("https://google.com", browser="Google Chrome")
        assert action.browser == "Google Chrome"

    @pytest.mark.asyncio
    async def test_execute(self):
        """Test execute method."""
        action = OpenURLAction("https://youtube.com", "Safari")

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True
            script = mock_run.call_args[0][0]
            assert "Safari" in script
            assert "youtube.com" in script
            assert "open location" in script


class TestQuitAppAction:
    """Test QuitAppAction."""

    def test_init(self):
        """Test initialization."""
        action = QuitAppAction("Safari")
        assert action.app_name == "Safari"

    @pytest.mark.asyncio
    async def test_execute(self):
        """Test execute method."""
        action = QuitAppAction("Terminal")

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True
            assert "Terminal" in mock_run.call_args[0][0]
            assert "quit" in mock_run.call_args[0][0]


class TestGetFrontmostAppAction:
    """Test GetFrontmostAppAction."""

    @pytest.mark.asyncio
    async def test_execute(self):
        """Test execute method."""
        action = GetFrontmostAppAction()

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True, output="Safari")
            result = await action.execute()

            assert result.success is True
            assert result.output == "Safari"
            assert "frontmost" in mock_run.call_args[0][0]


class TestIsAppRunningAction:
    """Test IsAppRunningAction."""

    def test_init(self):
        """Test initialization."""
        action = IsAppRunningAction("Safari")
        assert action.app_name == "Safari"

    @pytest.mark.asyncio
    async def test_execute_app_running(self):
        """Test execute when app is running."""
        action = IsAppRunningAction("Safari")

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True, output="true")
            result = await action.execute()

            assert result.success is True
            assert result.output == "true"

    @pytest.mark.asyncio
    async def test_execute_app_not_running(self):
        """Test execute when app is not running."""
        action = IsAppRunningAction("Safari")

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True, output="false")
            result = await action.execute()

            assert result.success is True
            assert result.output == "false"


class TestClickUIElementAction:
    """Test ClickUIElementAction."""

    def test_init(self):
        """Test initialization."""
        action = ClickUIElementAction("Safari", 'button "OK"')
        assert action.app_name == "Safari"
        assert action.element_description == 'button "OK"'

    @pytest.mark.asyncio
    async def test_execute(self):
        """Test execute method."""
        action = ClickUIElementAction("Safari", 'button "Submit"')

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True
            script = mock_run.call_args[0][0]
            assert "Safari" in script
            assert 'button "Submit"' in script
            assert "click" in script


class TestTypeTextAction:
    """Test TypeTextAction."""

    def test_init(self):
        """Test initialization."""
        action = TypeTextAction("Hello World")
        assert action.text == "Hello World"

    @pytest.mark.asyncio
    async def test_execute_simple_text(self):
        """Test execute with simple text."""
        action = TypeTextAction("hello")

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True
            script = mock_run.call_args[0][0]
            assert "keystroke" in script
            assert "hello" in script

    @pytest.mark.asyncio
    async def test_execute_text_with_quotes(self):
        """Test execute with text containing quotes."""
        action = TypeTextAction('Say "Hello"')

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True
            # Quotes should be escaped
            script = mock_run.call_args[0][0]
            assert '\\"' in script or "Hello" in script

    @pytest.mark.asyncio
    async def test_execute_text_with_backslash(self):
        """Test execute with text containing backslash."""
        action = TypeTextAction("path\\to\\file")

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_empty_text(self):
        """Test execute with empty text."""
        action = TypeTextAction("")

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True


class TestPressKeyAction:
    """Test PressKeyAction."""

    def test_init(self):
        """Test initialization."""
        action = PressKeyAction(["command", "c"])
        assert action.keys == ["command", "c"]

    def test_init_normalizes_case(self):
        """Test that keys are normalized to lowercase."""
        action = PressKeyAction(["COMMAND", "C"])
        assert action.keys == ["command", "c"]

    @pytest.mark.asyncio
    async def test_execute_single_key(self):
        """Test execute with single key."""
        action = PressKeyAction(["return"])

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True
            script = mock_run.call_args[0][0]
            assert "key code" in script
            assert "36" in script  # Return key code

    @pytest.mark.asyncio
    async def test_execute_modifier_and_key(self):
        """Test execute with modifier and key."""
        action = PressKeyAction(["command", "c"])

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True
            script = mock_run.call_args[0][0]
            assert "command down" in script
            assert "c" in script

    @pytest.mark.asyncio
    async def test_execute_multiple_modifiers(self):
        """Test execute with multiple modifiers."""
        action = PressKeyAction(["command", "shift", "s"])

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True
            script = mock_run.call_args[0][0]
            assert "command down" in script
            assert "shift down" in script

    @pytest.mark.asyncio
    async def test_execute_escape_key(self):
        """Test execute with escape key."""
        action = PressKeyAction(["escape"])

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True
            script = mock_run.call_args[0][0]
            assert "key code" in script
            assert "53" in script  # Escape key code

    @pytest.mark.asyncio
    async def test_execute_tab_key(self):
        """Test execute with tab key."""
        action = PressKeyAction(["tab"])

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True
            script = mock_run.call_args[0][0]
            assert "48" in script  # Tab key code

    @pytest.mark.asyncio
    async def test_execute_arrow_keys(self):
        """Test execute with arrow keys."""
        for key, code in [("up", "126"), ("down", "125"), ("left", "123"), ("right", "124")]:
            action = PressKeyAction([key])

            with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
                mock_run.return_value = AppleScriptResult(success=True)
                result = await action.execute()

                assert result.success is True
                script = mock_run.call_args[0][0]
                assert code in script

    @pytest.mark.asyncio
    async def test_execute_function_keys(self):
        """Test execute with function keys."""
        action = PressKeyAction(["f5"])

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            result = await action.execute()

            assert result.success is True
            script = mock_run.call_args[0][0]
            assert "96" in script  # F5 key code

    @pytest.mark.asyncio
    async def test_execute_no_main_key(self):
        """Test execute with only modifiers (no main key)."""
        action = PressKeyAction(["command"])

        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            result = await action.execute()

            assert result.success is False
            assert "No main key" in result.error

    @pytest.mark.asyncio
    async def test_execute_empty_keys(self):
        """Test execute with empty keys list."""
        action = PressKeyAction([])

        result = await action.execute()

        assert result.success is False

    @pytest.mark.asyncio
    async def test_modifier_aliases(self):
        """Test that modifier aliases work."""
        # Test cmd alias
        action = PressKeyAction(["cmd", "v"])
        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            await action.execute()
            assert "command down" in mock_run.call_args[0][0]

        # Test ctrl alias
        action = PressKeyAction(["ctrl", "c"])
        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            await action.execute()
            assert "control down" in mock_run.call_args[0][0]

        # Test alt alias
        action = PressKeyAction(["alt", "tab"])
        with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)
            await action.execute()
            assert "option down" in mock_run.call_args[0][0]


class TestPressKeyActionKeyCodes:
    """Test key code mappings in PressKeyAction."""

    def test_key_codes_dict_exists(self):
        """Test that KEY_CODES dictionary exists."""
        assert hasattr(PressKeyAction, 'KEY_CODES')
        assert isinstance(PressKeyAction.KEY_CODES, dict)

    def test_common_key_codes(self):
        """Test common key codes are defined."""
        codes = PressKeyAction.KEY_CODES
        assert codes["return"] == 36
        assert codes["tab"] == 48
        assert codes["space"] == 49
        assert codes["delete"] == 51
        assert codes["escape"] == 53

    def test_modifiers_dict_exists(self):
        """Test that MODIFIERS dictionary exists."""
        assert hasattr(PressKeyAction, 'MODIFIERS')
        assert isinstance(PressKeyAction.MODIFIERS, dict)

    def test_common_modifiers(self):
        """Test common modifiers are defined."""
        mods = PressKeyAction.MODIFIERS
        assert "command" in mods
        assert "shift" in mods
        assert "option" in mods
        assert "control" in mods


class TestAppleScriptActionsIntegration:
    """Integration tests for AppleScript actions."""

    @pytest.mark.asyncio
    async def test_combined_workflow(self):
        """Test a combined workflow of actions."""
        # Simulate: Activate Safari, type URL, press Enter
        actions = [
            ActivateAppAction("Safari"),
            TypeTextAction("https://google.com"),
            PressKeyAction(["return"]),
        ]

        for action in actions:
            with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
                mock_run.return_value = AppleScriptResult(success=True)
                result = await action.execute()
                assert result.success is True

    @pytest.mark.asyncio
    async def test_copy_paste_workflow(self):
        """Test copy-paste workflow."""
        # Cmd+A, Cmd+C, Tab, Cmd+V
        actions = [
            PressKeyAction(["command", "a"]),
            PressKeyAction(["command", "c"]),
            PressKeyAction(["tab"]),
            PressKeyAction(["command", "v"]),
        ]

        for action in actions:
            with patch.object(action, '_run_script', new_callable=AsyncMock) as mock_run:
                mock_run.return_value = AppleScriptResult(success=True)
                result = await action.execute()
                assert result.success is True
