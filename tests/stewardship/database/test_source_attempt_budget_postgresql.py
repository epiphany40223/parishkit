"""Only claims that reached ParishSoft use the provider-failure allowance (#386, L1).

Every claim increments the run's raw ``attempt``, including claims held for
the source lease before any read. These tests drive real claims, holds,
provider attempts and lease expiry through PostgreSQL and check that holds
no longer use up the allowance, that real attempts still do, and that a
used-up allowance still fails safely, live and in recovery.
"""

import pytest

from parishkit.parishsoft import ParishSoftAPIError
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source import failures, outcomes
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.errors import SourceCredentialChanged
from parishkit.stewardship.source.failures import settle_failed_read
from parishkit.stewardship.source.leases import (
    SourceLeaseUnavailable,
    acquire_source,
    release_source,
)
from parishkit.stewardship.source.models import (
    SourceCurrent,
    SourceMutationLease,
    SourceSnapshot,
)
from parishkit.stewardship.source.outcomes import counted_attempt, recovery_plan

from .test_source_attempts_postgresql import configured
from .test_source_leases_postgresql import delay
from .test_source_outcomes_postgresql import abandon, status
from .test_source_requests_postgresql import claim, command

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Recreate only idle migration seeds after the disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


@pytest.fixture
def budget(monkeypatch):
    """An allowance of two, with one-second live retries so claims stay quick.

    Both are module globals read at call time (``failures`` logs its own
    copy of the limit); recovery's own delay is left alone so its plan shows
    the real counted back-off.
    """
    monkeypatch.setattr(outcomes, "MAX_AUTOMATIC_ATTEMPTS", 2)
    monkeypatch.setattr(failures, "MAX_AUTOMATIC_ATTEMPTS", 2)
    monkeypatch.setattr(failures, "retry_delay", lambda attempt: 1)


def first_claim(tmp_path):
    """Configure a real full request and claim it for the first time."""
    credential, *_ = configured(tmp_path)
    receipt = command()
    return credential, receipt, claim(receipt)


def reclaim(receipt):
    """Claim the same run again once its one-second retry is due."""
    delay(1.05)
    execution = claim(receipt)
    assert execution is not None
    return execution


def hold(execution):
    """Settle a claim held for the source lease before any provider read."""
    result = settle_failed_read(execution, SourceLeaseUnavailable("PRIVATE"))
    assert result.state == "retry_wait"
    return result


def acquire(execution):
    """Take source ownership for this claim, as the executor does."""
    with execution.effect():
        return acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase="full",
        )


def provider_failure(execution, credential):
    """Begin a real provider attempt, then settle a retryable 503 for it."""
    lease = acquire(execution)
    attempt = begin_refresh_attempt(execution, lease, credential)
    result = settle_failed_read(
        execution, ParishSoftAPIError(503, "PRIVATE", "PRIVATE"), source_claim=lease
    )
    assert SourceSnapshot.objects.get(pk=attempt.snapshot_id).state == "rejected"
    return result


def provider_logs():
    """The provider-failure entries in order, as (level, attempt, outcome)."""
    return [
        (log.level, log.context["attempt"], log.context["outcome"])
        for log in OperationalLog.objects.filter(
            event="source_provider_failed"
        ).order_by("created_at", "id")
    ]


def test_holds_before_a_provider_failure_do_not_use_the_allowance(tmp_path, budget):
    """Two lease holds, then a provider failure on raw attempt 3 of an
    allowance of 2: it is the first counted attempt, so it retries."""
    credential, receipt, execution = first_claim(tmp_path)
    hold(execution)
    hold(execution := reclaim(receipt))
    execution = reclaim(receipt)
    assert status(execution).attempt == 3
    result = provider_failure(execution, credential)
    assert result.state == "retry_wait"
    log = OperationalLog.objects.get(event="source_provider_failed")
    assert log.context["attempt"] == 1 and log.context["attempt_limit"] == 2
    assert log.level == "WARNING"
    # The holds themselves keep the raw claim count and no allowance.
    held = OperationalLog.objects.filter(event="source_refresh_held").order_by(
        "created_at", "id"
    )
    assert [entry.context["attempt"] for entry in held] == [1, 2]
    assert all("attempt_limit" not in entry.context for entry in held)


def test_real_provider_attempts_still_use_up_the_allowance(tmp_path, budget):
    """A hold between two provider failures is skipped, but the second
    provider failure is counted attempt 2 of 2 and fails safely: the read is
    rejected, the source lease released and the failure logged CRITICAL."""
    credential, receipt, execution = first_claim(tmp_path)
    assert provider_failure(execution, credential).state == "retry_wait"
    hold(execution := reclaim(receipt))
    execution = reclaim(receipt)
    result = provider_failure(execution, credential)
    assert result.state == "failed" and execution.control.finished.is_set()
    assert SourceMutationLease.objects.get().owner_id is None
    assert provider_logs() == [("WARNING", 1, "retry"), ("CRITICAL", 2, "failed")]


def test_a_failure_before_any_read_keeps_counting_every_claim(tmp_path, budget):
    """A retryable failure on a claim that never reached ParishSoft (a stale
    credential) has no attempt row, so it keeps the raw claim count; a
    failure that recurs before any read therefore still ends."""
    _, receipt, execution = first_claim(tmp_path)
    hold(execution)
    execution = reclaim(receipt)
    lease = acquire(execution)
    result = settle_failed_read(
        execution, SourceCredentialChanged("PRIVATE"), source_claim=lease
    )
    assert result.state == "failed"
    assert not SourceSnapshot.objects.exists()
    log = OperationalLog.objects.get(event="source_credential_failed")
    assert log.level == "CRITICAL" and log.context["attempt"] == 2


def interrupted_read(execution, credential):
    """Begin a provider attempt, drain its source lease, then lose the worker."""
    lease = acquire(execution)
    begin_refresh_attempt(execution, lease, credential)
    release_source(lease)
    return abandon(execution)


def test_recovery_counts_only_attempts_that_reached_parishsoft(tmp_path, budget):
    """A worker lost mid-read after a hold is recovered as counted attempt 1
    (raw attempt 2 of an allowance of 2), with the first back-off."""
    credential, receipt, execution = first_claim(tmp_path)
    hold(execution)
    abandoned = interrupted_read(reclaim(receipt), credential)
    assert abandoned.attempt == 2
    with work_transaction():
        assert counted_attempt(abandoned, abandoned.fence - 1) == 1
        plan = recovery_plan(abandoned)
    assert plan.action == "recovery_retry"
    assert plan.retry_seconds == outcomes.retry_delay(1)


def test_recovery_still_fails_once_reached_attempts_are_used_up(tmp_path, budget):
    """Two claims that reached ParishSoft use up an allowance of two, so
    recovery fails the run rather than retrying it."""
    credential, receipt, execution = first_claim(tmp_path)
    assert provider_failure(execution, credential).state == "retry_wait"
    abandoned = interrupted_read(reclaim(receipt), credential)
    with work_transaction():
        assert recovery_plan(abandoned).action == "recovery_fail"


def test_recovery_of_a_claim_that_never_read_keeps_the_raw_count(tmp_path, budget):
    """A worker lost before its claim began a read leaves no attempt row, so
    recovery keeps counting every claim, including the earlier hold."""
    _, receipt, execution = first_claim(tmp_path)
    hold(execution)
    abandoned = abandon(reclaim(receipt))
    with work_transaction():
        assert counted_attempt(abandoned, abandoned.fence - 1) == 2
        assert recovery_plan(abandoned).action == "recovery_fail"
