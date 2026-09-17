"""Weekly schedule coverage and immutable capture through exact runtime roles."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.digest_schedule_planning import (
    DigestScheduleProducer,
)
from parishkit.stewardship.campaigns.recovery_coverage import covered_dates
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.ownership import TaskClaim, TaskOwnershipLost
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import _status, change_run
from parishkit.stewardship.reports import weekly_planning
from parishkit.stewardship.reports.weekly_capture import (
    capture_weekly_snapshot,
    retained_selection,
)
from parishkit.stewardship.reports.weekly_models import (
    WeeklyDigestPreparation,
    WeeklyDigestSnapshot,
)
from parishkit.stewardship.reports.weekly_observation import capture_weekly_observation
from parishkit.stewardship.reports.weekly_ownership import (
    WeeklyDigestProducer,
    checkpoint_preparation,
)
from parishkit.stewardship.reports.weekly_planning import cover_dates, discover_dates
from parishkit.stewardship.reports.weekly_selection import WeeklyHistory, select_weekly

from .campaign_builders import campaign_clock, complete_empty_catchup
from .test_background_grants_postgresql import task_login
from .test_digest_schedule_planning_postgresql import add_digest
from .test_taskrun_postgresql import act
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)
INSTANT = datetime(2026, 10, 29, tzinfo=UTC)


def allocate():
    """Allocate under scheduler authority, then claim the root without any mail."""
    producer = WeeklyDigestProducer(uuid4())
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        DigestScheduleProducer(uuid4(), limit=1)(guard)
        (status,) = producer(guard)
        assert producer(guard) == ()
    with work_transaction():
        status = change_run(
            run_id=status.run_id,
            action="claim",
            expected_version=status.version,
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=lambda *args: True,
            lease_seconds=300,
        )
    return TaskClaim(status.run_id, status.fence, status.worker_id)


def prepare(harness):
    """Complete discovery/coalescing through real fenced service and SQL owners."""
    if harness.campaign.state == "active":
        complete_empty_catchup(harness.campaign, uuid4())
    add_digest(harness.service.store, harness.campaign, weekly=True)
    claim = allocate()
    with task_login(ServiceRole.WORKER, exact=True):
        row = WeeklyDigestPreparation.objects.get(task_id=claim.run_id)
        for _ in range(15):
            if row.phase not in {"dates", "cover"}:
                break
            with work_transaction():
                row = (discover_dates if row.phase == "dates" else cover_dates)(claim)
    assert row.phase == "capture"
    return claim


def test_bounded_weekly_dates_select_latest_and_preserve_every_missed_slot(
    response_service, monkeypatch
):
    monkeypatch.setattr(weekly_planning, "LIMIT", 1)
    with campaign_clock(INSTANT):
        claim = prepare(response_service)
        row = WeeklyDigestPreparation.objects.get(task_id=claim.run_id)
        occurrences = list(
            ScheduleOccurrence.objects.filter(definition_id=row.definition_id).order_by(
                "due_at"
            )
        )
        assert [item.slot for item in occurrences] == [
            "2026-10-07",
            "2026-10-14",
            "2026-10-21",
            "2026-10-28",
        ]
        assert all(item.state == "coalesced" for item in occurrences[:-1])
        assert occurrences[-1].pk == row.occurrence_id
        assert [
            day.isoformat() for day in covered_dates(row.occurrence_id, mode=row.mode)
        ] == [item.slot for item in occurrences]
        assert not WeeklyDigestSnapshot.objects.exists()
        assert not OutboxMessage.objects.exists()


def test_capture_freezes_selection_and_cohort_and_replays_without_recapture(
    live_response_service,
):
    harness = live_response_service
    respond(harness, "Frozen weekly request")
    with campaign_clock(INSTANT):
        claim = prepare(harness)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            snapshot = capture_weekly_snapshot(claim)
            selection = retained_selection(snapshot)
        assert snapshot.recipients == ["admin@example.org"]
        assert snapshot.after_watermark == 0 and snapshot.submission_watermark == 1
        assert selection.information[0].text == "Frozen weekly request"
        assert selection.corrections == ()
        assert (
            WeeklyDigestPreparation.objects.get(task_id=claim.run_id).phase == "fanout"
        )
        with (
            patch(
                "parishkit.stewardship.reports.weekly_capture.capture_weekly_observation",
                side_effect=AssertionError("Must not recapture"),
            ),
            task_login(ServiceRole.WORKER, exact=True),
            work_transaction(),
        ):
            replay = capture_weekly_snapshot(claim)
        assert replay.pk == snapshot.pk
        assert retained_selection(replay) == selection
        assert WeeklyDigestSnapshot.objects.count() == 1
        assert not OutboxMessage.objects.filter(purpose="weekly_digest").exists()


def test_empty_capture_is_retained_but_does_not_claim_delivery(response_service):
    with campaign_clock(INSTANT):
        claim = prepare(response_service)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            snapshot = capture_weekly_snapshot(claim)
        assert retained_selection(snapshot).empty
        assert snapshot.information == snapshot.corrections == []
        row = WeeklyDigestPreparation.objects.get(task_id=claim.run_id)
        assert ScheduleOccurrence.objects.get(pk=row.occurrence_id).state == "pending"
        assert not OutboxMessage.objects.exists()


@pytest.mark.parametrize("failed", [False, True])
def test_unresolved_interval_holds_newer_slots_even_after_its_task_fails(
    response_service,
    failed,
):
    with campaign_clock(datetime(2026, 10, 22, tzinfo=UTC)):
        claim = prepare(response_service)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            snapshot = capture_weekly_snapshot(claim)
        if failed:
            with work_transaction():
                assert (
                    act(
                        _status(TaskRun.objects.get(pk=claim.run_id)),
                        "permanent_failure",
                    ).state
                    == "failed"
                )
    with (
        campaign_clock(INSTANT),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session() as guard,
    ):
        DigestScheduleProducer(uuid4())(guard)
        assert ScheduleOccurrence.objects.filter(
            slot="2026-10-28", state="pending"
        ).exists()
        assert WeeklyDigestProducer(uuid4())(guard) == ()
    assert (
        WeeklyDigestPreparation.objects.count()
        == WeeklyDigestSnapshot.objects.count()
        == 1
    )
    assert WeeklyDigestSnapshot.objects.get().pk == snapshot.pk


@pytest.mark.parametrize("tamper", ["omit", "invent_text", "invent_name"])
def test_sql_independently_rejects_fabricated_or_incomplete_capture(
    live_response_service, tamper
):
    harness = live_response_service
    respond(harness, "Genuine request")
    with campaign_clock(INSTANT):
        claim = prepare(harness)
        observed = capture_weekly_observation(harness.campaign.pk)
        if tamper == "omit":
            selected = select_weekly(observed, WeeklyHistory(harness.campaign.pk))
            faulty = patch(
                "parishkit.stewardship.reports.weekly_capture.select_weekly",
                return_value=replace(selected, information=()),
            )
        else:
            item = observed.items[0]
            value = replace(
                item.value,
                **(
                    {"text": "Fabricated request"}
                    if tamper == "invent_text"
                    else {"family_name": "Wrong household"}
                ),
            )
            faulty = patch(
                "parishkit.stewardship.reports.weekly_capture.capture_weekly_observation",
                return_value=replace(observed, items=(replace(item, value=value),)),
            )
        with (
            faulty,
            task_login(ServiceRole.WORKER, exact=True),
            pytest.raises(DatabaseError, match="exact live inputs"),
            work_transaction(),
        ):
            capture_weekly_snapshot(claim)
        assert not WeeklyDigestSnapshot.objects.exists()
        assert (
            WeeklyDigestPreparation.objects.get(task_id=claim.run_id).phase == "capture"
        )


def test_capture_and_fanout_phase_must_commit_together(response_service):
    with campaign_clock(INSTANT):
        claim = prepare(response_service)
        with (
            patch(
                "parishkit.stewardship.reports.weekly_capture.checkpoint_preparation"
            ),
            task_login(ServiceRole.WORKER, exact=True),
            pytest.raises(DatabaseError, match="commit together"),
            work_transaction(),
        ):
            capture_weekly_snapshot(claim)
        assert not WeeklyDigestSnapshot.objects.exists()


def test_stale_fence_and_premature_phase_jump_are_rejected(response_service):
    add_digest(response_service.service.store, response_service.campaign, weekly=True)
    with campaign_clock(INSTANT):
        claim = allocate()
        with work_transaction(), pytest.raises(TaskOwnershipLost):
            discover_dates(replace(claim, fence=claim.fence + 1))
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            with pytest.raises(DatabaseError), transaction.atomic():
                checkpoint_preparation(claim, phase="cover")
            with pytest.raises(DatabaseError), transaction.atomic():
                checkpoint_preparation(claim, phase="fanout")
            assert checkpoint_preparation(claim, phase="cancelled").phase == "cancelled"


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT observation FROM stewardship_weekly_digest_snapshot",
        "SELECT recipients FROM stewardship_weekly_digest_snapshot",
        "SELECT address FROM stewardship_weekly_digest_recipient",
        "SELECT html FROM stewardship_weekly_digest_recipient",
    ],
)
def test_scheduler_cannot_read_report_or_cohort(statement):
    with (
        task_login(ServiceRole.SCHEDULER, exact=True),
        pytest.raises(DatabaseError) as error,
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
    assert error.value.__cause__.sqlstate == "42501"


@pytest.mark.parametrize("mutation", ["update", "delete"])
def test_snapshot_is_sql_immutable_even_for_database_owner(response_service, mutation):
    with campaign_clock(INSTANT):
        claim = prepare(response_service)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            snapshot = capture_weekly_snapshot(claim)
        with (
            pytest.raises(DatabaseError, match="immutable"),
            connection.cursor() as cursor,
        ):
            if mutation == "update":
                cursor.execute(
                    "UPDATE stewardship_weekly_digest_snapshot "
                    "SET recipients='[]' WHERE id=%s",
                    [snapshot.pk],
                )
            else:
                cursor.execute(
                    "DELETE FROM stewardship_weekly_digest_snapshot WHERE id=%s",
                    [snapshot.pk],
                )
