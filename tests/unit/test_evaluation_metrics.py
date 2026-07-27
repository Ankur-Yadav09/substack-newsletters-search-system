import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import opik
import pytest
from opik import opik_context

from src.api.services.providers.utils import evaluation_metrics as em
from src.config import settings

ALL_METRIC_NAMES = {
    "faithfulness",
    "coherence",
    "completeness",
    "hallucination",
    "answer_relevance",
    "usefulness",
}


def _fake_score_result(value: float = 0.9, failed: bool = False):
    """Stand-in for opik's metric score result object."""
    return type(
        "ScoreResult", (), {"value": value, "reason": "ok", "scoring_failed": failed}
    )()


@contextmanager
def _patched_metric_classes(ascore):
    """Patch every metric class evaluate_metrics uses (the 3 custom GEval metrics
    plus the 3 built-in ones) so their `.ascore` all resolve to the given fake,
    and no real LLM judge calls ever happen.
    """
    with (
        patch(
            "src.api.services.providers.utils.evaluation_metrics.models.LiteLLMChatModel"
        ),
        patch(
            "src.api.services.providers.utils.evaluation_metrics.GEval"
        ) as mock_geval,
        patch(
            "src.api.services.providers.utils.evaluation_metrics.Hallucination"
        ) as mock_hallucination,
        patch(
            "src.api.services.providers.utils.evaluation_metrics.AnswerRelevance"
        ) as mock_answer_relevance,
        patch(
            "src.api.services.providers.utils.evaluation_metrics.Usefulness"
        ) as mock_usefulness,
    ):
        for cls_mock in (
            mock_geval,
            mock_hallucination,
            mock_answer_relevance,
            mock_usefulness,
        ):
            cls_mock.return_value.ascore = ascore
        yield


@pytest.mark.asyncio
async def test_evaluate_metrics_skipped_when_disabled():
    """Evaluation must no-op when OPIK__ENABLE_EVALUATION is false (the default)."""
    with patch.object(settings.opik, "enable_evaluation", False):
        result = await em.evaluate_metrics("query", "some answer", ["some context"])
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_metrics_skipped_without_openai_key():
    """G-Eval needs an OpenAI judge key even when evaluation is enabled."""
    with (
        patch.object(settings.opik, "enable_evaluation", True),
        patch.object(settings.openai, "api_key", ""),
    ):
        result = await em.evaluate_metrics("query", "some answer", ["some context"])
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_metrics_skipped_for_empty_output():
    with (
        patch.object(settings.opik, "enable_evaluation", True),
        patch.object(settings.openai, "api_key", "test-key"),
    ):
        result = await em.evaluate_metrics("query", "   ", ["some context"])
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_metrics_runs_all_metrics_when_enabled():
    """All 6 metrics (3 custom G-Eval + 3 built-in) should be scored concurrently."""
    fake_score = AsyncMock(return_value=_fake_score_result())

    with (
        patch.object(settings.opik, "enable_evaluation", True),
        patch.object(settings.openai, "api_key", "test-key"),
        _patched_metric_classes(fake_score),
    ):
        result = await em.evaluate_metrics("query", "answer", ["context chunk"])

    assert result is not None
    assert set(result.keys()) == ALL_METRIC_NAMES
    for metric in result.values():
        assert metric["score"] == 0.9
        assert metric["failed"] is False
    assert fake_score.await_count == 6


@pytest.mark.asyncio
async def test_evaluate_metrics_handles_partial_failure():
    """A single metric raising must not fail the other five."""

    async def flaky_ascore(*args, **kwargs):
        if "already failed" not in flaky_ascore.calls:
            flaky_ascore.calls.append("already failed")
            raise RuntimeError("judge model unavailable")
        return _fake_score_result()

    flaky_ascore.calls = []

    with (
        patch.object(settings.opik, "enable_evaluation", True),
        patch.object(settings.openai, "api_key", "test-key"),
        _patched_metric_classes(flaky_ascore),
    ):
        result = await em.evaluate_metrics("query", "answer", ["context chunk"])

    assert result is not None
    assert len(result) == 6
    failed_count = sum(1 for m in result.values() if m["failed"])
    assert failed_count == 1


def test_schedule_evaluation_noop_when_disabled():
    """Disabled evaluation must not even create a background task."""
    with patch.object(settings.opik, "enable_evaluation", False):
        em.schedule_evaluation("query", "answer", ["context chunk"])
    assert len(em._background_tasks) == 0


@pytest.mark.asyncio
async def test_schedule_evaluation_creates_and_cleans_up_background_task():
    fake_score = AsyncMock(return_value=_fake_score_result())

    with (
        patch.object(settings.opik, "enable_evaluation", True),
        patch.object(settings.openai, "api_key", "test-key"),
        _patched_metric_classes(fake_score),
    ):
        em.schedule_evaluation("query", "answer", ["context chunk"])
        assert len(em._background_tasks) == 1

        # Let the background task run to completion before the patches are undone.
        await asyncio.gather(*em._background_tasks)

    assert len(em._background_tasks) == 0


@pytest.mark.asyncio
async def test_schedule_evaluation_without_active_trace_never_calls_opik_client():
    """No active Opik trace (e.g. called outside @opik.track) must not attempt to
    log scores anywhere but the local logger — there's no trace to attach them to.
    """
    fake_score = AsyncMock(return_value=_fake_score_result())

    with (
        patch.object(settings.opik, "enable_evaluation", True),
        patch.object(settings.openai, "api_key", "test-key"),
        _patched_metric_classes(fake_score),
        patch(
            "src.api.services.providers.utils.evaluation_metrics.opik.Opik"
        ) as mock_opik_cls,
    ):
        em.schedule_evaluation("query", "answer", ["context chunk"])
        await asyncio.gather(*em._background_tasks)

    mock_opik_cls.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_evaluation_attaches_scores_to_active_trace():
    """This is the actual fix: when called from within an active Opik trace, the
    computed evaluation scores must be sent to Opik via log_traces_feedback_scores,
    tagged with that trace's id — not just logged locally.
    """
    fake_score = AsyncMock(return_value=_fake_score_result(value=0.75))

    @opik.track(name="fake_generate_answer_for_test")
    def call_inside_trace():
        em.schedule_evaluation("query", "answer", ["context chunk"])
        return opik_context.get_current_trace_data()

    with (
        patch.object(settings.opik, "enable_evaluation", True),
        patch.object(settings.openai, "api_key", "test-key"),
        _patched_metric_classes(fake_score),
        patch(
            "src.api.services.providers.utils.evaluation_metrics.opik.Opik"
        ) as mock_opik_cls,
    ):
        trace_data = call_inside_trace()
        await asyncio.gather(*em._background_tasks)

    mock_opik_cls.return_value.log_traces_feedback_scores.assert_called_once()
    _, kwargs = mock_opik_cls.return_value.log_traces_feedback_scores.call_args
    scores = kwargs["scores"]

    assert len(scores) == 6
    assert {s["id"] for s in scores} == {trace_data.id}
    assert {s["name"] for s in scores} == {f"eval_{name}" for name in ALL_METRIC_NAMES}
    assert all(s["value"] == 0.75 for s in scores)
