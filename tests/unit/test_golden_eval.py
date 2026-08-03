from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.models.api_models import SearchResult
from src.config import settings
from src.evaluation.golden_eval import (
    _build_fake_request,
    _compare_to_baseline,
    _compute_averages,
    _load_dataset,
    run_golden_eval,
)


@pytest.mark.unit
def test_load_dataset_returns_well_formed_queries() -> None:
    """Deliberately doesn't assert a specific count -- how many queries (and
    how many carry expected_output for Context Precision/Recall) is a content
    curation decision for golden_eval_dataset.yaml, not a code invariant. This
    only checks the structural shape every entry must have.
    """
    dataset = _load_dataset()

    assert len(dataset) > 0
    assert all("query" in entry for entry in dataset)


@pytest.mark.unit
def test_build_fake_request_exposes_vectorstore() -> None:
    fake_vectorstore = MagicMock()

    request = _build_fake_request(fake_vectorstore)

    assert request.app.state.vectorstore is fake_vectorstore


@pytest.mark.unit
def test_compute_averages_across_queries() -> None:
    results = [
        {"scores": {"faithfulness": {"score": 0.8}, "coherence": {"score": 1.0}}},
        {"scores": {"faithfulness": {"score": 0.6}, "coherence": {"score": 0.8}}},
    ]

    averages = _compute_averages(results)

    assert averages["faithfulness"] == pytest.approx(0.7)
    assert averages["coherence"] == pytest.approx(0.9)


@pytest.mark.unit
def test_compute_averages_ignores_none_scores_for_that_metric() -> None:
    results = [
        {"scores": {"faithfulness": {"score": 0.8}}},
        {"scores": {"faithfulness": {"score": None}}},  # scoring failed for this one
    ]

    averages = _compute_averages(results)

    assert averages["faithfulness"] == pytest.approx(0.8)


@pytest.mark.unit
def test_compute_averages_ignores_queries_with_no_scores_at_all() -> None:
    results = [
        {"scores": {"faithfulness": {"score": 0.8}}},
        {"scores": None},  # evaluation skipped entirely for this query
    ]

    averages = _compute_averages(results)

    assert averages["faithfulness"] == pytest.approx(0.8)


@pytest.mark.unit
def test_compare_to_baseline_flags_real_regression() -> None:
    baseline = {"averages": {"faithfulness": 0.9}}
    averages = {"faithfulness": 0.5}  # dropped 0.4, well past the 0.1 tolerance

    regressions = _compare_to_baseline(averages, baseline)

    assert len(regressions) == 1
    assert "faithfulness" in regressions[0]


@pytest.mark.unit
def test_compare_to_baseline_ignores_drop_within_tolerance() -> None:
    baseline = {"averages": {"faithfulness": 0.9}}
    averages = {"faithfulness": 0.85}  # dropped 0.05, within the 0.1 tolerance

    regressions = _compare_to_baseline(averages, baseline)

    assert regressions == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_golden_eval_fails_fast_when_evaluation_disabled() -> None:
    with (
        patch.object(settings.opik, "enable_evaluation", False),
        patch(
            "src.evaluation.golden_eval.AsyncQdrantVectorStore"
        ) as mock_vectorstore_cls,
    ):
        with pytest.raises(RuntimeError, match="OPIK__ENABLE_EVALUATION"):
            await run_golden_eval("openrouter")

    mock_vectorstore_cls.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_golden_eval_fails_fast_when_no_openai_key() -> None:
    with (
        patch.object(settings.opik, "enable_evaluation", True),
        patch.object(settings.openai, "api_key", ""),
        patch(
            "src.evaluation.golden_eval.AsyncQdrantVectorStore"
        ) as mock_vectorstore_cls,
    ):
        with pytest.raises(RuntimeError, match="OPENAI__API_KEY"):
            await run_golden_eval("openrouter")

    mock_vectorstore_cls.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_golden_eval_end_to_end_with_mocked_pipeline() -> None:
    fake_dataset = [{"query": "q1"}, {"query": "q2"}]
    fake_results = [SearchResult(title="t", chunk_text="c", score=1.0)]
    fake_scores = {"faithfulness": {"score": 0.9, "reason": "ok", "failed": False}}

    fake_vectorstore = MagicMock()
    fake_vectorstore.client.close = AsyncMock()

    with (
        patch.object(settings.opik, "enable_evaluation", True),
        patch.object(settings.openai, "api_key", "fake-key"),
        patch(
            "src.evaluation.golden_eval.AsyncQdrantVectorStore",
            return_value=fake_vectorstore,
        ),
        patch("src.evaluation.golden_eval._load_dataset", return_value=fake_dataset),
        patch(
            "src.evaluation.golden_eval.query_with_filters",
            new=AsyncMock(return_value=fake_results),
        ),
        patch(
            "src.evaluation.golden_eval.generate_answer",
            new=AsyncMock(return_value={"answer": "the answer", "model": "mock-model"}),
        ),
        patch(
            "src.evaluation.golden_eval.evaluate_metrics",
            new=AsyncMock(return_value=fake_scores),
        ),
    ):
        run = await run_golden_eval("openrouter")

    assert len(run["results"]) == 2
    assert run["averages"]["faithfulness"] == pytest.approx(0.9)
    fake_vectorstore.client.close.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_golden_eval_scores_context_metrics_only_with_expected_output() -> (
    None
):
    """Only entries with expected_output should get context_precision/recall --
    entries without it should be scored on the 6 default metrics alone.
    """
    fake_dataset = [
        {"query": "q1 (no reference)"},
        {"query": "q2 (has reference)", "expected_output": "the reference answer"},
    ]
    fake_results = [SearchResult(title="t", chunk_text="c", score=1.0)]
    fake_score = SimpleNamespace(value=0.8, reason="ok", scoring_failed=False)

    fake_vectorstore = MagicMock()
    fake_vectorstore.client.close = AsyncMock()

    fake_metric_instance = MagicMock()
    fake_metric_instance.ascore = AsyncMock(return_value=fake_score)

    with (
        patch.object(settings.opik, "enable_evaluation", True),
        patch.object(settings.openai, "api_key", "fake-key"),
        patch(
            "src.evaluation.golden_eval.AsyncQdrantVectorStore",
            return_value=fake_vectorstore,
        ),
        patch("src.evaluation.golden_eval._load_dataset", return_value=fake_dataset),
        patch(
            "src.evaluation.golden_eval.query_with_filters",
            new=AsyncMock(return_value=fake_results),
        ),
        patch(
            "src.evaluation.golden_eval.generate_answer",
            new=AsyncMock(return_value={"answer": "the answer", "model": "mock-model"}),
        ),
        # side_effect (not return_value) so each call gets its own fresh dict --
        # _run_one mutates the returned dict in place with .update(), and a
        # shared dict across calls would leak context scores between queries.
        patch(
            "src.evaluation.golden_eval.evaluate_metrics",
            new=AsyncMock(
                side_effect=lambda **_: {
                    "faithfulness": {"score": 0.9, "reason": "ok", "failed": False}
                }
            ),
        ),
        patch(
            "src.evaluation.golden_eval.ContextPrecision",
            return_value=fake_metric_instance,
        ),
        patch(
            "src.evaluation.golden_eval.ContextRecall",
            return_value=fake_metric_instance,
        ),
    ):
        run = await run_golden_eval("openrouter")

    no_reference_result, has_reference_result = run["results"]

    assert "context_precision" not in no_reference_result["scores"]
    assert "context_recall" not in no_reference_result["scores"]

    assert has_reference_result["scores"]["context_precision"][
        "score"
    ] == pytest.approx(0.8)
    assert has_reference_result["scores"]["context_recall"]["score"] == pytest.approx(
        0.8
    )
    assert has_reference_result["scores"]["faithfulness"]["score"] == pytest.approx(0.9)
