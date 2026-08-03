from datetime import date

import pytest
from qdrant_client.models import DatetimeRange, MatchValue

from src.api.services.search_service import _build_filter


@pytest.mark.unit
def test_build_filter_returns_none_when_nothing_set() -> None:
    assert _build_filter(None, None, None, None, None, None) is None


@pytest.mark.unit
def test_build_filter_adds_date_range_condition_when_either_bound_set() -> None:
    date_from = date(2026, 1, 1)
    date_to = date(2026, 6, 1)

    result = _build_filter(None, None, None, None, date_from, date_to)

    assert result is not None
    assert len(result.must) == 1
    condition = result.must[0]
    assert condition.key == "published_at"
    assert isinstance(condition.range, DatetimeRange)
    assert condition.range.gte == date_from
    assert condition.range.lte == date_to


@pytest.mark.unit
def test_build_filter_date_range_works_with_only_one_bound() -> None:
    date_from = date(2026, 1, 1)

    result = _build_filter(None, None, None, None, date_from, None)

    assert result is not None
    condition = result.must[0]
    assert condition.range.gte == date_from
    assert condition.range.lte is None


@pytest.mark.unit
def test_build_filter_combines_date_range_with_other_filters() -> None:
    result = _build_filter(
        "Feed Author", None, None, None, date(2026, 1, 1), date(2026, 6, 1)
    )

    assert result is not None
    assert len(result.must) == 2
    feed_author_condition = next(c for c in result.must if c.key == "feed_author")
    date_condition = next(c for c in result.must if c.key == "published_at")
    assert isinstance(feed_author_condition.match, MatchValue)
    assert feed_author_condition.match.value == "Feed Author"
    assert isinstance(date_condition.range, DatetimeRange)
