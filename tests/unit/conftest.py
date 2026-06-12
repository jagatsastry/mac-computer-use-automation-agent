"""Unit-test isolation: keep the developer's environment out of the suite.

Unit tests must be hermetic. Without this, AGENT_* variables exported in the
shell or set in the repo's .env file (e.g. AGENT_MODEL_PROVIDER=gemini,
AGENT_GROUNDING_MODEL_PROVIDER=openai) leak into pydantic-settings and
reroute code paths — historically causing unit tests to make live API calls.

Tests that need specific settings set them explicitly via monkeypatch or
AgentConfig(...) kwargs, which still works: this fixture only removes
ambient state before each test runs.
"""

import os

import pytest

# Pre-import numpy before any test runs. Several tests wrap bodies in
# patch.dict("sys.modules", ...); if numpy's FIRST import happens inside
# such a window, the exit-restore erases numpy's modules while its C
# extension stays initialized — and numpy>=2.1 then fails every later
# import with "cannot load module more than once per process".
try:
    import numpy  # noqa: F401
except ImportError:
    pass

from automation_agent.config import AgentConfig


@pytest.fixture(autouse=True)
def _isolate_agent_environment(monkeypatch):
    """Strip AGENT_* env vars and disable .env loading for unit tests."""
    for var in list(os.environ):
        if var.startswith("AGENT_"):
            monkeypatch.delenv(var, raising=False)
    # pydantic-settings reads env_file from model_config at instantiation;
    # setitem is restored by monkeypatch after each test.
    monkeypatch.setitem(AgentConfig.model_config, "env_file", None)
