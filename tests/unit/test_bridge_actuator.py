"""Unit tests for HammerspoonBridgeActuator.

All HTTP calls are mocked — no real Hammerspoon needed.
"""

import time
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
    def test_click_sends_correct_action(self, bridge):
        with patch.object(bridge, "_action", return_value={"success": True, "result": {"action": "click"}}) as mock:
            result = bridge.click(500, 300)

        mock.assert_called_once_with("click", {"x": 500, "y": 300})
        assert result["success"] is True

    def test_click_failure(self, bridge):
        with patch.object(bridge, "_action", return_value={"success": False, "error": "out of bounds"}):
            result = bridge.click(-1, -1)

        assert result["success"] is False
        assert "out of bounds" in result["error"]


class TestTypeText:
    def test_type_text_sends_text(self, bridge):
        with patch.object(bridge, "_action", return_value={"success": True, "result": {}}) as mock:
            result = bridge.type_text("hello world")

        mock.assert_called_once_with("type_text", {"text": "hello world"})
        assert result["success"] is True


class TestPressKey:
    def test_press_key_with_modifiers(self, bridge):
        with patch.object(bridge, "_action", return_value={"success": True, "result": {}}) as mock:
            result = bridge.press_key(["cmd", "c"])

        mock.assert_called_once_with("press_key", {"key": "c", "modifiers": ["cmd"]})
        assert result["success"] is True

    def test_press_key_no_key_only_modifiers(self, bridge):
        result = bridge.press_key(["cmd", "shift"])
        assert result["success"] is False
        assert "No key" in result["error"]

    def test_press_key_single_key(self, bridge):
        with patch.object(bridge, "_action", return_value={"success": True, "result": {}}) as mock:
            result = bridge.press_key(["Return"])

        mock.assert_called_once_with("press_key", {"key": "Return", "modifiers": []})


class TestActivateApp:
    def test_activate_app(self, bridge):
        with patch.object(bridge, "_action", return_value={"success": True, "result": {}}) as mock:
            result = bridge.activate_app("Safari")

        mock.assert_called_once_with("activate_app", {"appName": "Safari"})
        assert result["success"] is True


class TestOpenUrl:
    def test_open_url(self, bridge):
        with patch.object(bridge, "_action", return_value={"success": True, "result": {}}) as mock:
            result = bridge.open_url("https://example.com")

        mock.assert_called_once_with("open_url", {"url": "https://example.com"})


class TestQuitApp:
    def test_quit_app(self, bridge):
        with patch.object(bridge, "_action", return_value={"success": True, "result": {}}) as mock:
            result = bridge.quit_app("Safari")

        mock.assert_called_once_with("quit_app", {"appName": "Safari"})
        assert result["success"] is True


class TestGetState:
    def test_get_state_success(self, bridge):
        with patch("httpx.Client") as MockClient:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "success": True,
                "result": {"frontmostApp": "Safari", "frontmostAppError": None, "screen": {}}
            }
            mock_resp.raise_for_status = MagicMock()
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.get.return_value = mock_resp

            state = bridge.get_state()

        assert state["app_name"] == "Safari"

    def test_get_state_failure_returns_empty(self, bridge):
        with patch("httpx.Client") as MockClient:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"success": False, "error": "not connected"}
            mock_resp.raise_for_status = MagicMock()
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.get.return_value = mock_resp

            state = bridge.get_state()

        assert state["app_name"] == ""

    def test_get_state_connection_error(self, bridge):
        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.get.side_effect = Exception("Connection refused")

            state = bridge.get_state()

        assert state["app_name"] == ""
        assert state["app_bundle"] == ""


class TestActionEndpoint:
    def test_action_sends_post_to_action_endpoint(self, bridge):
        with patch("httpx.Client") as MockClient:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"success": True, "result": {"action": "click"}}
            mock_resp.raise_for_status = MagicMock()
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.post.return_value = mock_resp

            result = bridge._action("click", {"x": 100, "y": 200})

        MockClient.return_value.post.assert_called_once_with(
            "http://localhost:27741/action",
            json={"action": "click", "params": {"x": 100, "y": 200}},
        )
        assert result["success"] is True

    def test_action_without_params(self, bridge):
        with patch("httpx.Client") as MockClient:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"success": True}
            mock_resp.raise_for_status = MagicMock()
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.post.return_value = mock_resp

            result = bridge._action("wait")

        MockClient.return_value.post.assert_called_once_with(
            "http://localhost:27741/action",
            json={"action": "wait"},
        )


class TestAsyncVisionOps:
    def test_describe_screen_polls_and_returns(self, bridge):
        with patch("httpx.Client") as MockClient:
            # First call: POST /describe returns taskId
            post_resp = MagicMock()
            post_resp.json.return_value = {"success": True, "taskId": "1", "status": "pending"}
            post_resp.raise_for_status = MagicMock()

            # Second call: GET /task/1 returns completed
            poll_resp = MagicMock()
            poll_resp.json.return_value = {
                "success": True, "taskId": "1", "status": "completed",
                "result": {"description": "Safari showing Google homepage"}
            }
            poll_resp.raise_for_status = MagicMock()

            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.post.return_value = post_resp
            MockClient.return_value.get.return_value = poll_resp

            desc = bridge.describe_screen()

        assert "Safari" in desc

    def test_find_element_found(self, bridge):
        with patch.object(bridge, "_async_call", return_value={
            "success": True,
            "result": {"coordinates": {"logicalX": 500, "logicalY": 300, "normalizedX": 330}}
        }):
            coords = bridge.find_element("the search button")

        assert coords is not None
        assert coords["x"] == 500
        assert coords["y"] == 300

    def test_find_element_not_found(self, bridge):
        with patch.object(bridge, "_async_call", return_value={"success": False, "result": None}):
            coords = bridge.find_element("nonexistent element")

        assert coords is None

    def test_check_condition_true(self, bridge):
        with patch.object(bridge, "_async_call", return_value={
            "success": True, "result": {"conditionMet": True}
        }):
            assert bridge.check_condition("Safari is open") is True

    def test_check_condition_false(self, bridge):
        with patch.object(bridge, "_async_call", return_value={
            "success": True, "result": {"conditionMet": False}
        }):
            assert bridge.check_condition("Safari is open") is False

    def test_execute_goal(self, bridge):
        with patch.object(bridge, "_async_call", return_value={
            "success": True,
            "result": {"success": True, "message": "Done", "iterations": 3}
        }):
            result = bridge.execute_goal("Open Safari and search for cats")

        assert result["success"] is True


class TestAsyncPolling:
    def test_poll_returns_on_completed(self, bridge):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "success": True, "taskId": "5", "status": "completed",
            "result": {"description": "test"}
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_resp

        result = bridge._poll_task(mock_client, "5")
        assert result["success"] is True
        assert result["status"] == "completed"

    def test_poll_returns_on_failed(self, bridge):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "success": False, "taskId": "5", "status": "failed",
            "error": "API error"
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_resp

        result = bridge._poll_task(mock_client, "5")
        assert result["success"] is False
        assert result["status"] == "failed"

    def test_poll_waits_then_completes(self, bridge):
        mock_client = MagicMock()

        pending_resp = MagicMock()
        pending_resp.json.return_value = {"success": True, "taskId": "5", "status": "pending"}
        pending_resp.raise_for_status = MagicMock()

        done_resp = MagicMock()
        done_resp.json.return_value = {
            "success": True, "taskId": "5", "status": "completed",
            "result": {"description": "done"}
        }
        done_resp.raise_for_status = MagicMock()

        mock_client.get.side_effect = [pending_resp, done_resp]

        with patch("time.sleep"):
            result = bridge._poll_task(mock_client, "5")

        assert result["success"] is True
        assert mock_client.get.call_count == 2

    def test_poll_timeout(self, bridge):
        bridge.POLL_TIMEOUT = 0.1  # Very short timeout for test
        bridge.POLL_INTERVAL = 0.01

        mock_client = MagicMock()
        pending_resp = MagicMock()
        pending_resp.json.return_value = {"success": True, "taskId": "5", "status": "pending"}
        pending_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = pending_resp

        result = bridge._poll_task(mock_client, "5")
        assert result["success"] is False
        assert "timed out" in result["error"]


class TestConnectionErrors:
    def test_action_timeout_returns_error(self, bridge):
        import httpx
        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.post.side_effect = httpx.TimeoutException("timed out")

            result = bridge._action("click", {"x": 100, "y": 200})

        assert result["success"] is False
        assert "timeout" in result["error"].lower()

    def test_action_connection_refused_returns_error(self, bridge):
        import httpx
        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.post.side_effect = httpx.ConnectError("refused")

            result = bridge._action("click", {"x": 100, "y": 200})

        assert result["success"] is False
        assert "connect" in result["error"].lower()

    def test_async_timeout_returns_error(self, bridge):
        import httpx
        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.post.side_effect = httpx.TimeoutException("timed out")

            result = bridge._async_call("/describe")

        assert result["success"] is False
        assert "timeout" in result["error"].lower()

    def test_async_connection_refused_returns_error(self, bridge):
        import httpx
        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(return_value=MockClient.return_value)
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            MockClient.return_value.post.side_effect = httpx.ConnectError("refused")

            result = bridge._async_call("/findElement", {"description": "button"})

        assert result["success"] is False
        assert "connect" in result["error"].lower()
