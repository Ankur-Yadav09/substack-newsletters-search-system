from datetime import date

import opik
from fastapi import Request
from qdrant_client.models import (
    DatetimeRange,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchText,
    MatchValue,
    Prefetch,
)

from src.api.models.api_models import SearchResult
from src.infrastructure.qdrant.qdrant_vectorstore import AsyncQdrantVectorStore
from src.utils.logger_util import setup_logging

logger = setup_logging()


def _build_filter(
    feed_author: str | None,
    feed_name: str | None,
    article_author: list[str] | None,
    title_keywords: str | None,
    date_from: date | None,
    date_to: date | None,
) -> Filter | None:
    """Build the shared Qdrant filter used by both search functions.

    Args:
        feed_author (str | None): Exact-match filter for the feed author.
        feed_name (str | None): Exact-match filter for the feed name.
        article_author (list[str] | None): Match-any filter against article_authors.
        title_keywords (str | None): Substring/keyword filter on the title.
        date_from (date | None): Only include articles published on/after this date.
        date_to (date | None): Only include articles published on/before this date.

    Returns:
        Filter | None: A Qdrant Filter with a `must` condition per active
            filter, or None if no filters were provided.

    """
    conditions: list[FieldCondition] = []
    if feed_author:
        conditions.append(
            FieldCondition(key="feed_author", match=MatchValue(value=feed_author))
        )
    if feed_name:
        conditions.append(
            FieldCondition(key="feed_name", match=MatchValue(value=feed_name))
        )
    if article_author:
        conditions.append(
            FieldCondition(key="article_authors", match=MatchAny(any=article_author))
        )
    if title_keywords:
        conditions.append(
            FieldCondition(
                key="title", match=MatchText(text=title_keywords.strip().lower())
            )
        )
    if date_from or date_to:
        # published_at requires a datetime payload index (see
        # AsyncQdrantVectorStore.create_published_at_index) -- Qdrant rejects
        # range filters on an unindexed field. DatetimeRange accepts a plain
        # `date` directly (verified against the real collection), no manual
        # string conversion needed.
        conditions.append(
            FieldCondition(
                key="published_at", range=DatetimeRange(gte=date_from, lte=date_to)
            )
        )
    return Filter(must=conditions) if conditions else None  # type: ignore


def _rerank_results(
    vectorstore: AsyncQdrantVectorStore,
    query_text: str,
    results: list[SearchResult],
    limit: int,
) -> list[SearchResult]:
    """Re-score a candidate pool of RRF-fused results with a cross-encoder and
    return the top `limit`.

    RRF fusion ranks purely on reciprocal rank across the dense/sparse branches,
    which ignores how well each chunk's actual text answers the query. Re-scoring
    the top candidates with a cross-encoder (which looks at query+document
    together, unlike the independently-embedded dense/sparse vectors) typically
    produces a meaningfully better final ordering.

    Only the top `candidate_pool_size` RRF results are re-scored, not the full
    overfetched set, to keep per-query latency bounded. Falls back to the
    original RRF order if re-ranking is disabled or there's nothing to score.

    Args:
        vectorstore (AsyncQdrantVectorStore): Provides the reranker and its settings.
        query_text (str): The user's search query.
        results (list[SearchResult]): RRF-fused, deduplicated candidates, already
            ordered by fusion score (best first).
        limit (int): Final number of results to return.

    Returns:
        list[SearchResult]: Top `limit` results, re-ordered by cross-encoder score
            when re-ranking is enabled. Each result's `score` field is overwritten
            with the cross-encoder score, so the returned score always matches the
            criterion that produced the returned order (rather than leaving the
            original, now-stale RRF fusion score attached).

    """
    if not vectorstore.reranker_settings.enabled or not results:
        return results[:limit]

    candidate_pool = results[: vectorstore.reranker_settings.candidate_pool_size]
    scores = vectorstore.rerank(
        query_text, [result.chunk_text or "" for result in candidate_pool]
    )
    for result, score in zip(candidate_pool, scores, strict=True):
        result.score = score

    candidate_pool.sort(key=lambda result: result.score, reverse=True)
    return candidate_pool[:limit]


@opik.track(name="query_with_filters")
async def query_with_filters(
    request: Request,
    query_text: str = "",
    feed_author: str | None = None,
    feed_name: str | None = None,
    article_author: list[str] | None = None,
    title_keywords: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 5,
) -> list[SearchResult]:
    """Query the vector store with optional filters and return search results.

    Performs a hybrid dense + sparse search on Qdrant and applies filters based
    on feed author, feed name, article author(s), title keywords, and publish
    date range. Results are deduplicated by point ID.

    Args:
        request (Request): FastAPI request object containing the vector store in app.state.
        query_text (str): Text query to search for.
        feed_author (str | None): Optional filter for the feed author.
        feed_name (str | None): Optional filter for the feed name.
        article_author (list[str] | None): Optional filter matching any of the given
            article authors against the article_authors payload field.
        title_keywords (str | None): Optional filter for title keywords.
        date_from (date | None): Only include articles published on/after this date.
        date_to (date | None): Only include articles published on/before this date.
        limit (int): Maximum number of results to return.

    Returns:
        list[SearchResult]:
            List of search results containing title, feed info, URL, chunk text, and score.

    """
    vectorstore: AsyncQdrantVectorStore = request.app.state.vectorstore
    dense_vector = vectorstore.dense_vectors([query_text])[0]
    sparse_vector = vectorstore.sparse_vectors([query_text])[0]

    query_filter = _build_filter(
        feed_author, feed_name, article_author, title_keywords, date_from, date_to
    )

    fetch_limit = max(1, limit) * 100
    logger.info(f"Fetching up to {fetch_limit} points for unique Ids.")

    response = await vectorstore.client.query_points(
        collection_name=vectorstore.collection_name,
        query=FusionQuery(fusion=Fusion.RRF),
        prefetch=[
            Prefetch(
                query=dense_vector,
                using="Dense",
                limit=fetch_limit,
                filter=query_filter,
            ),
            Prefetch(
                query=sparse_vector,
                using="Sparse",
                limit=fetch_limit,
                filter=query_filter,
            ),
        ],
        query_filter=query_filter,
        limit=fetch_limit,
    )

    # Deduplicate by point ID
    seen_ids: set[str] = set()
    results: list[SearchResult] = []
    for point in response.points:
        if point.id in seen_ids:
            continue
        seen_ids.add(point.id)  # type: ignore
        payload = point.payload or {}
        results.append(
            SearchResult(
                title=payload.get("title", ""),
                feed_author=payload.get("feed_author"),
                feed_name=payload.get("feed_name"),
                article_author=payload.get("article_authors"),
                url=payload.get("url"),
                chunk_text=payload.get("chunk_text"),
                score=point.score,
            )
        )

    results = _rerank_results(vectorstore, query_text, results, limit)
    logger.info(f"Returning {len(results)} results for matching query '{query_text}'")
    return results


@opik.track(name="query_unique_titles")
async def query_unique_titles(
    request: Request,
    query_text: str,
    feed_author: str | None = None,
    feed_name: str | None = None,
    article_author: list[str] | None = None,
    title_keywords: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 5,
) -> list[SearchResult]:
    """Query the vector store and return only unique titles.

    Performs a hybrid dense + sparse search with optional filters and dynamically
    increases the fetch limit to account for duplicates. Deduplicates results
    by article title.

    Args:
        request (Request): FastAPI request object containing the vector store in app.state.
        query_text (str): Text query to search for.
        feed_author (str | None): Optional filter for the feed author.
        feed_name (str | None): Optional filter for the feed name.
        article_author (list[str] | None): Optional filter matching any of the given
            article authors against the article_authors payload field.
        title_keywords (str | None): Optional filter for title keywords.
        date_from (date | None): Only include articles published on/after this date.
        date_to (date | None): Only include articles published on/before this date.
        limit (int): Maximum number of unique results to return.

    Returns:
        list[SearchResult]:
            List of unique search results containing title, feed info, URL, chunk text, and score.

    """
    vectorstore: AsyncQdrantVectorStore = request.app.state.vectorstore
    dense_vector = vectorstore.dense_vectors([query_text])[0]
    sparse_vector = vectorstore.sparse_vectors([query_text])[0]

    query_filter = _build_filter(
        feed_author, feed_name, article_author, title_keywords, date_from, date_to
    )

    fetch_limit = max(1, limit) * 280
    logger.info(f"Fetching up to {fetch_limit} points for unique titles.")

    response = await vectorstore.client.query_points(
        collection_name=vectorstore.collection_name,
        query=FusionQuery(fusion=Fusion.RRF),
        prefetch=[
            Prefetch(
                query=dense_vector,
                using="Dense",
                limit=fetch_limit,
                filter=query_filter,
            ),
            Prefetch(
                query=sparse_vector,
                using="Sparse",
                limit=fetch_limit,
                filter=query_filter,
            ),
        ],
        query_filter=query_filter,
        limit=fetch_limit,
    )

    # Deduplicate by title. Collect enough candidates to fill the re-rank
    # candidate pool (not just `limit`) so re-ranking has a real pool of unique
    # titles to choose from, rather than pre-truncating to `limit` on raw RRF
    # order before re-ranking ever runs.
    pool_target = (
        max(limit, vectorstore.reranker_settings.candidate_pool_size)
        if vectorstore.reranker_settings.enabled
        else limit
    )
    seen_titles: set[str] = set()
    results: list[SearchResult] = []
    for point in response.points:
        payload = point.payload or {}
        title = payload.get("title")
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        results.append(
            SearchResult(
                title=title,
                feed_author=payload.get("feed_author"),
                feed_name=payload.get("feed_name"),
                article_author=payload.get("article_authors"),
                url=payload.get("url"),
                chunk_text=payload.get("chunk_text"),
                score=point.score,
            )
        )
        if len(results) >= pool_target:
            break

    results = _rerank_results(vectorstore, query_text, results, limit)
    logger.info(
        f"Returning {len(results)} unique title results for matching query '{query_text}'"
    )
    return results
