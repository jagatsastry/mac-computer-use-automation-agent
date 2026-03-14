"""Persistent skill observations learned from prior runs."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Set, Tuple

from automation_agent.skills.models import SkillObservation

_NORMALIZE_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_key(text: str) -> str:
    """Normalize text for matching: lowercase, strip punctuation, collapse whitespace.

    Must match SkillLibrarian._normalize_key() to ensure mark_promoted() keys
    align with the librarian's grouping keys.
    """
    text = text.strip().lower()
    text = _NORMALIZE_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


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
            if item.confidence >= min_confidence
            and item.recommendation.strip()
            and not getattr(item, "promoted", False)
        ]
        observations.sort(key=lambda item: (item.confidence, item.created_at), reverse=True)
        seen: set[tuple[str, str]] = set()
        selected: List[SkillObservation] = []
        for item in observations:
            key = (_normalize_key(item.category), _normalize_key(item.recommendation))
            if key in seen:
                continue
            selected.append(item)
            seen.add(key)
            if len(selected) >= limit:
                break
        return selected

    def mark_promoted(
        self, skill_name: str, keys: Set[Tuple[str, str]]
    ) -> int:
        """Mark observations matching (category, recommendation) keys as promoted.

        Returns count marked. Uses atomic write-to-temp + rename.
        """
        path = self._path_for(skill_name)
        if not path.exists():
            return 0
        lines = path.read_text(encoding="utf-8").splitlines()
        marked = 0
        updated_lines: List[str] = []
        for line in lines:
            if not line.strip():
                updated_lines.append(line)
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                updated_lines.append(line)
                continue
            key = (
                _normalize_key(data.get("category", "")),
                _normalize_key(data.get("recommendation", "")),
            )
            if key in keys and not data.get("promoted", False):
                data["promoted"] = True
                marked += 1
            updated_lines.append(json.dumps(data, sort_keys=True))
        if marked > 0:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
            os.replace(str(tmp), str(path))
        return marked

    def _path_for(self, skill_name: str) -> Path:
        safe_name = skill_name.replace("/", "_")
        return self.root / f"{safe_name}.jsonl"
