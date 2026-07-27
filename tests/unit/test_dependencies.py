from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import dependencies as deps
from src.config import settings


def _make_test_app() -> FastAPI:
    """A throwaway app with a single route guarded by both dependencies, so we can
    exercise them exactly as FastAPI would invoke them in production (real request
    parsing, real header handling) without needing the full app/lifespan.
    """
    app = FastAPI()

    @app.get(
        "/protected",
        dependencies=[Depends(deps.verify_api_key), Depends(deps.rate_limit)],
    )
    async def protected():
        return {"ok": True}

    return app


@pytest.fixture(autouse=True)
def reset_rate_limit_state():
    """The rate limiter's state is a module-level dict — clear it before and after
    every test so one test's request count never bleeds into the next.
    """
    deps._request_timestamps.clear()
    yield
    deps._request_timestamps.clear()


@pytest.mark.asyncio
async def test_verify_api_key_fails_closed_when_unconfigured():
    """An empty server-side key must reject every request, not allow them through."""
    test_app = _make_test_app()
    with patch.object(settings.api_security, "api_key", ""):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://testserver"
        ) as client:
            response = await client.get("/protected", headers={"X-API-Key": "anything"})
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_verify_api_key_rejects_missing_header():
    test_app = _make_test_app()
    with patch.object(settings.api_security, "api_key", "correct-key"):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://testserver"
        ) as client:
            response = await client.get("/protected")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_verify_api_key_rejects_wrong_key():
    test_app = _make_test_app()
    with patch.object(settings.api_security, "api_key", "correct-key"):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/protected", headers={"X-API-Key": "wrong-key"}
            )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_verify_api_key_accepts_correct_key():
    test_app = _make_test_app()
    with patch.object(settings.api_security, "api_key", "correct-key"):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/protected", headers={"X-API-Key": "correct-key"}
            )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_max_requests():
    test_app = _make_test_app()
    with (
        patch.object(settings.api_security, "api_key", "correct-key"),
        patch.object(settings.api_security, "rate_limit_max_requests", 3),
        patch.object(settings.api_security, "rate_limit_window_seconds", 60),
    ):
        headers = {"X-API-Key": "correct-key", "X-Forwarded-For": "1.2.3.4"}
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://testserver"
        ) as client:
            for _ in range(3):
                response = await client.get("/protected", headers=headers)
                assert response.status_code == 200
            blocked_response = await client.get("/protected", headers=headers)

    assert blocked_response.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_tracks_clients_independently_by_ip():
    """A busy client shouldn't exhaust another client's quota."""
    test_app = _make_test_app()
    with (
        patch.object(settings.api_security, "api_key", "correct-key"),
        patch.object(settings.api_security, "rate_limit_max_requests", 1),
        patch.object(settings.api_security, "rate_limit_window_seconds", 60),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://testserver"
        ) as client:
            first_client_response = await client.get(
                "/protected",
                headers={"X-API-Key": "correct-key", "X-Forwarded-For": "1.1.1.1"},
            )
            second_client_response = await client.get(
                "/protected",
                headers={"X-API-Key": "correct-key", "X-Forwarded-For": "2.2.2.2"},
            )

    assert first_client_response.status_code == 200
    assert second_client_response.status_code == 200


def test_get_client_ip_prefers_x_forwarded_for():
    """Behind an AWS ALB/API Gateway, request.client.host would be the load
    balancer's own IP — X-Forwarded-For carries the real client IP instead.
    """
    from starlette.datastructures import Headers
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": Headers({"x-forwarded-for": "9.9.9.9, 10.0.0.1"}).raw,
        "client": ("127.0.0.1", 12345),
    }
    request = Request(scope)
    assert deps._get_client_ip(request) == "9.9.9.9"


def test_get_client_ip_falls_back_to_request_client():
    from starlette.datastructures import Headers
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": Headers({}).raw,
        "client": ("127.0.0.1", 12345),
    }
    request = Request(scope)
    assert deps._get_client_ip(request) == "127.0.0.1"
