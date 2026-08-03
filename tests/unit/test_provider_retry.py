from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import openai
import pytest
from huggingface_hub.errors import InferenceTimeoutError

from src.api.models.provider_models import ModelConfig
from src.api.services.providers.huggingface_service import generate_huggingface
from src.api.services.providers.openai_service import generate_openai
from src.api.services.providers.openrouter_service import generate_openrouter

_REQUEST = httpx.Request("POST", "https://example.com")


def _rate_limit_error() -> openai.RateLimitError:
    return openai.RateLimitError(
        "rate limited",
        response=httpx.Response(status_code=429, request=_REQUEST),
        body=None,
    )


def _auth_error() -> openai.AuthenticationError:
    return openai.AuthenticationError(
        "bad api key",
        response=httpx.Response(status_code=401, request=_REQUEST),
        body=None,
    )


def _fake_completion(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _config() -> ModelConfig:
    return ModelConfig(primary_model="mock-model")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_openai_retries_transient_error_then_succeeds():
    mock_create = AsyncMock(
        side_effect=[_rate_limit_error(), _fake_completion("recovered answer")]
    )
    with (
        patch(
            "src.api.services.providers.openai_service.async_openai_client.chat.completions.create",
            new=mock_create,
        ),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        answer, model_used = await generate_openai("prompt", _config())

    assert answer == "recovered answer"
    assert mock_create.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_openai_does_not_retry_authentication_error():
    mock_create = AsyncMock(side_effect=_auth_error())
    with (
        patch(
            "src.api.services.providers.openai_service.async_openai_client.chat.completions.create",
            new=mock_create,
        ),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(openai.AuthenticationError):
            await generate_openai("prompt", _config())

    assert mock_create.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_openai_exhausts_after_max_attempts():
    mock_create = AsyncMock(side_effect=_rate_limit_error())
    with (
        patch(
            "src.api.services.providers.openai_service.async_openai_client.chat.completions.create",
            new=mock_create,
        ),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(openai.RateLimitError):
            await generate_openai("prompt", _config())

    assert mock_create.await_count == 3  # MAX_ATTEMPTS, no 4th call


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_openrouter_retries_transient_error_then_succeeds():
    mock_create = AsyncMock(
        side_effect=[_rate_limit_error(), _fake_completion("recovered answer")]
    )
    with (
        patch(
            "src.api.services.providers.openrouter_service.async_openrouter_client"
            ".chat.completions.create",
            new=mock_create,
        ),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        answer, model_used, finish_reason = await generate_openrouter(
            "prompt", _config()
        )

    assert answer == "recovered answer"
    assert mock_create.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_huggingface_retries_timeout_then_succeeds():
    mock_create = AsyncMock(
        side_effect=[
            InferenceTimeoutError("timed out"),
            _fake_completion("recovered answer"),
        ]
    )
    with (
        # hf_client.chat.completions.create is a read-only property that
        # forwards to hf_client.chat_completion -- that's the actual patchable
        # method (see huggingface_hub's ProxyClientChatCompletions.create).
        patch(
            "src.api.services.providers.huggingface_service.hf_client.chat_completion",
            new=mock_create,
        ),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        answer, model_used = await generate_huggingface("prompt", _config())

    assert answer == "recovered answer"
    assert mock_create.await_count == 2
