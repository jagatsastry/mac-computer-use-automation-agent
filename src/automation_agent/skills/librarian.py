"""Skill Librarian: promotes high-confidence observations into canonical skills."""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

import structlog

from automation_agent.config import AgentConfig
from automation_agent.skills.experience import (
    SkillExperienceStore,
    _normalize_key,
)
from automation_agent.skills.llm_utils import call_skill_llm
from automation_agent.skills.models import (
    PromotionDecision,
    Skill,
    SkillObservation,
)

if TYPE_CHECKING:
    from automation_agent.shared_models import StepResult
    from automation_agent.skills.derived_skill import DerivedSkillSession

logger = structlog.get_logger(__name__)

_DECIDE_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "librarian_decide.md"
_GENERATE_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "librarian_generate.md"

_NORMALIZE_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")
_STOP_WORDS = frozenset(
    "a an the is are was were be been being do does did will would shall should "
    "can could may might must have has had having for of to in on at by with from "
    "and or not no nor but if then else that this these those it its they them their "
    "he she his her we our you your all any each every some into also".split()
)


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically via temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(str(tmp), str(path))


class SkillLibrarian:
    """Post-run evaluator that promotes observations into canonical skills."""

    def __init__(
        self,
        config: AgentConfig,
        experience_store: SkillExperienceStore,
        registry: Any,  # SkillRegistryImpl — avoid circular import
    ) -> None:
        self.config = config
        self.experience_store = experience_store
        self.registry = registry
        # Resolve skill_dir from registry — fail fast if absent
        if not hasattr(registry, "_skill_dir"):
            raise ValueError(
                "SkillLibrarian requires registry._skill_dir to be set. "
                "Cannot fall back to hardcoded path — that would silently "
                "write to the source tree instead of the configured skill dir."
            )
        self._skill_dir: Path = registry._skill_dir
        self._decide_template = ""
        self._generate_template = ""
        if _DECIDE_TEMPLATE_PATH.exists():
            self._decide_template = _DECIDE_TEMPLATE_PATH.read_text(encoding="utf-8")
        if _GENERATE_TEMPLATE_PATH.exists():
            self._generate_template = _GENERATE_TEMPLATE_PATH.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate_run(
        self,
        goal: str,
        skill_name: str,
        derived_session: Optional[DerivedSkillSession],
        observations: list[SkillObservation],
        trace: list[StepResult],
        run_id: str,
        had_replan: bool,
        success: bool,
    ) -> Optional[PromotionDecision]:
        """Evaluate accumulated observations and optionally promote."""
        if not self.config.skill_librarian_enabled:
            return None

        try:
            return await self._evaluate_run_inner(
                goal=goal,
                skill_name=skill_name,
                derived_session=derived_session,
                observations=observations,
                trace=trace,
                run_id=run_id,
                had_replan=had_replan,
                success=success,
            )
        except Exception:
            logger.warning("skill_librarian_evaluate_failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Core evaluation logic
    # ------------------------------------------------------------------

    async def _evaluate_run_inner(
        self,
        goal: str,
        skill_name: str,
        derived_session: Optional[DerivedSkillSession],
        observations: list[SkillObservation],
        trace: list[StepResult],
        run_id: str,
        had_replan: bool,
        success: bool,
    ) -> Optional[PromotionDecision]:
        logger.info("skill_librarian_evaluation_started", skill_name=skill_name, run_id=run_id)

        # Load ALL observations for this skill (not just the ones from this run)
        all_observations = self.experience_store.load(skill_name)
        if not all_observations:
            logger.info("skill_librarian_no_observations", skill_name=skill_name)
            return None

        # Group and filter
        groups = self._group_observations(all_observations)
        promoted_history = self._load_promotion_history(skill_name)

        # Find qualifying groups
        qualifying: list[tuple[tuple[str, str], list[SkillObservation], float]] = []
        for key, group in groups.items():
            # Skip already promoted
            if key in promoted_history:
                continue
            # Check thresholds
            if len(group) < self.config.skill_librarian_min_observations:
                continue
            distinct_runs = len({o.run_id for o in group if o.run_id})
            if distinct_runs < self.config.skill_librarian_min_runs:
                continue
            score = self._compute_score(group)
            if score < self.config.skill_librarian_min_confidence:
                continue
            qualifying.append((key, group, score))

        if not qualifying:
            logger.info("skill_librarian_no_qualifying_groups", skill_name=skill_name)
            return None

        # Pick highest scoring group (one promotion per run)
        qualifying.sort(key=lambda x: x[2], reverse=True)
        best_key, best_group, best_score = qualifying[0]

        parent_skill = self.registry.get_skill(skill_name)
        if parent_skill is None:
            logger.warning("skill_librarian_skill_not_found", skill_name=skill_name)
            return None

        # LLM Call 1: decide promotion type
        llm_decision = await self._decide_promotion_type(
            skill_name=skill_name,
            group=best_group,
            score=best_score,
            parent_skill=parent_skill,
            derived_session=derived_session,
            success=success,
        )
        if llm_decision is None:
            logger.info("skill_librarian_llm_decide_failed", skill_name=skill_name)
            return None

        promotion_type = llm_decision.get("promotion_type", "observation_only")
        reason = llm_decision.get("reason", "")

        distinct_run_count = len({o.run_id for o in best_group if o.run_id})
        obs_keys = [[best_key[0], best_key[1]]]

        if promotion_type == "observation_only":
            decision = PromotionDecision(
                skill_name=skill_name,
                promotion_type="observation_only",
                reason=reason,
                confidence_score=best_score,
                observation_keys=obs_keys,
                run_id=run_id,
                timestamp=datetime.utcnow().isoformat(),
                observation_count=len(best_group),
                distinct_run_count=distinct_run_count,
            )
            logger.info(
                "skill_librarian_decision",
                skill_name=skill_name,
                promotion_type="observation_only",
                reason=reason,
            )
            return decision

        # Check tip limit for patch_parent
        if promotion_type == "patch_parent":
            existing_tips = parent_skill.learned_tips_text or ""
            existing_count = len(
                [l for l in existing_tips.splitlines() if l.strip().startswith("-")]
            )
            if existing_count >= self.config.skill_librarian_max_tips:
                decision = PromotionDecision(
                    skill_name=skill_name,
                    promotion_type="observation_only",
                    reason="tip_limit_reached",
                    confidence_score=best_score,
                    observation_keys=obs_keys,
                    run_id=run_id,
                    timestamp=datetime.utcnow().isoformat(),
                    observation_count=len(best_group),
                    distinct_run_count=distinct_run_count,
                )
                logger.info("skill_librarian_tip_limit_reached", skill_name=skill_name)
                return decision

        # LLM Call 2: generate content
        content = await self._generate_content(
            promotion_type=promotion_type,
            parent_skill=parent_skill,
            group=best_group,
            derived_session=derived_session,
        )
        if content is None:
            return PromotionDecision(
                skill_name=skill_name,
                promotion_type="observation_only",
                reason="content_generation_failed",
                confidence_score=best_score,
                observation_keys=obs_keys,
                run_id=run_id,
                timestamp=datetime.utcnow().isoformat(),
                observation_count=len(best_group),
                distinct_run_count=distinct_run_count,
            )

        # Compute baseline before committing
        baseline = self._compute_baseline(best_group)

        # Commit based on promotion type
        if promotion_type == "patch_parent":
            return await self._commit_patch_parent(
                skill_name=skill_name,
                parent_skill=parent_skill,
                tips_text=content.get("learned_tips", ""),
                score=best_score,
                obs_keys=obs_keys,
                run_id=run_id,
                best_key=best_key,
                best_group=best_group,
                reason=reason,
                baseline=baseline,
                distinct_run_count=distinct_run_count,
            )
        elif promotion_type == "create_sibling":
            return await self._commit_create_sibling(
                skill_name=skill_name,
                parent_skill=parent_skill,
                md_content=content.get("sibling_skill_md", ""),
                score=best_score,
                obs_keys=obs_keys,
                run_id=run_id,
                best_key=best_key,
                best_group=best_group,
                reason=reason,
                baseline=baseline,
                distinct_run_count=distinct_run_count,
            )

        return None

    # ------------------------------------------------------------------
    # Grouping and scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_topic(text: str, n_words: int = 4) -> str:
        """Extract first N content words (skipping stop words) as a topic key.

        This produces coarser grouping than full-text normalization, allowing
        semantically similar observations to cluster together.
        """
        text = text.strip().lower()
        text = _NORMALIZE_RE.sub("", text)
        words = _WHITESPACE_RE.split(text)
        content = [w for w in words if w and w not in _STOP_WORDS]
        return " ".join(content[:n_words])

    def _group_observations(
        self, observations: list[SkillObservation]
    ) -> Dict[Tuple[str, str], list[SkillObservation]]:
        """Group observations by (category, recommendation).

        Uses normalized (category, recommendation) keys so that grouping
        aligns with ``mark_promoted()`` in ``SkillExperienceStore``, which
        also keys by ``(category, recommendation)``.
        """
        groups: dict[tuple[str, str], list[SkillObservation]] = defaultdict(list)
        for obs in observations:
            cat = _normalize_key(obs.category)
            rec = _normalize_key(obs.recommendation)
            groups[(cat, rec)].append(obs)
        return dict(groups)

    def _compute_score(self, group: list[SkillObservation]) -> float:
        alpha = sum(1 for o in group if o.confidence >= 0.6)
        beta = sum(1 for o in group if o.confidence < 0.4)
        return (alpha + 1) / (alpha + beta + 2)

    def _compute_baseline(self, group: list[SkillObservation]) -> Dict:
        """Compute pre-promotion friction baseline from observation run_ids."""
        run_ids = {o.run_id for o in group if o.run_id}
        friction_runs = len(run_ids)
        # We don't have per-run success data in observations, so baseline is count-based
        return {
            "friction_runs": friction_runs,
            "friction_successes": 0,  # not available from observations alone
            "friction_success_rate": None,
        }

    # ------------------------------------------------------------------
    # Promotion history
    # ------------------------------------------------------------------

    def _load_promotion_history(self, skill_name: str) -> Set[Tuple[str, str]]:
        """Load previously promoted (category, recommendation) keys for a skill."""
        history_path = self.config.skill_learning_dir / "promotions" / "history.jsonl"
        if not history_path.exists():
            return set()
        promoted: set[tuple[str, str]] = set()
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("skill_name") != skill_name:
                continue
            for key_pair in data.get("observation_keys", []):
                if isinstance(key_pair, list) and len(key_pair) == 2:
                    promoted.add((key_pair[0], key_pair[1]))
        return promoted

    def _write_history(self, decision: PromotionDecision) -> None:
        """Append a promotion decision to history.jsonl."""
        history_dir = self.config.skill_learning_dir / "promotions"
        history_dir.mkdir(parents=True, exist_ok=True)
        history_path = history_dir / "history.jsonl"
        data = asdict(decision)
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data, sort_keys=True) + "\n")

    # ------------------------------------------------------------------
    # LLM calls
    # ------------------------------------------------------------------

    async def _decide_promotion_type(
        self,
        skill_name: str,
        group: list[SkillObservation],
        score: float,
        parent_skill: Skill,
        derived_session: Optional[DerivedSkillSession],
        success: bool,
    ) -> Optional[Dict]:
        obs_summary = "\n".join(
            f"- [{o.category}] {o.condition} -> {o.recommendation} (conf={o.confidence:.2f})"
            for o in group[:10]  # cap summary length
        )
        derived_summary = ""
        if derived_session is not None:
            try:
                derived_summary = derived_session.serialize_for_context()
            except Exception:
                derived_summary = "Error serializing derived session"

        existing_tips = parent_skill.learned_tips_text or "None"

        prompt = self._decide_template
        prompt = prompt.replace("{{skill_name}}", skill_name)
        prompt = prompt.replace("{{skill_summary}}", parent_skill.summary or parent_skill.description)
        prompt = prompt.replace("{{observation_summary}}", obs_summary)
        prompt = prompt.replace("{{bayesian_score}}", f"{score:.3f}")
        prompt = prompt.replace("{{distinct_runs}}", str(len({o.run_id for o in group if o.run_id})))
        prompt = prompt.replace("{{derived_session_summary}}", derived_summary or "None")
        prompt = prompt.replace("{{success}}", str(success))
        prompt = prompt.replace("{{existing_tips}}", existing_tips)

        raw = await call_skill_llm(self.config, prompt, max_tokens=256)
        return self._parse_response(raw)

    async def _generate_content(
        self,
        promotion_type: str,
        parent_skill: Skill,
        group: list[SkillObservation],
        derived_session: Optional[DerivedSkillSession],
    ) -> Optional[Dict]:
        obs_text = "\n".join(
            f"- [{o.category}] When {o.condition}, {o.recommendation} "
            f"(confidence={o.confidence:.2f}, run={o.run_id})"
            for o in group
        )
        derived_full = ""
        if derived_session is not None:
            try:
                derived_full = derived_session.serialize_for_context()
            except Exception:
                derived_full = "Error serializing derived session"

        existing_tips = parent_skill.learned_tips_text or "None"
        max_tokens = 4096 if promotion_type == "create_sibling" else 1024

        prompt = self._generate_template
        prompt = prompt.replace("{{promotion_type}}", promotion_type)
        prompt = prompt.replace("{{parent_skill_raw}}", parent_skill.raw_content)
        prompt = prompt.replace("{{observations}}", obs_text)
        prompt = prompt.replace("{{derived_session_full}}", derived_full or "None")
        prompt = prompt.replace("{{existing_tips}}", existing_tips)

        # Handle conditional sections
        if promotion_type == "patch_parent":
            # Keep patch_parent block, remove create_sibling block
            prompt = re.sub(
                r"\{%\s*if promotion_type == \"patch_parent\"\s*%\}(.*?)\{%\s*endif\s*%\}",
                r"\1",
                prompt,
                flags=re.DOTALL,
            )
            prompt = re.sub(
                r"\{%\s*if promotion_type == \"create_sibling\"\s*%\}.*?\{%\s*endif\s*%\}",
                "",
                prompt,
                flags=re.DOTALL,
            )
        elif promotion_type == "create_sibling":
            prompt = re.sub(
                r"\{%\s*if promotion_type == \"create_sibling\"\s*%\}(.*?)\{%\s*endif\s*%\}",
                r"\1",
                prompt,
                flags=re.DOTALL,
            )
            prompt = re.sub(
                r"\{%\s*if promotion_type == \"patch_parent\"\s*%\}.*?\{%\s*endif\s*%\}",
                "",
                prompt,
                flags=re.DOTALL,
            )

        raw = await call_skill_llm(self.config, prompt, max_tokens=max_tokens)
        return self._parse_response(raw)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, response: str) -> Optional[Dict]:
        """Parse LLM response: strip fences, json.loads, validate."""
        text = response.strip()
        if text.startswith("```"):
            lines = [l for l in text.splitlines() if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("skill_librarian_parse_failed", response=text[:200])
            return None
        if not isinstance(data, dict):
            return None

        # Validate based on what fields are present
        if "promotion_type" in data:
            valid_types = {"patch_parent", "create_sibling", "observation_only"}
            if data["promotion_type"] not in valid_types:
                logger.warning(
                    "skill_librarian_invalid_promotion_type",
                    promotion_type=data.get("promotion_type"),
                )
                return None
            if not data.get("reason"):
                return None

        return data

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_tips(self, tips_text: str) -> bool:
        """Validate generated tips text."""
        if not tips_text or not tips_text.strip():
            return False
        if "---" in tips_text:
            return False
        if "## Steps" in tips_text:
            return False
        if "## Error Recovery" in tips_text:
            return False
        return True

    def _validate_sibling_md(self, md_content: str) -> bool:
        """Validate generated sibling skill markdown.

        Enforces the full schema expected by loader.py and
        build_runtime_context: frontmatter fields (summary, tags) and
        body sections (## Error Recovery, ## Notes) are all required.
        """
        from automation_agent.skills.loader import parse_skill_file

        try:
            skill = parse_skill_file(md_content)
        except Exception:
            return False

        # Required fields check (same as validate_all)
        if not skill.trigger_keywords:
            return False
        if not skill.steps_text:
            return False
        if not skill.success_condition:
            return False

        # Full schema: summary and tags required for SkillCard / routing
        if not skill.summary:
            return False
        if not skill.tags:
            return False

        # Required body sections used by build_runtime_context
        if not skill.error_recovery_text:
            return False
        if not skill.notes_text:
            return False

        # parent-skill-id is required for siblings.
        # No fallback re-parse needed: parse_skill_file() faithfully extracts
        # the parent-skill-id frontmatter field into skill.parent_skill_id,
        # so a direct check is sufficient.
        if not skill.parent_skill_id:
            return False

        return True

    # ------------------------------------------------------------------
    # File manipulation
    # ------------------------------------------------------------------

    def _apply_patch_parent(self, skill_name: str, tips_text: str) -> str:
        """Append tips to the skill's Learned Tips section. Returns updated content."""
        skill = self.registry.get_skill(skill_name)
        content = skill.raw_content

        pattern = re.compile(
            r"^(##\s+Learned\s+Tips\s*\n)(.*?)(?=^##\s|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(content)

        if match:
            section_end = match.end(2)
            existing = match.group(2).rstrip()
            new_content = (
                content[: match.start(2)]
                + existing
                + "\n"
                + tips_text
                + "\n\n"
                + content[section_end:].lstrip("\n")
            )
        else:
            new_content = content.rstrip() + "\n\n## Learned Tips\n" + tips_text + "\n"

        return new_content

    def _apply_create_sibling(
        self, md_content: str, parent_skill: Skill
    ) -> Tuple[str, str, str]:
        """Write a new sibling skill file. Returns (skill_id, file_path, final_md)."""
        from automation_agent.skills.loader import parse_skill_file

        skill = parse_skill_file(md_content)
        skill_name = skill.name

        # Handle name collision
        existing = self.registry._skills
        if skill_name in existing:
            suffix = 2
            while f"{skill_name}-{suffix}" in existing:
                suffix += 1
            # Rewrite name in frontmatter
            new_name = f"{skill_name}-{suffix}"
            md_content = md_content.replace(
                f"name: {skill_name}", f"name: {new_name}", 1
            )
            if skill.skill_id:
                md_content = md_content.replace(
                    f"skill-id: {skill.skill_id}",
                    f"skill-id: {new_name}",
                    1,
                )
            skill_name = new_name

        skill_id = skill_name

        # Security: auto-promoted skills default to untrusted for embedding index.
        # Requires human review to set trusted: true.
        if "trusted:" not in md_content:
            md_content = md_content.replace("---\n", "---\ntrusted: false\n", 1)

        # Determine write path from registry's configured skill_dir
        file_path = self._skill_dir / f"{skill_name.replace('/', '_')}.md"
        _atomic_write(file_path, md_content)

        return skill_id, str(file_path), md_content

    # ------------------------------------------------------------------
    # Mark promoted (delegate to experience store)
    # ------------------------------------------------------------------

    def _mark_promoted(
        self, skill_name: str, keys: list[tuple[str, str]]
    ) -> None:
        try:
            self.experience_store.mark_promoted(skill_name, set(keys))
        except Exception:
            logger.warning("skill_librarian_mark_promoted_failed", exc_info=True)

    # ------------------------------------------------------------------
    # Commit sequences
    # ------------------------------------------------------------------

    async def _commit_patch_parent(
        self,
        skill_name: str,
        parent_skill: Skill,
        tips_text: str,
        score: float,
        obs_keys: list[list[str]],
        run_id: str,
        best_key: tuple[str, str],
        best_group: list[SkillObservation],
        reason: str,
        baseline: Dict,
        distinct_run_count: int,
    ) -> PromotionDecision:
        """Execute patch_parent commit sequence with rollback."""
        if not self._validate_tips(tips_text):
            return PromotionDecision(
                skill_name=skill_name,
                promotion_type="observation_only",
                reason="tips_validation_failed",
                confidence_score=score,
                observation_keys=obs_keys,
                run_id=run_id,
                timestamp=datetime.utcnow().isoformat(),
                observation_count=len(best_group),
                distinct_run_count=distinct_run_count,
            )

        # Step 1: backup + apply
        backup = parent_skill.raw_content
        updated = self._apply_patch_parent(skill_name, tips_text)

        # Step 2: write file — fail if canonical file can't be found on disk
        skill_path = self._find_skill_path(skill_name)
        if not skill_path:
            logger.warning(
                "skill_librarian_patch_parent_no_file",
                skill_name=skill_name,
                skill_dir=str(self._skill_dir),
            )
            return PromotionDecision(
                skill_name=skill_name,
                promotion_type="observation_only",
                reason="skill_file_not_found",
                confidence_score=score,
                observation_keys=obs_keys,
                run_id=run_id,
                timestamp=datetime.utcnow().isoformat(),
                observation_count=len(best_group),
                distinct_run_count=distinct_run_count,
            )
        _atomic_write(skill_path, updated)

        # Step 3: reload into registry
        try:
            self.registry.load_from_string(updated)
        except Exception:
            logger.warning("skill_librarian_reload_failed", exc_info=True)
            # Rollback file
            if skill_path:
                _atomic_write(skill_path, backup)
            try:
                self.registry.load_from_string(backup)
            except Exception:
                logger.critical(
                    "skill_librarian_rollback_failed_inconsistent_state"
                )
            return PromotionDecision(
                skill_name=skill_name,
                promotion_type="observation_only",
                reason="rollback_after_reload_failure",
                confidence_score=score,
                observation_keys=obs_keys,
                run_id=run_id,
                timestamp=datetime.utcnow().isoformat(),
                observation_count=len(best_group),
                distinct_run_count=distinct_run_count,
            )

        decision = PromotionDecision(
            skill_name=skill_name,
            promotion_type="patch_parent",
            reason=reason,
            confidence_score=score,
            observation_keys=obs_keys,
            run_id=run_id,
            timestamp=datetime.utcnow().isoformat(),
            generated_tips=tips_text,
            parent_skill_id=parent_skill.skill_id,
            observation_count=len(best_group),
            distinct_run_count=distinct_run_count,
            pre_promotion_baseline=baseline,
        )

        # Step 4: write history
        try:
            self._write_history(decision)
        except Exception:
            logger.warning("skill_librarian_history_write_failed", exc_info=True)
            # Rollback
            if skill_path:
                _atomic_write(skill_path, backup)
            try:
                self.registry.load_from_string(backup)
            except Exception:
                logger.critical(
                    "skill_librarian_rollback_failed_inconsistent_state"
                )
            return PromotionDecision(
                skill_name=skill_name,
                promotion_type="observation_only",
                reason="rollback_after_history_write_failure",
                confidence_score=score,
                observation_keys=obs_keys,
                run_id=run_id,
                timestamp=datetime.utcnow().isoformat(),
                observation_count=len(best_group),
                distinct_run_count=distinct_run_count,
            )

        # Step 5: mark promoted (best-effort)
        self._mark_promoted(skill_name, [best_key])

        logger.info(
            "skill_librarian_promotion_applied",
            skill_name=skill_name,
            promotion_type="patch_parent",
            tips=tips_text[:100],
        )
        return decision

    async def _commit_create_sibling(
        self,
        skill_name: str,
        parent_skill: Skill,
        md_content: str,
        score: float,
        obs_keys: list[list[str]],
        run_id: str,
        best_key: tuple[str, str],
        best_group: list[SkillObservation],
        reason: str,
        baseline: Dict,
        distinct_run_count: int,
    ) -> PromotionDecision:
        """Execute create_sibling commit sequence with rollback."""
        if not self._validate_sibling_md(md_content):
            return PromotionDecision(
                skill_name=skill_name,
                promotion_type="observation_only",
                reason="sibling_validation_failed",
                confidence_score=score,
                observation_keys=obs_keys,
                run_id=run_id,
                timestamp=datetime.utcnow().isoformat(),
                observation_count=len(best_group),
                distinct_run_count=distinct_run_count,
            )

        # Step 1: write file (returns final content after collision rename + trusted flag)
        new_skill_id, file_path, final_md = self._apply_create_sibling(
            md_content, parent_skill
        )

        # Step 2: load into registry (use final_md which has any renames applied)
        try:
            self.registry.load_from_string(final_md)
        except Exception:
            logger.warning("skill_librarian_sibling_reload_failed", exc_info=True)
            # Rollback: delete file
            try:
                Path(file_path).unlink(missing_ok=True)
            except Exception:
                pass
            self.registry._skills.pop(new_skill_id, None)
            return PromotionDecision(
                skill_name=skill_name,
                promotion_type="observation_only",
                reason="rollback_after_sibling_reload_failure",
                confidence_score=score,
                observation_keys=obs_keys,
                run_id=run_id,
                timestamp=datetime.utcnow().isoformat(),
                observation_count=len(best_group),
                distinct_run_count=distinct_run_count,
            )

        decision = PromotionDecision(
            skill_name=skill_name,
            promotion_type="create_sibling",
            reason=reason,
            confidence_score=score,
            observation_keys=obs_keys,
            run_id=run_id,
            timestamp=datetime.utcnow().isoformat(),
            new_skill_id=new_skill_id,
            new_skill_path=file_path,
            parent_skill_id=parent_skill.skill_id,
            observation_count=len(best_group),
            distinct_run_count=distinct_run_count,
            pre_promotion_baseline=baseline,
        )

        # Step 3: write history
        try:
            self._write_history(decision)
        except Exception:
            logger.warning("skill_librarian_history_write_failed", exc_info=True)
            try:
                Path(file_path).unlink(missing_ok=True)
            except Exception:
                pass
            self.registry._skills.pop(new_skill_id, None)
            return PromotionDecision(
                skill_name=skill_name,
                promotion_type="observation_only",
                reason="rollback_after_history_write_failure",
                confidence_score=score,
                observation_keys=obs_keys,
                run_id=run_id,
                timestamp=datetime.utcnow().isoformat(),
                observation_count=len(best_group),
                distinct_run_count=distinct_run_count,
            )

        # Step 4: mark promoted (best-effort)
        self._mark_promoted(skill_name, [best_key])

        logger.info(
            "skill_librarian_promotion_applied",
            skill_name=skill_name,
            promotion_type="create_sibling",
            new_skill_id=new_skill_id,
        )
        return decision

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_skill_path(self, skill_name: str) -> Optional[Path]:
        """Find the .md file path for a skill on disk.

        Searches in registry's configured skill_dir, not a hardcoded path.

        Separator assumption: the global replace('-', '_') / replace('_', '-')
        below assumes skill file names use consistent separators — either
        all-dashes (e.g., ``return-amazon-order.md``) or all-underscores
        (e.g., ``return_amazon_order.md``), never mixed within a single name.
        This convention is enforced by the skill file naming pattern in
        ``_apply_create_sibling`` and the canonical library files.
        """
        safe_name = skill_name.replace("/", "_")
        path = self._skill_dir / f"{safe_name}.md"
        if path.exists():
            return path
        # Try with dashes -> underscores (consistent separator convention)
        path2 = self._skill_dir / f"{safe_name.replace('-', '_')}.md"
        if path2.exists():
            return path2
        # Try with underscores -> dashes (consistent separator convention)
        path3 = self._skill_dir / f"{safe_name.replace('_', '-')}.md"
        if path3.exists():
            return path3
        return None
