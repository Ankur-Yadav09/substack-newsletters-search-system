from datetime import date

import pytest
from pydantic import ValidationError

from src.api.models.api_models import AskRequest, UniqueTitleRequest


@pytest.mark.unit
def test_ask_request_accepts_valid_date_strings() -> None:
    req = AskRequest(query_text="q", date_from="2026-01-01", date_to="2026-06-01")

    assert req.date_from == date(2026, 1, 1)
    assert req.date_to == date(2026, 6, 1)


@pytest.mark.unit
def test_ask_request_rejects_malformed_date_string() -> None:
    with pytest.raises(ValidationError):
        AskRequest(query_text="q", date_from="not-a-date")


@pytest.mark.unit
def test_ask_request_rejects_inverted_date_range() -> None:
    with pytest.raises(ValidationError, match="must not be after"):
        AskRequest(query_text="q", date_from="2026-06-01", date_to="2026-01-01")


@pytest.mark.unit
def test_ask_request_allows_equal_from_and_to() -> None:
    req = AskRequest(query_text="q", date_from="2026-01-01", date_to="2026-01-01")

    assert req.date_from == req.date_to


@pytest.mark.unit
def test_ask_request_allows_only_one_bound() -> None:
    req = AskRequest(query_text="q", date_from="2026-01-01")

    assert req.date_from == date(2026, 1, 1)
    assert req.date_to is None


@pytest.mark.unit
def test_unique_title_request_rejects_inverted_date_range() -> None:
    with pytest.raises(ValidationError, match="must not be after"):
        UniqueTitleRequest(query_text="q", date_from="2026-06-01", date_to="2026-01-01")


@pytest.mark.unit
def test_unique_title_request_accepts_valid_date_strings() -> None:
    req = UniqueTitleRequest(
        query_text="q", date_from="2026-01-01", date_to="2026-06-01"
    )

    assert req.date_from == date(2026, 1, 1)
    assert req.date_to == date(2026, 6, 1)
