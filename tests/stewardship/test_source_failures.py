"""Read failure classification never needs private exception messages or bodies."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.parishsoft import ParishSoftAPIError
from parishkit.parishsoft_changes import ChangeFeedIncomplete
from parishkit.parishsoft_pagination import (
    IncompleteSourceCollection,
    ShiftedSourceScan,
    SourceLoadBudgetExceeded,
)
from parishkit.parishsoft_source import SourceOrganizationMismatch
from parishkit.parishsoft_transport import (
    InvalidSourceResponse,
    SourceTransportDrainFailure,
    SourceTransportError,
)
from parishkit.retry import RetryError, TransientRetryError
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.audit.schemas import FAILURES, Outcome
from parishkit.stewardship.jobs.lifetime import ExecutionInterrupted
from parishkit.stewardship.observability import Event
from parishkit.stewardship.source import outcomes
from parishkit.stewardship.source.canonical import (
    InvalidSourcePayload,
    SourceReferenceSkew,
)
from parishkit.stewardship.source.errors import (
    SourceCredentialChanged,
    SourceScopeChanged,
    local_read_admission,
)
from parishkit.stewardship.source.failures import classify_read_failure, failure_context
from parishkit.stewardship.source.leases import SourceFenceLost, SourceLeaseUnavailable
from parishkit.stewardship.source.loading import CountCheck, DestructiveSourceChange
from parishkit.stewardship.source.outcomes import (
    MAX_AUTOMATIC_ATTEMPTS,
    counted_attempt,
    failure_action,
    retry_delay,
)
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


def test_destructive_change_carries_only_its_closed_loss_detail():
    """#320: the operator sees which count fell and by how much, never values."""
    decision = classify_read_failure(
        RetryError(
            "PRIVATE",
            DestructiveSourceChange(
                "PRIVATE", measure="portal_eligible_families", before=1238, after=12
            ),
        ),
        has_source_claim=True,
    )
    assert decision.loss == ("portal_eligible_families", 1238, 12)
    unnamed = DestructiveSourceChange("PRIVATE", measure="PRIVATE", before=1, after=0)
    assert classify_read_failure(unnamed, has_source_claim=True).loss is None


def test_destructive_change_carries_every_checked_count():
    """ADM-13: the settlement receives every count to record with the refusal."""
    checks = (
        CountCheck("family", 100, 50, 25, True),
        CountCheck("member", 200, 190, 25, False),
    )
    error = DestructiveSourceChange(
        "PRIVATE", measure="family", before=100, after=50, checks=checks
    )
    decision = classify_read_failure(error, has_source_claim=True)
    assert decision.checks == checks
    other = classify_read_failure(SourceScopeChanged("PRIVATE"), has_source_claim=True)
    assert other.checks == ()


def test_shifted_scan_is_a_retryable_provider_failure():
    """A scan that moved mid-read is retried, not reported as invalid data.

    So are a dangling cross-collection reference (a record added between two
    collections' reads) and a load that ran out of time (#387).
    """
    for error in (
        ShiftedSourceScan("PRIVATE"),
        RetryError("PRIVATE", ShiftedSourceScan("PRIVATE")),
        SourceReferenceSkew("PRIVATE"),
        SourceLoadBudgetExceeded("PRIVATE"),
        RetryError("PRIVATE", SourceLoadBudgetExceeded("PRIVATE")),
    ):
        decision = classify_read_failure(error, has_source_claim=True)
        assert decision.retry and not decision.contention
        assert decision.event is Event.SOURCE_PROVIDER_FAILED
        assert "PRIVATE" not in repr(decision)
    # Other incomplete collections remain permanent invalid-data failures.
    decision = classify_read_failure(
        IncompleteSourceCollection("PRIVATE"), has_source_claim=True
    )
    assert not decision.retry and decision.event is Event.SOURCE_INVALID


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


class _Attempts:
    """A stand-in for this run's SourceRefreshAttempt rows, by task fence."""

    def __init__(self, fences, **lookup):
        self.fences = [
            fence
            for fence in fences
            if fence <= lookup.get("task_fence__lte", fence)
            and fence == lookup.get("task_fence", fence)
        ]

    def filter(self, **lookup):
        return _Attempts(self.fences, **lookup)

    def exists(self):
        return bool(self.fences)

    def count(self):
        return len(self.fences)


@pytest.mark.parametrize(
    "fences,claim_fence,expected",
    [
        # Claims 1-4 were held before any read; claim 5 read and failed.
        ((5,), 5, 1),
        # Claims 1 and 3 read; claim 2 was held; claim 3 is counted second.
        ((1, 3), 3, 2),
        # A later claim's row is never counted for an earlier claim.
        ((1, 3, 4), 3, 2),
        # The settled claim never read (no row): the raw claim count stays.
        ((1, 2), 5, 5),
        ((), 5, 5),
    ],
)
def test_counted_attempt_counts_claims_that_reached_parishsoft(
    monkeypatch, fences, claim_fence, expected
):
    """Earlier holds are skipped only when the settled claim began a read (#386)."""
    rows = SimpleNamespace(objects=_Attempts(fences))
    monkeypatch.setattr(outcomes, "SourceRefreshAttempt", rows)
    status = SimpleNamespace(run_id=uuid4(), attempt=5)
    assert counted_attempt(status, claim_fence) == expected


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


@pytest.mark.parametrize(
    "error,failure,status",
    [
        (SourceOrganizationMismatch("PRIVATE"), "organization_mismatch", None),
        (ShiftedSourceScan("PRIVATE"), "shifted_scan", None),
        (SourceReferenceSkew("PRIVATE"), "shifted_scan", None),
        (SourceLoadBudgetExceeded("PRIVATE"), "provider_timeout", None),
        (InvalidSourcePayload("PRIVATE"), "invalid_payload", None),
        (InvalidSourceResponse("PRIVATE"), "invalid_response", None),
        (IncompleteSourceCollection("PRIVATE"), "incomplete_collection", None),
        (SourceLeaseUnavailable("PRIVATE"), "lease_unavailable", None),
        (SourceScopeChanged("PRIVATE"), "scope_changed", None),
        (CryptographicError("PRIVATE"), "credential_unreadable", None),
        (SourceCredentialChanged("PRIVATE"), "credential_changed", None),
        (ParishSoftAPIError(503, "PRIVATE", "PRIVATE"), "provider_status", 503),
        (TimeoutError("PRIVATE"), "provider_timeout", None),
        (SourceTransportError("PRIVATE"), "provider_unreachable", None),
        (ConnectionError("PRIVATE"), "provider_unreachable", None),
    ],
)
def test_each_classification_names_what_failed(error, failure, status):
    """The durable entry says which call failed and why (#633), as closed words."""
    decision = classify_read_failure(
        RetryError("PRIVATE", error), has_source_claim=True
    )
    assert decision.failure == failure and decision.status == status
    assert failure in FAILURES


def test_failure_context_says_what_happens_next():
    """A retry names its wait and attempt; a give-up names the last attempt."""
    result = SimpleNamespace(run_id=uuid4(), version=4)
    decision = classify_read_failure(
        ParishSoftAPIError(502, "PRIVATE", "PRIVATE"), has_source_claim=True
    )
    retry = failure_context(decision, result, attempt=2, action="retryable_failure")
    assert retry == {
        "failure": "provider_status",
        "task_id": result.run_id,
        "version": 4,
        "attempt": 2,
        "outcome": Outcome.RETRY,
        "retry_seconds": retry_delay(2),
        "attempt_limit": MAX_AUTOMATIC_ATTEMPTS,
        "status": 502,
    }
    final = failure_context(decision, result, attempt=5, action="permanent_failure")
    assert final["outcome"] is Outcome.FAILED and "retry_seconds" not in final
    held = classify_read_failure(
        SourceLeaseUnavailable("PRIVATE"), has_source_claim=True
    )
    waiting = failure_context(held, result, attempt=9, action="retryable_failure")
    # Contention waits without using the provider-failure allowance.
    assert "attempt_limit" not in waiting and waiting["retry_seconds"] == retry_delay(9)
