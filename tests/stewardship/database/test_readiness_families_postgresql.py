"""Readiness reads whole real campaign groups without creating delivery work."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.readiness_families import family_impact
from parishkit.stewardship.campaigns.resolutions import resolve_restore_hold
from parishkit.stewardship.campaigns.schedule_models import (
    RestoreDeliveryHold,
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import restored_runtime
from .credential_builders import family_campaign, populate
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_schedule_planning_postgresql import add_reminders

pytestmark = pytest.mark.django_db(transaction=True)


def test_real_web_reader_counts_every_page_and_binds_changed_source(tmp_path):
    """201 Families cross a page; no per-Family query or outbox allocation occurs."""
    store, campaign, actor, ring = family_campaign(tmp_path, count=201)
    add_reminders(store, campaign, actor)
    campaign.refresh_from_db()
    cutoff = campaign.active_configuration.starts_at + timedelta(days=5)
    with task_login(ServiceRole.WEB), work_transaction():
        with CaptureQueriesContext(connection) as queries:
            first = family_impact(campaign, cutoff=cutoff)
        assert first.counts.active == first.counts.messages == 201
        assert first.counts.coalesced_slots == 402
        # Inspect only this reader's relevant query shapes, not all queries in
        # authorization/locking helpers whose implementation can change freely.
        family_reads = [
            row["sql"]
            for row in queries
            if 'FROM "stewardship_family_campaign"' in row["sql"]
        ]
        assert len(family_reads) <= 3
        assert all("LIMIT 200" in sql for sql in family_reads)
        assert not any("code_ciphertext" in row["sql"] for row in queries)
        second = family_impact(campaign, cutoff=cutoff)
        assert second == first
    assert not ScheduleOccurrence.objects.exists()
    assert not OutboxMessage.objects.exists()

    populate(
        campaign,
        ring,
        [FamilyStatus(n, True, True, True, True) for n in range(1, 202)],
        generation=2,
    )
    with task_login(ServiceRole.WEB), work_transaction():
        newer = family_impact(campaign, cutoff=cutoff)
    assert newer.counts == first.counts
    assert newer.digest != first.digest


def test_due_boundary_changes_fingerprint_without_writing_any_work(tmp_path):
    """A preview just before a reminder cannot hide the newly due semantic slot."""
    store, campaign, actor, _ = family_campaign(tmp_path)
    add_reminders(store, campaign, actor)
    campaign.refresh_from_db()
    cutoff = campaign.active_configuration.starts_at + timedelta(days=2)
    with work_transaction():
        before = family_impact(campaign, cutoff=cutoff)
        after = family_impact(campaign, cutoff=cutoff + timedelta(days=1))
        closed = family_impact(campaign, cutoff=campaign.active_configuration.ends_at)
    assert before.counts.messages == after.counts.messages == 1
    assert before.counts.coalesced_slots == 0
    assert after.counts.coalesced_slots == 1
    assert before.digest != after.digest
    assert closed.counts.messages == 0 and closed.counts.skipped_slots == 4
    assert not ScheduleOccurrence.objects.exists()


def test_requires_owning_work_order_even_with_empty_population(tmp_path):
    _, campaign, _, _ = family_campaign(tmp_path, count=0)
    assert not FamilyCampaign.objects.exists()
    with pytest.raises(StorageInvariantError, match="ordered transaction"):
        family_impact(campaign, cutoff=campaign.active_configuration.starts_at)


@pytest.mark.parametrize("kind", ["initial", "reminder"])
def test_restore_hold_counts_match_the_dispatch_predicate(tmp_path, kind):
    """Repeated restore inventories preserve uncertainty, never imply fulfillment."""
    store, campaign, actor, _ = family_campaign(tmp_path)
    add_reminders(store, campaign, actor)
    campaign.refresh_from_db()
    definition = (
        ScheduleDefinition.objects.filter(campaign=campaign, kind=kind)
        .order_by("current_revision__due_at")
        .first()
    )
    start = campaign.active_configuration.starts_at
    family = FamilyCampaign.objects.get()
    holds = []
    for _ in range(201 if kind == "initial" else 2):
        with restored_runtime(start) as restore_id:
            hold = RestoreDeliveryHold.objects.create(
                restore_id=restore_id,
                definition=definition,
                mode="production",
                target=f"family:{family.pk}",
                slot="once",
                backup_at=start,
                window_start=start,
                window_end=start + timedelta(days=1),
                discovery="inventory",
                actor_id=actor,
                correlation_id=uuid4(),
            )
            holds.append(hold)
    # History can cross a streaming page; one unresolved restore still matters.
    for hold in holds[:-1]:
        resolve_restore_hold(
            hold_id=hold.pk,
            expected_version=hold.version,
            state="assumed_delivered",
            evidence="Synthetic review",
            actor_id=actor,
            correlation_id=uuid4(),
            admit=lambda *args: None,
        )
    with (
        task_login(ServiceRole.WEB),
        work_transaction(),
        CaptureQueriesContext(connection) as queries,
    ):
        result = family_impact(campaign, cutoff=start + timedelta(days=5))
    assert any(
        "CURSOR" in row["sql"] and '"stewardship_restore_delivery_hold"' in row["sql"]
        for row in queries
    )  # This reader streams retained history rather than caching the whole query.
    assert result.counts.messages == (0 if kind == "initial" else 1)
    assert result.counts.coalesced_slots == (0 if kind == "initial" else 1)
    assert result.counts.blocked_families == (1 if kind == "initial" else 0)
    assert not ScheduleOccurrence.objects.exists()
    resolve_restore_hold(
        hold_id=holds[-1].pk,
        expected_version=holds[-1].version,
        state="assumed_delivered",
        evidence="Synthetic second review",
        actor_id=actor,
        correlation_id=uuid4(),
        admit=lambda *args: None,
    )
    with task_login(ServiceRole.WEB), work_transaction():
        resolved = family_impact(campaign, cutoff=start + timedelta(days=5))
    assert resolved.counts.messages == 1 and resolved.counts.blocked_families == 0
    assert resolved.digest != result.digest


@pytest.mark.parametrize("delivered", [False, True])
def test_mixed_initial_evidence_matches_final_provider_guard(family_mail, delivered):  # noqa: F811
    """Planning a reminder is not provider permission while initial recovery is held."""
    from parishkit.stewardship.campaigns.family_schedule_planning import plan_family
    from parishkit.stewardship.family_delivery import (
        FamilyDeliveryResult,
        FamilyDeliveryStatus,
    )
    from parishkit.stewardship.jobs.family_mail_dispatch import (
        FamilyDeliveryHeld,
        begin_submission,
        disposition,
        finish_submission,
    )
    from parishkit.stewardship.jobs.scheduler import scheduler_session

    from .campaign_builders import campaign_clock, complete_empty_catchup
    from .response_builders import activate_response_service
    from .test_family_mail_dispatch_postgresql import claim, prepare

    actor = uuid4()
    harness = activate_response_service(family_mail)
    campaign = harness.campaign
    complete_empty_catchup(campaign, actor)
    initial = ScheduleDefinition.objects.get(campaign=campaign, kind="initial")
    if delivered:
        with campaign_clock(initial.current_revision.due_at):
            message = prepare(harness)
            with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
                execution = claim(message)
                begin_submission(
                    message.pk,
                    execution.claim,
                    private=harness.rings.private,
                    public_origin="http://localhost:8000",
                )
                finish_submission(
                    message.pk,
                    execution.claim,
                    FamilyDeliveryResult(FamilyDeliveryStatus.ACCEPTED, 1),
                )
    add_reminders(harness.service.store, campaign, actor)
    campaign.refresh_from_db()
    family = FamilyCampaign.objects.get(campaign=campaign, family_duid=1)
    start = campaign.active_configuration.starts_at
    holds = []
    for _ in range(1 if delivered else 2):
        with restored_runtime(start) as restore_id:
            holds.append(
                RestoreDeliveryHold.objects.create(
                    restore_id=restore_id,
                    definition=initial,
                    mode="production",
                    target=f"family:{family.pk}",
                    slot="once",
                    backup_at=start,
                    window_start=start,
                    window_end=start + timedelta(days=1),
                    discovery="inventory",
                    actor_id=actor,
                    correlation_id=uuid4(),
                )
            )
    if not delivered:
        resolve_restore_hold(
            hold_id=holds[0].pk,
            expected_version=holds[0].version,
            state="assumed_delivered",
            evidence="Synthetic prior review",
            actor_id=actor,
            correlation_id=uuid4(),
            admit=lambda *args: None,
        )
    cutoff = start + timedelta(days=5)
    with campaign_clock(cutoff):
        with task_login(ServiceRole.WEB), work_transaction():
            preview = family_impact(campaign, cutoff=cutoff)
        assert preview.counts.blocked_families == 1 and preview.counts.messages == 0
        with scheduler_session() as guard:
            planned = plan_family(guard, family_id=family.pk, worker_id=actor)
        assert planned.selected is not None and not planned.held
        occurrence = ScheduleOccurrence.objects.get(pk=planned.selected)
        assert occurrence.definition.kind == "reminder"
        # A metadata projection is sufficient for the actual pre-provider guard;
        # no rendering, task or network call is needed to exercise disposition.
        candidate = OutboxMessage(
            id=uuid4(),
            campaign=campaign,
            family=family,
            mode="production",
            semantic_key=occurrence.pk,
            purpose="reminder",
            not_before=timezone.now(),
        )
        with (
            task_login(ServiceRole.MAIL_DISPATCH, exact=True),
            work_transaction(),
            pytest.raises(FamilyDeliveryHeld, match="initial recovery review"),
        ):
            disposition(candidate)
        resolve_restore_hold(
            hold_id=holds[-1].pk,
            expected_version=holds[-1].version,
            state="assumed_delivered",
            evidence="Synthetic remaining review",
            actor_id=actor,
            correlation_id=uuid4(),
            admit=lambda *args: None,
        )
        with task_login(ServiceRole.WEB), work_transaction():
            resolved = family_impact(campaign, cutoff=cutoff)
        assert resolved.counts.blocked_families == 0 and resolved.counts.messages == 1
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True), work_transaction():
            assert disposition(candidate) is None
