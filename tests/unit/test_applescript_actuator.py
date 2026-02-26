"""Unit tests for AppleScriptActuator.

All subprocess calls are mocked — no real osascript is invoked.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from automation_agent.actuator.applescript_actuator import AppleScriptActuator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def actuator():
    return AppleScriptActuator()


@pytest.fixture
def mock_run_success():
    """Patch subprocess.run to return a successful result."""
    with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok", stderr=""
        )
        yield mock_run


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_available_when_osascript_works(self, actuator):
        with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            assert actuator.is_available() is True

    def test_unavailable_when_osascript_fails(self, actuator):
        with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
            assert actuator.is_available() is False

    def test_unavailable_on_exception(self, actuator):
        with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("osascript not found")
            assert actuator.is_available() is False


# ---------------------------------------------------------------------------
# type_text
# ---------------------------------------------------------------------------

class TestTypeText:
    def test_type_text_calls_osascript_with_keystroke(self, actuator, mock_run_success):
        result = actuator.type_text("hello")

        call_args = mock_run_success.call_args
        script = call_args[0][0][2]  # ["osascript", "-e", script]
        assert 'keystroke "hello"' in script
        assert result["success"] is True

    def test_type_text_escapes_double_quotes(self, actuator, mock_run_success):
        actuator.type_text('say "hi"')

        script = mock_run_success.call_args[0][0][2]
        assert 'say \\"hi\\"' in script

    def test_type_text_escapes_backslashes(self, actuator, mock_run_success):
        actuator.type_text("a\\b")

        script = mock_run_success.call_args[0][0][2]
        assert "a\\\\b" in script


# ---------------------------------------------------------------------------
# press_key
# ---------------------------------------------------------------------------

class TestPressKey:
    def test_press_key_return_uses_key_code(self, actuator, mock_run_success):
        result = actuator.press_key(["return"])

        script = mock_run_success.call_args[0][0][2]
        assert "key code 36" in script
        assert result["success"] is True

    def test_press_key_with_modifier(self, actuator, mock_run_success):
        actuator.press_key(["cmd", "c"])

        script = mock_run_success.call_args[0][0][2]
        assert 'keystroke "c"' in script
        assert "command down" in script

    def test_press_key_multiple_modifiers(self, actuator, mock_run_success):
        actuator.press_key(["cmd", "shift", "s"])

        script = mock_run_success.call_args[0][0][2]
        assert "command down" in script
        assert "shift down" in script
        assert 'keystroke "s"' in script

    def test_press_key_only_modifiers_returns_error(self, actuator):
        result = actuator.press_key(["cmd", "shift"])
        assert result["success"] is False
        assert "No key" in result["error"]

    def test_press_key_failure(self, actuator):
        with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="System Events got an error"
            )
            result = actuator.press_key(["a"])

        assert result["success"] is False
        assert "System Events" in result["error"]


# ---------------------------------------------------------------------------
# activate_app
# ---------------------------------------------------------------------------

class TestActivateApp:
    def test_activate_app_calls_open(self, actuator, mock_run_success):
        result = actuator.activate_app("Safari")

        call_args = mock_run_success.call_args[0][0]
        assert call_args == ["open", "-a", "Safari"]
        assert result["success"] is True

    def test_activate_app_failure(self, actuator):
        with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Unable to find application"
            )
            result = actuator.activate_app("NonExistent")

        assert result["success"] is False
        assert "Unable to find" in result["error"]

    def test_activate_app_timeout(self, actuator):
        with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["open", "-a", "Safari"], timeout=10
            )
            result = actuator.activate_app("Safari")

        assert result["success"] is False
        assert "timed out" in result["error"]


# ---------------------------------------------------------------------------
# open_url
# ---------------------------------------------------------------------------

class TestOpenUrl:
    def test_open_url_calls_osascript(self, actuator, mock_run_success):
        result = actuator.open_url("https://example.com")

        script = mock_run_success.call_args[0][0][2]
        assert 'open location "https://example.com"' in script
        assert result["success"] is True


# ---------------------------------------------------------------------------
# quit_app
# ---------------------------------------------------------------------------

class TestQuitApp:
    def test_quit_app_tells_app_to_quit(self, actuator, mock_run_success):
        result = actuator.quit_app("Safari")

        script = mock_run_success.call_args[0][0][2]
        assert 'tell application "Safari" to quit' in script
        assert result["success"] is True

    def test_quit_app_failure(self, actuator):
        with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Application isn't running"
            )
            result = actuator.quit_app("NonRunning")

        assert result["success"] is False


# ---------------------------------------------------------------------------
# get_state
# ---------------------------------------------------------------------------

class TestGetState:
    def test_get_state_parses_pipe_delimited_output(self, actuator):
        with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Finder|com.apple.finder|Documents|100|50|800|600",
                stderr="",
            )
            state = actuator.get_state()

        assert state["app_name"] == "Finder"
        assert state["app_bundle"] == "com.apple.finder"
        assert state["window_title"] == "Documents"
        assert state["window_x"] == 100
        assert state["window_y"] == 50
        assert state["window_w"] == 800
        assert state["window_h"] == 600

    def test_get_state_failure_returns_defaults(self, actuator):
        with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="error"
            )
            state = actuator.get_state()

        assert state["app_name"] == ""
        assert state["app_bundle"] == ""
        assert state["window_title"] == ""
        assert state["window_x"] == 0
        assert state["window_y"] == 0

    def test_get_state_with_zero_window_frame(self, actuator):
        """Window frame with 0 values parses correctly."""
        with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Safari|com.apple.Safari|Google|0|0|0|0",
                stderr="",
            )
            state = actuator.get_state()

        assert state["app_name"] == "Safari"
        assert state["window_x"] == 0
        assert state["window_w"] == 0


# ---------------------------------------------------------------------------
# _run_osascript
# ---------------------------------------------------------------------------

class TestRunOsascript:
    def test_timeout_returns_error(self, actuator):
        with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["osascript", "-e", "..."], timeout=10
            )
            result = actuator._run_osascript("some script")

        assert result.success is False
        assert "timed out" in result.error

    def test_nonzero_exit_returns_stderr(self, actuator):
        with patch("automation_agent.actuator.applescript_actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="execution error"
            )
            result = actuator._run_osascript("bad script")

        assert result.success is False
        assert "execution error" in result.error


# ---------------------------------------------------------------------------
# click (pyautogui-based)
# ---------------------------------------------------------------------------

class TestClick:
    def test_click_uses_pyautogui(self, actuator):
        mock_pyautogui = MagicMock()
        with patch.dict("sys.modules", {"pyautogui": mock_pyautogui}):
            result = actuator.click(100, 200)

        mock_pyautogui.click.assert_called_once_with(100, 200)
        assert result["success"] is True

    def test_click_failure_when_pyautogui_unavailable(self, actuator):
        with patch.dict("sys.modules", {"pyautogui": None}):
            # Importing None will cause an error
            result = actuator.click(100, 200)

        assert result["success"] is False
