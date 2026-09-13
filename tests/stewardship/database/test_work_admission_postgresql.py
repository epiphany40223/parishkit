"""Fresh domain gates and common lifecycle/task lock order on real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from django.db import connection, connections, transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.controls import change_control, reserve_work_gate
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.campaigns.lifecycle import Action, CampaignWorkKind
from parishkit.stewardship.campaigns.rehearsals import (
    invalidate_rehearsal,
    release_rehearsal_gate,
)
from parishkit.stewardship.campaigns.runtime import return_to_testing
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.admission import (
    require_campaign_work,
    require_source_refresh,
)
from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue, claim_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import (
    admit_test_work,
    campaign_clock,
    close_campaign,
    command,
    complete_empty_catchup,
    draft_campaign,
    initialized,
    restored_runtime,
)
from .credential_builders import family_campaign
from .test_rehearsals_postgresql import prepare

pytestmark = pytest.mark.django_db(transaction=True)


def ordinary(campaign, *, kind=CampaignWorkKind.READINESS_TEST, **kwargs):
    """A synthetic owning request supplies its fixed Testing routing binding."""
    return require_campaign_work(
        campaign_id=campaign.pk, kind=kind, mode=SystemMode.TESTING, **kwargs
    )


def test_admission_requires_outer_transaction_and_applied_configuration():
    """A detached ORM snapshot cannot be returned as a reusable authorization."""
    with pytest.raises(StorageInvariantError):
        require_source_refresh(campaign_id=None)
    with pytest.raises(PermissionError), work_transaction():
        require_source_refresh(campaign_id=None)


def test_source_refresh_binds_no_campaign_then_the_current_draft(tmp_path):
    """Creating a draft invalidates a request for the earlier no-giving window."""
    store, root, actor = initialized(tmp_path)
    with work_transaction():
        assert require_source_refresh(campaign_id=None).campaign is None
    from .campaign_builders import add_draft

    _, row, _ = add_draft(store, root, actor)
    from uuid import UUID

    with pytest.raises(PermissionError), work_transaction():
        require_source_refresh(campaign_id=None)
    with work_transaction():
        assert require_source_refresh(campaign_id=UUID(row["id"])).campaign is not None


def test_restore_holds_both_campaign_and_source_work(tmp_path):
    """An ordinary queue or known campaign UUID never grants maintenance authority."""
    _, campaign, _, _ = family_campaign(tmp_path)
    with restored_runtime(campaign.active_configuration.starts_at):
        for operation in (
            lambda: ordinary(campaign),
            lambda: require_source_refresh(campaign_id=campaign.pk),
        ):
            with pytest.raises(PermissionError), work_transaction():
                operation()


def test_go_live_blocks_ordinary_work_but_not_source_refresh(tmp_path):
    """Source is the documented cleanup exception, not a generic caller flag."""
    _, campaign, _, _ = family_campaign(tmp_path)
    with work_transaction():
        ordinary(campaign)
    invalidate_rehearsal(campaign_id=campaign.pk, admit=lambda *args: True)
    with pytest.raises(PermissionError), work_transaction():
        ordinary(campaign)
    with work_transaction():
        assert (
            require_source_refresh(campaign_id=campaign.pk).campaign.pk == campaign.pk
        )


def test_old_testing_scope_cannot_run_after_production_activation(tmp_path):
    """Fresh mode comparison forbids rebinding a delayed Testing task in place."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    with pytest.raises(PermissionError), work_transaction():
        ordinary(campaign)


def test_old_epoch_cannot_resume_after_cleanup_and_new_rehearsal(tmp_path):
    """The original durable epoch matters even after the go-live gate is released."""
    _, campaign, _, keys = family_campaign(tmp_path)
    prepare(campaign, keys)
    epoch = CampaignCredentialState.objects.get(campaign=campaign).rehearsal_epoch_id
    with campaign_clock(campaign.active_configuration.starts_at), work_transaction():
        ordinary(campaign, kind=CampaignWorkKind.REHEARSAL, rehearsal_epoch_id=epoch)
    invalidate_rehearsal(campaign_id=campaign.pk, admit=lambda *args: True)
    release_rehearsal_gate(campaign_id=campaign.pk, admit=lambda *args: True)
    prepare(campaign, keys)
    with (
        campaign_clock(campaign.active_configuration.starts_at),
        pytest.raises(PermissionError),
        work_transaction(),
    ):
        ordinary(campaign, kind=CampaignWorkKind.REHEARSAL, rehearsal_epoch_id=epoch)


def test_pause_holds_live_delivery_not_preparation_or_source(tmp_path):
    """Reuse the existing lifecycle decision instead of a parallel pause policy."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        complete_empty_catchup(campaign, actor)
        campaign.refresh_from_db()
        change_control(
            campaign_id=campaign.pk,
            request_id=uuid4(),
            action="pause",
            expected_version=campaign.version,
            expected_runtime_version=SystemConfiguration.objects.get().version,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
            reason="synthetic pause",
        )
        with pytest.raises(PermissionError), work_transaction():
            require_campaign_work(
                campaign_id=campaign.pk,
                kind=CampaignWorkKind.LIVE_DELIVERY,
                mode=SystemMode.PRODUCTION,
            )
        with work_transaction():
            require_campaign_work(
                campaign_id=campaign.pk,
                kind=CampaignWorkKind.LIVE_PREPARATION,
                mode=SystemMode.PRODUCTION,
            )
            require_source_refresh(campaign_id=campaign.pk)


def test_purge_window_holds_global_source_refresh(tmp_path):
    """Even no-campaign refresh waits for the exclusive purge window to close."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    close_campaign(campaign, actor)
    command(campaign, actor, Action.ARCHIVE)
    with work_transaction():
        # The archived current pointer excludes its giving window, but does not
        # stop parish-wide census refresh while waiting for Return to Testing.
        scope = require_source_refresh(campaign_id=campaign.pk)
        from parishkit.stewardship.source.windows import refresh_window

        assert (
            refresh_window(
                campaign_id=scope.campaign.pk,
                state=scope.campaign.state,
                values=scope.campaign.active_configuration.values,
            ).periods
            == ()
        )
    return_to_testing(
        campaign_id=campaign.pk,
        request_id=uuid4(),
        expected_runtime_version=SystemConfiguration.objects.get().version,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    with work_transaction():
        require_source_refresh(campaign_id=None)
    reserve_work_gate(
        campaign_id=campaign.pk,
        request_id=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    with pytest.raises(PermissionError), work_transaction():
        require_source_refresh(campaign_id=None)


def test_claim_and_cleanup_serialize_then_next_effect_rechecks_gate(tmp_path):
    """Competing lifecycle/TaskRun owners never take their locks in reverse order."""
    _, campaign, _, _ = family_campaign(tmp_path)
    entered, release, started = Event(), Event(), Event()

    def admit(action, status):
        """Use the exact synthetic parent binding at enqueue, claim and progress."""
        assert status.domain_request_id == campaign.pk
        ordinary(campaign)
        if action == "claim":
            entered.set()
            assert release.wait(5)
        return True

    with work_transaction():
        task = enqueue(
            task_type="admission_probe",
            domain_request_id=campaign.pk,
            actor_id=None,
            correlation_id=uuid4(),
            admit=admit,
        )

    def claim():
        """Use another backend, retaining the claim for the post-race assertion."""
        try:
            return claim_hint(
                task.run_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={
                    "admission_probe": Handler(
                        WorkQueue.GENERAL, admit, lambda _: None, scope=work_transaction
                    )
                },
            )
        finally:
            connections.close_all()

    def cleanup():
        """The real gate writer joins the same order before its credential row."""
        started.set()
        try:
            invalidate_rehearsal(campaign_id=campaign.pk, admit=lambda *args: True)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claiming = pool.submit(claim)
        try:
            assert entered.wait(5)
            cleaning = pool.submit(cleanup)
            assert started.wait(5)
            assert not cleaning.done()
        finally:
            release.set()
        context = claiming.result(timeout=5)
        cleaning.result(timeout=5)
    with pytest.raises(PermissionError):
        context.progress(1, 1)
    assert TaskRun.objects.get(pk=task.run_id).state == "running"


def test_task_claim_takes_lifecycle_order_before_calling_its_domain(tmp_path):
    """The shared lock covers the callback and its full durable mutation."""
    draft_campaign(tmp_path)

    def admit(action, status):
        """Check actual server ownership, not a process-local held-lock flag."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND locktype='advisory' AND classid=736220 AND objid=1 AND granted)"
            )
            assert cursor.fetchone() == (True,)
        return True

    with work_transaction():
        enqueue(
            task_type="admission_probe",
            domain_request_id=uuid4(),
            actor_id=None,
            correlation_id=uuid4(),
            admit=admit,
        )


def test_admission_rejects_missing_lock_order_instead_of_late_acquisition(tmp_path):
    """A callback cannot quietly acquire Campaign locks after an unscoped TaskRun."""
    _, campaign, _ = draft_campaign(tmp_path)
    with pytest.raises(StorageInvariantError), transaction.atomic():
        require_source_refresh(campaign_id=campaign.pk)


def test_scoped_heartbeat_and_domain_effect_share_the_same_fresh_gate(tmp_path):
    """Worker renewal and domain writes cannot bypass a post-claim gate change."""
    from parishkit.stewardship.jobs.lifetime import renew_once

    _, campaign, _, _ = family_campaign(tmp_path)

    def admit(action, status):
        """The synthetic parent has only the one immutable campaign binding."""
        assert status.domain_request_id == campaign.pk
        ordinary(campaign)
        return True

    handler = Handler(
        WorkQueue.GENERAL, admit, lambda *args: None, scope=work_transaction
    )
    with work_transaction():
        task = enqueue(
            task_type="admission_probe",
            domain_request_id=campaign.pk,
            actor_id=None,
            correlation_id=uuid4(),
            admit=admit,
        )
    context = claim_hint(
        task.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={"admission_probe": handler},
    )
    renew_once(context)
    with context.effect():
        assert connection.in_atomic_block
        ordinary(campaign)
    assert not connection.in_atomic_block
    invalidate_rehearsal(campaign_id=campaign.pk, admit=lambda *args: True)
    with pytest.raises(PermissionError):
        renew_once(context)
    with pytest.raises(PermissionError), context.effect():
        pytest.fail("The old scope admitted a new effect")
