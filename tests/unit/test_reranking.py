from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.api.models.api_models import SearchResult
from src.api.services.search_service import _rerank_results


def _fake_vectorstore(
    *,
    enabled: bool = True,
    candidate_pool_size: int = 25,
    scores: list[float] | None = None,
) -> MagicMock:
    """Build a stand-in for AsyncQdrantVectorStore exposing just what
    _rerank_results needs: reranker_settings and rerank().
    """
    store = MagicMock()
    store.reranker_settings = SimpleNamespace(
        enabled=enabled, candidate_pool_size=candidate_pool_size
    )
    if scores is not None:
        store.rerank = MagicMock(return_value=scores)
    return store


def _result(title: str, chunk_text: str = "") -> SearchResult:
    return SearchResult(title=title, chunk_text=chunk_text, score=0.5)


@pytest.mark.unit
def test_rerank_reorders_by_cross_encoder_score() -> None:
    """A result ranked lower by RRF but scored higher by the cross-encoder
    should end up first after re-ranking.
    """
    results = [_result("first-by-rrf"), _result("second-by-rrf")]
    vectorstore = _fake_vectorstore(scores=[0.1, 0.9])

    reranked = _rerank_results(vectorstore, "query", results, limit=2)

    assert [r.title for r in reranked] == ["second-by-rrf", "first-by-rrf"]
    # The returned score must be the cross-encoder score, not the stale RRF
    # score each result was constructed with (score=0.5 in `_result`) --
    # otherwise the displayed score no longer explains the displayed order.
    assert [r.score for r in reranked] == [0.9, 0.1]


@pytest.mark.unit
def test_rerank_truncates_to_limit() -> None:
    results = [_result("a"), _result("b"), _result("c")]
    vectorstore = _fake_vectorstore(scores=[0.3, 0.9, 0.1])

    reranked = _rerank_results(vectorstore, "query", results, limit=2)

    assert [r.title for r in reranked] == ["b", "a"]


@pytest.mark.unit
def test_rerank_disabled_falls_back_to_original_order() -> None:
    results = [_result("a"), _result("b"), _result("c")]
    vectorstore = _fake_vectorstore(enabled=False)

    reranked = _rerank_results(vectorstore, "query", results, limit=2)

    assert [r.title for r in reranked] == ["a", "b"]
    vectorstore.rerank.assert_not_called()


@pytest.mark.unit
def test_rerank_empty_results_returns_empty() -> None:
    vectorstore = _fake_vectorstore()

    reranked = _rerank_results(vectorstore, "query", [], limit=5)

    assert reranked == []
    vectorstore.rerank.assert_not_called()


@pytest.mark.unit
def test_rerank_only_scores_candidate_pool() -> None:
    """Only the top candidate_pool_size results should be sent to the
    cross-encoder, not the full (potentially much larger) RRF result set.
    """
    results = [_result(str(i)) for i in range(10)]
    vectorstore = _fake_vectorstore(candidate_pool_size=3, scores=[0.1, 0.2, 0.3])

    _rerank_results(vectorstore, "query", results, limit=3)

    scored_documents = vectorstore.rerank.call_args.args[1]
    assert len(scored_documents) == 3
