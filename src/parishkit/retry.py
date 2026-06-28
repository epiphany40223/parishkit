"""Retry helpers for atomic external-service operations."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any

import requests


class RetryError(RuntimeError):
    """Raised when a retryable operation exhausts all attempts."""

    def __init__(self, message: str, last_exception: BaseException):
        """Record the failure message and the final underlying exception.

        ``last_exception`` preserves the exception from the last attempt so
        callers can inspect or re-raise the original cause.
        """
        super().__init__(message)
        self.last_exception = last_exception


class TransientRetryError(RuntimeError):
    """Base exception for callers to wrap known transient failures."""


DEFAULT_RETRY_EXCEPTIONS = (
    TransientRetryError,
    TimeoutError,
    ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
)


@dataclass(frozen=True)
class RetryPolicy:
    """Immutable configuration for exponential backoff retries.

    ``attempts`` is the total number of tries (not extra retries).
    ``initial_delay`` is the wait before the first retry, in seconds;
    each subsequent wait is multiplied by ``backoff`` and capped at
    ``max_delay``. ``jitter`` adds a random amount in ``[0, jitter)`` to
    each delay to spread out retries from concurrent callers.
    """

    attempts: int = 3
    initial_delay: float = 1.0
    backoff: float = 2.0
    max_delay: float = 30.0
    jitter: float = 0.0

    def __post_init__(self) -> None:
        """Validate and normalize initialized values."""
        if self.attempts < 1:
            raise ValueError("attempts must be at least 1")
        if self.initial_delay < 0:
            raise ValueError("initial_delay must be non-negative")
        if self.backoff < 1:
            raise ValueError("backoff must be at least 1")
        if self.max_delay < 0:
            raise ValueError("max_delay must be non-negative")
        if self.jitter < 0:
            raise ValueError("jitter must be non-negative")

    def delay_for_attempt(self, attempt_index: int) -> float:
        """Return seconds to wait before the retry after ``attempt_index``.

        ``attempt_index`` is zero-based, so the delay grows geometrically as
        ``initial_delay * backoff**attempt_index`` and is clamped to
        ``max_delay``. When ``jitter`` is non-zero, a uniform random amount in
        ``[0, jitter)`` is added on top to desynchronize concurrent retriers.
        """
        base_delay = min(
            self.initial_delay * (self.backoff**attempt_index),
            self.max_delay,
        )
        # Skip the random draw entirely when jitter is disabled so delays stay
        # deterministic and easy to assert in tests.
        if self.jitter == 0:
            return base_delay
        return base_delay + random.uniform(0, self.jitter)


def retry_call[T](
    func: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    retry_on: tuple[type[BaseException], ...] = DEFAULT_RETRY_EXCEPTIONS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``func`` repeatedly until it succeeds or attempts run out.

    The call should be small and atomic because it may run multiple times;
    avoid wrapping operations with partial side effects that are unsafe to
    repeat. Only exceptions whose type is in ``retry_on`` trigger a retry;
    any other exception propagates immediately. ``sleep`` is injectable so
    tests can avoid real waits.

    Returns the first successful result. Raises ``RetryError`` (chained from
    the final exception) once every attempt has failed.
    """

    retry_policy = policy or RetryPolicy()
    last_exception: BaseException | None = None
    for attempt_index in range(retry_policy.attempts):
        try:
            return func()
        except retry_on as exc:
            last_exception = exc
            # Do not sleep after the final attempt; there is nothing left to
            # wait for, so break out and surface the failure instead.
            if attempt_index == retry_policy.attempts - 1:
                break
            sleep(retry_policy.delay_for_attempt(attempt_index))

    # The loop only reaches here after at least one failed attempt, so
    # last_exception is always set; the assert documents that invariant.
    assert last_exception is not None
    raise RetryError("retry attempts exhausted", last_exception) from last_exception


def retry[T](
    *,
    policy: RetryPolicy | None = None,
    retry_on: tuple[type[BaseException], ...] = DEFAULT_RETRY_EXCEPTIONS,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Return a decorator that retries the wrapped function.

    This is the decorator form of :func:`retry_call`; the same ``policy``,
    ``retry_on``, and ``sleep`` semantics apply. The decorated function should
    be safe to invoke more than once because it may be retried.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        """Wrap ``func`` so each call routes through ``retry_call``."""

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            """Run the wrapped callable with retry behavior."""
            return retry_call(
                lambda: func(*args, **kwargs),
                policy=policy,
                retry_on=retry_on,
                sleep=sleep,
            )

        return wrapper

    return decorator
