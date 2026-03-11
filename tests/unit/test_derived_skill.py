"""Unit tests for DerivedSkillSession and related shared_models types."""

import pytest

from automation_agent.shared_models import (
    MatchType,
    ReplanPatch,
    SkillMatchResult,
    SkillRouteCandidate,
    SkillRouteResult,
)
from automation_agent.skills.derived_skill import DerivedSkillSession


# ---------------------------------------------------------------------------
# DerivedSkillSession tests
# ---------------------------------------------------------------------------


class TestDerivedSkillSessionCreation:
    def test_seed_produces_valid_session(self):
        session = DerivedSkillSession.seed(
            parent_skill_ids=["return-amazon-order"],
            match_types=["direct"],
            steps_text="1. Open Amazon\n   - verify: page loaded",
        )
        assert session.parent_skill_ids == ["return-amazon-order"]
        assert session.match_types == ["direct"]
        assert "Open Amazon" in session.current_steps
        assert session.replaced_labels == []
        assert session.discovered_landmarks == []
        assert session.verification_notes == []
        assert session.failed_assumptions == []
        assert session.successful_adaptations == []


class TestApplyPatchExtendsLabels:
    def test_apply_patch_extends_labels(self):
        session = DerivedSkillSession.seed(["skill-a"], ["direct"], "steps")
        patch = ReplanPatch(
            replace_labels=[
                {"old": "Orders", "new": "Purchase History", "reason": "Walmart"}
            ]
        )
        session.apply_patch(patch)
        assert len(session.replaced_labels) == 1
        assert session.replaced_labels[0]["old"] == "Orders"
        assert session.replaced_labels[0]["new"] == "Purchase History"


class TestApplyPatchExtendsLandmarks:
    def test_apply_patch_extends_landmarks(self):
        session = DerivedSkillSession.seed(["skill-a"], ["direct"], "steps")
        patch = ReplanPatch(add_landmarks=["Start a return", "Purchase History"])
        session.apply_patch(patch)
        assert "Start a return" in session.discovered_landmarks
        assert "Purchase History" in session.discovered_landmarks


class TestApplyPatchReplacesSteps:
    def test_apply_patch_replaces_steps(self):
        session = DerivedSkillSession.seed(["skill-a"], ["direct"], "old steps")
        patch = ReplanPatch(revised_steps="new steps with corrections")
        session.apply_patch(patch)
        assert session.current_steps == "new steps with corrections"


class TestApplyEmptyPatch:
    def test_apply_empty_patch(self):
        session = DerivedSkillSession.seed(["skill-a"], ["direct"], "original steps")
        original_steps = session.current_steps
        patch = ReplanPatch()
        session.apply_patch(patch)
        assert session.current_steps == original_steps
        assert session.replaced_labels == []
        assert session.discovered_landmarks == []
        assert session.verification_notes == []
        assert session.failed_assumptions == []
        assert session.successful_adaptations == []


class TestSerializeForContext:
    def test_serialize_for_context(self):
        session = DerivedSkillSession.seed(
            ["return-amazon-order", "open-app-and-navigate"],
            ["direct", "generic"],
            "1. Open Amazon",
        )
        session.replaced_labels = [
            {"old": "Orders", "new": "Purchase History", "reason": "Walmart"}
        ]
        session.discovered_landmarks = ["Purchase History link"]
        session.verification_notes = ["Check for purchase history heading"]
        session.failed_assumptions = ["Assumed 'Orders' link exists"]
        session.successful_adaptations = ["Found via account menu"]

        output = session.serialize_for_context()
        assert "## Derived Procedure" in output
        assert "return-amazon-order" in output
        assert "open-app-and-navigate" in output
        assert "### Label Replacements" in output
        assert '"Orders" -> "Purchase History"' in output
        assert "### Discovered Landmarks" in output
        assert "Purchase History link" in output
        assert "### Verification Notes" in output
        assert "### Failed Assumptions (do NOT repeat)" in output
        assert "### Successful Adaptations" in output


# ---------------------------------------------------------------------------
# ReplanPatch tests
# ---------------------------------------------------------------------------


class TestReplanPatchFromDictValid:
    def test_replan_patch_from_dict_valid(self):
        data = {
            "replace_labels": [
                {"old": "Orders", "new": "Purchase History", "reason": "Walmart"}
            ],
            "add_landmarks": ["Start a return"],
            "verify_improvements": ["better verify condition"],
            "failed_assumptions": ["old assumption failed"],
            "successful_adaptations": ["new path worked"],
            "revised_steps": "new steps text",
        }
        patch = ReplanPatch.from_dict(data)
        assert len(patch.replace_labels) == 1
        assert patch.replace_labels[0]["old"] == "Orders"
        assert patch.add_landmarks == ["Start a return"]
        assert patch.verify_improvements == ["better verify condition"]
        assert patch.failed_assumptions == ["old assumption failed"]
        assert patch.successful_adaptations == ["new path worked"]
        assert patch.revised_steps == "new steps text"


class TestReplanPatchFromDictMalformed:
    def test_replan_patch_from_dict_malformed(self):
        """Missing keys produce empty defaults."""
        patch = ReplanPatch.from_dict({})
        assert patch.replace_labels == []
        assert patch.add_landmarks == []
        assert patch.verify_improvements == []
        assert patch.failed_assumptions == []
        assert patch.successful_adaptations == []
        assert patch.revised_steps == ""


class TestReplanPatchFromDictNonDict:
    def test_replan_patch_from_dict_non_dict(self):
        """Non-dict input returns empty patch."""
        patch = ReplanPatch.from_dict("not a dict")
        assert patch.replace_labels == []
        assert patch.revised_steps == ""


class TestReplanPatchFromDictExtraKeys:
    def test_replan_patch_from_dict_extra_keys(self):
        """Unknown keys silently ignored."""
        data = {
            "replace_labels": [],
            "unknown_field": "should be ignored",
            "another_unknown": 42,
        }
        patch = ReplanPatch.from_dict(data)
        assert patch.replace_labels == []
        assert not hasattr(patch, "unknown_field")


class TestReplanPatchFromDictWrongNesting:
    def test_replan_patch_from_dict_wrong_nesting(self):
        """replace_labels as string not list produces empty list."""
        data = {
            "replace_labels": "not a list",
            "add_landmarks": 42,
        }
        patch = ReplanPatch.from_dict(data)
        assert patch.replace_labels == []
        assert patch.add_landmarks == []


class TestReplanPatchEmptyRevisedSteps:
    def test_replan_patch_empty_revised_steps(self):
        """Empty string does NOT replace current_steps."""
        session = DerivedSkillSession.seed(["skill-a"], ["direct"], "original steps")
        patch = ReplanPatch(revised_steps="")
        session.apply_patch(patch)
        assert session.current_steps == "original steps"


class TestApplyPatchIdempotent:
    def test_apply_patch_idempotent(self):
        """Applying same patch twice is a no-op (set semantics)."""
        session = DerivedSkillSession.seed(["skill-a"], ["direct"], "steps")
        patch = ReplanPatch(
            add_landmarks=["landmark-1", "landmark-2"],
            failed_assumptions=["assumption-1"],
        )
        session.apply_patch(patch)
        count_after_first = len(session.discovered_landmarks)
        count_fail_first = len(session.failed_assumptions)

        session.apply_patch(patch)
        assert len(session.discovered_landmarks) == count_after_first
        assert len(session.failed_assumptions) == count_fail_first


class TestApplyPatchDedupWithinSinglePatch:
    def test_apply_patch_dedup_within_single_patch(self):
        """Duplicate 'old' keys within a single patch are deduped (last wins)."""
        session = DerivedSkillSession.seed(["skill-a"], ["direct"], "steps")
        patch = ReplanPatch(
            replace_labels=[
                {"old": "Orders", "new": "First", "reason": "1"},
                {"old": "Orders", "new": "Second", "reason": "2"},
            ]
        )
        session.apply_patch(patch)
        assert len(session.replaced_labels) == 1
        assert session.replaced_labels[0]["new"] == "Second"
        assert session.replaced_labels[0]["reason"] == "2"


class TestApplyPatchDedupLabelsByOld:
    def test_apply_patch_dedup_labels_by_old(self):
        """Second patch for same 'old' key overwrites first."""
        session = DerivedSkillSession.seed(["skill-a"], ["direct"], "steps")
        patch1 = ReplanPatch(
            replace_labels=[{"old": "Orders", "new": "My Orders", "reason": "first"}]
        )
        session.apply_patch(patch1)
        assert session.replaced_labels[0]["new"] == "My Orders"

        patch2 = ReplanPatch(
            replace_labels=[
                {"old": "Orders", "new": "Purchase History", "reason": "second"}
            ]
        )
        session.apply_patch(patch2)
        assert len(session.replaced_labels) == 1
        assert session.replaced_labels[0]["new"] == "Purchase History"
        assert session.replaced_labels[0]["reason"] == "second"


class TestApplyPatchCapsAtMaxItems:
    def test_apply_patch_caps_at_max_items(self):
        """Lists never exceed _MAX_ITEMS=20."""
        session = DerivedSkillSession.seed(["skill-a"], ["direct"], "steps")
        # Add 25 landmarks
        patch = ReplanPatch(
            add_landmarks=[f"landmark-{i}" for i in range(25)]
        )
        session.apply_patch(patch)
        assert len(session.discovered_landmarks) == 20
        # Oldest dropped: should contain landmarks 5-24
        assert session.discovered_landmarks[0] == "landmark-5"
        assert session.discovered_landmarks[-1] == "landmark-24"


class TestSerializeForContextBoundedOutput:
    def test_serialize_for_context_bounded_output(self):
        """Output size proportional to _MAX_ITEMS."""
        session = DerivedSkillSession.seed(["skill-a"], ["direct"], "steps")
        # Fill all lists to max
        patch = ReplanPatch(
            add_landmarks=[f"lm-{i}" for i in range(25)],
            verify_improvements=[f"vi-{i}" for i in range(25)],
            failed_assumptions=[f"fa-{i}" for i in range(25)],
            successful_adaptations=[f"sa-{i}" for i in range(25)],
        )
        session.apply_patch(patch)
        output = session.serialize_for_context()
        # Each list should have at most 20 items in the output
        assert output.count("- lm-") == 20
        assert output.count("- vi-") == 20
        assert output.count("- fa-") == 20
        assert output.count("- sa-") == 20


# ---------------------------------------------------------------------------
# SkillRouteCandidate tests
# ---------------------------------------------------------------------------


class TestSkillRouteCandidateValidation:
    def test_invalid_match_type_raises(self):
        """Invalid match_type raises ValueError (StrEnum)."""
        with pytest.raises(ValueError):
            SkillRouteCandidate(
                skill_id="test",
                match_type="invalid_type",
                confidence=0.5,
                reason="test",
            )

    def test_confidence_clamped(self):
        """Confidence is clamped to [0.0, 1.0]."""
        c = SkillRouteCandidate(
            skill_id="test",
            match_type="direct",
            confidence=1.5,
            reason="test",
        )
        assert c.confidence == 1.0

        c2 = SkillRouteCandidate(
            skill_id="test",
            match_type="direct",
            confidence=-0.5,
            reason="test",
        )
        assert c2.confidence == 0.0


class TestMatchTypeStrEnumValues:
    def test_match_type_strenum_values(self):
        """MatchType.DIRECT/ANALOGICAL/GENERIC have correct string values."""
        assert MatchType.DIRECT == "direct"
        assert MatchType.ANALOGICAL == "analogical"
        assert MatchType.GENERIC == "generic"
        assert MatchType.DIRECT.value == "direct"


# ---------------------------------------------------------------------------
# SkillRouteResult tests
# ---------------------------------------------------------------------------


class TestSkillRouteResultPrimary:
    def test_primary_returns_highest_confidence(self):
        """.primary returns first candidate (highest-confidence by convention)."""
        candidates = [
            SkillRouteCandidate("skill-a", MatchType.DIRECT, 0.9, "best"),
            SkillRouteCandidate("skill-b", MatchType.ANALOGICAL, 0.6, "second"),
        ]
        result = SkillRouteResult(candidates=candidates)
        assert result.primary is not None
        assert result.primary.skill_id == "skill-a"
        assert result.primary.confidence == 0.9


class TestSkillRouteResultEmpty:
    def test_primary_returns_none_on_empty(self):
        """.primary returns None on empty list."""
        result = SkillRouteResult(candidates=[])
        assert result.primary is None


class TestSkillRouteResultHasDirectMatch:
    def test_has_direct_match(self):
        candidates = [
            SkillRouteCandidate("skill-a", MatchType.ANALOGICAL, 0.6, "analog"),
            SkillRouteCandidate("skill-b", MatchType.DIRECT, 0.9, "direct"),
        ]
        result = SkillRouteResult(candidates=candidates)
        assert result.has_direct_match is True

    def test_no_direct_match(self):
        candidates = [
            SkillRouteCandidate("skill-a", MatchType.ANALOGICAL, 0.6, "analog"),
        ]
        result = SkillRouteResult(candidates=candidates)
        assert result.has_direct_match is False


# ---------------------------------------------------------------------------
# SkillMatchResult tests
# ---------------------------------------------------------------------------


class TestSkillMatchResultGetItemCompat:
    def test_getitem_compat(self):
        """result['skill_name'] works via shim."""
        result = SkillMatchResult(
            skill_name="test-skill",
            expanded_steps="1. Do thing",
            skill_context="context here",
            params={"query": "test"},
            candidates=[],
        )
        assert result["skill_name"] == "test-skill"
        assert result["expanded_steps"] == "1. Do thing"
        assert result["params"] == {"query": "test"}


class TestSkillMatchResultGetCompat:
    def test_get_compat(self):
        """result.get('params', {}) works via shim."""
        result = SkillMatchResult(
            skill_name="test-skill",
            expanded_steps="1. Do thing",
            skill_context="context here",
            params={"query": "test"},
            candidates=[],
        )
        assert result.get("skill_name") == "test-skill"
        assert result.get("nonexistent", "default") == "default"


class TestSkillMatchResultGetItemRaisesKeyError:
    def test_getitem_unknown_key_raises_key_error(self):
        """result['nonexistent'] raises KeyError, not AttributeError."""
        result = SkillMatchResult(
            skill_name="test-skill",
            expanded_steps="1. Do thing",
            skill_context="context here",
            params={},
            candidates=[],
        )
        with pytest.raises(KeyError, match="nonexistent"):
            result["nonexistent"]


# ---------------------------------------------------------------------------
# ReplanPatch edge cases
# ---------------------------------------------------------------------------


class TestReplanPatchFromDictMissingReason:
    def test_replace_labels_without_reason_key(self):
        """replace_labels entries without 'reason' still parse correctly."""
        data = {
            "replace_labels": [{"old": "Orders", "new": "Purchase History"}],
        }
        patch = ReplanPatch.from_dict(data)
        assert len(patch.replace_labels) == 1
        assert patch.replace_labels[0]["old"] == "Orders"
        assert patch.replace_labels[0]["new"] == "Purchase History"
        assert "reason" not in patch.replace_labels[0]
