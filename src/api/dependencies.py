"""Shared FastAPI dependencies: shared-secret API key auth + a lightweight rate limiter.

Both are deliberately simple — a single shared API key and in-process rate-limit
state — appropriate for a single running instance. If this is later scaled to
multiple instances (e.g. behind an AWS ALB with more than one task/container), the
rate limiter's in-memory state needs to move to a shared store (e.g. Redis/ElastiCache)
since each instance would otherwise track its own counts independently, making the
effective limit `max_requests * instance_count` instead of the configured value.
"""

import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request, status

from src.config import settings
from src.utils.logger_util import setup_logging

logger = setup_logging()

# client_ip -> timestamps (monotonic seconds) of requests within the current window
_request_timestamps: dict[str, deque[float]] = defaultdict(deque)


def _get_client_ip(request: Request) -> str:
    """Best-effort real client IP.

    Prefers X-Forwarded-For (set by AWS's ALB/API Gateway and most reverse proxies)
    over request.client.host, which would otherwise report the load balancer's own
    IP for every request once deployed behind one.

    Args:
        request (Request): The incoming FastAPI request.

    Returns:
        str: The best-effort client IP, or "unknown" if neither is available.

    """
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Require a valid shared API key on every request to a protected route.

    Fails closed: if no server-side key is configured at all, every request is
    rejected with a 500 rather than silently allowing unauthenticated access —
    an unset key should never be mistaken for "auth disabled".

    Args:
        x_api_key (str | None): The value of the X-API-Key request header.

    Returns:
        None

    Raises:
        HTTPException: 500 if no API key is configured server-side; 401 if the
            provided key is missing or doesn't match.

    """
    configured_key = settings.api_security.api_key
    if not configured_key:
        logger.error(
            "API_SECURITY__API_KEY is not configured — rejecting all requests to "
            "protected routes rather than allowing unauthenticated access."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfiguration: API key not set",
        )
    if x_api_key != configured_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


async def rate_limit(request: Request) -> None:
    """Fixed-window rate limit, keyed by client IP.

    In-memory only — correct for a single running instance. See module docstring
    for the caveat about horizontal scaling.

    Args:
        request (Request): The incoming FastAPI request.

    Returns:
        None

    Raises:
        HTTPException: 429 if the client has exceeded the configured request rate.

    """
    client_ip = _get_client_ip(request)
    now = time.monotonic()
    window_seconds = settings.api_security.rate_limit_window_seconds
    max_requests = settings.api_security.rate_limit_max_requests

    timestamps = _request_timestamps[client_ip]
    while timestamps and timestamps[0] <= now - window_seconds:
        timestamps.popleft()

    if len(timestamps) >= max_requests:
        logger.warning(f"Rate limit exceeded for client {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: max {max_requests} requests per {window_seconds}s",
        )

    timestamps.append(now)
