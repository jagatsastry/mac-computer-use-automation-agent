"""Tests for restaurant workflow behavior."""

import types
import pytest
from pathlib import Path

from automation_agent.workflows import restaurant


@pytest.mark.asyncio
async def test_ask_user_non_interactive_uses_default(monkeypatch):
    """Non-interactive mode should not block for input."""
    fake_stdin = types.SimpleNamespace(isatty=lambda: False)
    monkeypatch.setattr(restaurant.sys, "stdin", fake_stdin)

    result = await restaurant._ask_user("Cuisine?", default="italian")
    assert result == "italian"


def test_upsert_memory_handles_numeric_values(tmp_path: Path):
    """Updating numeric values should not trigger regex group-reference errors."""
    memory_path = tmp_path / "MEMORY.md"
    memory_path.write_text(
        "# User Memory\n\n"
        "## Latest Preferences\n"
        "- **Cuisine**: italian\n"
        "- **Location**: San Jose\n"
        "- **DateTime**: tonight at 7pm\n"
        "- **PartySize**: 4\n\n"
        "## Decision History\n"
        "- seeded\n",
        encoding="utf-8",
    )

    req = restaurant.RestaurantRequest(
        cuisine="sushi",
        location="Palo Alto",
        date_time="tomorrow at 8pm",
        party_size="2",  # critical numeric replacement
    )
    restaurant._upsert_memory(memory_path, req, "OpenTable -> demo")

    content = memory_path.read_text(encoding="utf-8")
    assert "- **PartySize**: 2" in content
