"""Skill registry: loads, matches, and expands skill templates."""

import logging
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import structlog

from automation_agent.config import AgentConfig
from automation_agent.logging.models import EventType
from automation_agent.shared_models import (
    MatchType,
    SkillMatchResult,
    SkillRouteCandidate,
    StepResult,
)

if TYPE_CHECKING:
    from automation_agent.skills.embeddings import EmbeddingIndex
from automation_agent.skills.distiller import SkillDistiller
from automation_agent.skills.experience import SkillExperienceStore
from automation_agent.skills.loader import load_skill_from_file, parse_skill_file
from automation_agent.skills.matcher import match_skill
from automation_agent.skills.models import Skill, SkillCard, SkillObservation
from automation_agent.skills.router import MIN_USEFUL_CONFIDENCE, SkillRouter

std_logger = logging.getLogger(__name__)
logger = structlog.get_logger(__name__)


def _build_card(skill: Skill) -> SkillCard:
    """Build a SkillCard from a Skill object."""
    skill_id = skill.skill_id or skill.name
    title = skill_id.replace("-", " ").replace("_", " ").title()
    summary = skill.summary or skill.description
    tags = list(skill.tags) if skill.tags else list(skill.trigger_keywords)
    param_names = list(skill.parameters.keys()) if skill.parameters else []
    return SkillCard(
        skill_id=skill_id,
        title=title,
        summary=summary,
        tags=tags,
        required_apps=list(skill.requires.apps),
        required_os=skill.requires.os,
        param_names=param_names,
    )


def _build_all_cards(skills: Dict[str, Skill]) -> list[SkillCard]:
    """Build cards for all skills. Filters out skills that fail card building."""
    cards = []
    for skill in skills.values():
        try:
            cards.append(_build_card(skill))
        except Exception:
            continue
    return cards


class SkillRegistryImpl:
    """Concrete implementation of the SkillRegistry protocol.

    Loads skill templates from .md files (YAML frontmatter + Markdown body),
    matches user prompts to skills via an LLM-driven router (with keyword
    fallback), and expands templates with parameter values.
    """

    def __init__(
        self,
        skill_dir: Optional[Path] = None,
        config: Optional[AgentConfig] = None,
        event_logger: Optional[Any] = None,
    ) -> None:
        self._skills: Dict[str, Skill] = {}
        self._cards: list[SkillCard] = []
        self._skill_dir = skill_dir or Path(__file__).parent / "library"
        self._config = config
        self._event_logger = event_logger
        self._experience_store: Optional[SkillExperienceStore] = None
        self._distiller: Optional[SkillDistiller] = None
        self._librarian = None
        if self._config is not None and self._config.skill_learning_enabled:
            self._experience_store = SkillExperienceStore(self._config.skill_learning_dir)
            self._distiller = SkillDistiller(self._config)
        if (
            self._config is not None
            and self._config.skill_librarian_enabled
            and self._experience_store is not None
        ):
            from automation_agent.skills.librarian import SkillLibrarian

            self._librarian = SkillLibrarian(
                config=self._config,
                experience_store=self._experience_store,
                registry=self,
            )
        if self._skill_dir.is_dir():
            self.load_from_directory(self._skill_dir)
        # Router created lazily after skills are loaded
        self._router: Optional[SkillRouter] = None
        # Gap 3: Embedding index for semantic skill retrieval
        self._embedding_index: Optional["EmbeddingIndex"] = None
        if self._config is not None:
            self._rebuild_router()

    def _rebuild_router(self) -> None:
        """Rebuild the router, cached cards, and embedding index from current skills."""
        self._cards = _build_all_cards(self._skills)
        if len(self._cards) >= 25:
            logger.warning(
                "Skill count approaching router prompt budget",
                skill_count=len(self._cards),
            )
        if self._config is not None:
            self._router = SkillRouter(self._config, self._skills, self._cards)

        # AC-18: rebuild embedding index when skills change
        if self._config and self._config.skill_embedding_enabled:
            try:
                from automation_agent.skills.embeddings import EmbeddingIndex

                if self._embedding_index is None:
                    self._embedding_index = EmbeddingIndex(
                        self._config.skill_embedding_model
                    )
                trusted_skills = {
                    name: skill
                    for name, skill in self._skills.items()
                    if skill.metadata.get("trusted", True)
                }
                _build_start = time.monotonic()
                self._embedding_index.build(trusted_skills)
                _build_ms = int((time.monotonic() - _build_start) * 1000)
                logger.info(
                    "embedding_index_built",
                    skill_count=len(trusted_skills),
                    model=self._config.skill_embedding_model,
                    duration_ms=_build_ms,
                )
                if self._event_logger:
                    self._event_logger.log_event(
                        EventType.EMBEDDING_BUILD,
                        f"Embedding index built ({len(trusted_skills)} skills)",
                        data={
                            "skill_count": len(trusted_skills),
                            "model": self._config.skill_embedding_model,
                        },
                        duration_ms=_build_ms,
                    )
            except (ImportError, OSError, RuntimeError) as e:
                # AC-19: graceful degradation
                std_logger.warning(
                    "Embedding index build failed: %s. Falling back to keyword matching.",
                    e,
                )
                if self._event_logger:
                    self._event_logger.log_event(
                        EventType.EMBEDDING_ERROR,
                        f"Embedding build failed: {e}",
                        data={"error": str(e)},
                    )
                self._embedding_index = None

    def load_from_directory(self, path: Path) -> None:
        """Load all .md skill files from a directory.

        Skills whose OS requirement does not match the current platform are
        silently skipped.

        Args:
            path: Directory containing .md skill files.
        """
        if not path.is_dir():
            return
        for md_file in sorted(path.glob("*.md")):
            try:
                skill = load_skill_from_file(md_file)
                # Gate by OS requirement
                if not _os_matches(skill.requires.os):
                    continue
                if skill.name in self._skills:
                    std_logger.warning(
                        "Duplicate skill name '%s': '%s' overwrites previous definition",
                        skill.name,
                        md_file,
                    )
                self._skills[skill.name] = skill
            except Exception:
                # Skip malformed files during loading; validate_all catches them
                continue
        # Rebuild router when skills change
        self._rebuild_router()

    def load_from_string(self, content: str) -> Skill:
        """Load a single skill from raw file content.

        Args:
            content: YAML frontmatter + Markdown body.

        Returns:
            The parsed Skill (also registered internally).
        """
        skill = parse_skill_file(content)
        self._skills[skill.name] = skill
        # Rebuild router when skills change
        self._rebuild_router()
        return skill

    def _build_match_result(
        self,
        skill: Skill,
        prompt: str,
        candidates: list[SkillRouteCandidate],
        params: Optional[Dict[str, str]] = None,
    ) -> SkillMatchResult:
        """Helper to build SkillMatchResult from a skill and candidates."""
        params = params or {}
        try:
            expanded = self.expand(skill.name, params)
        except (ValueError, KeyError):
            expanded = None
        skill_context = self._build_multi_skill_context(candidates, params)
        return SkillMatchResult(
            skill_name=skill.name,
            expanded_steps=expanded or skill.steps_text,
            skill_context=skill_context,
            params=params,
            candidates=candidates,
        )

    async def match(self, prompt: str) -> Optional[SkillMatchResult]:
        """AC-17: Three-stage pipeline — embed → conditional LLM re-rank → keyword fallback.

        Returns:
            SkillMatchResult with skill_name, expanded_steps, skill_context,
            params, and candidates, or None if no match.
        """
        # Stage 1: Embedding retrieval (AC-20: gated by config)
        if (
            self._config
            and self._config.skill_embedding_enabled
            and self._embedding_index is not None
        ):
            _emb_start = time.monotonic()
            emb_candidates = self._embedding_index.query(prompt, top_k=5)
            _emb_duration = int((time.monotonic() - _emb_start) * 1000)

            # Observability (DE review round 3, issue 13): structured embedding query log
            if emb_candidates:
                logger.info(
                    "embedding_query",
                    event_type="embedding_query",
                    top_skill=emb_candidates[0].skill_id,
                    top_sim=round(emb_candidates[0].confidence, 3),
                    top_k=[
                        {"id": c.skill_id, "sim": round(c.confidence, 3)}
                        for c in emb_candidates
                    ],
                    prompt=prompt[:100],
                    duration_ms=_emb_duration,
                )
                if self._event_logger:
                    self._event_logger.log_event(
                        EventType.EMBEDDING_QUERY,
                        f"Embedding query: top={emb_candidates[0].skill_id} "
                        f"sim={emb_candidates[0].confidence:.3f}",
                        data={
                            "top_skill": emb_candidates[0].skill_id,
                            "top_sim": round(emb_candidates[0].confidence, 3),
                            "candidate_count": len(emb_candidates),
                        },
                        duration_ms=_emb_duration,
                    )

            if emb_candidates:
                top_sim = emb_candidates[0].confidence
                top_gap = (
                    emb_candidates[0].confidence - emb_candidates[1].confidence
                    if len(emb_candidates) > 1
                    else 1.0
                )

                # AC-17: skip LLM re-rank for clear winners
                if (
                    top_sim >= self._config.skill_embedding_rerank_threshold
                    and top_gap >= self._config.skill_embedding_min_gap
                ):
                    best = emb_candidates[0]
                    skill = self._skills.get(best.skill_id)
                    if skill:
                        # Observability: structured rerank-skip log
                        logger.info(
                            "embedding_rerank_skip",
                            event_type="embedding_rerank_skip",
                            skill_id=best.skill_id,
                            similarity=top_sim,
                            gap=top_gap,
                        )
                        if self._event_logger:
                            self._event_logger.log_event(
                                EventType.EMBEDDING_RERANK_SKIP,
                                f"Embedding rerank skipped: {best.skill_id} "
                                f"(sim={top_sim:.3f}, gap={top_gap:.3f})",
                                data={
                                    "skill_id": best.skill_id,
                                    "similarity": round(top_sim, 3),
                                    "gap": round(top_gap, 3),
                                },
                            )
                        return self._build_match_result(skill, prompt, [best])

                # Stage 2a: LLM re-rank on embedding candidates only
                if self._router is not None:
                    route_result = await self._router.route(
                        prompt,
                        candidate_ids=[c.skill_id for c in emb_candidates],
                    )
                    if route_result and route_result.primary:
                        valid = [
                            c
                            for c in route_result.candidates
                            if c.confidence >= MIN_USEFUL_CONFIDENCE
                        ]
                        if valid:
                            # Finding 7 fix: use valid[0].skill_id, not route_result.primary
                            skill = self._skills.get(valid[0].skill_id)
                            if skill:
                                params = route_result.params or {}
                                logger.info(
                                    "Skill matched via embedding + LLM re-rank",
                                    skill_name=valid[0].skill_id,
                                    params=params,
                                )
                                return self._build_match_result(
                                    skill, prompt, valid, params
                                )

        # Stage 2b fallback: full LLM routing (existing behavior)
        if self._router is not None:
            router_result = await self._router.route(prompt)
            if router_result is not None:
                valid = [
                    c
                    for c in router_result.candidates
                    if c.confidence >= MIN_USEFUL_CONFIDENCE
                ]
                if not valid:
                    logger.info(
                        "All router candidates below confidence threshold",
                        threshold=MIN_USEFUL_CONFIDENCE,
                    )
                    return None

                primary = valid[0]
                skill_name = primary.skill_id
                params = router_result.params or {}
                try:
                    expanded = self.expand(skill_name, params)
                except (ValueError, KeyError):
                    expanded = None
                skill = self._skills[skill_name]
                skill_context = self._build_multi_skill_context(valid, params)

                logger.info(
                    "Skill matched via LLM router",
                    skill_name=skill_name,
                    params=params,
                    candidates=len(valid),
                )
                return SkillMatchResult(
                    skill_name=skill_name,
                    expanded_steps=expanded or skill.steps_text,
                    skill_context=skill_context,
                    params=params,
                    candidates=valid,
                )

        # Stage 3: keyword fallback (no param extraction)
        result = match_skill(prompt, list(self._skills.values()))
        if result is None:
            return None
        skill, params, hit_count = result

        # Compute proportional confidence from keyword hits
        total_keywords = len(skill.trigger_keywords)
        confidence = hit_count / total_keywords if total_keywords > 0 else 0.0

        if confidence < MIN_USEFUL_CONFIDENCE:
            logger.info(
                "Keyword fallback confidence below threshold",
                skill_name=skill.name,
                confidence=confidence,
                threshold=MIN_USEFUL_CONFIDENCE,
            )
            return None

        # Keyword fallback doesn't extract params, so skip required-param
        # check — the orchestrator will handle missing params at execution time.
        try:
            expanded = self.expand(skill.name, params)
        except ValueError:
            expanded = None

        candidate = SkillRouteCandidate(
            skill_id=skill.name,
            match_type=MatchType.DIRECT,
            confidence=confidence,
            reason=f"Keyword fallback: {hit_count}/{total_keywords} keywords matched",
        )
        skill_context = self._build_multi_skill_context([candidate])

        logger.info(
            "Skill matched via keyword fallback",
            skill_name=skill.name,
            params=params,
            confidence=confidence,
        )
        return SkillMatchResult(
            skill_name=skill.name,
            expanded_steps=expanded or skill.steps_text,
            skill_context=skill_context,
            params=params,
            candidates=[candidate],
        )

    def _build_multi_skill_context(
        self,
        candidates: list[SkillRouteCandidate],
        primary_params: Optional[Dict[str, str]] = None,
    ) -> str:
        """Build multi-skill context string from routing candidates."""
        sections = [
            "## Skill Priors",
            "",
            "The following skills were selected as procedural priors for this task.",
            "- **direct** matches may be followed closely.",
            "- **analogical** matches are structural priors only -- do NOT assume "
            "site-specific",
            "  labels, buttons, or navigation are identical.",
            "- **generic** matches are general-purpose priors -- use only if more "
            "specific guidance is absent.",
        ]

        for i, candidate in enumerate(candidates):
            skill = self._skills.get(candidate.skill_id)
            if skill is None:
                continue

            sections.append("")
            sections.append(
                f"### [{candidate.match_type.value}] {candidate.skill_id} "
                f"(confidence: {candidate.confidence:.2f})"
            )
            sections.append(f"Reason: {candidate.reason}")
            sections.append("")

            # Build per-skill context (pass params only for primary candidate)
            params = primary_params if i == 0 else None
            runtime_ctx = self.build_runtime_context(candidate.skill_id, params)
            if runtime_ctx:
                sections.append(runtime_ctx)

            sections.append("")
            sections.append("---")

        return "\n".join(sections).strip()

    def list_skills(self) -> List[Dict[str, str]]:
        """List all available skills with name and description."""
        return [
            {"name": s.name, "description": s.description}
            for s in self._skills.values()
        ]

    def expand(self, skill_name: str, params: Dict[str, str]) -> Optional[str]:
        """Expand a skill template with parameter values.

        Replaces ``{{param}}`` placeholders in the steps text.

        Args:
            skill_name: Name of the skill to expand.
            params: Parameter values to substitute.

        Returns:
            Expanded steps text, or None if skill not found.

        Raises:
            ValueError: If a required parameter is missing.
        """
        skill = self._skills.get(skill_name)
        if skill is None:
            return None

        # Check for missing required params
        missing = [
            name
            for name, p in skill.parameters.items()
            if p.required and name not in params
        ]
        if missing:
            raise ValueError(
                f"Missing required parameter(s) for skill '{skill_name}': "
                + ", ".join(missing)
            )

        text = skill.steps_text

        # Single-pass replacement to avoid template injection
        # (user-supplied values containing {{...}} won't be re-expanded)
        def _replace_placeholder(m: re.Match) -> str:
            pname = m.group(1)
            if pname in params:
                return params[pname]
            # Optional param not provided -- log warning and strip
            std_logger.warning(
                "Unexpanded placeholder '{{%s}}' in skill '%s' (stripped)",
                pname,
                skill_name,
            )
            return ""

        text = re.sub(r"\{\{(\w+)\}\}", _replace_placeholder, text)
        return text

    def validate_all(self) -> List[str]:
        """Validate all loaded skills. Returns list of error messages."""
        errors: List[str] = []
        for name, skill in self._skills.items():
            if not skill.name:
                errors.append(f"Skill at '{name}' missing 'name'")
            if not skill.description:
                errors.append(f"Skill '{name}' missing 'description'")
            if not skill.trigger_keywords:
                errors.append(f"Skill '{name}' has no trigger keywords")
            if not skill.steps_text:
                errors.append(f"Skill '{name}' has no Steps section")
            if not skill.success_condition:
                errors.append(f"Skill '{name}' missing 'success-condition'")
        return errors

    def get_skill(self, skill_name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(skill_name)

    def build_runtime_context(
        self, skill_name: str, params: Optional[Dict[str, str]] = None
    ) -> Optional[str]:
        """Build rich skill context for planning and replanning."""
        skill = self._skills.get(skill_name)
        if skill is None:
            return None
        params = params or {}
        try:
            expanded_steps = self.expand(skill_name, params) or skill.steps_text
        except ValueError:
            expanded_steps = skill.steps_text
        sections = [
            f"Skill: {skill.name}",
            f"Description: {skill.description}",
        ]
        if skill.success_condition:
            sections.append(f"Success condition: {skill.success_condition}")
        sections.extend(["", "## Steps", expanded_steps or "No steps available"])
        if skill.error_recovery_text:
            sections.extend(["", "## Recovery Heuristics", skill.error_recovery_text])
        if skill.notes_text:
            sections.extend(["", "## Notes", skill.notes_text])
        if skill.learned_tips_text:
            sections.extend(["", "## Learned Tips", skill.learned_tips_text])
        observations = self._load_observations_for_context(skill_name)
        if observations:
            sections.extend(["", "## Observed Variants"])
            for observation in observations:
                sections.append(
                    "- "
                    f"[{observation.category}] When {observation.condition}, "
                    f"{observation.recommendation}"
                )
        return "\n".join(sections).strip()

    async def learn_from_run(
        self,
        skill_name: str,
        goal: str,
        trace: List[StepResult],
        *,
        skill_context: str = "",
        run_id: str = "",
        had_replan: bool = False,
    ) -> List[SkillObservation]:
        """Distill generalized observations from a run and persist them."""
        if not self._distiller or not self._experience_store or not trace:
            return []
        if not had_replan and not self._trace_deserves_learning(trace):
            return []
        skill = self._skills.get(skill_name)
        if skill is None:
            return []
        runtime_context = (
            skill_context or self.build_runtime_context(skill_name) or skill.steps_text
        )
        observations = await self._distiller.distill(
            skill=skill,
            goal=goal,
            skill_context=runtime_context,
            trace=trace,
            run_id=run_id,
        )
        if had_replan:
            for obs in observations:
                obs.confidence = min(obs.confidence, 0.6)
        if run_id:
            for observation in observations:
                if not observation.run_id:
                    observation.run_id = run_id
        self._experience_store.append(skill_name, observations)
        return observations

    async def promote_from_run(
        self,
        goal: str,
        skill_name: str,
        derived_session,
        observations: List[SkillObservation],
        trace: List[StepResult],
        run_id: str,
        had_replan: bool,
        success: bool,
    ):
        """Evaluate and promote accumulated observations after a run.

        No-op if librarian is disabled. Same duck-typing pattern as learn_from_run.
        """
        if self._librarian is None:
            return None
        return await self._librarian.evaluate_run(
            goal=goal,
            skill_name=skill_name,
            derived_session=derived_session,
            observations=observations,
            trace=trace,
            run_id=run_id,
            had_replan=had_replan,
            success=success,
        )

    def _load_observations_for_context(self, skill_name: str) -> List[SkillObservation]:
        if not self._experience_store or not self._config:
            return []
        return self._experience_store.top_for_context(
            skill_name,
            limit=self._config.skill_learning_max_observations,
        )

    @staticmethod
    def _trace_deserves_learning(trace: List[StepResult]) -> bool:
        for result in trace:
            if result.retry_strategies_used:
                return True
            if result.suggested_element:
                return True
            if result.reflection_hint or result.reflection_observed:
                return True
            if result.step.action == "wait_for_user":
                return True
        return False


def validate_skill_file(path: Path) -> List[str]:
    """Validate a single skill file and return error messages."""
    errors: List[str] = []
    try:
        content = path.read_text(encoding="utf-8")
        skill = parse_skill_file(content)
        if not skill.trigger_keywords:
            errors.append(f"{path.name}: no trigger keywords")
        if not skill.steps_text:
            errors.append(f"{path.name}: no Steps section")
        if not skill.success_condition:
            errors.append(f"{path.name}: no success-condition")
    except Exception as exc:
        errors.append(f"{path.name}: {exc}")
    return errors


def _os_matches(required_os: str) -> bool:
    """Check if the required OS matches the current platform."""
    if not required_os:
        return True
    current = sys.platform  # "darwin", "linux", "win32"
    return current.startswith(required_os)
