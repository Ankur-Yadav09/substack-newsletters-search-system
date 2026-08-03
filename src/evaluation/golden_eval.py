import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import yaml
from opik.evaluation import models
from opik.evaluation.metrics import ContextPrecision, ContextRecall

from src.api.models.api_models import SearchResult
from src.api.services.generation_service import generate_answer
from src.api.services.providers.utils.evaluation_metrics import evaluate_metrics
from src.api.services.search_service import query_with_filters
from src.config import settings
from src.infrastructure.qdrant.qdrant_vectorstore import AsyncQdrantVectorStore
from src.utils.logger_util import setup_logging

logger = setup_logging()

DATASET_PATH = (
    Path(__file__).resolve().parent.parent / "configs" / "golden_eval_dataset.yaml"
)
BASELINE_PATH = Path(__file__).resolve().parent / "golden_eval_baseline.json"

# A metric averaging more than this much lower than the saved baseline counts
# as a regression. 0.1 on Opik's 0-1 score scale is a deliberately loose bound --
# LLM-judge scores aren't perfectly deterministic run to run, so this is meant to
# catch real degradation (a worse prompt, a broken retrieval change), not noise.
REGRESSION_TOLERANCE = 0.1


def _load_dataset() -> list[dict]:
    """Load the golden query set from src/configs/golden_eval_dataset.yaml.

    Returns:
        list[dict]: Each entry has at least "query", and optionally
            "feed_author"/"feed_name"/"article_author"/"title_keywords"/"limit".

    """
    with open(DATASET_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["queries"]


def _build_fake_request(vectorstore: AsyncQdrantVectorStore) -> SimpleNamespace:
    """Build a minimal stand-in for FastAPI's Request.

    query_with_filters only ever reads `request.app.state.vectorstore` -- this
    duck-typed object satisfies that without needing a real running server, so
    this script reuses the exact same production retrieval code path instead of
    reimplementing it (and risking the eval harness silently drifting from what
    /search/ask actually does).

    Args:
        vectorstore (AsyncQdrantVectorStore): The vector store to expose.

    Returns:
        SimpleNamespace: Object shaped like `request.app.state.vectorstore`.

    """
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(vectorstore=vectorstore))
    )


async def _score_retrieval_metric(
    metric, name: str, **score_kwargs
) -> tuple[str, dict]:
    """Score using one of Opik's reference-based retrieval metrics (ContextPrecision,
    ContextRecall) -- these need `expected_output`, unlike the reference-free metrics
    in evaluation_metrics.py, so they're only run for dataset entries that have one.

    Mirrors evaluation_metrics.py's `_score_builtin_metric` shape so results merge
    cleanly into the same `scores` dict.

    Args:
        metric: An instantiated Opik metric object exposing an async `.ascore(...)`.
        name (str): Metric name to key the result by (e.g. "context_precision").
        **score_kwargs: Keyword arguments forwarded to `metric.ascore(...)`.

    Returns:
        tuple[str, dict]: (metric name, {"score", "reason", "failed"}).

    """
    try:
        score_result = await metric.ascore(**score_kwargs)
        return name, {
            "score": score_result.value,
            "reason": score_result.reason,
            "failed": score_result.scoring_failed,
        }
    except Exception as e:
        logger.warning(f"{name} metric failed: {e}")
        return name, {"score": 0.0, "reason": str(e), "failed": True}


async def _run_one(
    vectorstore: AsyncQdrantVectorStore, entry: dict, provider: str, judge_model
) -> dict:
    """Run retrieval, generation, and scoring for a single golden query.

    Args:
        vectorstore (AsyncQdrantVectorStore): Shared vector store instance.
        entry (dict): One entry from the golden dataset.
        provider (str): LLM provider to generate the answer with.
        judge_model: Opik judge model, reused across queries for
            Context Precision/Recall (only entries with "expected_output" get these).

    Returns:
        dict: {"query", "answer", "sources", "scores"} -- "scores" is None if
            evaluation was skipped for this query (e.g. empty answer).

    """
    request = _build_fake_request(vectorstore)
    results: list[SearchResult] = await query_with_filters(
        request,
        query_text=entry["query"],
        feed_author=entry.get("feed_author"),
        feed_name=entry.get("feed_name"),
        article_author=entry.get("article_author"),
        title_keywords=entry.get("title_keywords"),
        limit=entry.get("limit", 5),
    )
    answer_data = await generate_answer(
        query=entry["query"], contexts=results, provider=provider
    )
    context_chunks = [r.chunk_text for r in results if r.chunk_text]
    scores = await evaluate_metrics(
        query=entry["query"],
        output=answer_data["answer"],
        context_chunks=context_chunks,
    )

    expected_output = entry.get("expected_output")
    if expected_output and scores is not None:
        retrieval_scores = dict(
            await asyncio.gather(
                _score_retrieval_metric(
                    ContextPrecision(model=judge_model),
                    "context_precision",
                    input=entry["query"],
                    output=answer_data["answer"],
                    expected_output=expected_output,
                    context=context_chunks,
                ),
                _score_retrieval_metric(
                    ContextRecall(model=judge_model),
                    "context_recall",
                    input=entry["query"],
                    output=answer_data["answer"],
                    expected_output=expected_output,
                    context=context_chunks,
                ),
            )
        )
        scores.update(retrieval_scores)

    return {
        "query": entry["query"],
        "answer": answer_data["answer"],
        "sources": [r.url for r in results],
        "scores": scores,
    }


def _compute_averages(results: list[dict]) -> dict[str, float]:
    """Average each metric across all queries that produced a (non-None) score.

    A query is excluded entirely from a metric's average if that query has no
    scores at all (evaluation skipped) or that specific metric's score is None
    (scoring failed for just that metric) -- so one bad query can't silently
    zero out the average.

    Args:
        results (list[dict]): Per-query results from `_run_one`.

    Returns:
        dict[str, float]: Metric name -> average score.

    """
    totals: dict[str, list[float]] = {}
    for result in results:
        scores = result["scores"]
        if not scores:
            continue
        for name, metric in scores.items():
            if metric.get("score") is not None:
                totals.setdefault(name, []).append(metric["score"])
    return {name: sum(values) / len(values) for name, values in totals.items()}


async def run_golden_eval(provider: str) -> dict:
    """Run the full golden dataset through retrieval + generation + evaluation.

    Args:
        provider (str): LLM provider to generate answers with.

    Returns:
        dict: {"generated_at", "provider", "results", "averages"}.

    Raises:
        RuntimeError: If evaluation scoring is disabled/misconfigured -- fails
            fast before spending money on retrieval + generation for every
            query, since scoring is the entire point of this script.

    """
    if not settings.opik.enable_evaluation:
        raise RuntimeError(
            "OPIK__ENABLE_EVALUATION must be true to run the golden eval -- it's "
            "what powers the scoring metrics. Set it in .env and restart."
        )
    if not settings.openai.api_key:
        raise RuntimeError(
            "OPENAI__API_KEY must be set to run the golden eval -- G-Eval uses "
            "OpenAI's gpt-4o as the judge model."
        )

    dataset = _load_dataset()
    vectorstore = AsyncQdrantVectorStore()
    judge_model = models.LiteLLMChatModel(
        model_name="gpt-4o", api_key=settings.openai.api_key
    )
    results = []
    try:
        for entry in dataset:
            logger.info(f"Evaluating: {entry['query']}")
            results.append(await _run_one(vectorstore, entry, provider, judge_model))
    finally:
        await vectorstore.client.close()

    return {
        "generated_at": datetime.now().isoformat(),
        "provider": provider,
        "results": results,
        "averages": _compute_averages(results),
    }


def _load_baseline() -> dict | None:
    if not BASELINE_PATH.exists():
        return None
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _compare_to_baseline(averages: dict[str, float], baseline: dict) -> list[str]:
    """Compare this run's averages against a saved baseline.

    Args:
        averages (dict[str, float]): This run's per-metric averages.
        baseline (dict): Previously saved baseline (see `run_golden_eval`'s
            return shape).

    Returns:
        list[str]: One human-readable line per metric that regressed by more
            than REGRESSION_TOLERANCE. Empty if nothing regressed.

    """
    regressions = []
    for name, new_score in averages.items():
        old_score = baseline["averages"].get(name)
        if old_score is not None and (old_score - new_score) > REGRESSION_TOLERANCE:
            regressions.append(
                f"{name}: {old_score:.3f} -> {new_score:.3f} "
                f"(dropped {old_score - new_score:.3f})"
            )
    return regressions


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the golden eval dataset against the live pipeline and "
        "compare average scores to the saved baseline."
    )
    parser.add_argument(
        "--provider", default="openrouter", help="LLM provider to generate answers with"
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Save this run's averages as the new baseline for future comparisons",
    )
    args = parser.parse_args()

    run = asyncio.run(run_golden_eval(args.provider))

    print("\n=== Average scores ===")
    for name, score in run["averages"].items():
        print(f"  {name}: {score:.3f}")

    baseline = _load_baseline()
    if baseline is None:
        print("\nNo baseline found yet -- run with --save-baseline to create one.")
    else:
        regressions = _compare_to_baseline(run["averages"], baseline)
        if regressions:
            print("\n!!! REGRESSIONS DETECTED vs baseline !!!")
            for regression in regressions:
                print(f"  - {regression}")
        else:
            print("\nNo regressions vs baseline.")

    if args.save_baseline:
        BASELINE_PATH.write_text(json.dumps(run, indent=2), encoding="utf-8")
        print(f"\nSaved new baseline to {BASELINE_PATH}")


if __name__ == "__main__":
    main()
