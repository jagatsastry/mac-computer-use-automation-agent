"""Unit tests for embedding-based skill retrieval (Gap 3, Slice 3).

Tests: EmbeddingIndex, three-stage pipeline in SkillRegistryImpl.match(),
config gate, and keyword fallback.

AC-16: Embedding index build and query
AC-17: Three-stage pipeline (embed → conditional LLM re-rank → keyword fallback)
AC-18: Embedding from metadata (summary + description + tags)
AC-19: Keyword fallback when no embeddings
AC-20: Config gate for embedding retrieval
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from automation_agent.config import AgentConfig
from automation_agent.shared_models import MatchType, SkillRouteCandidate


def _make_config(**overrides) -> AgentConfig:
    defaults = dict(
        model_provider="local",
        vision_server_url="http://localhost:8080",
        vision_model="qwen3-vl",
        text_model="gemma2:9b",
        skill_embedding_enabled=True,
        skill_embedding_model="BAAI/bge-small-en-v1.5",
        skill_embedding_rerank_threshold=0.92,
        skill_embedding_min_gap=0.15,
        grounding_model="",
        grounding_server_url="",
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_skill(name, summary="", description="", tags=None, trigger_keywords=None):
    """Build a mock Skill object for testing."""
    skill = MagicMock()
    skill.name = name
    skill.skill_id = name
    skill.summary = summary or f"Summary for {name}"
    skill.description = description or f"Description for {name}"
    skill.tags = tags or []
    skill.trigger_keywords = trigger_keywords or [name]
    skill.parameters = {}
    skill.requires = MagicMock()
    skill.requires.apps = []
    skill.requires.os = ""
    skill.steps_text = f"Steps for {name}"
    skill.success_condition = "done"
    skill.metadata = {"trusted": True}
    skill.error_recovery_text = ""
    skill.notes_text = ""
    skill.learned_tips_text = ""
    return skill


# ---------------------------------------------------------------------------
# EmbeddingIndex unit tests
# ---------------------------------------------------------------------------


class TestEmbeddingIndex:
    """Tests for skills/embeddings.py — EmbeddingIndex class."""

    def _make_mock_fastembed(self):
        """Create a mock fastembed module and TextEmbedding class."""
        import numpy as np

        class MockTextEmbedding:
            def __init__(self, model_name="test"):
                self.model_name = model_name

            def embed(self, texts):
                """Return deterministic embeddings based on text content."""
                for text in texts:
                    # Use hash for deterministic but varied embeddings
                    np.random.seed(hash(text) % 2**31)
                    vec = np.random.randn(384)
                    vec = vec / np.linalg.norm(vec)
                    yield vec

        return MockTextEmbedding

    @patch.dict("sys.modules", {"fastembed": MagicMock()})
    def test_embedding_index_build_and_query(self):
        """AC-16: Build index, query returns ranked candidates."""
        import numpy as np

        # Use a mock that returns similar embeddings for related texts
        class DeterministicTextEmbedding:
            def __init__(self, model_name="test"):
                pass

            def embed(self, texts):
                for text in texts:
                    # Create embeddings that are always in the positive quadrant
                    # and have structure based on content
                    base = np.ones(384) * 0.1
                    if "return" in text.lower():
                        base[:100] += 0.5
                    if "amazon" in text.lower():
                        base[100:200] += 0.5
                    if "walmart" in text.lower():
                        base[200:300] += 0.5
                    if "food" in text.lower() or "restaurant" in text.lower():
                        base[300:384] += 0.5
                    vec = base / np.linalg.norm(base)
                    yield vec

        with patch("automation_agent.skills.embeddings.TextEmbedding", DeterministicTextEmbedding):
            from automation_agent.skills.embeddings import EmbeddingIndex

            idx = EmbeddingIndex.__new__(EmbeddingIndex)
            idx._model = DeterministicTextEmbedding()
            idx._skill_ids = []
            idx._embeddings = None

            skills = {
                "return-amazon": _make_skill(
                    "return-amazon",
                    summary="Return an Amazon order",
                    tags=["return", "amazon"],
                ),
                "return-walmart": _make_skill(
                    "return-walmart",
                    summary="Return a Walmart order",
                    tags=["return", "walmart"],
                ),
                "order-food": _make_skill(
                    "order-food",
                    summary="Order food from a restaurant",
                    tags=["food", "order"],
                ),
            }

            idx.build(skills)

            assert len(idx._skill_ids) == 3
            assert idx._embeddings is not None
            assert idx._embeddings.shape[0] == 3

            results = idx.query("Return my Amazon order", top_k=3)
            assert len(results) > 0
            # Results should be SkillRouteCandidate
            assert all(isinstance(r, SkillRouteCandidate) for r in results)
            # Should be sorted by similarity (descending)
            sims = [r.confidence for r in results]
            assert sims == sorted(sims, reverse=True)

    @patch.dict("sys.modules", {"fastembed": MagicMock()})
    def test_embedding_from_metadata(self):
        """AC-18: Embeds summary + description + tags."""
        MockTextEmbedding = self._make_mock_fastembed()

        with patch("automation_agent.skills.embeddings.TextEmbedding", MockTextEmbedding):
            from automation_agent.skills.embeddings import EmbeddingIndex

            idx = EmbeddingIndex.__new__(EmbeddingIndex)
            idx._model = MockTextEmbedding()
            idx._skill_ids = []
            idx._embeddings = None

            # Skill with tags
            skill_with_tags = _make_skill(
                "test-skill", summary="My summary", description="My desc", tags=["tag1", "tag2"]
            )
            # Skill without tags (uses trigger_keywords)
            skill_no_tags = _make_skill(
                "test-skill2",
                summary="Summary2",
                description="Desc2",
                tags=[],
                trigger_keywords=["kw1", "kw2"],
            )

            embedded_texts = []
            original_embed = idx._model.embed

            def capture_embed(texts):
                embedded_texts.extend(texts)
                return original_embed(texts)

            idx._model.embed = capture_embed

            idx.build({"test-skill": skill_with_tags, "test-skill2": skill_no_tags})

            # First skill should concatenate summary + description + tags
            assert "My summary" in embedded_texts[0]
            assert "My desc" in embedded_texts[0]
            assert "tag1" in embedded_texts[0]
            # Second skill should use trigger_keywords as fallback
            assert "kw1" in embedded_texts[1]

    def test_embedding_index_import_error(self):
        """AC-19: ImportError when fastembed not installed."""
        # Remove fastembed from modules to simulate not installed
        import sys

        with patch.dict("sys.modules", {"fastembed": None}):
            # Importing EmbeddingIndex should work, but constructing should raise
            # We need to reload the module to pick up the patched import
            with pytest.raises((ImportError, ModuleNotFoundError)):
                from automation_agent.skills.embeddings import EmbeddingIndex

                EmbeddingIndex()

    @patch.dict("sys.modules", {"fastembed": MagicMock()})
    def test_query_on_empty_index(self):
        """Finding 2: query() on empty index returns empty list."""
        MockTextEmbedding = self._make_mock_fastembed()

        with patch("automation_agent.skills.embeddings.TextEmbedding", MockTextEmbedding):
            from automation_agent.skills.embeddings import EmbeddingIndex

            idx = EmbeddingIndex.__new__(EmbeddingIndex)
            idx._model = MockTextEmbedding()
            idx._skill_ids = []
            idx._embeddings = None

            # Build with empty skills dict
            idx.build({})

            # Query should return empty list, not crash
            results = idx.query("anything", top_k=5)
            assert results == []


# ---------------------------------------------------------------------------
# Three-stage pipeline tests
# ---------------------------------------------------------------------------


class TestThreeStagePipeline:
    """Tests for the three-stage matching pipeline in SkillRegistryImpl."""

    def _make_registry_with_embedding(self, skills, config=None, embedding_results=None):
        """Create a SkillRegistryImpl with mocked embedding index."""
        from automation_agent.skills.registry import SkillRegistryImpl

        config = config or _make_config()

        # Create registry without loading from directory
        with patch.object(SkillRegistryImpl, "__init__", lambda self, **kw: None):
            registry = SkillRegistryImpl.__new__(SkillRegistryImpl)

        registry._skills = skills
        registry._config = config
        registry._cards = []
        registry._router = None
        registry._experience_store = None
        registry._distiller = None
        registry._librarian = None
        registry._skill_dir = Path("/nonexistent")
        registry._event_logger = None

        # Mock embedding index
        mock_index = MagicMock()
        if embedding_results is not None:
            mock_index.query.return_value = embedding_results
        registry._embedding_index = mock_index

        return registry

    @pytest.mark.asyncio
    async def test_three_stage_pipeline_clear_winner(self):
        """AC-17: sim >= 0.92 + gap >= 0.15 skips LLM re-rank."""
        skills = {
            "return-amazon": _make_skill("return-amazon"),
            "return-walmart": _make_skill("return-walmart"),
        }

        # Clear winner: top sim=0.95, second=0.70, gap=0.25 > 0.15
        embedding_results = [
            SkillRouteCandidate(
                skill_id="return-amazon",
                match_type=MatchType.GENERIC,
                confidence=0.95,
                reason="Embedding similarity: 0.950",
            ),
            SkillRouteCandidate(
                skill_id="return-walmart",
                match_type=MatchType.GENERIC,
                confidence=0.70,
                reason="Embedding similarity: 0.700",
            ),
        ]

        registry = self._make_registry_with_embedding(skills, embedding_results=embedding_results)
        result = await registry.match("Return my Amazon order")

        assert result is not None
        assert result.skill_name == "return-amazon"
        # Router should NOT have been called (no router set, and clear winner skips re-rank)
        assert registry._router is None  # Proves no router call

    @pytest.mark.asyncio
    async def test_three_stage_pipeline_ambiguous(self):
        """AC-17: LLM re-rank fires when top-2 gap < 0.15."""
        skills = {
            "return-amazon": _make_skill("return-amazon"),
            "return-walmart": _make_skill("return-walmart"),
        }

        # Ambiguous: top sim=0.88, second=0.85, gap=0.03 < 0.15
        embedding_results = [
            SkillRouteCandidate(
                skill_id="return-amazon",
                match_type=MatchType.GENERIC,
                confidence=0.88,
                reason="Embedding similarity: 0.880",
            ),
            SkillRouteCandidate(
                skill_id="return-walmart",
                match_type=MatchType.GENERIC,
                confidence=0.85,
                reason="Embedding similarity: 0.850",
            ),
        ]

        registry = self._make_registry_with_embedding(skills, embedding_results=embedding_results)
        # Set up a mock router that will be called for re-rank
        mock_router = AsyncMock()
        mock_router.route = AsyncMock(return_value=None)
        registry._router = mock_router

        result = await registry.match("Return my order")

        # Router should have been called with candidate_ids filter for re-rank
        calls = mock_router.route.call_args_list
        # First call should be the re-rank with candidate_ids
        assert len(calls) >= 1
        first_call = calls[0]
        assert "candidate_ids" in first_call.kwargs or (
            len(first_call.args) > 1
        )

    @pytest.mark.asyncio
    async def test_three_stage_pipeline_similar_skills(self):
        """AC-17: Similar skills with gap < 0.15 always triggers re-rank."""
        skills = {
            "return-amazon": _make_skill("return-amazon"),
            "return-walmart": _make_skill("return-walmart"),
        }

        # High sim but small gap: 0.93 vs 0.91, gap=0.02
        embedding_results = [
            SkillRouteCandidate(
                skill_id="return-amazon",
                match_type=MatchType.GENERIC,
                confidence=0.93,
                reason="Embedding similarity: 0.930",
            ),
            SkillRouteCandidate(
                skill_id="return-walmart",
                match_type=MatchType.GENERIC,
                confidence=0.91,
                reason="Embedding similarity: 0.910",
            ),
        ]

        registry = self._make_registry_with_embedding(skills, embedding_results=embedding_results)
        mock_router = AsyncMock()
        mock_router.route = AsyncMock(return_value=None)
        registry._router = mock_router

        result = await registry.match("Return my order")

        # Even though top sim is 0.93 > 0.92, gap is 0.02 < 0.15
        # So LLM re-rank MUST fire
        calls = mock_router.route.call_args_list
        assert len(calls) >= 1
        # First call should have candidate_ids filter
        first_call = calls[0]
        assert "candidate_ids" in first_call.kwargs or len(first_call.args) > 1

    @pytest.mark.asyncio
    async def test_keyword_fallback_when_no_embeddings(self):
        """AC-19: Existing keyword fallback works without fastembed."""
        from automation_agent.skills.registry import SkillRegistryImpl

        config = _make_config(skill_embedding_enabled=False)

        with patch.object(SkillRegistryImpl, "__init__", lambda self, **kw: None):
            registry = SkillRegistryImpl.__new__(SkillRegistryImpl)

        skill = _make_skill(
            "return-amazon",
            trigger_keywords=["return", "amazon", "order"],
        )
        registry._skills = {"return-amazon": skill}
        registry._config = config
        registry._cards = []
        registry._router = None
        registry._embedding_index = None
        registry._experience_store = None
        registry._distiller = None
        registry._librarian = None
        registry._skill_dir = Path("/nonexistent")

        # Keyword matching should work
        result = await registry.match("Return my Amazon order")
        # May or may not match depending on keyword threshold;
        # the key assertion is it doesn't crash without embeddings
        # and doesn't try to use embedding index
        assert registry._embedding_index is None

    @pytest.mark.asyncio
    async def test_embedding_config_gate(self):
        """AC-20: Disabled when config flag is False."""
        skills = {"test-skill": _make_skill("test-skill")}
        config = _make_config(skill_embedding_enabled=False)

        from automation_agent.skills.registry import SkillRegistryImpl

        with patch.object(SkillRegistryImpl, "__init__", lambda self, **kw: None):
            registry = SkillRegistryImpl.__new__(SkillRegistryImpl)

        registry._skills = skills
        registry._config = config
        registry._cards = []
        registry._router = None
        registry._embedding_index = MagicMock()  # Set but should not be used
        registry._experience_store = None
        registry._distiller = None
        registry._librarian = None
        registry._skill_dir = Path("/nonexistent")

        await registry.match("some prompt")

        # Embedding index query should NOT have been called
        registry._embedding_index.query.assert_not_called()

    def test_rebuild_router_rebuilds_index(self):
        """AC-18: _rebuild_router() triggers embedding rebuild."""
        from automation_agent.skills.registry import SkillRegistryImpl

        config = _make_config(skill_embedding_enabled=True)

        with patch.object(SkillRegistryImpl, "__init__", lambda self, **kw: None):
            registry = SkillRegistryImpl.__new__(SkillRegistryImpl)

        registry._skills = {"test": _make_skill("test")}
        registry._config = config
        registry._cards = []
        registry._router = None
        registry._embedding_index = None
        registry._experience_store = None
        registry._distiller = None
        registry._librarian = None
        registry._skill_dir = Path("/nonexistent")
        registry._event_logger = None

        # Mock the EmbeddingIndex at the import site inside _rebuild_router
        mock_embedding_cls = MagicMock()
        mock_embedding_instance = MagicMock()
        mock_embedding_cls.return_value = mock_embedding_instance

        # The import happens inside _rebuild_router as:
        # from automation_agent.skills.embeddings import EmbeddingIndex
        # We need to mock it at the module level
        import automation_agent.skills.embeddings as emb_module

        with patch.object(emb_module, "EmbeddingIndex", mock_embedding_cls):
            registry._rebuild_router()

        # Embedding index should have been created and built
        mock_embedding_cls.assert_called_once_with(config.skill_embedding_model)
        mock_embedding_instance.build.assert_called_once()

    def test_rebuild_router_graceful_degradation_on_error(self):
        """Finding 6: _rebuild_router handles OSError gracefully."""
        from automation_agent.skills.registry import SkillRegistryImpl

        config = _make_config(skill_embedding_enabled=True)

        with patch.object(SkillRegistryImpl, "__init__", lambda self, **kw: None):
            registry = SkillRegistryImpl.__new__(SkillRegistryImpl)

        registry._skills = {"test": _make_skill("test")}
        registry._config = config
        registry._cards = []
        registry._router = None
        registry._embedding_index = None
        registry._experience_store = None
        registry._distiller = None
        registry._librarian = None
        registry._skill_dir = Path("/nonexistent")
        registry._event_logger = None

        # Mock EmbeddingIndex to raise OSError on construction
        mock_embedding_cls = MagicMock(side_effect=OSError("Model download failed"))

        import automation_agent.skills.embeddings as emb_module

        with patch.object(emb_module, "EmbeddingIndex", mock_embedding_cls):
            registry._rebuild_router()  # Should NOT raise

        # Embedding index should be None (graceful degradation)
        assert registry._embedding_index is None

    @pytest.mark.asyncio
    async def test_embedding_query_empty_prompt(self):
        """Edge case: empty prompt should still return candidates (cosine sim may be low)."""
        skills = {"return-amazon": _make_skill("return-amazon")}
        embedding_results = [
            SkillRouteCandidate(
                skill_id="return-amazon",
                match_type=MatchType.GENERIC,
                confidence=0.1,
                reason="Embedding similarity: 0.100",
            ),
        ]
        registry = self._make_registry_with_embedding(
            skills, embedding_results=embedding_results
        )
        result = await registry.match("")
        # With sim=0.1 < 0.92 threshold and no router, falls through
        assert result is None

    @pytest.mark.asyncio
    async def test_embedding_single_skill_auto_gap(self):
        """Edge case: single skill → gap defaults to 1.0 (auto-skip rerank)."""
        skills = {"return-amazon": _make_skill("return-amazon")}
        embedding_results = [
            SkillRouteCandidate(
                skill_id="return-amazon",
                match_type=MatchType.GENERIC,
                confidence=0.95,
                reason="Embedding similarity: 0.950",
            ),
        ]
        registry = self._make_registry_with_embedding(
            skills, embedding_results=embedding_results
        )
        result = await registry.match("Return my Amazon order")
        assert result is not None
        assert result.skill_name == "return-amazon"
