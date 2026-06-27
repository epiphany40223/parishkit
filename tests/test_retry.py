import pytest
import requests

from parishkit.retry import RetryError, RetryPolicy, retry, retry_call


def test_retry_call_retries_until_success():
    attempts = []
    delays = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise TimeoutError("temporary")
        return "ok"

    result = retry_call(
        flaky,
        policy=RetryPolicy(attempts=3, initial_delay=2, backoff=2, jitter=0),
        retry_on=(TimeoutError,),
        sleep=delays.append,
    )

    assert result == "ok"
    assert delays == [2, 4]


def test_retry_call_raises_retry_error_after_exhaustion():
    with pytest.raises(RetryError) as exc_info:
        retry_call(
            lambda: (_ for _ in ()).throw(TimeoutError("temporary")),
            policy=RetryPolicy(attempts=2, initial_delay=0),
            retry_on=(TimeoutError,),
            sleep=lambda _delay: None,
        )

    assert isinstance(exc_info.value.last_exception, TimeoutError)


def test_retry_decorator_preserves_function_result():
    calls = []

    @retry(policy=RetryPolicy(attempts=2, initial_delay=0), sleep=lambda _delay: None)
    def operation():
        calls.append(1)
        return 42

    assert operation() == 42
    assert calls == [1]


def test_retry_defaults_include_requests_timeout():
    attempts = []

    def flaky():
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
