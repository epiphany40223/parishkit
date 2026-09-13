"""Read failure classification never needs private exception messages or bodies."""

import pytest

from parishkit.parishsoft import ParishSoftAPIError
from parishkit.parishsoft_changes import ChangeFeedIncomplete
from parishkit.parishsoft_pagination import IncompleteSourceCollection
from parishkit.parishsoft_transport import (
    SourceTransportDrainFailure,
    SourceTransportError,
)
from parishkit.retry import RetryError
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.jobs.lifetime import ExecutionInterrupted
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.failures import classify_read_failure
from parishkit.stewardship.source.leases import SourceFenceLost, SourceLeaseUnavailable


@pytest.mark.parametrize(
    "error,retry,contention",
    [
        (InvalidSourcePayload("PRIVATE"), False, False),
        (IncompleteSourceCollection("PRIVATE"), False, False),
        (SourceLeaseUnavailable("PRIVATE"), True, True),
        (PermissionError("PRIVATE"), True, False),
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


@pytest.mark.parametrize("claimed", [False, True])
def test_credential_intake_and_later_inventory_rotation_have_distinct_retry_policy(
    claimed,
):
    """Missing intake needs repair; a concurrently changing key inventory can retry."""
    decision = classify_read_failure(
        CryptographicError("PRIVATE"), has_source_claim=claimed
    )
    assert decision.retry is claimed


@pytest.mark.parametrize(
    "error",
    [
        SourceTransportDrainFailure("PRIVATE"),
        SourceFenceLost("PRIVATE"),
        ExecutionInterrupted("PRIVATE"),
        ChangeFeedIncomplete("PRIVATE"),
        RuntimeError("PRIVATE"),
        ValueError("PRIVATE"),
        KeyboardInterrupt(),
    ],
)
def test_unknown_ownership_drain_and_fallback_cases_cannot_use_failure_settlement(
    error,
):
    """These cases retain their distinct recovery or full-fallback owners."""
    assert classify_read_failure(error, has_source_claim=True) is None
