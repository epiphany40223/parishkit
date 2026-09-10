"""Regression evidence for strict owner contracts and held-state SQL defenses."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, connections, transaction
from django.db.models import F

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns import admission
from parishkit.stewardship.campaigns.controls import release_work_gate
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    CampaignBoundaryOccurrence,
    ScheduleDefinition,
)
from parishkit.stewardship.campaigns.read_guards import (
    DownloadBusy,
    DownloadPool,
    ReadUnavailable,
)
from parishkit.stewardship.campaigns.runtime import return_to_testing
from parishkit.stewardship.jobs.storage import change_run
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import (
    admit_task_work,
    admit_test_work,
    campaign_clock,
    command,
    draft_campaign,
    restored_runtime,
)
from .test_boundary_catchup_postgresql import claimed_task
from .test_exceptional_end_postgresql import complete_empty_catchup, end_request
from .test_read_guards_postgresql import family_campaign, guard

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("value", [True, 0, "1", -1])
def test_runtime_return_and_gate_release_require_canonical_versions(value):
    """Reject coercible versions before querying state or invoking any owner."""
    with pytest.raises(ValueError):
        return_to_testing(
            campaign_id=uuid4(),
            request_id=uuid4(),
            expected_runtime_version=value,
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    with pytest.raises(ValueError):
        release_work_gate(
            gate_id=uuid4(),
            expected_version=value,
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    with pytest.raises(TypeError):
        release_work_gate(
            gate_id=uuid4(),
            expected_version=1,
            actor_id="invalid",
            correlation_id=uuid4(),
            admit=admit_test_work,
        )


@pytest.mark.parametrize(
    "action",
    [
        Action.PURGE,
        Action.PURGE_ABORT,
        Action.PURGE_COMPLETE,
        Action.PURGE_CLEANUP_FAILED,
        Action.REOPEN,
    ],
)
def test_generic_transition_refuses_unowned_workflows(tmp_path, action):
    """Future purge and exact-config reopen cannot enter the generic command path."""
    _, campaign, actor = draft_campaign(tmp_path)
    with pytest.raises(StorageInvariantError, match="separate owning workflow"):
        command(campaign, actor, action)


def test_committed_pending_close_blocks_end_edit_until_task_reconciled(tmp_path):
    """Pending allocation is durable storage, even before BG-02 owns its queue."""
    store, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        complete_empty_catchup(campaign, actor)
        row = CampaignBoundaryOccurrence.objects.create(
            campaign=campaign,
            kind="close",
            due_at=campaign.active_configuration.ends_at,
            actor_id=actor,
            correlation_id=uuid4(),
        )
        run = claimed_task("campaign_boundary", campaign.pk, actor)
        with transaction.atomic():
            CampaignBoundaryOccurrence.objects.filter(pk=row.pk).update(
                task_id=run.run_id,
                task_fence=run.fence,
                version=F("version") + 1,
                actor_id=actor,
                correlation_id=uuid4(),
            )
        request, _ = end_request(store, campaign, actor, "edit_end")
        with pytest.raises(admission.CampaignAdmissionUnavailable):
            install_request(
                store,
                request_id=request.request_id,
                correlation_id=uuid4(),
                admit_campaign=admit_test_work,
            )
        change_run(
            run_id=run.run_id,
            expected_version=run.version,
            action="safe_cancel",
            fence=run.fence,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_task_work,
        )
        assert (
            install_request(
                store,
                request_id=request.request_id,
                correlation_id=uuid4(),
                admit_campaign=admit_test_work,
            ).state
            == "applied"
        )
    row.refresh_from_db()
    assert row.state == "skipped" and row.reason == "boundary_replaced"


@pytest.mark.parametrize("section", ["campaigns", "schedules"])
def test_sql_restore_gate_survives_missing_python_preflight(
    tmp_path, monkeypatch, section
):
    """A buggy caller cannot mutate held campaign/schedule selection through SQL."""
    store, campaign, actor = draft_campaign(tmp_path)
    identifier = (
        campaign.pk if section == "campaigns" else ScheduleDefinition.objects.get().pk
    )
    values = (
        {"name": "Changed name"} if section == "campaigns" else {"time": "10:00:00"}
    )
    request = record_request(
        base_digest=store.active().digest,
        patch=[
            {
                "operation": "update",
                "section": section,
                "id": str(identifier),
                "values": values,
            }
        ],
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    original = SystemConfiguration.objects.get().active_configuration_id
    with restored_runtime(campaign.active_configuration.starts_at):
        with monkeypatch.context() as patch:
            patch.setattr(
                admission, "validate_installation", lambda document, **kwargs: None
            )
            with pytest.raises(IntegrityError, match="Restore review holds"):
                install_request(
                    store, request_id=request.request_id, correlation_id=uuid4()
                )
        assert SystemConfiguration.objects.get().active_configuration_id == original
    # Exact selected-candidate recovery remains forward-only once the gate clears.
    assert (
        install_request(
            store, request_id=request.request_id, correlation_id=uuid4()
        ).state
        == "applied"
    )


def test_contended_download_budget_has_typed_unavailable_response(tmp_path):
    """Maintenance-row contention is an ordinary 503, not an opaque database 500."""
    identifier = family_campaign(tmp_path)
    other = connections["default"].copy(alias="budget-contention")
    try:
        with other.cursor() as cursor:
            cursor.execute("BEGIN")
            cursor.execute("SELECT id FROM stewardship_download_policy FOR UPDATE")
        with pytest.raises(DownloadBusy), guard(identifier, pool=DownloadPool()):
            pytest.fail("Read crossed a locked deployment budget")
    finally:
        other.rollback()
        other.close()


def test_deadline_racing_owner_rollback_never_reconnects(tmp_path, monkeypatch):
    """Force expiration after close starts, before raw rollback reaches the backend."""
    identifier = family_campaign(tmp_path)
    reader = guard(identifier, pool=DownloadPool())
    reader.__enter__()
    rollback = reader._raw.rollback

    def expire_during_rollback():
        """Model the timer winning immediately after the owner's continuity check."""
        reader._expire()
        rollback()

    def unexpected_connection(*args, **kwargs):
        pytest.fail("Cleanup opened another database connection")

    monkeypatch.setattr(reader._raw, "rollback", expire_during_rollback)
    monkeypatch.setattr(reader.db, "get_new_connection", unexpected_connection)
    reader.close()
    assert reader.closed.is_set() and reader.db.connection is None
    with pytest.raises(ReadUnavailable):
        reader.check()
