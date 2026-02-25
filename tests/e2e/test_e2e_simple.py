import pytest
from tests.e2e.conftest import requires_hammerspoon, requires_anthropic


@pytest.mark.e2e
class TestSimpleE2E:
    """Simple end-to-end tests requiring real macOS desktop + Hammerspoon + API key."""

    @requires_hammerspoon()
    @requires_anthropic()
    async def test_open_calculator(self, real_agent, real_actuator):
        """Open Calculator app and verify it's running."""
        result = await real_agent.execute("Open Calculator")
        assert result.success, f"Failed: {result.error or result.message}"
        # Every step has real evidence
        for sr in result.steps:
            assert sr.evidence, f"Step {sr.step.action} has no evidence"
        # Independent verification
        state = real_actuator.get_state()
        assert "Calculator" in state.get("app_name", ""), f"Calculator not frontmost: {state}"
        # Cleanup
        real_actuator.quit_app("Calculator")

    @requires_hammerspoon()
    @requires_anthropic()
    async def test_open_safari(self, real_agent, real_actuator):
        """Open Safari and verify it's running."""
        result = await real_agent.execute("Open Safari")
        assert result.success
        for sr in result.steps:
            assert sr.evidence
        state = real_actuator.get_state()
        assert "Safari" in state.get("app_name", "")
