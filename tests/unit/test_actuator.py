"""Unit tests for the HammerspoonActuator component.

All tests mock subprocess.run — no real hs CLI calls are made.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from automation_agent.actuator.actuator import HammerspoonActuator
from automation_agent.actuator.models import ActuatorResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def actuator():
    """Create a HammerspoonActuator with a mocked hs path (always available)."""
    with patch("automation_agent.actuator.actuator.shutil.which", return_value="/usr/local/bin/hs"):
        return HammerspoonActuator()


@pytest.fixture
def mock_run_success():
    """Patch subprocess.run to return a successful result."""
    with patch("automation_agent.actuator.actuator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok", stderr=""
        )
        yield mock_run


@pytest.fixture
def templates_dir():
    """Path to the Lua templates directory."""
    return Path(__file__).resolve().parent.parent.parent / "src" / "automation_agent" / "actuator" / "lua_templates"


# ---------------------------------------------------------------------------
# Test 1: click — correct Lua generated
# ---------------------------------------------------------------------------

class TestClick:
    def test_click_generates_correct_lua(self, actuator, mock_run_success):
        """click(500, 300) renders the click.lua template with x=500, y=300."""
        actuator.click(500, 300)

        call_args = mock_run_success.call_args
        lua_code = call_args[0][0][2]  # [hs_path, "-c", lua_code]
        assert "hs.eventtap.leftClick({x=500, y=300})" in lua_code
        assert 'return "clicked"' in lua_code


# ---------------------------------------------------------------------------
# Tests 2-5: type_text — correct Lua and escaping
# ---------------------------------------------------------------------------

class TestTypeText:
    def test_type_text_generates_correct_lua(self, actuator, mock_run_success):
        """type_text('hello') renders the type_text.lua template."""
        actuator.type_text("hello")

        lua_code = mock_run_success.call_args[0][0][2]
        assert 'hs.eventtap.keyStrokes("hello")' in lua_code
        assert 'return "typed"' in lua_code

    def test_type_text_escapes_double_quotes(self, actuator, mock_run_success):
        """Double quotes in text are escaped for Lua."""
        actuator.type_text('say "hello"')

        lua_code = mock_run_success.call_args[0][0][2]
        assert 'say \\"hello\\"' in lua_code
        # The Lua string should still be valid (no unescaped quotes)
        assert 'hs.eventtap.keyStrokes("say \\"hello\\"")' in lua_code

    def test_type_text_escapes_backslashes(self, actuator, mock_run_success):
        """Backslashes in text are escaped for Lua."""
        actuator.type_text("path\\to\\file")

        lua_code = mock_run_success.call_args[0][0][2]
        assert "path\\\\to\\\\file" in lua_code

    def test_type_text_escapes_newlines(self, actuator, mock_run_success):
        """Newlines in text are escaped for Lua."""
        actuator.type_text("line1\nline2")

        lua_code = mock_run_success.call_args[0][0][2]
        assert "line1\\nline2" in lua_code


# ---------------------------------------------------------------------------
# Tests 6-7: press_key — modifier mapping
# ---------------------------------------------------------------------------

class TestPressKey:
    def test_press_key_single_modifier(self, actuator, mock_run_success):
        """press_key(['cmd', 'c']) maps modifiers correctly."""
        actuator.press_key(["cmd", "c"])

        lua_code = mock_run_success.call_args[0][0][2]
        assert '{"cmd"}' in lua_code
        assert '"c"' in lua_code
        assert "hs.eventtap.keyStroke" in lua_code

    def test_press_key_multiple_modifiers(self, actuator, mock_run_success):
        """press_key(['cmd', 'shift', 's']) handles multiple modifiers."""
        actuator.press_key(["cmd", "shift", "s"])

        lua_code = mock_run_success.call_args[0][0][2]
        assert '"cmd"' in lua_code
        assert '"shift"' in lua_code
        assert '"s"' in lua_code

    def test_press_key_modifier_aliases(self, actuator, mock_run_success):
        """'command' maps to 'cmd', 'option' to 'alt', 'control' to 'ctrl'."""
        actuator.press_key(["command", "option", "a"])

        lua_code = mock_run_success.call_args[0][0][2]
        assert '"cmd"' in lua_code
        assert '"alt"' in lua_code
        assert '"a"' in lua_code

    def test_press_key_only_modifiers_returns_error(self, actuator):
        """press_key with only modifiers (no key) returns error."""
        result = actuator.press_key(["cmd", "shift"])
        assert result["success"] is False
        assert "No key specified" in result["error"]


# ---------------------------------------------------------------------------
# Test 8: activate_app
# ---------------------------------------------------------------------------

class TestActivateApp:
    def test_activate_app_generates_correct_lua(self, actuator, mock_run_success):
        """activate_app('Safari') renders the activate_app.lua template."""
        actuator.activate_app("Safari")

        lua_code = mock_run_success.call_args[0][0][2]
        assert 'hs.application.launchOrFocus("Safari")' in lua_code
        assert 'return "activated"' in lua_code


# ---------------------------------------------------------------------------
# Test 9: open_url
# ---------------------------------------------------------------------------

class TestOpenUrl:
    def test_open_url_generates_correct_lua(self, actuator, mock_run_success):
        """open_url('https://example.com') renders the open_url.lua template."""
        actuator.open_url("https://example.com")

        lua_code = mock_run_success.call_args[0][0][2]
        assert 'hs.urlevent.openURL("https://example.com")' in lua_code
        assert 'return "opened"' in lua_code


# ---------------------------------------------------------------------------
# Test 10: quit_app
# ---------------------------------------------------------------------------

class TestQuitApp:
    def test_quit_app_generates_correct_lua(self, actuator, mock_run_success):
        """quit_app('Safari') renders the quit_app.lua template."""
        actuator.quit_app("Safari")

        lua_code = mock_run_success.call_args[0][0][2]
        assert 'hs.application.get("Safari")' in lua_code
        assert "app:kill()" in lua_code


# ---------------------------------------------------------------------------
# Tests 11-12: get_state
# ---------------------------------------------------------------------------

class TestGetState:
    def test_get_state_parses_json_response(self, actuator):
        """get_state() parses valid JSON from hs into a dict with expected keys."""
        state_json = json.dumps({
            "app_name": "Finder",
            "app_bundle": "com.apple.finder",
            "window_title": "Documents",
            "window_frame": '{"x":0,"y":25,"w":1440,"h":875}',
        })
        with patch("automation_agent.actuator.actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=state_json, stderr=""
            )
            result = actuator.get_state()

        assert result["app_name"] == "Finder"
        assert result["app_bundle"] == "com.apple.finder"
        assert result["window_title"] == "Documents"
        assert "window_frame" in result

    def test_get_state_empty_response_returns_defaults(self, actuator):
        """get_state() with failed hs call returns safe defaults."""
        with patch("automation_agent.actuator.actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="error"
            )
            result = actuator.get_state()

        assert result["app_name"] == ""
        assert result["app_bundle"] == ""
        assert result["window_title"] == ""
        assert result["window_frame"] == ""

    def test_get_state_invalid_json_returns_raw(self, actuator):
        """get_state() with non-JSON output returns defaults with raw field."""
        with patch("automation_agent.actuator.actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="not json at all", stderr=""
            )
            result = actuator.get_state()

        assert result["app_name"] == ""
        assert "raw" in result
        assert result["raw"] == "not json at all"


# ---------------------------------------------------------------------------
# Tests 13-14: is_available
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_is_available_when_hs_found(self):
        """is_available() returns True when shutil.which finds hs."""
        with patch("automation_agent.actuator.actuator.shutil.which", return_value="/usr/local/bin/hs"):
            actuator = HammerspoonActuator()
            assert actuator.is_available() is True

    def test_is_available_when_hs_not_found(self):
        """is_available() returns False when shutil.which returns None."""
        with patch("automation_agent.actuator.actuator.shutil.which", return_value=None):
            actuator = HammerspoonActuator()
            assert actuator.is_available() is False


# ---------------------------------------------------------------------------
# Test 15: hs CLI error (non-zero exit code)
# ---------------------------------------------------------------------------

class TestHsCliError:
    def test_nonzero_exit_code_returns_failure(self, actuator):
        """Non-zero exit code from hs results in success=False with error."""
        with patch("automation_agent.actuator.actuator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Lua error: attempt to index nil"
            )
            result = actuator.click(100, 200)

        assert result["success"] is False
        assert "Lua error" in result["error"]


# ---------------------------------------------------------------------------
# Test 16: hs CLI timeout
# ---------------------------------------------------------------------------

class TestHsCliTimeout:
    def test_timeout_returns_failure(self, actuator):
        """subprocess.TimeoutExpired results in a timeout error."""
        with patch("automation_agent.actuator.actuator.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["hs", "-c", "..."], timeout=10
            )
            result = actuator.click(100, 200)

        assert result["success"] is False
        assert "timed out" in result["error"]
        assert "10s" in result["error"]


# ---------------------------------------------------------------------------
# Test 17: Lua template files exist on disk
# ---------------------------------------------------------------------------

class TestLuaTemplates:
    EXPECTED_TEMPLATES = [
        "click.lua",
        "type_text.lua",
        "press_key.lua",
        "activate_app.lua",
        "open_url.lua",
        "quit_app.lua",
        "get_state.lua",
    ]

    def test_all_template_files_exist(self, templates_dir):
        """All expected Lua template files exist on disk."""
        for template_name in self.EXPECTED_TEMPLATES:
            path = templates_dir / template_name
            assert path.exists(), f"Template file missing: {path}"
            content = path.read_text()
            assert len(content) > 0, f"Template file is empty: {path}"

    def test_templates_load_via_render(self, actuator, mock_run_success):
        """Templates are loadable via _render_template."""
        # Verify click.lua renders without error
        lua = actuator._render_template("click.lua", {"x": "100", "y": "200"})
        assert "100" in lua
        assert "200" in lua


# ---------------------------------------------------------------------------
# Test 18: Missing template file
# ---------------------------------------------------------------------------

class TestMissingTemplate:
    def test_missing_template_raises_file_not_found(self, actuator):
        """Attempting to render a non-existent template raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Lua template not found"):
            actuator._render_template("nonexistent.lua", {})


# ---------------------------------------------------------------------------
# ActuatorResult model tests
# ---------------------------------------------------------------------------

class TestActuatorResult:
    def test_to_dict_success(self):
        """ActuatorResult.to_dict() includes success and output."""
        r = ActuatorResult(success=True, output="clicked")
        d = r.to_dict()
        assert d == {"success": True, "output": "clicked"}

    def test_to_dict_with_error(self):
        """ActuatorResult.to_dict() includes error when present."""
        r = ActuatorResult(success=False, output="", error="something broke")
        d = r.to_dict()
        assert d["success"] is False
        assert d["error"] == "something broke"

    def test_to_dict_no_error_key_when_none(self):
        """ActuatorResult.to_dict() omits error key when error is None."""
        r = ActuatorResult(success=True, output="ok")
        d = r.to_dict()
        assert "error" not in d
