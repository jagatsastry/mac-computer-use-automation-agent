"""Type-Text Hardening tests: AC-1 through AC-5.

Verifies shipped fixes for buy_on_target direct search URL (AC-1),
compiler element extraction (AC-2), URL encoding (AC-3), word-boundary
matching (AC-4), and domain injection scoping (AC-5).
"""

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automation_agent.config import AgentConfig
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.shared_models import ActionPlan, ActionStep
from automation_agent.skills.matcher import match_skill
from automation_agent.skills.models import Skill, SkillParam, SkillRequirements


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",
        "grounding_model": "",
        "grounding_server_url": "",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_agent(**config_overrides) -> AutomationAgent:
    cfg = _make_config(**config_overrides)
    planner = MagicMock()
    coordinator = MagicMock()
    actuator = MagicMock()
    skill_registry = MagicMock()
    return AutomationAgent(
        config=cfg,
        planner=planner,
        coordinator=coordinator,
        actuator=actuator,
        skill_registry=skill_registry,
    )


def _make_skill(
    name="test-skill",
    trigger_keywords=None,
    required_keywords=None,
    steps_text="",
    parameters=None,
):
    """Build a minimal Skill for testing."""
    meta = {"name": name}
    if required_keywords:
        meta["required-keywords"] = required_keywords
    return Skill(
        name=name,
        description=f"Test skill {name}",
        trigger_keywords=trigger_keywords or ["buy", "shop"],
        parameters=parameters or {},
        requires=SkillRequirements(apps=["Safari"], os="darwin"),
        success_condition="done",
        steps_text=steps_text,
        metadata=meta,
    )


SKILL_DIR = Path(__file__).resolve().parents[2] / "src" / "automation_agent" / "skills" / "library"


def _load_buy_on_target_raw() -> str:
    """Load the raw content of buy_on_target.md."""
    return (SKILL_DIR / "buy_on_target.md").read_text()


# ---------------------------------------------------------------------------
# AC-1: Direct Search URL in buy_on_target.md
# ---------------------------------------------------------------------------

class TestAC1DirectSearchURL:
    """AC-1: buy_on_target.md uses direct search URL, not type-and-enter."""

    def test_step1_is_navigate_to_url(self):
        """Step 1 instruction starts with 'Navigate to https://www.target.com/s?searchTerm='."""
        raw = _load_buy_on_target_raw()
        # Find step 1 line
        for line in raw.splitlines():
            m = re.match(r"^\d+\.\s+(.*)$", line.strip())
            if m:
                instruction = m.group(1)
                assert instruction.startswith(
                    "Navigate to https://www.target.com/s?searchTerm="
                ), f"Step 1 should start with Navigate to URL, got: {instruction}"
                return
        pytest.fail("No numbered step found in buy_on_target.md")

    def test_no_type_and_enter_for_search(self):
        """No step in buy_on_target.md contains 'Type' + 'press Enter' for initial search."""
        raw = _load_buy_on_target_raw()
        steps_section = raw.split("## Steps")[1].split("## Error Recovery")[0]
        # Check no step line matches Type ... press Enter
        for line in steps_section.splitlines():
            m = re.match(r"^\d+\.\s+(.*)$", line.strip())
            if m:
                instruction = m.group(1)
                assert not re.search(
                    r"Type\s+.+press\s+Enter", instruction, re.IGNORECASE
                ), f"Found type-and-enter pattern in step: {instruction}"

    def test_url_encoding_after_param_substitution(self):
        """When {{product}} = 'queen size bed sheets', the compiled URL has encoded spaces."""
        agent = _make_agent()
        instruction = "Navigate to https://www.target.com/s?searchTerm=queen size bed sheets"
        verify = "Target search results page is visible"
        steps = agent._compile_skill_instruction(instruction, verify)
        assert steps is not None and len(steps) == 1
        url = steps[0].params["url"]
        assert " " not in url, f"URL still contains unencoded spaces: {url}"
        # urlencode uses + for query params
        assert "queen+size+bed+sheets" in url or "queen%20size%20bed%20sheets" in url

    def test_ac1_expand_then_compile_end_to_end(self):
        """End-to-end: expand() substitutes params, then _compile_skill_instruction encodes."""
        from automation_agent.skills.registry import SkillRegistryImpl

        registry = SkillRegistryImpl()
        # Load skills from the library directory
        registry.load_from_directory(SKILL_DIR)

        expanded = registry.expand("buy-on-target", {"product": "queen size bed sheets"})
        assert expanded is not None

        # Extract step 1 instruction
        for line in expanded.splitlines():
            m = re.match(r"^\d+\.\s+(.*)$", line.strip())
            if m:
                instruction = m.group(1)
                break
        else:
            pytest.fail("No numbered step in expanded skill")

        agent = _make_agent()
        steps = agent._compile_skill_instruction(instruction, "Results visible")
        assert steps is not None and len(steps) == 1
        assert steps[0].action == "open_url"
        url = steps[0].params["url"]
        assert " " not in url, f"URL has unencoded spaces: {url}"


# ---------------------------------------------------------------------------
# AC-2: Compiler Element Extraction from "in the..." Clause
# ---------------------------------------------------------------------------

class TestAC2CompilerElementExtraction:
    """AC-2: Type instruction extracts element from 'in the' clause."""

    def setup_method(self):
        self.agent = _make_agent()

    def test_type_with_in_the_extracts_element(self):
        """'Type "foo" in the search bar and press Enter' -> element='search bar'."""
        steps = self.agent._compile_skill_instruction(
            'Type "foo" in the search bar and press Enter',
            "Results visible",
        )
        assert steps is not None and len(steps) == 2
        assert steps[0].params["element"] == "search bar"

    def test_type_without_in_the_defaults_to_search_field(self):
        """'Type "foo" and press Enter' -> element='search or text input field'."""
        steps = self.agent._compile_skill_instruction(
            'Type "foo" and press Enter',
            "Results visible",
        )
        assert steps is not None and len(steps) == 2
        assert steps[0].params["element"] == "search or text input field"

    def test_type_produces_two_steps(self):
        """Type+Enter always produces [type_text, press_key(return)]."""
        steps = self.agent._compile_skill_instruction(
            'Type "queen sheets" in the search bar and press Enter',
            "Results visible",
        )
        assert steps is not None and len(steps) == 2
        assert steps[0].action == "type_text"
        assert steps[1].action == "press_key"
        assert steps[1].params["keys"] == ["return"]

    def test_type_text_step_has_verify(self):
        """type_text step has non-empty verify containing the typed text."""
        steps = self.agent._compile_skill_instruction(
            'Type "queen sheets" and press Enter',
            "Results visible",
        )
        assert steps is not None
        assert "queen sheets" in steps[0].verify

    def test_type_with_in_the_in_quoted_text(self):
        """'Type "sign in" in the username field and press Enter'
        -> typed_text='sign in', element='username field'."""
        steps = self.agent._compile_skill_instruction(
            'Type "sign in" in the username field and press Enter',
            "Login complete",
        )
        assert steps is not None and len(steps) == 2
        assert steps[0].params["text"] == "sign in"
        assert steps[0].params["element"] == "username field"

    def test_type_unquoted_single_word(self):
        """'Type foo and press Enter' (no quotes) -> typed_text='foo'.
        Regex uses "? making quotes optional; lazy (.+?) captures correctly."""
        steps = self.agent._compile_skill_instruction(
            "Type foo and press Enter",
            "Results visible",
        )
        assert steps is not None and len(steps) == 2
        assert steps[0].params["text"] == "foo"
        assert steps[0].action == "type_text"

    def test_type_unquoted_multi_word(self):
        """'Type foo bar and press Enter' (no quotes) -> typed_text='foo bar'.
        Lazy (.+?) stops before ' and press Enter', not at first space."""
        steps = self.agent._compile_skill_instruction(
            "Type foo bar and press Enter",
            "Results visible",
        )
        assert steps is not None and len(steps) == 2
        assert steps[0].params["text"] == "foo bar"

    def test_type_unquoted_with_element(self):
        """'Type foo in the search bar and press Enter' (no quotes)
        -> typed_text='foo', element='search bar'."""
        steps = self.agent._compile_skill_instruction(
            "Type foo in the search bar and press Enter",
            "Results visible",
        )
        assert steps is not None and len(steps) == 2
        assert steps[0].params["text"] == "foo"
        assert steps[0].params["element"] == "search bar"


# ---------------------------------------------------------------------------
# AC-3: URL Encoding via urllib.parse
# ---------------------------------------------------------------------------

class TestAC3URLEncoding:
    """AC-3: URL encoding uses urllib.parse for all paths."""

    def setup_method(self):
        self.agent = _make_agent()

    def test_spaces_in_query_param_encoded(self):
        """'Navigate to https://target.com/s?searchTerm=queen size sheets' -> encoded."""
        steps = self.agent._compile_skill_instruction(
            "Navigate to https://target.com/s?searchTerm=queen size sheets",
            "Results visible",
        )
        assert steps is not None and len(steps) == 1
        url = steps[0].params["url"]
        assert " " not in url
        assert "queen+size+sheets" in url or "queen%20size%20sheets" in url

    def test_url_without_query_uses_component_encoding(self):
        """'Navigate to https://target.com/queen size sheets' -> %20 via urlparse+quote."""
        steps = self.agent._compile_skill_instruction(
            "Navigate to https://target.com/queen size sheets",
            "Page visible",
        )
        assert steps is not None and len(steps) == 1
        url = steps[0].params["url"]
        assert "queen%20size%20sheets" in url
        assert " " not in url

    def test_special_chars_in_path_encoded(self):
        """'Navigate to https://target.com/bed & bath' -> & encoded as %26 in path."""
        steps = self.agent._compile_skill_instruction(
            "Navigate to https://target.com/bed & bath",
            "Page visible",
        )
        assert steps is not None and len(steps) == 1
        url = steps[0].params["url"]
        assert "%26" in url, f"& not encoded as %26 in URL: {url}"

    def test_already_encoded_path_not_double_encoded(self):
        """'Navigate to https://target.com/queen%20sheets' -> no %2520."""
        steps = self.agent._compile_skill_instruction(
            "Navigate to https://target.com/queen%20sheets",
            "Page visible",
        )
        assert steps is not None and len(steps) == 1
        url = steps[0].params["url"]
        assert "%2520" not in url, f"Double-encoded: {url}"
        assert "queen%20sheets" in url

    def test_multiple_query_params_preserved(self):
        """URL with multiple params keeps all after encoding."""
        steps = self.agent._compile_skill_instruction(
            "Navigate to https://example.com?a=foo bar&b=baz",
            "Page visible",
        )
        assert steps is not None and len(steps) == 1
        url = steps[0].params["url"]
        assert "a=foo+bar" in url or "a=foo%20bar" in url
        assert "b=baz" in url

    def test_empty_query_value_preserved(self):
        """parse_qs with keep_blank_values=True preserves empty values."""
        steps = self.agent._compile_skill_instruction(
            "Navigate to https://example.com?k=&q=test",
            "Page visible",
        )
        assert steps is not None and len(steps) == 1
        url = steps[0].params["url"]
        assert "k=" in url
        assert "q=test" in url

    def test_encoded_slash_in_path_known_limitation(self):
        """KNOWN LIMITATION: unquote() decodes %2F back to /, destroying encoded-slash
        semantics. The limitation only manifests when the path also contains spaces
        (triggering the elif branch). Documents the trade-off."""
        # Path with BOTH spaces and %2F — triggers unquote+quote branch
        steps = self.agent._compile_skill_instruction(
            "Navigate to https://target.com/search/bed%2Fbath results",
            "Page visible",
        )
        assert steps is not None and len(steps) == 1
        url = steps[0].params["url"]
        # %2F is decoded to / by unquote — this is the documented limitation
        assert "%2F" not in url, (
            "Expected %2F to be decoded (known limitation). "
            f"Got: {url}"
        )

    def test_encoded_slash_without_spaces_preserved(self):
        """When path has %2F but NO spaces, unquote branch does not fire -- %2F preserved."""
        steps = self.agent._compile_skill_instruction(
            "Navigate to https://target.com/search/bed%2Fbath/results",
            "Page visible",
        )
        assert steps is not None and len(steps) == 1
        url = steps[0].params["url"]
        assert "%2F" in url, f"Expected %2F preserved (no-space path), got: {url}"

    def test_query_params_with_spaces_in_path_both_encoded(self):
        """URL with query params AND spaces in path -- both path and query are encoded.
        Path spaces are encoded via component-based quote(), query via parse_qs+urlencode."""
        steps = self.agent._compile_skill_instruction(
            "Navigate to https://target.com/bed bath?q=test value",
            "Page visible",
        )
        assert steps is not None and len(steps) == 1
        url = steps[0].params["url"]
        # Path spaces encoded
        assert "bed%20bath" in url, f"Path space not encoded: {url}"
        assert "bed bath" not in url, f"Unencoded path space found: {url}"
        # Query also encoded
        assert "q=test+value" in url or "q=test%20value" in url, (
            f"Query space not encoded: {url}"
        )
        # No unencoded spaces anywhere
        assert " " not in url, f"Unencoded spaces in URL: {url}"

    def test_ampersand_in_query_param_known_limitation(self):
        """KNOWN LIMITATION: & in query param value splits on param separator.
        Documents the pre-existing bug in expand() raw substitution."""
        steps = self.agent._compile_skill_instruction(
            "Navigate to https://target.com/s?searchTerm=bed & bath",
            "Page visible",
        )
        assert steps is not None and len(steps) == 1
        url = steps[0].params["url"]
        # parse_qs splits on & — 'bed & bath' becomes two params
        # This documents the known limitation
        assert "searchTerm=bed+%26+bath" not in url, (
            "If this passes, the limitation is fixed (update spec)"
        )


# ---------------------------------------------------------------------------
# AC-4: Word-Boundary Matching in Required-Keywords Gate
# ---------------------------------------------------------------------------

class TestAC4WordBoundaryMatching:
    """AC-4: required-keywords use word-boundary matching."""

    def _make_target_skill(self):
        return _make_skill(
            name="buy-on-target",
            trigger_keywords=["buy", "target", "shop"],
            required_keywords=["target"],
        )

    def test_exact_word_matches(self):
        """Prompt 'buy on target' matches required-keyword 'target'."""
        skill = self._make_target_skill()
        result = match_skill("buy on target", [skill])
        assert result is not None
        assert result[0].name == "buy-on-target"

    def test_substring_retarget_rejected(self):
        """Prompt 'retarget the ad' does NOT match required-keyword 'target'."""
        skill = self._make_target_skill()
        result = match_skill("retarget the ad", [skill])
        assert result is None

    def test_substring_untargeted_rejected(self):
        """Prompt 'untargeted campaign' does NOT match required-keyword 'target'."""
        skill = self._make_target_skill()
        result = match_skill("untargeted campaign", [skill])
        assert result is None

    def test_domain_form_matches(self):
        """Prompt 'buy on target.com' matches required-keyword 'target.com'."""
        skill = _make_skill(
            name="buy-on-target",
            trigger_keywords=["buy", "target", "shop"],
            required_keywords=["target.com"],
        )
        result = match_skill("buy on target.com", [skill])
        assert result is not None

    def test_case_insensitive_match(self):
        """Prompt 'Buy on TARGET' matches required-keyword 'target'."""
        skill = self._make_target_skill()
        result = match_skill("Buy on TARGET", [skill])
        assert result is not None

    def test_any_keyword_sufficient(self):
        """With required-keywords ['target', 'target.com'], matching either suffices."""
        skill = _make_skill(
            name="buy-on-target",
            trigger_keywords=["buy", "target", "shop"],
            required_keywords=["target", "target.com"],
        )
        # 'target' alone should match
        result = match_skill("buy on target", [skill])
        assert result is not None


# ---------------------------------------------------------------------------
# AC-5: Domain Injection Scoped to Matching-Domain Steps
# ---------------------------------------------------------------------------

class TestAC5DomainInjectionScoping:
    """AC-5: Domain injection only on matching-domain open_url steps."""

    def setup_method(self):
        self.agent = _make_agent()

    def _make_plan(self, steps):
        return ActionPlan(goal="test", steps=steps)

    def _open_url_step(self, url, verify="Page loaded"):
        return ActionStep(
            action="open_url",
            params={"url": url},
            verify=verify,
            on_fail="retry_different",
        )

    def test_matching_domain_gets_injection(self):
        """open_url with target.com URL gets 'AND browser domain is target.com' appended."""
        step = self._open_url_step("https://target.com/s?q=test")
        plan = self._make_plan([step])
        self.agent._inject_domain_verification(plan, "target.com")
        assert "AND browser domain is target.com" in step.verify

    def test_subdomain_gets_injection(self):
        """open_url with www.target.com URL gets injection (subdomain of target.com)."""
        step = self._open_url_step("https://www.target.com/s?q=test")
        plan = self._make_plan([step])
        self.agent._inject_domain_verification(plan, "target.com")
        assert "AND browser domain is target.com" in step.verify

    def test_non_matching_domain_skipped(self):
        """open_url with google.com URL does NOT get target.com domain injection."""
        step = self._open_url_step("https://google.com/search")
        plan = self._make_plan([step])
        self.agent._inject_domain_verification(plan, "target.com")
        assert "browser domain is" not in step.verify

    def test_domain_injection_not_triggered_by_substring_overlap(self):
        """open_url with nottarget.com URL does NOT get target.com domain injection.
        Regression test: raw 'target.com in url' would false-positive here."""
        step = self._open_url_step("https://nottarget.com/redirect")
        plan = self._make_plan([step])
        self.agent._inject_domain_verification(plan, "target.com")
        assert "browser domain is" not in step.verify

    def test_idempotent_no_double_injection(self):
        """If verify already contains 'browser domain is', no duplicate added."""
        step = self._open_url_step(
            "https://target.com/page",
            verify="Page loaded AND browser domain is target.com",
        )
        plan = self._make_plan([step])
        self.agent._inject_domain_verification(plan, "target.com")
        # Should appear exactly once
        assert step.verify.count("browser domain is") == 1

    def test_multi_step_plan_selective_injection(self):
        """Plan with target.com and google.com URLs: only target.com step gets injected."""
        target_step = self._open_url_step("https://www.target.com/s?q=test")
        google_step = self._open_url_step("https://google.com/search")
        plan = self._make_plan([target_step, google_step])
        self.agent._inject_domain_verification(plan, "target.com")
        assert "AND browser domain is target.com" in target_step.verify
        assert "browser domain is" not in google_step.verify

    def test_schemeless_url_skips_injection(self):
        """open_url with scheme-less URL (e.g., 'target.com/page') skips injection.
        urlparse().hostname returns None for scheme-less URLs. (R1-2)"""
        step = self._open_url_step("target.com/page")
        plan = self._make_plan([step])
        self.agent._inject_domain_verification(plan, "target.com")
        assert "browser domain is" not in step.verify

    def test_userinfo_at_host_not_tricked(self):
        """Security: target.com@evil.com -> hostname='evil.com' -> NOT injected.
        Validates urlparse().hostname correctly extracts actual host. (SEC PB-2)"""
        step = self._open_url_step("https://target.com@evil.com/redirect")
        plan = self._make_plan([step])
        self.agent._inject_domain_verification(plan, "target.com")
        assert "browser domain is target.com" not in step.verify

    def test_port_spoofing_userinfo_with_port(self):
        """Security: evil.com:443@target.com -> hostname='target.com' (safe).
        urlparse treats evil.com:443 as userinfo, target.com as the host."""
        step = self._open_url_step("https://evil.com:443@target.com/page")
        plan = self._make_plan([step])
        self.agent._inject_domain_verification(plan, "target.com")
        # urlparse().hostname returns 'target.com' — injection IS correct here
        assert "AND browser domain is target.com" in step.verify

    def test_legitimate_port_gets_injection(self):
        """open_url with target.com:8443 gets injection (hostname is target.com)."""
        step = self._open_url_step("https://target.com:8443/page")
        plan = self._make_plan([step])
        self.agent._inject_domain_verification(plan, "target.com")
        assert "AND browser domain is target.com" in step.verify

    def test_open_url_without_url_param_skips_injection(self):
        """open_url step with empty/missing URL param skips domain injection.
        Empty URLs have no domain to verify -- injection is skipped."""
        step_no_url = ActionStep(
            action="open_url",
            params={},
            verify="Page loaded",
            on_fail="retry_different",
        )
        step_empty_url = ActionStep(
            action="open_url",
            params={"url": ""},
            verify="Page loaded",
            on_fail="retry_different",
        )
        plan = self._make_plan([step_no_url, step_empty_url])
        self.agent._inject_domain_verification(plan, "target.com")
        assert "browser domain is" not in step_no_url.verify
        assert "browser domain is" not in step_empty_url.verify
