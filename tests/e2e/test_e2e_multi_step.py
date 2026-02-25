import pytest
from tests.e2e.conftest import requires_hammerspoon, requires_anthropic


@pytest.mark.e2e
class TestMultiStepE2E:
    @requires_hammerspoon()
    @requires_anthropic()
    async def test_google_search(self, real_agent):
        """Open Safari, navigate to Google, search for weather."""
        result = await real_agent.execute("Search Google for weather in San Francisco")
        # This may partially succeed -- check that at least some steps executed
        assert len(result.steps) > 0, "No steps executed"
        for sr in result.steps:
            assert sr.evidence, f"Step {sr.step.action} missing evidence"
