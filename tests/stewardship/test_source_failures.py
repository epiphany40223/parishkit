"""Read failure classification never needs private exception messages or bodies."""

import pytest

from parishkit.config import ConfigError
from parishkit.parishsoft import ParishSoftAPIError
from parishkit.parishsoft_changes import ChangeFeedIncomplete
from parishkit.parishsoft_pagination import IncompleteSourceCollection
from parishkit.parishsoft_source import SourceOrganizationMismatch
from parishkit.parishsoft_transport import (
    InvalidSourceResponse,
    SourceTransportDrainFailure,
    SourceTransportError,
)
from parishkit.retry import RetryError, TransientRetryError
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.jobs.lifetime import ExecutionInterrupted
from parishkit.stewardship.observability import Event
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.errors import (
    SourceCredentialChanged,
    SourceScopeChanged,
    local_read_admission,
)
from parishkit.stewardship.source.failures import classify_read_failure
from parishkit.stewardship.source.leases import SourceFenceLost, SourceLeaseUnavailable
from parishkit.stewardship.source.loading import DestructiveSourceChange
from parishkit.stewardship.source.outcomes import failure_action, retry_delay
from parishkit.stewardship.storage import StorageInvariantError


@pytest.mark.parametrize(
    "kind,event",
    [
        (SourceOrganizationMismatch, Event.SOURCE_TENANT_MISMATCH),
        (DestructiveSourceChange, Event.SOURCE_DESTRUCTIVE_CHANGE),
    ],
)
def test_source_safety_failures_keep_specific_value_free_classification(kind, event):
    """Specific fatal source conditions cannot collapse into generic parser errors."""
    decision = classify_read_failure(
        RetryError("PRIVATE", kind("PRIVATE")), has_source_claim=True
    )
    assert decision.event is event and not decision.retry and not decision.contention
    assert "PRIVATE" not in repr(decision)


@pytest.mark.parametrize(
    "attempt,delay", [(1, 30), (2, 60), (5, 480), (6, 600), (1000000, 600)]
)
def test_source_retry_delay_is_shared_and_bounded(attempt, delay):
    """Even prolonged contention cannot produce an unbounded exponent or delay."""
    assert retry_delay(attempt) == delay
    assert failure_action(attempt, retry=False) == "permanent_failure"
    assert failure_action(attempt, retry=True) == (
        "retryable_failure" if attempt < 5 else "permanent_failure"
    )
    assert failure_action(attempt, retry=True, contention=True) == "retryable_failure"


@pytest.mark.parametrize("attempt", [None, True, 0, -1, 1.5])
def test_source_retry_delay_rejects_invalid_attempts(attempt):
    """Retry owners supply actual positive integer Task attempt numbers."""
    with pytest.raises(ValueError):
        retry_delay(attempt)


def test_source_retry_classification_rejects_truthy_non_booleans():
    """A loose caller flag cannot silently authorize retry after permanent failure."""
    with pytest.raises(TypeError):
        failure_action(1, retry="yes")
    with pytest.raises(TypeError):
        failure_action(1, retry=True, contention=1)


@pytest.mark.parametrize(
    "kind", [ConfigError, ValueError, TypeError, KeyError, OverflowError]
)
def test_local_admission_errors_are_not_provider_data_failures(kind):
    """The outer shared DTO parser cannot consume a local callback's ValueError."""

    @local_read_admission
    def callback():
        """Simulate local configuration parsing or connection cleanup failure."""
        raise kind("synthetic-private")

    with pytest.raises(StorageInvariantError) as raised:
        callback()
    assert "synthetic-private" not in str(raised.value)
    assert classify_read_failure(raised.value, has_source_claim=True) is None


@pytest.mark.parametrize("kind", [SourceScopeChanged, SourceFenceLost, PermissionError])
def test_local_admission_preserves_typed_ownership_errors(kind):
    """No new blanket permission-error retry path is introduced."""
    error = kind("synthetic-private")

    @local_read_admission
    def callback():
        """Deliver the original typed ownership result unchanged."""
        raise error

    with pytest.raises(kind) as raised:
        callback()
    assert raised.value is error


@pytest.mark.parametrize(
    "error,retry,contention",
    [
        (InvalidSourcePayload("PRIVATE"), False, False),
        (InvalidSourceResponse("PRIVATE"), False, False),
        (IncompleteSourceCollection("PRIVATE"), False, False),
        (SourceLeaseUnavailable("PRIVATE"), True, True),
        (SourceScopeChanged("PRIVATE"), True, False),
        (SourceTransportError("PRIVATE"), True, False),
        (RetryError("PRIVATE", SourceTransportError("PRIVATE")), True, False),
        (ParishSoftAPIError(401, "PRIVATE", "PRIVATE"), False, False),
        (ParishSoftAPIError(429, "PRIVATE", "PRIVATE"), True, False),
        (ParishSoftAPIError(503, "PRIVATE", "PRIVATE"), True, False),
    ],
)
def test_known_classification_is_value_free(error, retry, contention):
    """Only type/status policy survives; repr contains none of the private input."""
    decision = classify_read_failure(error, has_source_claim=False)
    assert decision.retry is retry and decision.contention is contention
    assert "PRIVATE" not in repr(decision)
    assert (
        classify_read_failure(
            RetryError("PRIVATE", RetryError("PRIVATE", error)), has_source_claim=False
        )
        == decision
    )


@pytest.mark.parametrize("kind", [TransientRetryError, TimeoutError, ConnectionError])
def test_shared_retry_transport_failures_settle_without_abandonment(kind):
    """All shared retry transport types retain the bounded source retry policy."""
    decision = classify_read_failure(
        RetryError("PRIVATE", kind("PRIVATE")), has_source_claim=True
    )
    assert decision.retry and not decision.contention
    assert "PRIVATE" not in repr(decision)


def test_cyclic_retry_cause_is_not_a_known_failure():
    """A malformed wrapper cannot recurse forever or manufacture settlement."""
    error = RetryError("PRIVATE", ValueError("PRIVATE"))
    error.last_exception = error
    assert classify_read_failure(error, has_source_claim=True) is None


@pytest.mark.parametrize("claimed", [False, True])
@pytest.mark.parametrize("error_type", [CryptographicError, SourceCredentialChanged])
def test_credential_intake_and_later_inventory_rotation_have_distinct_retry_policy(
    claimed,
    error_type,
):
    """Missing intake needs repair; a concurrently changing key inventory can retry."""
    decision = classify_read_failure(error_type("PRIVATE"), has_source_claim=claimed)
    assert decision.retry is claimed


@pytest.mark.parametrize(
    "error",
    [
        SourceTransportDrainFailure("PRIVATE"),
        SourceFenceLost("PRIVATE"),
        ExecutionInterrupted("PRIVATE"),
        ChangeFeedIncomplete("PRIVATE"),
        RuntimeError("PRIVATE"),
        PermissionError("PRIVATE"),
        ValueError("PRIVATE"),
        KeyboardInterrupt(),
    ],
)
def test_unknown_ownership_drain_and_fallback_cases_cannot_use_failure_settlement(
    error,
):
    """These cases retain their distinct recovery or full-fallback owners."""
    assert classify_read_failure(error, has_source_claim=True) is None
    assert (
        classify_read_failure(RetryError("PRIVATE", error), has_source_claim=True)
        is None
    )
