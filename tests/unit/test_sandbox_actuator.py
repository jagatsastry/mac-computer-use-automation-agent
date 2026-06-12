"""Unit tests for the Docker X11 sandbox actuator.

All subprocess calls are mocked — no Docker required.
"""

from unittest.mock import MagicMock, patch

import pytest

from automation_agent.sandbox.docker_actuator import SandboxActuator


def _ok(stdout=""):
    return MagicMock(returncode=0, stdout=stdout, stderr="")


def _fail(stderr="boom"):
    return MagicMock(returncode=1, stdout="", stderr=stderr)


@pytest.fixture
def fake_cdp():
    cdp = MagicMock()
    cdp.active_page.return_value = None
    cdp.batch_state.return_value = {}
    cdp.scroll_position.return_value = None
    return cdp


@pytest.fixture
def actuator(fake_cdp):
    return SandboxActuator(container="testbox", cdp=fake_cdp)


class TestClick:
    def test_click_runs_xdotool_mousemove_then_click(self, actuator):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", return_value=_ok()
        ) as run:
            result = actuator.click(310, 220)
        assert result["success"] is True
        cmd = run.call_args[0][0]
        assert cmd[:3] == ["docker", "exec", "testbox"]
        assert "xdotool" in cmd
        assert "mousemove" in cmd and "310" in cmd and "220" in cmd
        assert "click" in cmd and "1" in cmd

    def test_click_failure_propagates_error(self, actuator):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run",
            return_value=_fail("no display"),
        ):
            result = actuator.click(1, 2)
        assert result["success"] is False
        assert "no display" in result["error"]


class TestTypeText:
    def test_type_text_passes_text_as_single_argument(self, actuator):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", return_value=_ok()
        ) as run:
            result = actuator.type_text('hello "world" $HOME')
        assert result["success"] is True
        cmd = run.call_args[0][0]
        # Text must be the final argv element after `--` — never shell-interpolated
        assert cmd[-1] == 'hello "world" $HOME'
        assert cmd[-2] == "--"
        assert "type" in cmd

    def test_type_text_strips_newlines_like_applescript_actuator(self, actuator):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", return_value=_ok()
        ) as run:
            actuator.type_text("line1\nline2\tend")
        cmd = run.call_args[0][0]
        assert cmd[-1] == "line1line2 end"


class TestPressKey:
    @pytest.mark.parametrize(
        "keys,expected_combo",
        [
            (["cmd", "l"], "ctrl+l"),
            (["command", "a"], "ctrl+a"),
            (["ctrl", "shift", "t"], "ctrl+shift+t"),
            (["return"], "Return"),
            (["enter"], "Return"),
            (["tab"], "Tab"),
            (["escape"], "Escape"),
            (["esc"], "Escape"),
            (["delete"], "BackSpace"),
            (["space"], "space"),
            (["up"], "Up"),
            (["down"], "Down"),
            (["left"], "Left"),
            (["right"], "Right"),
            (["alt", "f4"], "alt+F4"),
            (["option", "tab"], "alt+Tab"),
            (["x"], "x"),
        ],
    )
    def test_key_mapping(self, actuator, keys, expected_combo):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", return_value=_ok()
        ) as run:
            result = actuator.press_key(keys)
        assert result["success"] is True
        cmd = run.call_args[0][0]
        assert cmd[-1] == expected_combo
        assert "key" in cmd

    def test_modifiers_only_is_an_error(self, actuator):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", return_value=_ok()
        ) as run:
            result = actuator.press_key(["cmd", "shift"])
        assert result["success"] is False
        assert "key" in result["error"].lower()
        run.assert_not_called()

    def test_empty_keys_is_an_error(self, actuator):
        result = actuator.press_key([])
        assert result["success"] is False


class TestActivateApp:
    def test_browser_names_route_to_launch_browser(self, actuator):
        for name in ("Safari", "Google Chrome", "browser", "Chromium", "Firefox"):
            with patch(
                "automation_agent.sandbox.docker_actuator.subprocess.run", return_value=_ok()
            ) as run:
                result = actuator.activate_app(name)
            assert result["success"] is True, name
            cmd = run.call_args[0][0]
            assert "launch-browser" in cmd, name

    def test_calculator_alias_spawns_galculator(self, actuator):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run",
            return_value=_fail(),  # xdotool search finds no existing window
        ), patch(
            "automation_agent.sandbox.docker_actuator.subprocess.Popen"
        ) as popen:
            result = actuator.activate_app("Calculator")
        assert result["success"] is True
        spawn_cmd = popen.call_args[0][0]
        assert "galculator" in spawn_cmd

    def test_existing_window_is_focused_not_respawned(self, actuator):
        # xdotool search returns a window id -> windowactivate path
        def fake_run(cmd, **kwargs):
            if "search" in cmd:
                return _ok("31457282\n")
            return _ok()

        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", side_effect=fake_run
        ) as run, patch(
            "automation_agent.sandbox.docker_actuator.subprocess.Popen"
        ) as popen:
            result = actuator.activate_app("Calculator")
        assert result["success"] is True
        popen.assert_not_called()
        all_cmds = [c[0][0] for c in run.call_args_list]
        assert any("windowactivate" in cmd for cmd in all_cmds)

    def test_unknown_app_fails_cleanly(self, actuator):
        def fake_run(cmd, **kwargs):
            return _fail("not found")

        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", side_effect=fake_run
        ):
            result = actuator.activate_app("Photoshop")
        assert result["success"] is False
        assert "Photoshop" in result["error"]


class TestOpenUrl:
    def test_open_url_uses_launch_browser(self, actuator):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", return_value=_ok()
        ) as run:
            result = actuator.open_url("http://localhost:8000/search.html")
        assert result["success"] is True
        cmd = run.call_args[0][0]
        assert "launch-browser" in cmd
        assert cmd[-1] == "http://localhost:8000/search.html"


class TestScroll:
    @pytest.mark.parametrize(
        "clicks,horizontal,button",
        [
            (3, False, "4"),   # positive vertical = up
            (-3, False, "5"),  # negative vertical = down
            (2, True, "7"),    # positive horizontal = right
            (-2, True, "6"),   # negative horizontal = left
        ],
    )
    def test_scroll_buttons(self, actuator, clicks, horizontal, button):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", return_value=_ok()
        ) as run:
            result = actuator.scroll(clicks, horizontal=horizontal)
        assert result["success"] is True
        cmd = run.call_args[0][0]
        assert button in cmd
        assert "--repeat" in cmd
        assert str(abs(clicks)) in cmd

    def test_scroll_without_coords_centers_pointer_first(self, actuator):
        """Wheel events land wherever the cursor is — a stale position over
        browser chrome scrolls nothing. Default to the page center."""

        def fake_run(cmd, **kwargs):
            if "getdisplaygeometry" in " ".join(cmd):
                return _ok("1024 768\n")
            return _ok()

        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", side_effect=fake_run
        ) as run:
            result = actuator.scroll(-3)
        assert result["success"] is True
        cmd = run.call_args[0][0]
        assert "mousemove" in cmd
        assert "512" in cmd  # display center x
        assert cmd.index("mousemove") < cmd.index("click")

    def test_scroll_moves_mouse_first_when_coords_given(self, actuator):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", return_value=_ok()
        ) as run:
            actuator.scroll(-2, x=100, y=200)
        cmd = run.call_args[0][0]
        assert "mousemove" in cmd
        assert cmd.index("mousemove") < cmd.index("click")


class TestGetState:
    def test_browser_state_merges_xdotool_and_cdp(self, actuator, fake_cdp):
        fake_cdp.active_page.return_value = {
            "title": "Sandbox Test Portal",
            "url": "http://localhost:8000/",
        }
        fake_cdp.batch_state.return_value = {
            "focused_value": "blue widgets",
            "selected_text": "",
            "page_title": "Sandbox Test Portal",
            "page_heading": "Sandbox Test Portal",
        }

        def fake_run(cmd, **kwargs):
            joined = " ".join(cmd)
            if "getwindowname" in joined:
                return _ok("Sandbox Test Portal - Chromium\n")
            if "WM_CLASS" in joined:
                return _ok('WM_CLASS(STRING) = "chromium", "Chromium"\n')
            if "getwindowgeometry" in joined:
                return _ok("WINDOW=123\nX=0\nY=0\nWIDTH=1024\nHEIGHT=768\n")
            return _ok()

        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", side_effect=fake_run
        ):
            state = actuator.get_state()

        # Verifier compatibility: name must contain a known browser token
        assert "chrome" in state["app_name"].lower()
        assert state["browser_url"] == "http://localhost:8000/"
        assert state["page_title"] == "Sandbox Test Portal"
        assert state["focused_value"] == "blue widgets"
        assert state["window_w"] == 1024
        assert state["window_h"] == 768

    def test_profile_dir_in_wm_class_instance_still_detects_browser(self, actuator, fake_cdp):
        """Real chromium reports instance='chromium (/tmp/chromium-profile)'."""
        fake_cdp.active_page.return_value = {"title": "P", "url": "http://localhost:8000/x"}
        fake_cdp.batch_state.return_value = {"page_title": "P"}

        def fake_run(cmd, **kwargs):
            joined = " ".join(cmd)
            if "getwindowname" in joined:
                return _ok("Product Search - Chromium\n")
            if "WM_CLASS" in joined:
                return _ok('WM_CLASS(STRING) = "chromium (/tmp/chromium-profile)", "Chromium"\n')
            if "getwindowgeometry" in joined:
                return _ok("X=0\nY=0\nWIDTH=1024\nHEIGHT=768\n")
            return _ok()

        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", side_effect=fake_run
        ):
            state = actuator.get_state()

        assert state["browser_url"] == "http://localhost:8000/x"
        assert state["page_title"] == "P"
        assert "chrome" in state["app_name"].lower()

    def test_calculator_window_maps_to_friendly_name(self, actuator, fake_cdp):
        def fake_run(cmd, **kwargs):
            joined = " ".join(cmd)
            if "getwindowname" in joined:
                return _ok("galculator\n")
            if "WM_CLASS" in joined:
                return _ok('WM_CLASS(STRING) = "galculator", "Galculator"\n')
            if "getwindowgeometry" in joined:
                return _ok("X=10\nY=20\nWIDTH=300\nHEIGHT=400\n")
            return _ok()

        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", side_effect=fake_run
        ):
            state = actuator.get_state()

        # "Calculator" must be a substring so Tier-1 app matching works
        assert "calculator" in state["app_name"].lower()
        assert state["browser_url"] == ""

    def test_state_survives_total_failure(self, actuator, fake_cdp):
        fake_cdp.active_page.side_effect = Exception("cdp down")
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run",
            side_effect=Exception("docker down"),
        ):
            state = actuator.get_state()
        assert state["app_name"] == ""
        assert state["browser_url"] == ""


class TestScrollPosition:
    def test_get_scroll_position_from_cdp(self, actuator, fake_cdp):
        fake_cdp.scroll_position.return_value = 540
        assert actuator.get_scroll_position(axis="y") == 540
        fake_cdp.scroll_position.assert_called_with("y")

    def test_get_scroll_position_invalid_axis_raises(self, actuator):
        with pytest.raises(ValueError):
            actuator.get_scroll_position(axis="z")

    def test_get_scroll_position_none_when_cdp_down(self, actuator, fake_cdp):
        fake_cdp.scroll_position.side_effect = Exception("down")
        assert actuator.get_scroll_position(axis="y") is None


class TestAvailability:
    def test_is_available_true_when_container_responds(self, actuator):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", return_value=_ok()
        ):
            assert actuator.is_available() is True

    def test_is_available_false_when_container_missing(self, actuator):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run",
            return_value=_fail("No such container"),
        ):
            assert actuator.is_available() is False


class TestQuitApp:
    def test_quit_browser_kills_chromium(self, actuator):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", return_value=_ok()
        ) as run:
            result = actuator.quit_app("Safari")
        assert result["success"] is True
        joined = " ".join(run.call_args[0][0])
        assert "pkill" in joined and "chromium" in joined

    def test_quit_generic_app_closes_windows(self, actuator):
        with patch(
            "automation_agent.sandbox.docker_actuator.subprocess.run", return_value=_ok()
        ) as run:
            result = actuator.quit_app("Calculator")
        assert result["success"] is True
        joined = " ".join(run.call_args[0][0])
        assert "galculator" in joined


class TestFactory:
    def test_create_actuator_returns_sandbox_backend_when_configured(self):
        from automation_agent.actuator import create_actuator
        from automation_agent.config import AgentConfig

        config = AgentConfig(model_provider="local", actuator_backend="sandbox")
        actuator = create_actuator(config)
        assert isinstance(actuator, SandboxActuator)

    def test_create_actuator_defaults_to_applescript(self):
        from automation_agent.actuator import create_actuator
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator
        from automation_agent.config import AgentConfig

        config = AgentConfig(model_provider="local")
        actuator = create_actuator(config)
        assert isinstance(actuator, AppleScriptActuator)

    def test_sandbox_config_defaults(self):
        from automation_agent.config import AgentConfig

        config = AgentConfig(model_provider="local")
        assert config.actuator_backend == "applescript"
        assert config.sandbox_container == "agent-sandbox"
        assert config.sandbox_cdp_port == 19222
