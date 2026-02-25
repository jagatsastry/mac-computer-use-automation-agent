"""Unit tests for the skill registry component."""

import textwrap

import pytest

from automation_agent.skills.loader import parse_skill_file
from automation_agent.skills.matcher import match_skill
from automation_agent.skills.models import Skill, SkillParam, SkillRequirements
from automation_agent.skills.registry import SkillRegistryImpl


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


# ---------------------------------------------------------------------------
# Test 5: match() with keyword hit -> correct skill
# ---------------------------------------------------------------------------

class TestMatchKeywordHit:
    def test_match_by_keyword(self, registry_with_sample):
        result = registry_with_sample.match("I want to test something")
        assert result is not None
        assert result["skill_name"] == "test-skill"

    def test_match_by_keyword_case_insensitive(self, registry_with_sample):
        result = registry_with_sample.match("Run the DEMO please")
        assert result is not None
        assert result["skill_name"] == "test-skill"


# ---------------------------------------------------------------------------
# Test 6: match() with no hit -> None
# ---------------------------------------------------------------------------

class TestMatchNoHit:
    def test_no_match(self, registry_with_sample):
        result = registry_with_sample.match("fly me to the moon")
        assert result is None

    def test_empty_prompt(self, registry_with_sample):
        result = registry_with_sample.match("")
        assert result is None


# ---------------------------------------------------------------------------
# Test 7: match() extracts params from prompt
# ---------------------------------------------------------------------------

class TestMatchExtractsParams:
    def test_quoted_param_extraction(self, registry_with_sample):
        result = registry_with_sample.match('test "pandas tutorial"')
        assert result is not None
        assert result["params"].get("query") == "pandas tutorial"

    def test_single_quoted_param_extraction(self, registry_with_sample):
        result = registry_with_sample.match("test 'pandas tutorial'")
        assert result is not None
        assert result["params"].get("query") == "pandas tutorial"


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
