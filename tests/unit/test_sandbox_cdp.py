"""Unit tests for the CDP (Chrome DevTools Protocol) client used by the sandbox."""

import json
from unittest.mock import MagicMock, patch

from automation_agent.sandbox.cdp import CdpClient


def _http_response(payload):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: False
    return resp


PAGES = [
    {"type": "iframe", "title": "ad", "url": "http://ads", "webSocketDebuggerUrl": "ws://x/1"},
    {
        "type": "page",
        "title": "Search Results",
        "url": "http://localhost:8000/results.html?q=mugs",
        "webSocketDebuggerUrl": "ws://127.0.0.1:19222/devtools/page/AAA",
    },
]


class TestActivePage:
    def test_picks_first_page_target(self):
        client = CdpClient(port=19222)
        with patch(
            "automation_agent.sandbox.cdp.urllib.request.urlopen",
            return_value=_http_response(PAGES),
        ):
            page = client.active_page()
        assert page["title"] == "Search Results"
        assert page["url"].startswith("http://localhost:8000/results.html")

    def test_returns_none_when_cdp_unreachable(self):
        client = CdpClient(port=19222)
        with patch(
            "automation_agent.sandbox.cdp.urllib.request.urlopen",
            side_effect=OSError("refused"),
        ):
            assert client.active_page() is None

    def test_returns_none_when_no_page_targets(self):
        client = CdpClient(port=19222)
        with patch(
            "automation_agent.sandbox.cdp.urllib.request.urlopen",
            return_value=_http_response([{"type": "service_worker"}]),
        ):
            assert client.active_page() is None


class TestEvaluate:
    def test_evaluate_returns_value_via_websocket(self):
        client = CdpClient(port=19222)
        ws = MagicMock()
        ws.recv.return_value = json.dumps(
            {"id": 1, "result": {"result": {"type": "number", "value": 540}}}
        )
        with patch(
            "automation_agent.sandbox.cdp.urllib.request.urlopen",
            return_value=_http_response(PAGES),
        ), patch.object(CdpClient, "_connect_ws", return_value=ws):
            value = client.evaluate("window.scrollY")
        assert value == 540
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["method"] == "Runtime.evaluate"
        assert sent["params"]["expression"] == "window.scrollY"
        ws.close.assert_called_once()

    def test_evaluate_none_on_websocket_error(self):
        client = CdpClient(port=19222)
        with patch(
            "automation_agent.sandbox.cdp.urllib.request.urlopen",
            return_value=_http_response(PAGES),
        ), patch.object(CdpClient, "_connect_ws", side_effect=OSError("ws down")):
            assert client.evaluate("1+1") is None


class TestBatchState:
    def test_batch_state_parses_json_payload(self):
        client = CdpClient(port=19222)
        payload = {
            "focused_value": "alice",
            "selected_text": "",
            "page_title": "Contact Form",
            "page_heading": "Contact Form",
        }
        with patch.object(CdpClient, "evaluate", return_value=json.dumps(payload)):
            state = client.batch_state()
        assert state == payload

    def test_batch_state_empty_on_failure(self):
        client = CdpClient(port=19222)
        with patch.object(CdpClient, "evaluate", return_value=None):
            assert client.batch_state() == {}


class TestScrollPosition:
    def test_scroll_position_casts_to_int(self):
        client = CdpClient(port=19222)
        with patch.object(CdpClient, "evaluate", return_value=412.0):
            assert client.scroll_position("y") == 412

    def test_scroll_position_none_when_unavailable(self):
        client = CdpClient(port=19222)
        with patch.object(CdpClient, "evaluate", return_value=None):
            assert client.scroll_position("x") is None
