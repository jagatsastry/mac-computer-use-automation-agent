"""Run-local derived procedure for adaptive skill execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation_agent.shared_models import ReplanPatch


@dataclass
class DerivedSkillSession:
    """Run-local derived procedure. In-memory only, discarded after run.

    Created when a skill match occurs. Seeded from the best parent skill.
    Updated by ReplanPatch during replanning.
    """

    parent_skill_ids: list[str]
    match_types: list[str]
    current_steps: str
    replaced_labels: list[dict] = field(default_factory=list)
    discovered_landmarks: list[str] = field(default_factory=list)
    verification_notes: list[str] = field(default_factory=list)
    failed_assumptions: list[str] = field(default_factory=list)
    successful_adaptations: list[str] = field(default_factory=list)

    _MAX_ITEMS = 20

    def apply_patch(self, patch: "ReplanPatch") -> None:
        """Apply a replan patch. Deduplicates and caps all lists.

        - replaced_labels: dedup by 'old' key (last write wins)
        - string lists: set semantics (no duplicates)
        - all lists capped at _MAX_ITEMS (oldest dropped)
        - idempotent: applying the same patch twice is a no-op
        """
        # Labels: dedup by 'old' key -- newer replacement wins
        for label in patch.replace_labels:
            self.replaced_labels = [
                r for r in self.replaced_labels if r["old"] != label["old"]
            ]
            self.replaced_labels.append(label)
        # Dedup within-patch duplicates (last write wins)
        seen: dict[str, dict] = {}
        for label in self.replaced_labels:
            seen[label["old"]] = label
        self.replaced_labels = list(seen.values())
        self.replaced_labels = self.replaced_labels[-self._MAX_ITEMS:]

        # String lists: set semantics + cap
        self.discovered_landmarks = self._merge_capped(
            self.discovered_landmarks, patch.add_landmarks
        )
        self.verification_notes = self._merge_capped(
            self.verification_notes, patch.verify_improvements
        )
        self.failed_assumptions = self._merge_capped(
            self.failed_assumptions, patch.failed_assumptions
        )
        self.successful_adaptations = self._merge_capped(
            self.successful_adaptations, patch.successful_adaptations
        )

        if patch.revised_steps:
            self.current_steps = patch.revised_steps

    @staticmethod
    def _merge_capped(
        existing: list[str], new: list[str], cap: int = 20
    ) -> list[str]:
        """Merge new items into existing with set semantics and cap."""
        seen = set(existing)
        merged = list(existing)
        for item in new:
            if item not in seen:
                merged.append(item)
                seen.add(item)
        return merged[-cap:]

    def serialize_for_context(self) -> str:
        """Serialize to a string for injection into skill_context."""
        sections = ["## Derived Procedure (run-local, current best hypothesis)"]
        sections.append(f"Parent skill(s): {', '.join(self.parent_skill_ids)}")
        if self.replaced_labels:
            sections.append("### Label Replacements")
            for r in self.replaced_labels:
                sections.append(
                    f"- \"{r['old']}\" -> \"{r['new']}\" ({r.get('reason', '')})"
                )
        if self.discovered_landmarks:
            sections.append("### Discovered Landmarks")
            for lm in self.discovered_landmarks:
                sections.append(f"- {lm}")
        if self.verification_notes:
            sections.append("### Verification Notes")
            for v in self.verification_notes:
                sections.append(f"- {v}")
        if self.failed_assumptions:
            sections.append("### Failed Assumptions (do NOT repeat)")
            for f in self.failed_assumptions:
                sections.append(f"- {f}")
        if self.successful_adaptations:
            sections.append("### Successful Adaptations")
            for s in self.successful_adaptations:
                sections.append(f"- {s}")
        return "\n".join(sections)

    @classmethod
    def seed(
        cls,
        parent_skill_ids: list[str],
        match_types: list[str],
        steps_text: str,
    ) -> "DerivedSkillSession":
        """Create a session seeded from routing candidates.

        Args:
            parent_skill_ids: Skill IDs of matched candidates.
            match_types: Corresponding match types (direct/analogical/generic).
            steps_text: Steps text from the primary (highest-confidence) skill.
        """
        return cls(
            parent_skill_ids=parent_skill_ids,
            match_types=match_types,
            current_steps=steps_text,
            replaced_labels=[],
            discovered_landmarks=[],
            verification_notes=[],
            failed_assumptions=[],
            successful_adaptations=[],
        )
