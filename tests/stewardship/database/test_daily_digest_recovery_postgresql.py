"""Recovery boundaries reject truncated coverage and preserve immutable lineage."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.digest_schedule_planning import (
    DigestScheduleProducer,
)
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import ActivationCatchUpDemand
from parishkit.stewardship.campaigns.recovery_coverage import covered_dates
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import change_run
from parishkit.stewardship.reports import digest_planning
from parishkit.stewardship.reports.digest_models import DailyDigestPreparation
from parishkit.stewardship.reports.digest_ownership import (
    DailyDigestProducer,
    checkpoint_preparation,
)
from parishkit.stewardship.reports.digest_planning import cover_dates, discover_dates

from .campaign_builders import campaign_clock, command, draft_campaign
from .test_background_grants_postgresql import task_login
from .test_catchup_preparation_postgresql import execution_arguments
from .test_daily_digest_capture_postgresql import prepare
from .test_daily_digest_planning_postgresql import INSTANT, allocate
from .test_digest_schedule_planning_postgresql import add_digest
from .test_schedule_reconciliation_postgresql import replace_schedule

pytestmark = pytest.mark.django_db(transaction=True)


def finish_coverage(claim):
    """Bound the test driver too, so a broken cursor fails instead of hanging CI."""
    row = DailyDigestPreparation.objects.get(task_id=claim.run_id)
    for _ in range(30):
        if row.phase not in {"dates", "cover"}:
            break
        with work_transaction():
            row = (discover_dates if row.phase == "dates" else cover_dates)(claim)
    assert row.phase == "facts"
    return row


def test_delayed_first_claim_includes_every_day_then_freezes_its_cutoff(
    response_service, monkeypatch
):
    add_digest(response_service.service.store, response_service.campaign)
    monkeypatch.setattr(digest_planning, "LIMIT", 2)
    with campaign_clock(INSTANT):
        claim = allocate()
    executed = datetime(2026, 10, 20, tzinfo=UTC)
    with (
        campaign_clock(executed),
        task_login(ServiceRole.WORKER, exact=True),
        work_transaction(),
    ):
        assert discover_dates(claim).cutoff == executed
    with (
        campaign_clock(datetime(2026, 10, 21, tzinfo=UTC)),
        task_login(ServiceRole.WORKER, exact=True),
    ):
        row = finish_coverage(claim)
        assert row.cutoff == executed
        assert len(covered_dates(row.occurrence_id, mode=row.mode)) == 18


def test_raw_shortcuts_cannot_claim_complete_dates_or_coverage(
    response_service, monkeypatch
):
    add_digest(response_service.service.store, response_service.campaign)
    with campaign_clock(INSTANT):
        claim = allocate()
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            with (
                pytest.raises(DatabaseError, match="fenced monotonic"),
                transaction.atomic(),
            ):
                checkpoint_preparation(claim, phase="cover")
            row = discover_dates(claim)
        assert row.phase == "cover"
        monkeypatch.setattr(digest_planning, "LIMIT", 2)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            row = cover_dates(claim)
            assert row.phase == "cover" and row.occurrence_id is not None
            with (
                pytest.raises(DatabaseError, match="coverage must finish"),
                transaction.atomic(),
            ):
                checkpoint_preparation(claim, phase="facts")


def test_sql_live_proof_uses_the_supplied_fence_not_the_current_row(response_service):
    add_digest(response_service.service.store, response_service.campaign)
    with campaign_clock(INSTANT):
        claim = allocate()
        row = DailyDigestPreparation.objects.get(task_id=claim.run_id)
        with task_login(ServiceRole.WORKER, exact=True), connection.cursor() as cursor:
            for fence, worker, expected in (
                (claim.fence, claim.worker_id, True),
                (claim.fence + 1, claim.worker_id, False),
                (claim.fence, uuid4(), False),
            ):
                cursor.execute(
                    "SELECT stewardship_daily_digest_live_v1(%s,%s,%s,%s)",
                    (row.pk, claim.run_id, fence, worker),
                )
                assert cursor.fetchone() == (expected,)


def test_ordinary_aggregation_preserves_an_older_aggregate_lineage(response_service):
    identifier = add_digest(response_service.service.store, response_service.campaign)
    with campaign_clock(INSTANT):
        with scheduler_session() as guard:
            DigestScheduleProducer(uuid4())(guard)
        rows = list(
            ScheduleOccurrence.objects.filter(definition_id=identifier).order_by("slot")
        )
        # Construct a valid prior semantic aggregate with normal occurrence and
        # fulfillment guards enabled. No task, outbox or lease is fabricated.
        with work_transaction():
            for prior in rows[:2]:
                ScheduleOccurrence.objects.filter(pk=prior.pk).update(
                    state="coalesced",
                    replacement=rows[2],
                    reason="missed_daily_recovery",
                    version=prior.version + 1,
                    actor_id=uuid4(),
                    correlation_id=uuid4(),
                )
                ScheduleFulfillment.objects.create(
                    definition_id=identifier,
                    mode=prior.mode,
                    target="admins",
                    slot=prior.slot,
                    disposition="coalesced",
                    occurrence=rows[2],
                    actor_id=uuid4(),
                )
        claim = allocate()
        with task_login(ServiceRole.WORKER, exact=True):
            selected = finish_coverage(claim)
            assert [
                day.isoformat()
                for day in covered_dates(selected.occurrence_id, mode=selected.mode)
            ] == [row.slot for row in rows]
        rows[2].refresh_from_db()
        assert rows[2].replacement_id == selected.occurrence_id


def test_failed_old_generation_does_not_block_new_daily_obligations(response_service):
    with campaign_clock(INSTANT):
        claim = prepare(response_service)
        task = TaskRun.objects.get(pk=claim.run_id)
        with work_transaction():
            change_run(
                run_id=task.pk,
                action="permanent_failure",
                expected_version=task.version,
                actor_id=claim.worker_id,
                fence=claim.fence,
                correlation_id=uuid4(),
                admit=lambda *args: True,
            )
    with campaign_clock(datetime(2026, 10, 12, tzinfo=UTC)):
        with (
            task_login(ServiceRole.SCHEDULER, exact=True),
            scheduler_session() as guard,
        ):
            DigestScheduleProducer(uuid4())(guard)
            (new,) = DailyDigestProducer(uuid4())(guard)
            assert new.run_id != claim.run_id
        assert DailyDigestPreparation.objects.count() == 2


def test_completed_activation_coverage_survives_later_schedule_replacement(tmp_path):
    """No new original slot is needed to recover an already coalesced full range."""
    store, campaign, actor = draft_campaign(tmp_path)
    identifier = add_digest(store, campaign)
    with campaign_clock(INSTANT):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        with task_login(ServiceRole.WORKER, exact=True):
            assert execute_hint(**execution_arguments(demand))
        demand.refresh_from_db()
        assert demand.completed_at is not None
        previous = ScheduleOccurrence.objects.get(
            definition_id=identifier, state="pending"
        )
        dates = covered_dates(previous.pk)
        assert len(dates) == 8
        definition = ScheduleDefinition.objects.get(pk=identifier)
        assert replace_schedule(store, definition, actor).state == "applied"
        claim = allocate()
        with task_login(ServiceRole.WORKER, exact=True):
            row = finish_coverage(claim)
            assert covered_dates(row.occurrence_id) == dates
        selected = ScheduleOccurrence.objects.get(pk=row.occurrence_id)
        assert selected.slot == f"recovery:{row.pk}"
        assert selected.recovered_aggregates.get().previous_id == previous.pk
