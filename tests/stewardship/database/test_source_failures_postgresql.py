"""Failed read cleanup is atomic and cannot become a new-work gate bypass."""

from dataclasses import replace
from importlib import import_module
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.parishsoft import ParishSoftAPIError
from parishkit.parishsoft_transport import SourceTransportDrainFailure
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.observability import Event
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.failures import settle_failed_read
from parishkit.stewardship.source.leases import (
    SourceLeaseUnavailable,
    reserve_source_request,
)
from parishkit.stewardship.source.models import (
    SourceCurrent,
    SourceMutationLease,
    SourceSnapshot,
)
from parishkit.stewardship.source.snapshots import promote_snapshot
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import add_draft
from .test_source_attempts_postgresql import configured, setup, stage
from .test_source_fallback_postgresql import unseeded
from .test_source_requests_postgresql import claim, command
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Disposable flush recreates only idle source migration seeds."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def test_event_registry_and_database_allowlist_agree():
    """New closed diagnostics must be admitted without accepting arbitrary text."""
    for event in Event:
        operational(event)
    assert OperationalLog.objects.count() == len(Event)
    with pytest.raises(IntegrityError), transaction.atomic():
        OperationalLog.objects.create(
            event="PRIVATE-UNREGISTERED", level="INFO", schema="task", context={}
        )


def test_failure_history_prevents_destructive_migration_downgrade():
    """A failed downgrade keeps both history and the current event constraint."""
    migration = import_module(
        "parishkit.stewardship.audit.migrations.0010_source_failure_events"
    )
    operational(Event.SOURCE_INVALID)
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cur,
    ):
        cur.execute(migration.constraint(migration.PREVIOUS))
    operational(Event.SOURCE_PROVIDER_FAILED)
    assert OperationalLog.objects.count() == 2


@pytest.mark.parametrize("ready", [False, True])
def test_rejection_release_failure_and_private_free_log_commit_together(
    tmp_path, ready
):
    """A definitive invalid load cannot leave candidate truth or active ownership."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, lease, credential)
    if ready:
        stage(attempt, execution, lease)
    with execution.effect():
        deadline = reserve_source_request(lease, timeout_seconds=30, safety_seconds=15)
    result = settle_failed_read(
        execution, InvalidSourcePayload("PRIVATE-CENSUS"), source_claim=lease
    )
    assert result.state == "failed" and execution.control.finished.is_set()
    assert SourceSnapshot.objects.get(pk=attempt.snapshot_id).state == "rejected"
    saved = SourceMutationLease.objects.get()
    assert saved.owner_id is None and saved.external_deadline == deadline
    assert SourceCurrent.objects.get().snapshot_id is None
    log = OperationalLog.objects.get(event="source_refresh_invalid")
    assert log.level == "CRITICAL" and log.context["task_id"] == str(result.run_id)
    assert log.correlation_id == execution.correlation_id
    assert "PRIVATE" not in str(OperationalLog.objects.values().get())


def test_scope_change_can_only_reject_and_release_old_read(tmp_path):
    """Closed new-work admission does not prevent narrowly scoped historical cleanup."""
    credential, execution, lease, store, version, actor = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, lease, credential)
    add_draft(store, version, actor)
    with pytest.raises(PermissionError), execution.effect():
        pytest.fail("Old window must not have new-work admission")
    result = settle_failed_read(
        execution, PermissionError("PRIVATE"), source_claim=lease
    )
    assert result.state == "retry_wait"
    assert SourceSnapshot.objects.get(pk=attempt.snapshot_id).state == "rejected"
    assert SourceMutationLease.objects.get().owner_id is None
    assert SourceCurrent.objects.get().snapshot_id is None


def test_provider_transience_does_not_persist_response_text(tmp_path):
    """The Task retry records a safe category, never endpoint/body diagnostics."""
    credential, execution, lease, *_ = setup(tmp_path)
    begin_refresh_attempt(execution, lease, credential)
    result = settle_failed_read(
        execution, ParishSoftAPIError(503, "PRIVATE", "PRIVATE"), source_claim=lease
    )
    assert result.state == "retry_wait"
    log = OperationalLog.objects.get(event="source_provider_failed")
    assert log.level == "WARNING" and "PRIVATE" not in str(log.context)


def test_missing_preclaim_credential_fails_without_manufacturing_an_attempt(tmp_path):
    """Credential intake failures have no synthetic provider request or manifest."""
    configured(tmp_path)
    execution = claim(command())
    result = settle_failed_read(execution, CryptographicError("PRIVATE-PATH"))
    assert result.state == "failed" and not SourceSnapshot.objects.exists()
    assert (
        OperationalLog.objects.get(event="source_credential_failed").level == "CRITICAL"
    )


def test_claim_contention_waits_without_claiming_failure_of_provider_data(tmp_path):
    """A worker without source ownership may safely wait and retain its Task."""
    configured(tmp_path)
    execution = claim(command())
    result = settle_failed_read(execution, SourceLeaseUnavailable("PRIVATE"))
    assert result.state == "retry_wait" and not SourceSnapshot.objects.exists()
    assert OperationalLog.objects.get(event="source_refresh_held").level == "INFO"


def test_log_failure_rolls_back_rejection_release_and_task_outcome(
    tmp_path, monkeypatch
):
    """Mark control finished only after the whole failure transaction commits."""
    from parishkit.stewardship.source import failures

    credential, execution, lease, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, lease, credential)

    def fail(*args, **kwargs):
        """Inject a durable diagnostic failure after other SQL effects were prepared."""
        raise RuntimeError("Synthetic log unavailable")

    monkeypatch.setattr(failures, "operational", fail)
    with pytest.raises(RuntimeError, match="log unavailable"):
        settle_failed_read(
            execution, InvalidSourcePayload("PRIVATE"), source_claim=lease
        )
    assert SourceSnapshot.objects.get(pk=attempt.snapshot_id).state == "staging"
    assert SourceMutationLease.objects.get().owner_id == execution.claim.run_id
    assert TaskRun.objects.get(pk=execution.claim.run_id).state == "running"
    assert not execution.control.finished.is_set()


def test_completed_observation_cannot_be_marked_failed(tmp_path):
    """A crash after successful promotion belongs to verified completion recovery."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, lease, credential)
    stage(attempt, execution, lease)
    with execution.effect():
        promote_snapshot(attempt.snapshot_id, lease, admit=permit, reconcile=permit)
    with pytest.raises(StorageInvariantError, match="another outcome owner"):
        settle_failed_read(
            execution, InvalidSourcePayload("PRIVATE"), source_claim=lease
        )
    assert SourceSnapshot.objects.get(pk=attempt.snapshot_id).state == "promoted"


def test_fallback_work_has_its_own_outcome_owner(tmp_path):
    """A pending full dependency cannot be replaced by failure classification."""
    from parishkit.stewardship.source.fallback import request_full_fallback

    execution = unseeded(tmp_path)
    request_full_fallback(execution)
    with pytest.raises(StorageInvariantError, match="another outcome owner"):
        settle_failed_read(execution, InvalidSourcePayload("PRIVATE"))
    assert TaskRun.objects.get(pk=execution.claim.run_id).state == "running"


def test_cannot_omit_or_mix_an_owned_source_claim(tmp_path):
    """Failure settlement cannot forget ownership or borrow another worker identity."""
    _, execution, lease, *_ = setup(tmp_path)
    with pytest.raises(StorageInvariantError, match="retain its source claim"):
        settle_failed_read(execution, InvalidSourcePayload("PRIVATE"))
    with pytest.raises(ValueError, match="exact"):
        settle_failed_read(
            execution,
            InvalidSourcePayload("PRIVATE"),
            source_claim=replace(lease, worker_id=uuid4()),
        )


def test_unknown_drain_propagates_without_any_task_outcome(tmp_path):
    """The consumer must still stop when helper drainage could not be confirmed."""
    _, execution, lease, *_ = setup(tmp_path)
    with pytest.raises(SourceTransportDrainFailure):
        settle_failed_read(
            execution, SourceTransportDrainFailure("PRIVATE"), source_claim=lease
        )
    assert TaskRun.objects.get(pk=execution.claim.run_id).state == "running"
    assert SourceMutationLease.objects.get().owner_id == execution.claim.run_id


def test_failure_settlement_must_own_its_commit_boundary(tmp_path):
    """Do not mark the execution finished while an outer transaction could roll back."""
    _, execution, lease, *_ = setup(tmp_path)
    with (
        pytest.raises(StorageInvariantError, match="settlement transaction"),
        transaction.atomic(),
    ):
        settle_failed_read(
            execution, InvalidSourcePayload("PRIVATE"), source_claim=lease
        )
    assert not execution.control.finished.is_set()
