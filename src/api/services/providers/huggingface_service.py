from collections.abc import AsyncGenerator

from huggingface_hub import AsyncInferenceClient

from src.api.models.provider_models import ModelConfig
from src.api.services.providers.utils.messages import build_messages
from src.api.services.providers.utils.retry import retry_huggingface_call
from src.config import settings
from src.utils.logger_util import setup_logging

logger = setup_logging()

# -----------------------
# Hugging Face client
# -----------------------
hf_key = settings.hugging_face.api_key
hf_client = AsyncInferenceClient(
    provider="auto", api_key=hf_key, timeout=settings.hugging_face.timeout_seconds
)


@retry_huggingface_call
async def generate_huggingface(prompt: str, config: ModelConfig) -> tuple[str, None]:
    """Generate a response from Hugging Face for a given prompt and model configuration.

    Retries on transient errors (timeouts, model-overloaded) with exponential
    backoff -- see retry.py for exactly which errors qualify.

    Args:
        prompt (str): The input prompt.
        config (ModelConfig): The model configuration.

    Returns:
        tuple[str, None]: The generated response and None for model and finish reason.

    """
    resp = await hf_client.chat.completions.create(
        model=config.primary_model,
        messages=build_messages(prompt),
        temperature=config.temperature,
        max_tokens=config.max_completion_tokens,
    )
    return resp.choices[0].message.content or "", None


def stream_huggingface(prompt: str, config: ModelConfig) -> AsyncGenerator[str, None]:
    """Stream a response from Hugging Face for a given prompt and model configuration.

    Deliberately not wrapped in @retry_huggingface_call, unlike
    generate_huggingface: retrying after chunks have already been yielded to
    the client would mean re-sending or duplicating partial output, which
    isn't safe to do transparently. Retry is only applied to the
    non-streaming path.

    Args:
        prompt (str): The input prompt.
        config (ModelConfig): The model configuration.

    Returns:
        AsyncGenerator[str, None]: An asynchronous generator yielding response chunks.

    """

    async def gen() -> AsyncGenerator[str, None]:
        stream = await hf_client.chat.completions.create(
            model=config.primary_model,
            messages=build_messages(prompt),
            temperature=config.temperature,
            max_tokens=config.max_completion_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta_text = getattr(chunk.choices[0].delta, "content", None)
            if delta_text:
                yield delta_text

    return gen()
