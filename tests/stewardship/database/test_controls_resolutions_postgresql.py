"""Lifecycle controls, historical successor admission, and exact mail resolutions."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.admission import CampaignAdmissionUnavailable
from parishkit.stewardship.campaigns.controls import (
    change_control,
    release_work_gate,
    reserve_work_gate,
)
from parishkit.stewardship.campaigns.lifecycle import Action, portal_admitted
from parishkit.stewardship.campaigns.models import (
    Campaign,
    PostCloseMailResolution,
    RestoreDeliveryHold,
    ScheduleDefinition,
    ScheduleFulfillment,
)
from parishkit.stewardship.campaigns.resolutions import (
    resolve_postclose,
    resolve_restore_hold,
)
from parishkit.stewardship.campaigns.runtime import campaign_facts, return_to_testing
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from ..campaign_factory import campaign as campaign_row
from .campaign_builders import (
    add_draft,
    admit_test_work,
    campaign_clock,
    close_campaign,
    closed_digest,
    command,
    complete_empty_catchup,
    draft_campaign,
    restored_runtime,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_pause_is_orthogonal_and_withdrawal_unlocks_only_never_active(tmp_path):
    """Pause does not change routing or Family access; withdrawal restores a draft."""
    _, campaign, actor = draft_campaign(tmp_path)
    before_start = campaign.active_configuration.starts_at - timedelta(days=1)
    with campaign_clock(before_start):
        command(campaign, actor, Action.ACTIVATE)
        complete_empty_catchup(campaign, actor)
        for index, action in enumerate(("pause", "resume", "pause", "resume"), 1):
            campaign.refresh_from_db()
            runtime = SystemConfiguration.objects.get()
            change_control(
                campaign_id=campaign.pk,
                request_id=uuid4(),
                action=action,
                expected_version=campaign.version,
                expected_runtime_version=runtime.version,
                actor_id=actor,
                correlation_id=uuid4(),
                admit=admit_test_work,
                reason="reviewed",
            )
            campaign.refresh_from_db()
            runtime.refresh_from_db()
            assert campaign.delivery_paused is (action == "pause")
            assert campaign.pause_version == index
            assert (
                campaign.pause_actor_id == actor and campaign.pause_reason == "reviewed"
            )
            assert campaign.paused_at == before_start
            assert campaign.resumed_at == (before_start if action == "resume" else None)
            assert (
                campaign.state == "scheduled"
                and SystemConfiguration.objects.get().mode == "production"
            )
            assert portal_admitted(
                campaign_facts(campaign, runtime),
                campaign.active_configuration.starts_at,
            )
        command(campaign, actor, Action.WITHDRAW, reason="postponed")
    campaign.refresh_from_db()
    assert campaign.state == "draft" and not campaign.structural_locked
    assert campaign.active_token_generation_id is None
    assert SystemConfiguration.objects.get().mode == "testing"


def test_archive_return_testing_and_purge_reservation_serialize_successor(tmp_path):
    """History and the global work gate survive clearing the current pointer."""
    store, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    close_campaign(campaign, actor)
    command(campaign, actor, Action.ARCHIVE)
    command(campaign, actor, Action.UNARCHIVE)
    command(campaign, actor, Action.ARCHIVE)
    campaign.refresh_from_db()
    archived_version = campaign.version
    return_to_testing(
        campaign_id=campaign.pk,
        request_id=uuid4(),
        expected_runtime_version=SystemConfiguration.objects.get().version,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    campaign.refresh_from_db()
    assert campaign.version == archived_version and campaign.state == "archived"
    assert SystemConfiguration.objects.get().current_campaign_id is None
    gate = reserve_work_gate(
        campaign_id=campaign.pk,
        request_id=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    replay = dict(
        campaign_id=campaign.pk,
        request_id=gate.request_id,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    assert reserve_work_gate(**replay).pk == gate.pk
    with pytest.raises(StorageInvariantError, match="already bound"):
        reserve_work_gate(**(replay | {"actor_id": uuid4()}))
    with pytest.raises(StaleRecordError):
        release_work_gate(
            gate_id=gate.pk,
            expected_version=gate.version + 1,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    with pytest.raises(CampaignAdmissionUnavailable, match="creation is held"):
        add_draft(store, store.active(), actor, campaign_row(name="Successor"))
    assert Campaign.objects.count() == 1

    def load_future_gate_state(state):
        """Test future-owner sentinel states without implementing a purge executor.

        Only this disposable schema-owner fixture bypasses the gate transition
        guard. Release itself always runs with all guards enabled, even against
        the otherwise permissive archived-campaign precondition.
        """
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
            )
            cursor.execute(
                "UPDATE stewardship_campaign_work_gate SET state=%s WHERE id=%s",
                [state, gate.pk],
            )
            cursor.execute(
                "ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER"
            )

    try:
        for state in ("running", "tombstone"):
            load_future_gate_state(state)
            with pytest.raises(IntegrityError, match="Invalid work gate transition"):
                release_work_gate(
                    gate_id=gate.pk,
                    expected_version=gate.version,
                    actor_id=actor,
                    correlation_id=uuid4(),
                    admit=admit_test_work,
                )
    finally:
        load_future_gate_state("preparing")
    release_work_gate(
        gate_id=gate.pk,
        expected_version=gate.version,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    assert reserve_work_gate(**replay).state == "released"
    with pytest.raises(StaleRecordError):
        release_work_gate(
            gate_id=gate.pk,
            expected_version=gate.version,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    with pytest.raises(IntegrityError, match="Invalid work gate transition"):
        release_work_gate(
            gate_id=gate.pk,
            expected_version=gate.version + 1,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    accepted, _, _ = add_draft(
        store, store.active(), actor, campaign_row(name="Successor")
    )
    assert accepted.state == "applied" and Campaign.objects.count() == 2


def test_restore_assumption_never_becomes_fulfillment(tmp_path):
    """Review history is append-only; direct writes cannot impersonate decisions."""
    _, campaign, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.get()
    start = campaign.active_configuration.starts_at
    with restored_runtime(start) as restore_id:
        hold = RestoreDeliveryHold.objects.create(
            restore_id=restore_id,
            definition=definition,
            mode="production",
            target="family:1",
            slot="once",
            backup_at=start,
            window_start=start,
            window_end=start + timedelta(days=1),
            discovery="inventory",
            actor_id=actor,
            correlation_id=uuid4(),
        )
    arguments = dict(
        hold_id=hold.pk,
        expected_version=hold.version,
        state="assumed_delivered",
        evidence="Admin reviewed duplicate risk",
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    first = resolve_restore_hold(**arguments)
    assert resolve_restore_hold(**arguments).pk == first.pk
    hold.refresh_from_db()
    assert (
        hold.state == "assumed_delivered" and not ScheduleFulfillment.objects.exists()
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        RestoreDeliveryHold.objects.filter(pk=hold.pk).update(
            state="not_applicable", version=F("version") + 1
        )


def test_postclose_exact_versions_do_not_suppress_later_corrections(tmp_path):
    """A later input version has distinct resolution, not reused coverage."""
    store, campaign, actor, skipped = closed_digest(tmp_path)
    identifier = str(uuid4())
    arguments = dict(
        campaign_id=campaign.pk,
        mode="production",
        obligation_key=f"schedule:{skipped.definition_id}:{skipped.slot}",
        occurrence_id=skipped.pk,
        reason="Reviewed explicit skip",
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    coverage = {
        "items": [{"kind": "correction", "id": identifier, "version": 1}],
        "daily_range": None,
    }
    first = resolve_postclose(**arguments, coverage=coverage)
    with pytest.raises(IntegrityError, match="Post-close skip"):
        resolve_postclose(**(arguments | {"mode": "testing"}), coverage=coverage)
    assert resolve_postclose(**arguments, coverage=coverage).pk == first.pk
    coverage["items"][0]["version"] = 2
    # A changed version is not covered by the prior skip. It needs a new
    # occurrence/cancellation receipt, not reuse of the first evidence row.
    with pytest.raises(IntegrityError):
        resolve_postclose(**arguments, coverage=coverage)
    from parishkit.stewardship.campaigns.schedules import create_occurrence

    from .campaign_builders import advance, change

    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": str(skipped.definition_id),
                    "values": {"time": "10:00:00"},
                }
            ],
        ).state
        == "applied"
    )
    definition = ScheduleDefinition.objects.get(pk=skipped.definition_id)
    with campaign_clock(campaign.active_configuration.ends_at):
        replacement = create_occurrence(
            definition_id=definition.pk,
            revision_id=definition.current_revision_id,
            mode="production",
            target=skipped.target,
            slot=skipped.slot,
            due_at=skipped.due_at,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        advance(replacement, actor, "skipped", reason="admin_post_close_skip")
    second = resolve_postclose(
        **(arguments | {"occurrence_id": replacement.pk}), coverage=coverage
    )
    assert (
        first.coverage_digest != second.coverage_digest
        and PostCloseMailResolution.objects.count() == 2
    )


@pytest.mark.parametrize(
    "change",
    [
        {"evidence_id": "00000000-0000-0000-0000-000000000001"},
        {"reason": 1},
        {"expected_version": True},
    ],
)
def test_control_input_types_are_validated_before_any_write(tmp_path, change):
    """Command receipts must not change identity through implicit model coercion."""
    _, campaign, actor = draft_campaign(tmp_path)
    with pytest.raises((TypeError, ValueError)):
        change_control(
            **(
                dict(
                    campaign_id=campaign.pk,
                    request_id=uuid4(),
                    action="pause",
                    expected_version=campaign.version,
                    expected_runtime_version=SystemConfiguration.objects.get().version,
                    actor_id=actor,
                    correlation_id=uuid4(),
                    admit=admit_test_work,
                    reason="Reviewed",
                )
                | change
            )
        )
    campaign.refresh_from_db()
    assert not campaign.delivery_paused


@pytest.mark.parametrize(
    "action,column",
    [
        ("first_delivery", "first_live_delivery_at"),
        ("first_submission", "first_live_submission_at"),
    ],
)
def test_first_live_effect_requires_exact_once_only_in_interval_evidence(
    tmp_path, action, column
):
    """Require future-owner evidence and verify real timestamps/effect writes."""
    _, campaign, actor = draft_campaign(tmp_path)
    start, end = (
        campaign.active_configuration.starts_at,
        campaign.active_configuration.ends_at,
    )
    with campaign_clock(start):
        command(campaign, actor, Action.ACTIVATE)
    campaign.refresh_from_db()
    args = dict(
        campaign_id=campaign.pk,
        action=action,
        request_id=uuid4(),
        expected_version=campaign.version,
        expected_runtime_version=SystemConfiguration.objects.get().version,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
        evidence_id=uuid4(),
        occurred_at=start,
    )
    with campaign_clock(end):
        for change in (
            {"evidence_id": None},
            {"occurred_at": start - timedelta(microseconds=1)},
            {"occurred_at": end},
        ):
            with pytest.raises(IntegrityError, match="first-live-effect"):
                change_control(**(args | change))
        receipt = change_control(**args)
        assert change_control(**args).pk == receipt.pk
        campaign.refresh_from_db()
        assert getattr(campaign, column) == start
        with pytest.raises(IntegrityError, match="first-live-effect"):
            change_control(
                **(args | {"request_id": uuid4(), "expected_version": campaign.version})
            )
