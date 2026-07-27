import asyncio

import opik
from opik import opik_context
from opik.evaluation import models
from opik.evaluation.metrics import AnswerRelevance, GEval, Hallucination, Usefulness
from opik.types import BatchFeedbackScoreDict

from src.config import settings
from src.utils.logger_util import setup_logging

logger = setup_logging()

# -----------------------
# Evaluation helper
# -----------------------

# Keep strong references to fire-and-forget evaluation tasks so asyncio doesn't
# garbage-collect them mid-flight before they finish (see asyncio.create_task docs).
_background_tasks: set[asyncio.Task] = set()

# Custom G-Eval metrics: LLM-as-judge prompted with our own task/criteria text.
METRIC_CONFIGS = {
    "faithfulness": (
        (
            "You are an expert judge tasked with evaluating whether an AI-generated answer is "
            "faithful to the provided Substack excerpts."
        ),
        (
            "The OUTPUT must not introduce new information beyond "
            "what is contained in the CONTEXT. "
            "All claims in the OUTPUT should be directly supported by the CONTEXT."
        ),
    ),
    "coherence": (
        (
            "You are an expert judge tasked with evaluating whether an AI-generated answer is "
            "logically coherent."
        ),
        "The answer should be well-structured, readable, and maintain consistent reasoning.",
    ),
    "completeness": (
        (
            "You are an expert judge tasked with evaluating whether an AI-generated answer "
            "covers all relevant aspects of the query."
        ),
        (
            "The answer should include all major points from the CONTEXT "
            "and address the user's query fully."
        ),
    ),
}


async def _score_geval_metric(
    judge_model,
    name: str,
    task_intro: str,
    eval_criteria: str,
    output: str,
    context_text: str,
) -> tuple[str, dict]:
    """Score a single custom G-Eval metric (LLM-as-judge prompted with our own criteria).

    Args:
        judge_model: The Opik LiteLLM judge model instance.
        name (str): Metric name (e.g. "faithfulness").
        task_intro (str): G-Eval task introduction for this metric.
        eval_criteria (str): G-Eval evaluation criteria for this metric.
        output (str): The LLM-generated output being evaluated.
        context_text (str): The retrieved source chunks, joined into one string.

    Returns:
        tuple[str, dict]: (metric name, {"score", "reason", "failed"}).

    """
    try:
        metric = GEval(
            task_introduction=task_intro,
            evaluation_criteria=eval_criteria,
            model=judge_model,
            name=f"G-Eval {name.capitalize()}",
        )
        eval_input = f"OUTPUT: {output}\nCONTEXT: {context_text}"
        score_result = await metric.ascore(eval_input)
        return name, {
            "score": score_result.value,
            "reason": score_result.reason,
            "failed": score_result.scoring_failed,
        }
    except Exception as e:
        logger.warning(f"G-Eval {name} failed: {e}")
        return name, {"score": 0.0, "reason": str(e), "failed": True}


async def _score_builtin_metric(metric, name: str, **score_kwargs) -> tuple[str, dict]:
    """Score using one of Opik's pre-built metric classes (Hallucination, AnswerRelevance, ...).

    Args:
        metric: An instantiated Opik metric object exposing an async `.ascore(...)`.
        name (str): Metric name to key the result by (e.g. "hallucination").
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


async def evaluate_metrics(
    query: str, output: str, context_chunks: list[str]
) -> dict[str, dict] | None:
    """Score an LLM output on faithfulness, coherence, completeness, hallucination,
    answer relevance, and usefulness.

    Every metric is scored concurrently. Evaluation is skipped (returns None) when
    disabled via config, no OpenAI judge key is configured, or the output is empty.

    Args:
        query (str): The original user question.
        output (str): The LLM-generated answer to evaluate.
        context_chunks (list[str]): The retrieved source chunks the answer was based on.

    Returns:
        dict[str, dict] | None: Per-metric {"score", "reason", "failed"} results,
            or None if evaluation was skipped.

    """
    if not settings.opik.enable_evaluation:
        logger.debug(
            "G-Eval evaluation disabled (OPIK__ENABLE_EVALUATION=false). Skipping."
        )
        return None

    if not settings.openai.api_key:
        logger.warning(
            "G-Eval evaluation is enabled but OPENAI__API_KEY is not set (G-Eval uses "
            "OpenAI's gpt-4o as the judge model). Skipping."
        )
        return None

    if not output.strip():
        logger.warning("Output is empty. Skipping evaluation.")
        return None

    judge_model = models.LiteLLMChatModel(
        model_name="gpt-4o",  # gpt-4o, gpt-5-mini
        api_key=settings.openai.api_key,
    )
    context_text = "\n\n".join(context_chunks)

    geval_tasks = [
        _score_geval_metric(
            judge_model, name, task_intro, eval_criteria, output, context_text
        )
        for name, (task_intro, eval_criteria) in METRIC_CONFIGS.items()
    ]
    builtin_tasks = [
        _score_builtin_metric(
            Hallucination(model=judge_model),
            "hallucination",
            input=query,
            output=output,
            context=context_chunks,
        ),
        _score_builtin_metric(
            AnswerRelevance(model=judge_model),
            "answer_relevance",
            input=query,
            output=output,
            context=context_chunks,
        ),
        _score_builtin_metric(
            Usefulness(model=judge_model),
            "usefulness",
            input=query,
            output=output,
        ),
    ]

    scored = await asyncio.gather(*geval_tasks, *builtin_tasks)
    return dict(scored)


def _log_scores_to_opik(trace_id: str, results: dict[str, dict]) -> None:
    """Attach evaluation scores to their originating trace so they appear on the
    Opik dashboard, instead of only ever being written to the local application log.

    Args:
        trace_id (str): The Opik trace ID the scores belong to.
        results (dict[str, dict]): Per-metric {"score", "reason", "failed"} results.

    Returns:
        None

    """
    scores: list[BatchFeedbackScoreDict] = [
        {
            "id": trace_id,
            "name": f"eval_{name}",
            "value": metric["score"] if metric["score"] is not None else 0.0,
            "reason": metric["reason"],
        }
        for name, metric in results.items()
    ]
    opik.Opik().log_traces_feedback_scores(scores=scores)
    logger.info(f"Logged evaluation scores to Opik for trace {trace_id}: {results}")


def schedule_evaluation(query: str, output: str, context_chunks: list[str]) -> None:
    """Fire-and-forget evaluation scoring so it never adds latency to the user-facing
    response. No-ops immediately (without creating a task) if evaluation is disabled.

    The current Opik trace ID is captured synchronously, before the background
    task starts, so the scores can be attached to the correct trace on the
    dashboard even if that trace has already ended by the time scoring finishes.
    Note: this only works for a call made while an Opik trace is active (e.g. from
    within @opik.track-decorated generate_answer). The streaming path currently has
    no active trace by the time it calls this (see get_streaming_function), so
    scores there are still only logged locally until that's wired up separately.

    Args:
        query (str): The original user question.
        output (str): The LLM-generated answer to evaluate.
        context_chunks (list[str]): The retrieved source chunks the answer was based on.

    Returns:
        None

    """
    if not settings.opik.enable_evaluation:
        return

    current_trace = opik_context.get_current_trace_data()
    trace_id = current_trace.id if current_trace else None

    async def _run() -> None:
        try:
            results = await evaluate_metrics(query, output, context_chunks)
            if not results:
                return
            if trace_id:
                _log_scores_to_opik(trace_id, results)
            else:
                logger.warning(
                    "Evaluation results computed but no active Opik trace was found to "
                    f"attach them to; results were only logged locally: {results}"
                )
        except Exception as e:
            logger.warning(f"Background evaluation failed: {e}")

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
