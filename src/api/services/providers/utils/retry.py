import openai
from huggingface_hub.errors import InferenceTimeoutError, OverloadedError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.utils.logger_util import setup_logging

logger = setup_logging()

MAX_ATTEMPTS = 3


def _log_retry(retry_state: RetryCallState) -> None:
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    fn_name = retry_state.fn.__name__ if retry_state.fn else "provider call"
    logger.warning(
        f"Retrying {fn_name} (attempt {retry_state.attempt_number}/{MAX_ATTEMPTS}) "
        f"after {exception!r}"
    )


# Only transient, provider-side failures are worth retrying -- connection
# drops, timeouts, rate limiting, and 5xx server errors are the kind of thing
# a second attempt (after a short backoff) can actually fix. A bad API key,
# malformed request, or content-filter rejection will fail identically every
# time, so those are deliberately excluded and fail on the first attempt.
retry_openai_call = retry(
    retry=retry_if_exception_type(
        (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        )
    ),
    wait=wait_exponential_jitter(initial=1, max=10),
    stop=stop_after_attempt(MAX_ATTEMPTS),
    reraise=True,
    before_sleep=_log_retry,
)
"""Applies to OpenAI SDK-based providers (OpenAI directly, and OpenRouter,
which is also an AsyncOpenAI client pointed at a different base_url).
"""

retry_huggingface_call = retry(
    retry=retry_if_exception_type((InferenceTimeoutError, OverloadedError)),
    wait=wait_exponential_jitter(initial=1, max=10),
    stop=stop_after_attempt(MAX_ATTEMPTS),
    reraise=True,
    before_sleep=_log_retry,
)
