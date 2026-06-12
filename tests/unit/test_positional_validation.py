"""Positionally-disambiguated click targets must be validated even at high
grounding confidence.

Grounding models (GPT computer-use, molmo, qwen) are frequently overconfident
about WHICH row/item a control belongs to — they return conf>=0.9 but point one
row off. The confidence gate skips pre-click validation at >=0.9, removing the
only safety net. For targets whose description pins a specific row/item/position,
keep validation on so the row-band validator can catch a wrong-row click.
"""

from automation_agent.orchestrator.agent import AutomationAgent


class TestPositionalTargetDetection:
    def test_row_phrases_are_positional(self):
        for desc in [
            "Add to Cart button in the Green Lamp row",
            "Add to Cart button for the Blue Notebook",
            "the 3rd row delete icon",
            "Edit link next to john@example.com",
            "checkbox beside 'Enable notifications'",
            "second result's title",
        ]:
            assert AutomationAgent._is_positional_target(desc) is True, desc

    def test_plain_targets_are_not_positional(self):
        for desc in [
            "Search button",
            "the OK button",
            "username field",
            "Submit",
            "hamburger menu icon",
        ]:
            assert AutomationAgent._is_positional_target(desc) is False, desc


class TestSkipGate:
    def test_high_confidence_positional_target_is_still_validated(self):
        # The skip decision: skip only when high-confidence AND not positional.
        assert (
            AutomationAgent._should_skip_preclick_validation(
                0.98, "Add to Cart button for the Blue Notebook"
            )
            is False
        )

    def test_high_confidence_plain_target_skips_validation(self):
        assert (
            AutomationAgent._should_skip_preclick_validation(0.98, "Search button") is True
        )

    def test_low_confidence_always_validates(self):
        assert (
            AutomationAgent._should_skip_preclick_validation(0.6, "Search button") is False
        )
        assert (
            AutomationAgent._should_skip_preclick_validation(
                0.6, "Add to Cart for Blue Notebook"
            )
            is False
        )
