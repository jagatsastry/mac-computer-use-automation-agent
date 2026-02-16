"""Tests for restaurant workflow behavior."""

import types
import pytest

from automation_agent.workflows import restaurant


@pytest.mark.asyncio
async def test_ask_user_non_interactive_uses_default(monkeypatch):
    """Non-interactive mode should not block for input."""
    fake_stdin = types.SimpleNamespace(isatty=lambda: False)
    monkeypatch.setattr(restaurant.sys, "stdin", fake_stdin)

    result = await restaurant._ask_user("Cuisine?", default="italian")
    assert result == "italian"
