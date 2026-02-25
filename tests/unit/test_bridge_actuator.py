"""Unit tests for HammerspoonBridgeActuator.

All HTTP calls are mocked — no real Hammerspoon needed.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from automation_agent.actuator.bridge_actuator import HammerspoonBridgeActuator


@pytest.fixture
def bridge():
    return HammerspoonBridgeActuator(port=27741)


class TestIsAvailable:
    def test_available_when_health_returns_ok(self, bridge):
        with patch("httpx.Client") as MockClient:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"status": "ok", "configured": True}
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.get.return_value = mock_resp

            assert bridge.is_available() is True

    def test_unavailable_on_connection_error(self, bridge):
        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.get.side_effect = Exception("Connection refused")

            assert bridge.is_available() is False


class TestClick:
    def test_click_sends_correct_params(self, bridge):
        with patch.object(bridge, "_call", return_value={"success": True, "result": "clicked"}) as mock_call:
            result = bridge.click(500, 300)

        mock_call.assert_called_once_with("click", {"x": 500, "y": 300})
        assert result["success"] is True

    def test_click_failure(self, bridge):
        with patch.object(bridge, "_call", return_value={"success": False, "error": "out of bounds"}):
            result = bridge.click(-1, -1)

        assert result["success"] is False
        assert "out of bounds" in result["error"]


class TestTypeText:
    def test_type_text_sends_text(self, bridge):
        with patch.object(bridge, "_call", return_value={"success": True, "result": ""}) as mock_call:
            result = bridge.type_text("hello world")

        mock_call.assert_called_once_with("typeText", {"text": "hello world"})
        assert result["success"] is True


class TestPressKey:
    def test_press_key_with_modifiers(self, bridge):
        with patch.object(bridge, "_call", return_value={"success": True, "result": ""}) as mock_call:
            result = bridge.press_key(["cmd", "c"])

        mock_call.assert_called_once_with("pressKey", {"key": "c", "modifiers": ["cmd"]})
        assert result["success"] is True

    def test_press_key_no_key_only_modifiers(self, bridge):
        result = bridge.press_key(["cmd", "shift"])
        assert result["success"] is False
        assert "No key" in result["error"]

    def test_press_key_single_key(self, bridge):
        with patch.object(bridge, "_call", return_value={"success": True, "result": ""}) as mock_call:
            result = bridge.press_key(["Return"])

        mock_call.assert_called_once_with("pressKey", {"key": "Return", "modifiers": []})


class TestActivateApp:
    def test_activate_app(self, bridge):
        with patch.object(bridge, "_call", return_value={"success": True, "result": "activated"}) as mock_call:
            result = bridge.activate_app("Safari")

        mock_call.assert_called_once_with("activateApp", {"appName": "Safari"})
        assert result["success"] is True


class TestOpenUrl:
    def test_open_url(self, bridge):
        with patch.object(bridge, "_call", return_value={"success": True, "result": ""}) as mock_call:
            result = bridge.open_url("https://example.com")

        mock_call.assert_called_once_with("openUrl", {"url": "https://example.com"})


class TestGetState:
    def test_get_state_success(self, bridge):
        with patch.object(bridge, "_call", return_value={
            "success": True,
            "result": {"appName": "Safari", "appBundle": "com.apple.Safari", "windowTitle": "Google"}
        }):
            state = bridge.get_state()

        assert state["app_name"] == "Safari"
        assert state["app_bundle"] == "com.apple.Safari"
        assert state["window_title"] == "Google"

    def test_get_state_failure_returns_empty(self, bridge):
        with patch.object(bridge, "_call", return_value={"success": False, "error": "not connected"}):
            state = bridge.get_state()

        assert state["app_name"] == ""
        assert state["app_bundle"] == ""

    def test_get_state_handles_string_result(self, bridge):
        with patch.object(bridge, "_call", return_value={
            "success": True,
            "result": '{"app_name": "Finder", "app_bundle": "com.apple.Finder", "window_title": "Desktop"}'
        }):
            state = bridge.get_state()

        assert state["app_name"] == "Finder"


class TestExtendedCapabilities:
    def test_describe_screen(self, bridge):
        with patch.object(bridge, "_call", return_value={
            "success": True, "result": "Safari showing Google homepage"
        }):
            desc = bridge.describe_screen()

        assert "Safari" in desc

    def test_find_element_found(self, bridge):
        with patch.object(bridge, "_call", return_value={
            "success": True,
            "result": {"logicalX": 500, "logicalY": 300, "normalizedX": 330, "normalizedY": 305}
        }):
            coords = bridge.find_element("the search button")

        assert coords is not None
        assert coords["x"] == 500
        assert coords["y"] == 300

    def test_find_element_not_found(self, bridge):
        with patch.object(bridge, "_call", return_value={"success": False, "result": None}):
            coords = bridge.find_element("nonexistent element")

        assert coords is None

    def test_check_condition_true(self, bridge):
        with patch.object(bridge, "_call", return_value={"success": True, "result": True}):
            assert bridge.check_condition("Safari is open") is True

    def test_check_condition_false(self, bridge):
        with patch.object(bridge, "_call", return_value={"success": True, "result": False}):
            assert bridge.check_condition("Safari is open") is False

    def test_execute_goal(self, bridge):
        with patch.object(bridge, "_call", return_value={
            "success": True,
            "result": {"success": True, "message": "Done", "iterations": 3}
        }):
            result = bridge.execute_goal("Open Safari and search for cats")

        assert result["success"] is True


class TestConnectionErrors:
    def test_timeout_returns_error(self, bridge):
        import httpx
        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.post.side_effect = httpx.TimeoutException("timed out")

            result = bridge._call("click", {"x": 100, "y": 200})

        assert result["success"] is False
        assert "timeout" in result["error"].lower()

    def test_connection_refused_returns_error(self, bridge):
        import httpx
        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.post.side_effect = httpx.ConnectError("refused")

            result = bridge._call("click", {"x": 100, "y": 200})

        assert result["success"] is False
        assert "connect" in result["error"].lower()
