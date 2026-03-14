"""Embedding-based skill retrieval using fastembed (AC-16, AC-19)."""

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

from automation_agent.shared_models import MatchType, SkillRouteCandidate
from automation_agent.skills.models import Skill

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# Lazy import to allow mocking in tests
try:
    from fastembed import TextEmbedding
except ImportError:
    TextEmbedding = None  # type: ignore[assignment,misc]


class EmbeddingIndex:
    """Semantic skill index using local embeddings (AC-16).

    Default backend: fastembed with BAAI/bge-small-en-v1.5 (~50MB, no torch).
    """

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        if TextEmbedding is None:
            raise ImportError(
                "fastembed is required for embedding-based skill retrieval. "
                "Install with: pip install -e '.[embeddings]'"
            )
        try:
            self._model = TextEmbedding(model_name=model_name)
        except (OSError, RuntimeError) as e:
            logger.warning(
                "EmbeddingIndex init failed (model download?): %s. "
                "Embedding retrieval will be disabled.",
                e,
            )
            raise
        self._skill_ids: List[str] = []
        self._embeddings: Optional["np.ndarray"] = None  # shape: (n_skills, embed_dim)

    def build(self, skills: Dict[str, Skill]) -> None:
        """AC-18: Build index from skill metadata.

        Embeds concatenation of summary + description + tags for each skill.
        """
        import numpy as np

        texts = []
        self._skill_ids = []
        for skill_id, skill in skills.items():
            parts = [
                skill.summary or "",
                skill.description or "",
                " ".join(skill.tags) if skill.tags else " ".join(skill.trigger_keywords),
            ]
            texts.append(" ".join(p for p in parts if p))
            self._skill_ids.append(skill_id)

        if not texts:
            self._embeddings = np.empty((0, 0))
            return

        embeddings_gen = self._model.embed(texts)
        self._embeddings = np.array(list(embeddings_gen))

    def query(
        self,
        prompt: str,
        top_k: int = 5,
    ) -> List[SkillRouteCandidate]:
        """AC-16: Retrieve top-k skills by cosine similarity.

        Returns list of SkillRouteCandidate sorted by similarity (descending).
        """
        import numpy as np

        if self._embeddings is None or len(self._skill_ids) == 0:
            return []

        query_emb = np.array(list(self._model.embed([prompt])))[0]

        # Cosine similarity via normalized dot product
        emb_norms = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
        emb_norms = np.maximum(emb_norms, 1e-8)
        normed_embs = self._embeddings / emb_norms

        query_norm = np.linalg.norm(query_emb)
        query_norm = max(query_norm, 1e-8)
        normed_query = query_emb / query_norm

        similarities = normed_embs @ normed_query  # shape: (n_skills,)

        top_indices = np.argsort(similarities)[::-1][:top_k]

        results = []
        for idx in top_indices:
            sim = float(similarities[idx])
            if sim <= 0:
                continue
            results.append(
                SkillRouteCandidate(
                    skill_id=self._skill_ids[idx],
                    match_type=MatchType.GENERIC,
                    confidence=sim,
                    reason=f"Embedding similarity: {sim:.3f}",
                )
            )
        return results
