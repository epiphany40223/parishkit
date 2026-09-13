"""Closed deadline settlement retries once without hiding unrelated SQL failures."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from django.db import IntegrityError

from parishkit.stewardship.accounts import delivery_results as results
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("outcome", list(DeliveryOutcome))
@pytest.mark.parametrize("expired", [False, True])
def test_result_uses_closed_deadline_without_retry(monkeypatch, outcome, expired):
    """An already late result is uncertain regardless of the observed reply."""
    monkeypatch.setattr(results, "require_work_order", lambda: None)
    monkeypatch.setattr(results.transaction, "atomic", nullcontext)
    monkeypatch.setattr(results, "database_now", lambda: NOW)
    write = Mock(return_value="saved")
    deadline = NOW + timedelta(seconds=-1 if expired else 1)
    assert results.settle_result(outcome, deadline=deadline, write=write) == "saved"
    write.assert_called_once_with(DeliveryOutcome.UNKNOWN if expired else outcome)


@pytest.mark.parametrize("crossing", [False, True])
@pytest.mark.parametrize("second_failure", [False, True])
def test_sql_failure_gets_only_one_guarded_deadline_retry(
    monkeypatch, crossing, second_failure
):
    """No pre-deadline error is suppressed; the fallback must also pass SQL guards."""
    monkeypatch.setattr(results, "require_work_order", lambda: None)
    monkeypatch.setattr(results.transaction, "atomic", nullcontext)
    clock = iter([NOW, NOW + timedelta(seconds=2) if crossing else NOW])
    monkeypatch.setattr(results, "database_now", lambda: next(clock))
    write = Mock(
        side_effect=[IntegrityError(), IntegrityError() if second_failure else 1]
    )
    if crossing and not second_failure:
        assert (
            results.settle_result(
                DeliveryOutcome.ACCEPTED,
                deadline=NOW + timedelta(seconds=1),
                write=write,
            )
            == 1
        )
    else:
        with pytest.raises(IntegrityError):
            results.settle_result(
                DeliveryOutcome.ACCEPTED,
                deadline=NOW + timedelta(seconds=1),
                write=write,
            )
    assert write.call_count == (2 if crossing else 1)
    if crossing:
        assert write.call_args.args == (DeliveryOutcome.UNKNOWN,)
