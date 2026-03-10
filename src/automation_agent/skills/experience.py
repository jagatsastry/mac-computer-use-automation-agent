"""Persistent skill observations learned from prior runs."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from automation_agent.skills.models import SkillObservation


class SkillExperienceStore:
    """Stores generalized observations per skill in JSONL sidecar files."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, skill_name: str) -> List[SkillObservation]:
        """Load all observations for a skill."""
        path = self._path_for(skill_name)
        if not path.exists():
            return []
        observations: List[SkillObservation] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            created_at = data.get("created_at")
            if created_at:
                try:
                    data["created_at"] = datetime.fromisoformat(created_at)
                except ValueError:
                    data["created_at"] = datetime.utcnow()
            observations.append(SkillObservation(**data))
        return observations

    def append(self, skill_name: str, observations: Iterable[SkillObservation]) -> int:
        """Append generalized observations for a skill. Returns count written."""
        items = list(observations)
        if not items:
            return 0
        path = self._path_for(skill_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for observation in items:
                payload = asdict(observation)
                payload["created_at"] = observation.created_at.isoformat()
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
        return len(items)

    def top_for_context(
        self,
        skill_name: str,
        limit: int = 5,
        min_confidence: float = 0.6,
    ) -> List[SkillObservation]:
        """Return the highest-confidence unique observations for prompting."""
        observations = [
            item
            for item in self.load(skill_name)
            if item.confidence >= min_confidence and item.recommendation.strip()
        ]
        observations.sort(key=lambda item: (item.confidence, item.created_at), reverse=True)
        seen: set[tuple[str, str]] = set()
        selected: List[SkillObservation] = []
        for item in observations:
            key = (item.category.strip().lower(), item.recommendation.strip().lower())
            if key in seen:
                continue
            selected.append(item)
            seen.add(key)
            if len(selected) >= limit:
                break
        return selected

    def _path_for(self, skill_name: str) -> Path:
        safe_name = skill_name.replace("/", "_")
        return self.root / f"{safe_name}.jsonl"
