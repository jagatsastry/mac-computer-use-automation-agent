"""Unit tests for the skill registry component."""

import json
import logging
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.skills.loader import parse_skill_file
from automation_agent.skills.matcher import match_skill
from automation_agent.skills.models import Skill, SkillParam, SkillRequirements
from automation_agent.skills.registry import SkillRegistryImpl, validate_skill_file
from automation_agent.skills.router import SkillRouter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SKILL_CONTENT = textwrap.dedent("""\
    ---
    name: test-skill
    description: A test skill for unit testing
    trigger-keywords: [test, demo, example]
    parameters:
      query:
        type: string
        required: true
        description: The search query
        examples: ["hello", "world"]
      count:
        type: int
        required: false
        description: Number of results
        examples: ["5", "10"]
    requires:
      apps: [Safari]
      os: darwin
    success-condition: Results are visible on screen
    max-retries: 2
    ---

    ## Steps
    1. Open the app
       - verify: App is open
    2. Search for "{{query}}"
       - verify: Results for "{{query}}" are visible
    3. Show {{count}} results
       - verify: Correct number of results shown

    ## Error Recovery
    - If app doesn't open: try again
    - If search fails: clear and retry

    ## Notes
    - This is a test skill
    - Used only for unit testing
""")


@pytest.fixture
def sample_skill():
    """Parse the sample skill content and return a Skill."""
    return parse_skill_file(SAMPLE_SKILL_CONTENT)


@pytest.fixture
def registry_with_sample(tmp_path):
    """A registry loaded with sample skill files in a temp directory."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "test_skill.md").write_text(SAMPLE_SKILL_CONTENT)
    return SkillRegistryImpl(skill_dir=skill_dir)


# ---------------------------------------------------------------------------
# Test 1: Load skill from string -> verify parsed fields
# ---------------------------------------------------------------------------

class TestLoadSkillFromString:
    def test_parsed_fields(self, sample_skill):
        assert sample_skill.name == "test-skill"
        assert sample_skill.description == "A test skill for unit testing"
        assert sample_skill.trigger_keywords == ["test", "demo", "example"]
        assert "query" in sample_skill.parameters
        assert "count" in sample_skill.parameters
        assert sample_skill.parameters["query"].type == "string"
        assert sample_skill.parameters["query"].required is True
        assert sample_skill.parameters["count"].type == "int"
        assert sample_skill.parameters["count"].required is False
        assert sample_skill.max_retries == 2
        assert sample_skill.success_condition == "Results are visible on screen"


# ---------------------------------------------------------------------------
# Test 2: YAML frontmatter parsed correctly
# ---------------------------------------------------------------------------

class TestYAMLFrontmatter:
    def test_requires_parsed(self, sample_skill):
        assert sample_skill.requires.apps == ["Safari"]
        assert sample_skill.requires.os == "darwin"

    def test_param_examples(self, sample_skill):
        assert sample_skill.parameters["query"].examples == ["hello", "world"]
        assert sample_skill.parameters["query"].description == "The search query"

    def test_missing_frontmatter_raises(self):
        with pytest.raises(ValueError, match="frontmatter"):
            parse_skill_file("No frontmatter here\nJust text")

    def test_missing_name_raises(self):
        content = textwrap.dedent("""\
            ---
            description: No name field
            trigger-keywords: [test]
            ---

            ## Steps
            1. Do something
        """)
        with pytest.raises(ValueError, match="name"):
            parse_skill_file(content)

    def test_missing_description_raises(self):
        content = textwrap.dedent("""\
            ---
            name: no-desc
            trigger-keywords: [test]
            ---

            ## Steps
            1. Do something
        """)
        with pytest.raises(ValueError, match="description"):
            parse_skill_file(content)


# ---------------------------------------------------------------------------
# Test 3: Markdown body sections extracted
# ---------------------------------------------------------------------------

class TestBodySections:
    def test_steps_extracted(self, sample_skill):
        assert '1. Open the app' in sample_skill.steps_text
        assert 'Search for "{{query}}"' in sample_skill.steps_text
        assert 'Show {{count}} results' in sample_skill.steps_text

    def test_error_recovery_extracted(self, sample_skill):
        assert "If app doesn't open: try again" in sample_skill.error_recovery_text
        assert "If search fails: clear and retry" in sample_skill.error_recovery_text

    def test_notes_extracted(self, sample_skill):
        assert "This is a test skill" in sample_skill.notes_text
        assert "Used only for unit testing" in sample_skill.notes_text

    def test_raw_content_preserved(self, sample_skill):
        assert sample_skill.raw_content == SAMPLE_SKILL_CONTENT


# ---------------------------------------------------------------------------
# Test 4: expand() replaces {{param}} with param value
# ---------------------------------------------------------------------------

class TestExpand:
    def test_single_param(self, registry_with_sample):
        expanded = registry_with_sample.expand(
            "test-skill", {"query": "pandas tutorial"}
        )
        assert expanded is not None
        assert "pandas tutorial" in expanded
        assert "{{query}}" not in expanded

    def test_multiple_params(self, registry_with_sample):
        expanded = registry_with_sample.expand(
            "test-skill", {"query": "python docs", "count": "10"}
        )
        assert expanded is not None
        assert "python docs" in expanded
        assert "10" in expanded
        assert "{{query}}" not in expanded
        assert "{{count}}" not in expanded

    def test_unknown_skill_returns_none(self, registry_with_sample):
        result = registry_with_sample.expand("nonexistent-skill", {"query": "test"})
        assert result is None

    def test_expand_blocks_template_injection(self, tmp_path):
        """Verify user-supplied param containing {{...}} is NOT double-substituted."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: inject-test
            description: Template injection test
            trigger-keywords: [inject]
            parameters:
              a:
                type: string
                required: true
              b:
                type: string
                required: true
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Use {{a}} and {{b}}
               - verify: done
        """)
        (skill_dir / "inject.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        # If params["a"] = "{{b}}" and params["b"] = "INJECTED",
        # the output should contain literal "{{b}}", NOT "INJECTED"
        expanded = registry.expand("inject-test", {"a": "{{b}}", "b": "INJECTED"})
        assert expanded is not None
        assert "{{b}}" in expanded
        assert expanded.count("INJECTED") == 1  # only the direct substitution of b


# ---------------------------------------------------------------------------
# Test 5: async match() with LLM router mocked -> correct skill
# ---------------------------------------------------------------------------

class TestMatchWithRouter:
    @pytest.mark.asyncio
    async def test_match_via_router(self, tmp_path):
        """When LLM router returns a result, match() uses it."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "test_skill.md").write_text(SAMPLE_SKILL_CONTENT)

        # Create registry without config (no router) then patch
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        mock_router = AsyncMock()
        mock_router.route.return_value = {
            "skill_name": "test-skill",
            "params": {"query": "pandas tutorial"},
        }
        registry._router = mock_router

        result = await registry.match("I want to test pandas tutorial")
        assert result is not None
        assert result["skill_name"] == "test-skill"
        assert result["params"]["query"] == "pandas tutorial"
        assert "pandas tutorial" in result["expanded_steps"]
        mock_router.route.assert_called_once()

    @pytest.mark.asyncio
    async def test_match_router_returns_none_falls_back_to_keywords(self, tmp_path):
        """When router misses, fallback skips skills it cannot safely expand."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "test_skill.md").write_text(SAMPLE_SKILL_CONTENT)

        registry = SkillRegistryImpl(skill_dir=skill_dir)
        mock_router = AsyncMock()
        mock_router.route.return_value = None
        registry._router = mock_router

        result = await registry.match("I want to test something")
        assert result is None

    @pytest.mark.asyncio
    async def test_match_no_router_uses_keyword_fallback(self, tmp_path):
        """Without a router, fallback still refuses unsafe parameterized skills."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "test_skill.md").write_text(SAMPLE_SKILL_CONTENT)

        registry = SkillRegistryImpl(skill_dir=skill_dir)
        assert registry._router is None  # No config = no router

        result = await registry.match("I want to test something")
        assert result is None

    @pytest.mark.asyncio
    async def test_keyword_fallback_skips_skills_missing_required_params(self, tmp_path):
        """Fallback should not return a skill it cannot safely expand."""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: open-app-and-navigate
            description: Open an app
            trigger-keywords: [open, launch]
            parameters:
              app_name:
                type: string
                required: true
            requires:
              os: darwin
            success-condition: App open
            ---

            ## Steps
            1. Open {{app_name}}
               - verify: app is open
        """)
        (skill_dir / "open_app.md").write_text(content)

        registry = SkillRegistryImpl(skill_dir=skill_dir)

        result = await registry.match("Open Calculator")

        assert result is None


# ---------------------------------------------------------------------------
# Test 6: async match() with no hit -> None
# ---------------------------------------------------------------------------

class TestMatchNoHitAsync:
    @pytest.mark.asyncio
    async def test_no_match(self, registry_with_sample):
        result = await registry_with_sample.match("fly me to the moon")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_prompt(self, registry_with_sample):
        result = await registry_with_sample.match("")
        assert result is None


# ---------------------------------------------------------------------------
# Test 7: keyword fallback match (no param extraction)
# ---------------------------------------------------------------------------

class TestKeywordFallbackMatch:
    def test_keyword_match_returns_empty_params(self):
        """Keyword fallback now returns empty params (no extract_params)."""
        skill = Skill(
            name="test-skill",
            description="A test skill",
            trigger_keywords=["test", "demo"],
            parameters={
                "query": SkillParam(type="string", required=True),
            },
            requires=SkillRequirements(),
            success_condition="Done",
            steps_text="1. Do something",
        )
        result = match_skill("I want to test something", [skill])
        assert result is not None
        matched_skill, params = result
        assert matched_skill.name == "test-skill"
        assert params == {}  # No param extraction in fallback

    def test_keyword_match_case_insensitive(self):
        skill = Skill(
            name="demo-skill",
            description="A demo",
            trigger_keywords=["demo", "example"],
            parameters={},
            requires=SkillRequirements(),
            success_condition="Done",
            steps_text="1. Do it",
        )
        result = match_skill("Run the DEMO please", [skill])
        assert result is not None
        assert result[0].name == "demo-skill"


# ---------------------------------------------------------------------------
# Test 8: validate_all() catches malformed files
# ---------------------------------------------------------------------------

class TestValidateAll:
    def test_valid_skill_no_errors(self, registry_with_sample):
        errors = registry_with_sample.validate_all()
        assert errors == []

    def test_missing_keywords_flagged(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: bad-skill
            description: Missing keywords
            trigger-keywords: []
            parameters: {}
            requires:
              os: darwin
            success-condition: Something visible
            ---

            ## Steps
            1. Do something
               - verify: done
        """)
        (skill_dir / "bad.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        errors = registry.validate_all()
        assert any("trigger keywords" in e for e in errors)

    def test_missing_steps_flagged(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: no-steps
            description: No steps section
            trigger-keywords: [test]
            parameters: {}
            requires:
              os: darwin
            success-condition: Something
            ---

            Just some text without a Steps heading.
        """)
        (skill_dir / "nosteps.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        errors = registry.validate_all()
        assert any("Steps" in e for e in errors)

    def test_missing_success_condition_flagged(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: no-sc
            description: No success condition
            trigger-keywords: [test]
            parameters: {}
            requires:
              os: darwin
            ---

            ## Steps
            1. Do something
               - verify: done
        """)
        (skill_dir / "nosc.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        errors = registry.validate_all()
        assert any("success-condition" in e for e in errors)


# ---------------------------------------------------------------------------
# Test 9: list_skills() returns all loaded skills
# ---------------------------------------------------------------------------

class TestListSkills:
    def test_list_returns_all(self, registry_with_sample):
        skills = registry_with_sample.list_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "test-skill"
        assert skills[0]["description"] == "A test skill for unit testing"

    def test_list_multiple_skills(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        for i in range(3):
            content = textwrap.dedent(f"""\
                ---
                name: skill-{i}
                description: Skill number {i}
                trigger-keywords: [kw{i}]
                parameters: {{}}
                requires:
                  os: darwin
                success-condition: Done
                ---

                ## Steps
                1. Step one
                   - verify: done
            """)
            (skill_dir / f"skill_{i}.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        skills = registry.list_skills()
        assert len(skills) == 3
        names = {s["name"] for s in skills}
        assert names == {"skill-0", "skill-1", "skill-2"}


# ---------------------------------------------------------------------------
# Test 10: Missing required param -> error on expand
# ---------------------------------------------------------------------------

class TestMissingRequiredParam:
    def test_missing_required_raises(self, registry_with_sample):
        with pytest.raises(ValueError, match="Missing required parameter"):
            registry_with_sample.expand("test-skill", {})

    def test_missing_one_of_multiple_required(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: multi-param
            description: Skill with multiple required params
            trigger-keywords: [multi]
            parameters:
              a:
                type: string
                required: true
              b:
                type: string
                required: true
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Use {{a}} and {{b}}
               - verify: done
        """)
        (skill_dir / "multi.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        # Providing only 'a' but not 'b'
        with pytest.raises(ValueError, match="Missing required parameter"):
            registry.expand("multi-param", {"a": "value-a"})


# ---------------------------------------------------------------------------
# Test 11: OS gating - skill with requires.os: linux skipped on darwin
# ---------------------------------------------------------------------------

class TestOSGating:
    def test_linux_skill_skipped_on_darwin(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: linux-only
            description: A Linux-only skill
            trigger-keywords: [linux]
            parameters: {}
            requires:
              os: linux
            success-condition: Done
            ---

            ## Steps
            1. Do linux thing
               - verify: done
        """)
        (skill_dir / "linux_only.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        skills = registry.list_skills()
        assert len(skills) == 0

    def test_darwin_skill_loaded_on_darwin(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: darwin-skill
            description: A macOS skill
            trigger-keywords: [mac]
            parameters: {}
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Do mac thing
               - verify: done
        """)
        (skill_dir / "darwin_skill.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        skills = registry.list_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "darwin-skill"


# ---------------------------------------------------------------------------
# Test 12: load_from_directory() loads all .md files from a temp directory
# ---------------------------------------------------------------------------

class TestLoadFromDirectory:
    def test_loads_multiple_md_files(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()

        for name in ["alpha", "beta", "gamma"]:
            content = textwrap.dedent(f"""\
                ---
                name: {name}
                description: The {name} skill
                trigger-keywords: [{name}]
                parameters: {{}}
                requires:
                  os: darwin
                success-condition: {name} done
                ---

                ## Steps
                1. Run {name}
                   - verify: {name} completed
            """)
            (skill_dir / f"{name}.md").write_text(content)

        # Also add a non-.md file that should be ignored
        (skill_dir / "readme.txt").write_text("Not a skill")

        registry = SkillRegistryImpl(skill_dir=skill_dir)
        skills = registry.list_skills()
        assert len(skills) == 3
        names = {s["name"] for s in skills}
        assert names == {"alpha", "beta", "gamma"}

    def test_empty_directory(self, tmp_path):
        skill_dir = tmp_path / "empty_skills"
        skill_dir.mkdir()
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        assert registry.list_skills() == []

    def test_nonexistent_directory(self, tmp_path):
        skill_dir = tmp_path / "does_not_exist"
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        assert registry.list_skills() == []

    def test_malformed_file_skipped(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        # Valid skill
        valid = textwrap.dedent("""\
            ---
            name: good-skill
            description: A good skill
            trigger-keywords: [good]
            parameters: {}
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Do good
               - verify: done
        """)
        (skill_dir / "good.md").write_text(valid)
        # Malformed skill (no frontmatter)
        (skill_dir / "bad.md").write_text("This file has no YAML frontmatter")

        registry = SkillRegistryImpl(skill_dir=skill_dir)
        skills = registry.list_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "good-skill"


# ---------------------------------------------------------------------------
# Test 13: Skill with no requires block loads on any platform
# ---------------------------------------------------------------------------

class TestNoRequiresBlock:
    def test_skill_with_no_requires_block_loads_on_any_platform(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: universal-skill
            description: Works everywhere
            trigger-keywords: [universal]
            parameters: {}
            success-condition: Done
            ---

            ## Steps
            1. Do something
               - verify: done
        """)
        (skill_dir / "universal.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        skills = registry.list_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "universal-skill"
        # Also verify the parsed os field is empty (any platform)
        skill = registry.get_skill("universal-skill")
        assert skill is not None
        assert skill.requires.os == ""


# ---------------------------------------------------------------------------
# Test 14: Keyword fallback preserves case (no param extraction)
# ---------------------------------------------------------------------------

class TestKeywordFallbackPreservesCase:
    def test_keyword_fallback_returns_empty_params(self):
        """Keyword fallback returns empty params (extract_params removed)."""
        skill = Skill(
            name="send-email",
            description="Send an email",
            trigger_keywords=["send", "email"],
            parameters={
                "recipient": SkillParam(type="string", required=True),
            },
            requires=SkillRequirements(),
            success_condition="Email sent",
            steps_text="1. Send to {{recipient}}",
        )
        result = match_skill("send email to John Smith", [skill])
        assert result is not None
        matched_skill, params = result
        assert matched_skill.name == "send-email"
        assert params == {}  # No param extraction in fallback


# ---------------------------------------------------------------------------
# Test 15: validate_skill_file standalone
# ---------------------------------------------------------------------------

class TestValidateSkillFileStandalone:
    def test_validate_skill_file_standalone(self, tmp_path):
        # Valid skill file
        valid_content = textwrap.dedent("""\
            ---
            name: valid-skill
            description: A valid skill
            trigger-keywords: [test]
            parameters: {}
            success-condition: Done
            ---

            ## Steps
            1. Do something
               - verify: done
        """)
        valid_path = tmp_path / "valid.md"
        valid_path.write_text(valid_content)
        errors = validate_skill_file(valid_path)
        assert errors == []

        # Invalid skill file (no frontmatter)
        invalid_path = tmp_path / "invalid.md"
        invalid_path.write_text("No frontmatter at all")
        errors = validate_skill_file(invalid_path)
        assert len(errors) > 0
        assert any("frontmatter" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Test 16: Duplicate skill name warns
# ---------------------------------------------------------------------------

class TestDuplicateSkillName:
    def test_duplicate_skill_name_warns(self, tmp_path, caplog):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        for filename in ["first.md", "second.md"]:
            content = textwrap.dedent("""\
                ---
                name: same-name
                description: Duplicate name skill
                trigger-keywords: [dup]
                parameters: {}
                requires:
                  os: darwin
                success-condition: Done
                ---

                ## Steps
                1. Do something
                   - verify: done
            """)
            (skill_dir / filename).write_text(content)

        with caplog.at_level(logging.WARNING, logger="automation_agent.skills.registry"):
            registry = SkillRegistryImpl(skill_dir=skill_dir)
        assert any("Duplicate skill name" in msg for msg in caplog.messages)
        # Should still have the skill (the second one overwrites)
        skills = registry.list_skills()
        assert len(skills) == 1


# ---------------------------------------------------------------------------
# Test 17: expand() strips unexpanded optional placeholders
# ---------------------------------------------------------------------------

class TestExpandStripsOptionalPlaceholders:
    def test_expand_strips_unexpanded_optional_placeholders(self, tmp_path, caplog):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        content = textwrap.dedent("""\
            ---
            name: opt-skill
            description: Skill with optional param
            trigger-keywords: [opt]
            parameters:
              name:
                type: string
                required: true
              title:
                type: string
                required: false
                description: Optional title
            requires:
              os: darwin
            success-condition: Done
            ---

            ## Steps
            1. Greet {{name}} with title {{title}}
               - verify: done
        """)
        (skill_dir / "opt.md").write_text(content)
        registry = SkillRegistryImpl(skill_dir=skill_dir)
        with caplog.at_level(logging.WARNING, logger="automation_agent.skills.registry"):
            expanded = registry.expand("opt-skill", {"name": "Alice"})
        assert expanded is not None
        assert "Alice" in expanded
        assert "{{title}}" not in expanded
        assert any("Unexpanded placeholder" in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# Test 18: load_from_string registers skill
# ---------------------------------------------------------------------------

class TestLoadFromString:
    def test_load_from_string_registers_skill(self):
        registry = SkillRegistryImpl(skill_dir=Path("/nonexistent"))
        content = textwrap.dedent("""\
            ---
            name: string-loaded
            description: Loaded from string
            trigger-keywords: [strload]
            parameters: {}
            success-condition: Done
            ---

            ## Steps
            1. Do something
               - verify: done
        """)
        skill = registry.load_from_string(content)
        assert skill.name == "string-loaded"
        # Verify retrievable via get_skill
        retrieved = registry.get_skill("string-loaded")
        assert retrieved is not None
        assert retrieved.name == "string-loaded"
        # Verify appears in list_skills
        skills = registry.list_skills()
        assert any(s["name"] == "string-loaded" for s in skills)


# ---------------------------------------------------------------------------
# Test 19: SkillRouter builds skills summary correctly
# ---------------------------------------------------------------------------

class TestSkillRouterSummary:
    def test_builds_summary(self):
        skills = {
            "test-skill": Skill(
                name="test-skill",
                description="A test skill",
                trigger_keywords=["test"],
                parameters={
                    "query": SkillParam(
                        type="string",
                        required=True,
                        description="The query",
                        examples=["hello", "world"],
                    ),
                },
                requires=SkillRequirements(),
                success_condition="Done",
                steps_text="1. Test",
            ),
        }
        config = MagicMock()
        config.model_provider.value = "local"
        router = SkillRouter(config, skills)
        summary = router._build_skills_summary()
        assert "### test-skill" in summary
        assert "A test skill" in summary
        assert "query (required)" in summary
        assert "'hello'" in summary


# ---------------------------------------------------------------------------
# Test 20: SkillRouter parses valid JSON response
# ---------------------------------------------------------------------------

class TestSkillRouterParseResponse:
    def test_parse_valid_json(self):
        skills = {
            "test-skill": Skill(
                name="test-skill",
                description="A test skill",
                trigger_keywords=["test"],
                parameters={},
                requires=SkillRequirements(),
                success_condition="Done",
                steps_text="1. Test",
            ),
        }
        config = MagicMock()
        router = SkillRouter(config, skills)

        result = router._parse_response('{"skill_name": "test-skill", "params": {"q": "hello"}}')
        assert result is not None
        assert result["skill_name"] == "test-skill"
        assert result["params"]["q"] == "hello"

    def test_parse_null_skill(self):
        skills = {}
        config = MagicMock()
        router = SkillRouter(config, skills)

        result = router._parse_response('{"skill_name": null, "params": {}}')
        assert result is None

    def test_parse_markdown_wrapped_json(self):
        skills = {
            "test-skill": Skill(
                name="test-skill",
                description="A test",
                trigger_keywords=["test"],
                parameters={},
                requires=SkillRequirements(),
                success_condition="Done",
                steps_text="1. Test",
            ),
        }
        config = MagicMock()
        router = SkillRouter(config, skills)

        result = router._parse_response(
            '```json\n{"skill_name": "test-skill", "params": {}}\n```'
        )
        assert result is not None
        assert result["skill_name"] == "test-skill"

    def test_parse_invalid_json(self):
        skills = {}
        config = MagicMock()
        router = SkillRouter(config, skills)

        result = router._parse_response("not json at all")
        assert result is None

    def test_parse_unknown_skill_returns_none(self):
        skills = {
            "real-skill": Skill(
                name="real-skill",
                description="Real",
                trigger_keywords=["real"],
                parameters={},
                requires=SkillRequirements(),
                success_condition="Done",
                steps_text="1. Do",
            ),
        }
        config = MagicMock()
        router = SkillRouter(config, skills)

        result = router._parse_response('{"skill_name": "fake-skill", "params": {}}')
        assert result is None


# ---------------------------------------------------------------------------
# Test 21: SkillRouter.route() end-to-end with mocked LLM
# ---------------------------------------------------------------------------

class TestSkillRouterRoute:
    @pytest.mark.asyncio
    async def test_route_local_success(self):
        skills = {
            "amazon-search": Skill(
                name="amazon-search",
                description="Search Amazon",
                trigger_keywords=["amazon", "search"],
                parameters={
                    "product": SkillParam(type="string", required=True, description="Product"),
                },
                requires=SkillRequirements(),
                success_condition="Results visible",
                steps_text="1. Search",
            ),
        }
        config = MagicMock()
        config.model_provider.value = "local"
        router = SkillRouter(config, skills)

        with patch.object(router, "_call_local", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps({
                "skill_name": "amazon-search",
                "params": {"product": "wireless mouse"},
            })
            result = await router.route("Search for the cheapest wireless mouse on Amazon")

        assert result is not None
        assert result["skill_name"] == "amazon-search"
        assert result["params"]["product"] == "wireless mouse"

    @pytest.mark.asyncio
    async def test_route_no_match(self):
        skills = {
            "amazon-search": Skill(
                name="amazon-search",
                description="Search Amazon",
                trigger_keywords=["amazon"],
                parameters={},
                requires=SkillRequirements(),
                success_condition="Done",
                steps_text="1. Search",
            ),
        }
        config = MagicMock()
        config.model_provider.value = "local"
        router = SkillRouter(config, skills)

        with patch.object(router, "_call_local", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = '{"skill_name": null, "params": {}}'
            result = await router.route("What's the weather today?")

        assert result is None

    @pytest.mark.asyncio
    async def test_route_llm_failure_returns_none(self):
        skills = {}
        config = MagicMock()
        config.model_provider.value = "local"
        router = SkillRouter(config, skills)

        with patch.object(router, "_call_local", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = Exception("Connection refused")
            result = await router.route("anything")

        assert result is None
