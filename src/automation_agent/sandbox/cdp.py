"""Minimal Chrome DevTools Protocol client for sandbox browser state.

Connects to the CDP endpoint that the sandbox container publishes
(socat-forwarded chromium remote debugging port). Used to populate
actuator get_state() fields — browser_url, page_title, focused_value,
selected_text, page_heading — so Tier-1 verification works without
vision calls, matching the AppleScript actuator's JS injection batch.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

import structlog

slog = structlog.get_logger(__name__)

# Single batched expression, mirroring AppleScriptActuator._get_browser_js_batch
_BATCH_JS = (
    "JSON.stringify({focused_value: (document.activeElement "
    "? (document.activeElement.value || document.activeElement.textContent || '') "
    ": ''), selected_text: (window.getSelection "
    "? window.getSelection().toString() : ''), "
    "page_title: document.title || '', "
    "page_heading: (document.querySelector('h1') "
    "? document.querySelector('h1').textContent : '') || ''})"
)


class CdpClient:
    """Talks to chromium's DevTools endpoint on the published sandbox port."""

    def __init__(self, host: str = "127.0.0.1", port: int = 19222, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def active_page(self) -> dict[str, Any] | None:
        """Return the first page-type CDP target (most recently used), or None."""
        url = f"http://{self.host}:{self.port}/json"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                targets = json.loads(resp.read().decode())
        except Exception as exc:
            slog.debug("cdp_http_unreachable", error=str(exc))
            return None
        for target in targets:
            if target.get("type") == "page":
                return target
        return None

    def _connect_ws(self, ws_url: str):
        """Open a websocket to a CDP target. Separated for testability."""
        import websocket  # websocket-client; host-side dev dependency

        return websocket.create_connection(ws_url, timeout=self.timeout)

    def evaluate(self, expression: str) -> Any | None:
        """Run a JS expression in the active page; return its value or None."""
        page = self.active_page()
        if not page:
            return None
        ws_url = page.get("webSocketDebuggerUrl")
        if not ws_url:
            return None
        try:
            ws = self._connect_ws(ws_url)
        except Exception as exc:
            slog.debug("cdp_ws_connect_failed", error=str(exc))
            return None
        try:
            ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {"expression": expression, "returnByValue": True},
                    }
                )
            )
            # CDP may interleave events; scan a bounded number of frames
            for _ in range(10):
                message = json.loads(ws.recv())
                if message.get("id") == 1:
                    return message.get("result", {}).get("result", {}).get("value")
            return None
        except Exception as exc:
            slog.debug("cdp_evaluate_failed", error=str(exc))
            return None
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def batch_state(self) -> dict[str, str]:
        """Fetch focused_value/selected_text/page_title/page_heading in one eval."""
        raw = self.evaluate(_BATCH_JS)
        if not raw or not isinstance(raw, str):
            return {}
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def scroll_position(self, axis: str) -> int | None:
        """Return window.scrollX/scrollY as int, or None when unavailable."""
        prop = "window.scrollX" if axis == "x" else "window.scrollY"
        value = self.evaluate(prop)
        if value is None:
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
