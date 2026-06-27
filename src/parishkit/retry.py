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
    attempts: int = 3
    initial_delay: float = 1.0
    backoff: float = 2.0
    max_delay: float = 30.0
    jitter: float = 0.0

    def __post_init__(self) -> None:
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
        base_delay = min(
            self.initial_delay * (self.backoff**attempt_index),
            self.max_delay,
        )
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
    """Run a small atomic operation with retry behavior."""

    retry_policy = policy or RetryPolicy()
    last_exception: BaseException | None = None
    for attempt_index in range(retry_policy.attempts):
        try:
            return func()
        except retry_on as exc:
            last_exception = exc
            if attempt_index == retry_policy.attempts - 1:
                break
            sleep(retry_policy.delay_for_attempt(attempt_index))

    assert last_exception is not None
    raise RetryError("retry attempts exhausted", last_exception) from last_exception


def retry[T](
    *,
    policy: RetryPolicy | None = None,
    retry_on: tuple[type[BaseException], ...] = DEFAULT_RETRY_EXCEPTIONS,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorate a small atomic operation with retry behavior."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return retry_call(
                lambda: func(*args, **kwargs),
                policy=policy,
                retry_on=retry_on,
                sleep=sleep,
            )

        return wrapper

    return decorator
