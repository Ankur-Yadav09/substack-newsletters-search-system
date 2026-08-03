import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from src.api.models.api_models import AskRequest, SearchResult
from src.config import settings
from src.utils.logger_util import setup_logging

logger = setup_logging()


@dataclass
class CacheEntry:
    context_key: tuple
    embedding: list[float]
    answer: str
    sources: list[SearchResult]
    model: str | None
    finish_reason: str | None
    created_at: float


def build_context_key(ask: AskRequest) -> tuple:
    """Build the exact-match part of a cache lookup key from an /ask request.

    Everything except `query_text` must match exactly for a cache entry to be
    eligible — a cached OpenRouter answer shouldn't be served for an OpenAI
    request, a cached answer scoped to one feed_author shouldn't leak into
    an unfiltered request, and a cached answer for one date range shouldn't
    leak into a request scoped to a different date range. `query_text` itself
    is deliberately excluded: that's compared by embedding similarity instead,
    not exact match.

    Args:
        ask (AskRequest): The incoming /ask request.

    Returns:
        tuple: A hashable key grouping requests that a cached answer could
            validly be reused across.

    """
    return (
        ask.provider,
        ask.model,
        ask.feed_author,
        ask.feed_name,
        tuple(sorted(ask.article_author)) if ask.article_author else None,
        ask.title_keywords,
        ask.date_from,
        ask.date_to,
        ask.limit,
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    a_arr = np.asarray(a)
    b_arr = np.asarray(b)
    denom = float(np.linalg.norm(a_arr) * np.linalg.norm(b_arr))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / denom)


class SemanticCache:
    """In-memory cache mapping a previously answered /ask request to its answer,
    so a new question that's semantically close to one already answered skips
    retrieval and LLM generation entirely.

    Deliberately in-process (no Redis) -- this is a single-instance personal
    project, same reasoning as the in-memory rate limiter in dependencies.py.
    Entries are bounded by `max_size` (oldest evicted first via `deque.maxlen`)
    and expire after `ttl_seconds` regardless of eviction order.

    Only wired into the non-streaming /ask endpoint. Streaming responses would
    need to fake a stream from a cached string to benefit, which adds
    complexity not worth it for a first version -- see PRODUCTION_CHECKLIST.md.
    """

    def __init__(self) -> None:
        self._entries: deque[CacheEntry] = deque(
            maxlen=settings.semantic_cache.max_size
        )

    def get(self, context_key: tuple, query_vector: list[float]) -> CacheEntry | None:
        """Return the best matching cache entry for this context and query, if any.

        Args:
            context_key (tuple): Exact-match key from `build_context_key`.
            query_vector (list[float]): Dense embedding of the incoming query text.

        Returns:
            CacheEntry | None: The highest-similarity non-expired entry that
                meets the configured similarity threshold, or None on a miss.

        """
        if not settings.semantic_cache.enabled:
            return None

        now = time.monotonic()
        ttl = settings.semantic_cache.ttl_seconds
        threshold = settings.semantic_cache.similarity_threshold

        best_entry: CacheEntry | None = None
        best_similarity = -1.0

        for entry in self._entries:
            if entry.context_key != context_key:
                continue
            if now - entry.created_at > ttl:
                continue
            similarity = _cosine_similarity(entry.embedding, query_vector)
            if similarity >= threshold and similarity > best_similarity:
                best_similarity = similarity
                best_entry = entry

        if best_entry is not None:
            logger.info(
                f"Semantic cache hit (similarity={best_similarity:.4f}) "
                f"for context {context_key}"
            )
        return best_entry

    def set(
        self,
        context_key: tuple,
        query_vector: list[float],
        *,
        answer: str,
        sources: list[SearchResult],
        model: str | None,
        finish_reason: str | None,
    ) -> None:
        """Store a newly generated answer in the cache.

        Args:
            context_key (tuple): Exact-match key from `build_context_key`.
            query_vector (list[float]): Dense embedding of the query text that
                produced this answer.
            answer (str): The generated answer text.
            sources (list[SearchResult]): Context documents used to generate the answer.
            model (str | None): The specific model that produced the answer, if known.
            finish_reason (str | None): Why generation finished, if known.

        Returns:
            None

        """
        if not settings.semantic_cache.enabled:
            return
        self._entries.append(
            CacheEntry(
                context_key=context_key,
                embedding=query_vector,
                answer=answer,
                sources=sources,
                model=model,
                finish_reason=finish_reason,
                created_at=time.monotonic(),
            )
        )

    def clear(self) -> None:
        """Remove all cached entries. Mainly useful for tests."""
        self._entries.clear()


semantic_cache = SemanticCache()
