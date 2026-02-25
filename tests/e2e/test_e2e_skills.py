import pytest
from tests.e2e.conftest import requires_hammerspoon, requires_anthropic


@pytest.mark.e2e
class TestSkillsE2E:
    @requires_hammerspoon()
    @requires_anthropic()
    async def test_skill_based_google_search(self, real_agent):
        """Test that a skill-matched task uses skill context."""
        result = await real_agent.execute("Search Google for Python tutorials")
        assert len(result.steps) > 0
        for sr in result.steps:
            assert sr.evidence

    def test_skill_matching_offline(self):
        """Verify skill matching works without API (offline test)."""
        from automation_agent.skills import SkillRegistryImpl
        registry = SkillRegistryImpl()
        match = registry.match("return my blue headphones on Amazon")
        assert match is not None
        assert match["skill_name"] == "return-amazon-order"
