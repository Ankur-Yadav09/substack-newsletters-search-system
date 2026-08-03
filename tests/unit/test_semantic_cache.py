from unittest.mock import patch

import pytest

from src.api.models.api_models import AskRequest, SearchResult
from src.api.services.semantic_cache_service import (
    SemanticCache,
    build_context_key,
)
from src.config import settings


def _store(
    cache: SemanticCache,
    context_key: tuple,
    vector: list[float],
    answer: str = "cached",
) -> None:
    cache.set(
        context_key,
        vector,
        answer=answer,
        sources=[SearchResult(title="t", chunk_text="c", score=1.0)],
        model="mock-model",
        finish_reason="stop",
    )


@pytest.mark.unit
def test_build_context_key_ignores_query_text_but_includes_everything_else() -> None:
    base = AskRequest(query_text="first question", provider="openrouter", limit=5)
    same_context_different_query = AskRequest(
        query_text="a totally different question", provider="openrouter", limit=5
    )
    different_provider = AskRequest(
        query_text="first question", provider="openai", limit=5
    )

    assert build_context_key(base) == build_context_key(same_context_different_query)
    assert build_context_key(base) != build_context_key(different_provider)


@pytest.mark.unit
def test_context_key_normalizes_article_author_order() -> None:
    a = AskRequest(query_text="q", article_author=["Alice", "Bob"])
    b = AskRequest(query_text="q", article_author=["Bob", "Alice"])

    assert build_context_key(a) == build_context_key(b)


@pytest.mark.unit
def test_cache_hit_for_identical_embedding() -> None:
    cache = SemanticCache()
    context_key = ("openrouter", None, None, None, None, None, 5)
    vector = [1.0, 0.0, 0.0]
    _store(cache, context_key, vector)

    hit = cache.get(context_key, vector)

    assert hit is not None
    assert hit.answer == "cached"


@pytest.mark.unit
def test_cache_miss_for_different_context_key() -> None:
    cache = SemanticCache()
    vector = [1.0, 0.0, 0.0]
    _store(cache, ("openrouter", None, None, None, None, None, 5), vector)

    miss = cache.get(("openai", None, None, None, None, None, 5), vector)

    assert miss is None


@pytest.mark.unit
def test_cache_miss_below_similarity_threshold() -> None:
    cache = SemanticCache()
    context_key = ("openrouter", None, None, None, None, None, 5)
    _store(cache, context_key, [1.0, 0.0])

    # Orthogonal vector -> cosine similarity 0.0, well below any sane threshold.
    miss = cache.get(context_key, [0.0, 1.0])

    assert miss is None


@pytest.mark.unit
def test_cache_hit_ignores_magnitude_only_direction_matters() -> None:
    """Cosine similarity is scale-invariant -- a scaled copy of the same
    direction should still count as a hit.
    """
    cache = SemanticCache()
    context_key = ("openrouter", None, None, None, None, None, 5)
    _store(cache, context_key, [1.0, 2.0, 3.0])

    hit = cache.get(context_key, [2.0, 4.0, 6.0])

    assert hit is not None


@pytest.mark.unit
def test_disabled_cache_never_hits_or_stores() -> None:
    cache = SemanticCache()
    context_key = ("openrouter", None, None, None, None, None, 5)
    vector = [1.0, 0.0, 0.0]

    with patch.object(settings.semantic_cache, "enabled", False):
        _store(cache, context_key, vector)
        miss = cache.get(context_key, vector)

    assert miss is None
    assert len(cache._entries) == 0


@pytest.mark.unit
def test_expired_entry_is_not_returned() -> None:
    cache = SemanticCache()
    context_key = ("openrouter", None, None, None, None, None, 5)
    vector = [1.0, 0.0, 0.0]

    with patch(
        "src.api.services.semantic_cache_service.time.monotonic", return_value=1000.0
    ):
        _store(cache, context_key, vector)

    with (
        patch.object(settings.semantic_cache, "ttl_seconds", 60),
        patch(
            "src.api.services.semantic_cache_service.time.monotonic",
            return_value=1000.0 + 61,
        ),
    ):
        miss = cache.get(context_key, vector)

    assert miss is None


@pytest.mark.unit
def test_max_size_evicts_oldest_entry() -> None:
    with patch.object(settings.semantic_cache, "max_size", 2):
        cache = SemanticCache()
        key_a = ("openrouter", None, None, None, None, "a", 5)
        key_b = ("openrouter", None, None, None, None, "b", 5)
        key_c = ("openrouter", None, None, None, None, "c", 5)
        vector = [1.0, 0.0, 0.0]

        _store(cache, key_a, vector, answer="answer-a")
        _store(cache, key_b, vector, answer="answer-b")
        _store(cache, key_c, vector, answer="answer-c")  # evicts key_a's entry

        assert cache.get(key_a, vector) is None
        assert cache.get(key_b, vector) is not None
        assert cache.get(key_c, vector) is not None
