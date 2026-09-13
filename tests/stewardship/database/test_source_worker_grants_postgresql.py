"""Real compiled source effects must work without schema-owner SQL privileges."""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.accounts.chair_models import ChairReconciliation
from parishkit.stewardship.accounts.configuration_installation import (
    coherent_configuration,
)
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.runtime_background import bind_authority, matching_authority
from parishkit.stewardship.source.effects import refresh_reconciler
from parishkit.stewardship.source.production import SourceProducer
from parishkit.stewardship.source.refresh_models import SourceRefreshFallback
from parishkit.stewardship.source.requests import TASK_TYPE
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from .campaign_builders import add_draft
from .credential_builders import keys
from .test_background_grants_postgresql import task_login
from .test_source_attempts_postgresql import configured
from .test_source_execution_postgresql import handler, run
from .test_source_families_postgresql import source_singletons  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider, pages
from .test_source_requests_postgresql import command

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("with_campaign", [False, True])
def test_restricted_worker_observes_promotes_and_reconciles(
    tmp_path, monkeypatch, with_campaign
):
    """Reconnections and renewal use restricted SQL, not an accidental owner login."""
    credential, store, version, actor = configured(tmp_path)
    if with_campaign:
        add_draft(store, version, actor)
    ring = keys()
    compiled = handler(
        tmp_path,
        credential,
        reconcile=refresh_reconciler(
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
            suppressions=lambda scope: frozenset(),
        ),
    )
    request = command()
    remaining, calls = fake_provider(monkeypatch, pages())
    with task_login(ServiceRole.WORKER, reconnect=True):
        coherent_configuration(store)
        bound = bind_authority({TASK_TYPE: compiled}, store)[TASK_TYPE]
        assert run(request, bound)
        assert not run(request, bound)
    assert not remaining and calls
    assert SourceCurrent.objects.get().snapshot_id is not None
    assert ChairReconciliation.objects.count() == 1
    assert FamilyCampaign.objects.exists() == with_campaign
    assert TaskRun.objects.get(pk=request.task_root_id).state == "succeeded"


def test_restricted_scheduler_produces_and_cancels_only_waiting_source_work(tmp_path):
    """A stale scope is retired before current ticks without acquiring source work."""
    _, store, version, actor = configured(tmp_path)
    old = command()
    add_draft(store, version, actor)
    producer = SourceProducer(uuid4())
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        coherent_configuration(store)
        matching_authority(store)
        receipts = producer(guard)
        assert producer(guard) == receipts
    assert len(receipts) == 2
    assert TaskRun.objects.get(pk=old.task_root_id).state == "cancelled"


@pytest.mark.parametrize("action", ["heartbeat", "safe_cancel"])
def test_scheduler_cannot_impersonate_running_worker(tmp_path, action):
    """Even exact known worker IDs do not authorize direct live-claim mutation."""
    from .test_source_attempts_postgresql import setup

    _, execution, _, *_ = setup(tmp_path)
    with (
        task_login(ServiceRole.SCHEDULER),
        scheduler_session(),
        pytest.raises(DatabaseError),
        work_transaction(),
        connection.cursor() as cur,
    ):
        cur.execute(
            "UPDATE stewardship_task_run SET state=%s, action=%s, "
            "actor_id=worker_id, version=version+1, "
            "lease_expires_at=CASE WHEN %s='heartbeat' "
            "THEN statement_timestamp()+interval '120 seconds' ELSE NULL END "
            "WHERE id=%s",
            [
                "running" if action == "heartbeat" else "cancelled",
                action,
                action,
                execution.claim.run_id,
            ],
        )
    assert TaskRun.objects.get(pk=execution.claim.run_id).state == "running"


@pytest.mark.parametrize("outcome", ["invalid", "no_base"])
def test_restricted_worker_failure_and_fallback_remain_durable(
    tmp_path, monkeypatch, outcome
):
    """Failure audit and fallback creation must not need schema-owner privileges."""
    credential, *_ = configured(tmp_path)
    ring = keys()
    compiled = handler(
        tmp_path,
        credential,
        reconcile=refresh_reconciler(
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
            suppressions=lambda scope: frozenset(),
        ),
    )
    request = (
        command(cause="delta", actor_id=None) if outcome == "no_base" else command()
    )
    remaining, _ = fake_provider(
        monkeypatch,
        [] if outcome == "no_base" else pages(member_change={"birthdate": "invalid"}),
    )
    with task_login(ServiceRole.WORKER, reconnect=True):
        assert run(request, compiled)
    # Invalid input aborts early; later pages must not be fetched or consumed.
    assert bool(remaining) == (outcome == "invalid")
    assert SourceCurrent.objects.get().snapshot_id is None
    assert TaskRun.objects.get(pk=request.task_root_id).state == (
        "retry_wait" if outcome == "no_base" else "failed"
    )
    assert SourceRefreshFallback.objects.exists() == (outcome == "no_base")


def test_restricted_scheduler_can_scan_before_any_campaign_exists(tmp_path):
    """Timezone access is limited to the one parish column the scheduler needs."""
    configured(tmp_path)
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        assert len(SourceProducer(uuid4())(guard)) == 2


def test_background_liveness_pulse_follows_committed_restricted_renewal(tmp_path):
    """A long import reports liveness only after its real task lease is renewed."""
    from parishkit.stewardship.jobs.lifetime import renew_once

    from .test_source_attempts_postgresql import setup

    _, execution, _, *_ = setup(tmp_path)
    before = TaskRun.objects.get(pk=execution.claim.run_id).version
    calls = []

    def pulse():
        """The local heartbeat is outside the committed database transaction."""
        assert not connection.in_atomic_block
        assert TaskRun.objects.get(pk=execution.claim.run_id).version == before + 1
        calls.append(True)

    execution = replace(execution, handler=replace(execution.handler, pulse=pulse))
    with task_login(ServiceRole.WORKER):
        renew_once(execution)
    assert calls == [True]
