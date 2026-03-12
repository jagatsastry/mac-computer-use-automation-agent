import pytest
from tests.e2e.conftest import requires_actuator, requires_anthropic


@pytest.mark.e2e
class TestSkillsE2E:
    @requires_actuator()
    @requires_anthropic()
    async def test_skill_based_google_search(self, real_agent):
        """Test that a skill-matched task uses skill context."""
        result = await real_agent.execute("Search Google for Python tutorials")
        assert len(result.steps) > 0
        for sr in result.steps:
            assert sr.evidence

    @pytest.mark.asyncio
    async def test_skill_matching_offline(self):
        """Offline keyword fallback matches return-amazon-order for Amazon return prompts."""
        from automation_agent.skills import SkillRegistryImpl
        registry = SkillRegistryImpl()
        match = await registry.match("return my blue headphones on Amazon")
        # With the improved router, keyword fallback correctly matches this prompt
        assert match is not None
        assert match["skill_name"] == "return-amazon-order"
