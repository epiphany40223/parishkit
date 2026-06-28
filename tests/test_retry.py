import pytest
import requests

from parishkit.retry import RetryError, RetryPolicy, retry, retry_call


def test_retry_call_retries_until_success():
    """Retries on the listed exception and applies exponential backoff delays.

    With initial_delay=2 and backoff=2, two retries should sleep 2 then 4
    seconds before the third attempt succeeds.
    """
    attempts = []
    delays = []

    def flaky():
        """Fail with TimeoutError on the first two calls, then return "ok"."""
        attempts.append(1)
        if len(attempts) < 3:
            raise TimeoutError("temporary")
        return "ok"

    # Capture the requested sleep durations instead of actually sleeping.
    result = retry_call(
        flaky,
        policy=RetryPolicy(attempts=3, initial_delay=2, backoff=2, jitter=0),
        retry_on=(TimeoutError,),
        sleep=delays.append,
    )

    assert result == "ok"
    assert delays == [2, 4]


def test_retry_call_raises_retry_error_after_exhaustion():
    """Exhausting all attempts raises RetryError wrapping the last exception."""
    with pytest.raises(RetryError) as exc_info:
        retry_call(
            lambda: (_ for _ in ()).throw(TimeoutError("temporary")),
            policy=RetryPolicy(attempts=2, initial_delay=0),
            retry_on=(TimeoutError,),
            sleep=lambda _delay: None,
        )

    assert isinstance(exc_info.value.last_exception, TimeoutError)


def test_retry_decorator_preserves_function_result():
    """The @retry decorator returns the wrapped function's result on first success.

    A function that does not raise should be called exactly once and its value
    returned unchanged.
    """
    calls = []

    @retry(policy=RetryPolicy(attempts=2, initial_delay=0), sleep=lambda _delay: None)
    def operation():
        """Record each invocation and return a fixed value without raising."""
        calls.append(1)
        return 42

    assert operation() == 42
    assert calls == [1]


def test_retry_defaults_include_requests_timeout():
    """Without an explicit retry_on, a requests Timeout is retried by default."""
    attempts = []

    def flaky():
        """Raise a requests Timeout on the first call, then return "ok"."""
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.exceptions.Timeout("temporary")
        return "ok"

    assert (
        retry_call(
            flaky,
            policy=RetryPolicy(attempts=2, initial_delay=0),
            sleep=lambda _delay: None,
        )
        == "ok"
    )
