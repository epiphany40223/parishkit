"""Adversarial Phase 1A storage inputs, without operational restore/provider IO."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from psycopg.types.json import Jsonb

from parishkit.stewardship.campaigns.models import (
    RestoreDeliveryHold,
    RestoreHoldResolution,
    ScheduleDefinition,
    ScheduleFulfillment,
)
from parishkit.stewardship.campaigns.resolutions import (
    coverage_digest,
    resolve_restore_hold,
)
from parishkit.stewardship.campaigns.schedules import create_occurrence

from .campaign_builders import (
    admit_test_work,
    campaign_clock,
    draft_campaign,
    restored_runtime,
)

pytestmark = pytest.mark.django_db(transaction=True)

ITEM = {"kind": "item", "id": str(UUID(int=1)), "version": 1}
VALID = {"items": [ITEM], "daily_range": None}


@pytest.mark.parametrize(
    "manifest",
    [
        None,
        [],
        {},
        {"items": []},
        VALID | {"extra": True},
        VALID | {"items": None},
        VALID | {"items": {}},
        VALID | {"items": []},
        VALID | {"items": [None]},
        VALID | {"items": [ITEM | {"extra": True}]},
        *[
            VALID | {"items": [ITEM | {"version": value}]}
            for value in (0, -1, 1.5, True, "1", None)
        ],
        *[VALID | {"items": [ITEM | {"id": value}]} for value in ("bad", None, 1)],
        VALID | {"items": [ITEM | {"kind": "private"}]},
        VALID | {"items": [ITEM | {"kind": {}}]},
        VALID | {"items": [ITEM, ITEM]},
        VALID | {"items": [ITEM | {"version": 2}, ITEM]},
        VALID | {"items": [ITEM] * 10001},
        *[
            VALID | {"daily_range": days}
            for days in (
                {},
                [],
                {"start": "2026-01-01"},
                {"start": "2026-02-31", "end": "2026-03-01"},
                {"start": "2026-02-01", "end": "2026-01-01"},
                {"start": "20260201", "end": "2026-03-01"},
                {"start": None, "end": "2026-03-01"},
                {"start": "2026-01-01", "end": "2026-03-01", "extra": True},
            )
        ],
    ],
)
def test_invalid_coverage_is_rejected_by_python_and_sql(manifest):
    """Malformed or noncanonical manifests cannot claim exact coverage."""
    with pytest.raises(ValueError):
        coverage_digest(manifest)
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_coverage_valid_v1(%s)", [Jsonb(manifest)])
        assert cursor.fetchone()[0] is False


def inventory_values(campaign, actor, restore_id):
    """Return a valid, identifiers-only inventory bound to a synthetic restore."""
    start = campaign.active_configuration.starts_at
    return dict(
        restore_id=restore_id,
        definition=ScheduleDefinition.objects.get(),
        mode="testing",
        target="family:1",
        slot="once",
        backup_at=start,
        window_start=start,
        window_end=start + timedelta(days=1),
        discovery="inventory",
        actor_id=actor,
        correlation_id=uuid4(),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"state": "assumed_delivered"},
        {"version": 2},
        {"evidence": "premature"},
        {"target": ""},
        {"slot": ""},
        {"discovery": ""},
        {"restore_id": UUID(int=1)},
        {"mode": "invalid"},
        {"recovery_occurrence_id": UUID(int=1)},
        {"actor_id": None},
    ],
)
def test_invalid_restore_inventory_rejected(tmp_path, change):
    """Shape and restore provenance are checked before a hold can block delivery."""
    _, campaign, actor = draft_campaign(tmp_path)
    with (
        restored_runtime(campaign.active_configuration.starts_at) as restore_id,
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        RestoreDeliveryHold.objects.create(
            **(inventory_values(campaign, actor, restore_id) | change)
        )
    assert not RestoreDeliveryHold.objects.exists()


def test_restore_inventory_requires_exact_active_gate_and_backup(tmp_path):
    """An invented gate or a different backup cannot suppress a semantic slot."""
    _, campaign, actor = draft_campaign(tmp_path)
    values = inventory_values(campaign, actor, uuid4())
    with pytest.raises(IntegrityError), transaction.atomic():
        RestoreDeliveryHold.objects.create(**values)
    with restored_runtime(campaign.active_configuration.starts_at) as restore_id:
        for change in (
            {"backup_at": values["backup_at"] + timedelta(seconds=1)},
            {"window_start": values["backup_at"] + timedelta(seconds=1)},
            {"resolved_at": values["backup_at"]},
        ):
            with pytest.raises(IntegrityError), transaction.atomic():
                RestoreDeliveryHold.objects.create(
                    **(values | {"restore_id": restore_id} | change)
                )


@pytest.mark.parametrize("outcome", ["not_applicable", "resend_authorized"])
def test_restore_resend_requires_released_global_gate_and_exact_pending_slot(
    tmp_path, outcome
):
    """Inventory precedes global release; per-slot review controls live recovery."""
    _, campaign, actor = draft_campaign(tmp_path)
    start = campaign.active_configuration.starts_at
    definition = ScheduleDefinition.objects.get()
    occurrence_args = dict(
        definition_id=definition.pk,
        revision_id=definition.current_revision_id,
        mode="testing",
        target="family:1",
        slot="once",
        due_at=start,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    with campaign_clock(start):
        with restored_runtime(start) as restore_id:
            hold = RestoreDeliveryHold.objects.create(
                **inventory_values(campaign, actor, restore_id)
            )
            with pytest.raises(IntegrityError):
                create_occurrence(**occurrence_args)
        recovery = (
            create_occurrence(**occurrence_args)
            if outcome == "resend_authorized"
            else None
        )
        decision = resolve_restore_hold(
            hold_id=hold.pk,
            expected_version=hold.version,
            state=outcome,
            evidence="Reviewed live recovery window",
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
            recovery_occurrence_id=recovery.pk if recovery else None,
        )
    hold.refresh_from_db()
    assert hold.state == outcome and decision.version == 2
    assert not ScheduleFulfillment.objects.exists()
    with pytest.raises(IntegrityError), transaction.atomic():
        RestoreHoldResolution.objects.create(
            hold=hold,
            version=3,
            state="assumed_delivered",
            evidence="Cannot undo terminal review",
            actor_id=actor,
            correlation_id=uuid4(),
        )
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "DELETE FROM stewardship_restore_delivery_hold WHERE id=%s", [hold.pk]
        )


@pytest.mark.parametrize(
    "change",
    [
        {"version": 1},
        {"actor_id": None},
        {"evidence": " "},
        {"state": "unknown"},
        {"state": "resend_authorized"},
        {"recovery_occurrence_id": UUID(int=1)},
    ],
)
def test_restore_resolution_requires_exact_review_evidence(tmp_path, change):
    """Raw inserts cannot bypass version, actor, decision or resend constraints."""
    _, campaign, actor = draft_campaign(tmp_path)
    with restored_runtime(campaign.active_configuration.starts_at) as restore_id:
        hold = RestoreDeliveryHold.objects.create(
            **inventory_values(campaign, actor, restore_id)
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        RestoreHoldResolution.objects.create(
            **(
                dict(
                    hold=hold,
                    version=2,
                    state="assumed_delivered",
                    evidence="Reviewed",
                    actor_id=actor,
                    correlation_id=uuid4(),
                )
                | change
            )
        )


@pytest.mark.parametrize(
    "column,value",
    [
        ("restore_review_required", True),
        ("restore_id", UUID(int=1)),
        ("restore_backup_at", "2026-01-01T00:00:00Z"),
        ("restore_activated_at", "2026-01-01T00:00:00Z"),
        ("restore_released_at", "2026-01-01T00:00:00Z"),
    ],
)
def test_reserved_restore_metadata_cannot_change_through_application_sql(
    tmp_path, column, value
):
    """Until OPS-06 owns release, ordinary runtime writers cannot flip the gate."""
    draft_campaign(tmp_path)
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        # The column is exclusively a developer-defined parametrization above.
        cursor.execute(
            "UPDATE stewardship_system_configuration "
            f"SET {column}=%s, version=version+1",
            [value],
        )


@pytest.mark.parametrize("evidence", ["occurrence_id", "task_id", "outbox_id"])
def test_postclose_cancellation_evidence_cannot_cover_unrelated_obligations(
    tmp_path, evidence
):
    """Each cancellation receipt can back only one semantic resolution."""
    from parishkit.stewardship.campaigns.boundaries import apply_due_boundaries
    from parishkit.stewardship.campaigns.lifecycle import Action
    from parishkit.stewardship.campaigns.resolutions import resolve_postclose
    from parishkit.stewardship.jobs.storage import change_run

    from .campaign_builders import command
    from .test_boundary_catchup_postgresql import claimed_task
    from .test_exceptional_end_postgresql import complete_empty_catchup
    from .test_schedules_postgresql import advance

    _, campaign, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.get()
    with campaign_clock(definition.current_revision.due_at):
        command(campaign, actor, Action.ACTIVATE)
        complete_empty_catchup(campaign, actor)
        row = create_occurrence(
            definition_id=definition.pk,
            revision_id=definition.current_revision_id,
            mode="production",
            target="family:1",
            slot="once",
            due_at=definition.current_revision.due_at,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        advance(row, actor, "skipped", reason="reviewed_skip")
        cancelled = claimed_task("schedule_occurrence", row.pk, actor)
        change_run(
            run_id=cancelled.run_id,
            action="safe_cancel",
            expected_version=cancelled.version,
            fence=cancelled.fence,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    run = claimed_task("campaign_boundary", campaign.pk, actor)
    with campaign_clock(campaign.active_configuration.ends_at):
        apply_due_boundaries(
            campaign_id=campaign.pk,
            task_id=run.run_id,
            fence=run.fence,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    identifiers = {
        "occurrence_id": row.pk,
        "task_id": cancelled.run_id,
        "outbox_id": uuid4(),
    }
    args = dict(
        campaign_id=campaign.pk,
        mode="production",
        coverage=VALID,
        reason="Reviewed cancellation",
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
        **{evidence: identifiers[evidence]},
    )
    resolve_postclose(**args, obligation_key="first")
    with pytest.raises(IntegrityError, match="postclose_.*_once"):
        resolve_postclose(**args, obligation_key="unrelated")


def test_closed_report_can_recover_but_family_invitation_cannot(tmp_path):
    """Global release permits a held final digest, not new Family invitations."""
    from parishkit.stewardship.campaigns.lifecycle import Action

    from ..campaign_factory import schedule
    from .campaign_builders import command
    from .test_boundary_catchup_postgresql import claimed_task
    from .test_controls_resolutions_postgresql import close_campaign
    from .test_policy_postgresql import change
    from .test_schedules_postgresql import advance

    store, campaign, actor = draft_campaign(tmp_path)
    digest = schedule(str(campaign.pk), kind="daily_digest", date=None)
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "add", "section": "schedules", **digest}],
        ).state
        == "applied"
    )
    campaign.refresh_from_db()
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    close_campaign(campaign, actor)
    end = campaign.active_configuration.ends_at
    definition = ScheduleDefinition.objects.get(pk=digest["id"])
    initial = ScheduleDefinition.objects.get(kind="initial")
    with campaign_clock(end + timedelta(hours=1)):
        with restored_runtime(end) as restore_id:
            hold = RestoreDeliveryHold.objects.create(
                restore_id=restore_id,
                definition=definition,
                mode="production",
                target="admins",
                slot="final-day",
                backup_at=end,
                window_start=end,
                window_end=end + timedelta(hours=1),
                discovery="inventory",
                actor_id=actor,
                correlation_id=uuid4(),
            )
        args = dict(
            mode="production",
            target="admins",
            slot="final-day",
            due_at=end,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        with pytest.raises(IntegrityError, match="not admitted"):
            create_occurrence(
                **args,
                definition_id=initial.pk,
                revision_id=initial.current_revision_id,
            )
        recovery = create_occurrence(
            **args,
            definition_id=definition.pk,
            revision_id=definition.current_revision_id,
        )
        run = claimed_task("schedule_occurrence", recovery.pk, actor)
        with pytest.raises(IntegrityError, match="current fenced work"):
            advance(recovery, actor, "running", task_id=run.run_id, fence=run.fence)
        resolve_restore_hold(
            hold_id=hold.pk,
            expected_version=hold.version,
            state="resend_authorized",
            recovery_occurrence_id=recovery.pk,
            evidence="Provider history reviewed",
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        result = advance(
            recovery, actor, "running", task_id=run.run_id, fence=run.fence
        )
        assert result.state == "running"
