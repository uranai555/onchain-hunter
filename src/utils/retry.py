"""Reusable retry/backoff decorator for API calls."""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

import requests

from src.utils.logger import get_logger

logger = get_logger("retry")

F = TypeVar("F", bound=Callable[..., Any])


def retry_on_http_error(
    max_retries: int = 3,
    base_delay: float = 2.0,
    retry_status_codes: tuple[int, ...] = (429, 502, 503, 504),
) -> Callable[[F], F]:
    """Decorator that retries a function on HTTP errors with exponential backoff.

    Args:
        max_retries: Maximum number of attempts.
        base_delay: Base delay in seconds (multiplied by attempt number).
        retry_status_codes: HTTP status codes that trigger a retry.
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except requests.HTTPError as exc:
                    last_exc = exc
                    status = exc.response.status_code if exc.response is not None else 0
                    if status in retry_status_codes and attempt < max_retries - 1:
                        wait = base_delay * (attempt + 1)
                        logger.warning(
                            "%s: HTTP %d, retrying in %.1fs (attempt %d/%d)",
                            func.__name__, status, wait, attempt + 1, max_retries,
                        )
                        time.sleep(wait)
                    else:
                        raise
                except (requests.ConnectionError, requests.Timeout) as exc:
                    last_exc = exc
                    if attempt < max_retries - 1:
                        wait = base_delay * (attempt + 1)
                        logger.warning(
                            "%s: %s, retrying in %.1fs (attempt %d/%d)",
                            func.__name__, type(exc).__name__, wait, attempt + 1, max_retries,
                        )
                        time.sleep(wait)
                    else:
                        raise
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator
