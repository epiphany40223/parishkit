"""The local seeder's settled conditions and configuration patch on real storage.

No fake clock here (the seed tests proper are a documented VM run): these
prove the settled and fatal conditions read the real task, outbox and
occurrence rows as the specification states them, that pending configuration
requests are seen until installed, and that the seeder's one configuration
patch (campaign dates, the Initial moved, Reminders replaced) is accepted by
the real request and installation path and lands as schedules.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from django.db import transaction

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import Campaign, ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.storage import change_run
from parishkit.stewardship.local import seed_timeline, seeder

from .campaign_builders import (
    advance,
    campaign_clock,
    change,
    claimed_task,
    draft_campaign,
    occurrence,
)
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_setup_staging_postgresql import setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


def inside_window(campaign):
    """An instant inside the fixture campaign's dates, whatever the real date.

    Occurrence creation is admitted only while the campaign clock lies within
    the campaign; CI runs the PostgreSQL shards on the fixture calendar (the day
    before the shared fixture campaign, issue #421), so the tests that create
    occurrences pin the campaign clock here instead of trusting today's date.
    """
    return campaign.active_configuration.starts_at + timedelta(hours=1)


def test_settled_conditions_follow_real_task_and_occurrence_states(tmp_path):
    """Queued or claimed tasks and due pending occurrences are unsettled; done not."""
    store, campaign, actor = draft_campaign(tmp_path)
    with transaction.atomic():
        now = seeder.database_now()
    assert seeder.unsettled_conditions(now, LONG_AGO) == []
    assert seeder.fatal_conditions(LONG_AGO) == []
    # A claimed (running) task of a type routed to a worker queue.
    run = claimed_task("report_export", uuid4(), actor)
    reasons = seeder.unsettled_conditions(now + timedelta(seconds=1), LONG_AGO)
    assert reasons == ["1 task(s) queued, running or due for retry"]
    # Rows created before ``since`` are never waited on.
    assert seeder.unsettled_conditions(now, now + timedelta(hours=1)) == []
    # A due occurrence that is still pending.
    definition = ScheduleDefinition.objects.get(campaign=campaign)
    with campaign_clock(inside_window(campaign)):
        row = occurrence(definition, actor, due_at=now - timedelta(minutes=1))
        # A future occurrence is not due and so not waited on.
        future = occurrence(
            definition, actor, target="family:2", due_at=now + timedelta(days=1)
        )
    reasons = seeder.unsettled_conditions(now + timedelta(seconds=1), LONG_AGO)
    assert "1 due occurrence(s) not yet succeeded or skipped" in reasons
    assert future.state == "pending"
    assert sum("occurrence" in r for r in reasons) == 1
    # Skipped is settled; failed is fatal.
    advance(row, actor, "skipped", reason="family_submitted")
    reasons = seeder.unsettled_conditions(now + timedelta(seconds=1), LONG_AGO)
    assert not any("occurrence" in r for r in reasons)
    assert seeder.fatal_conditions(LONG_AGO) == []
    # A task that fails for good is fatal, and no longer unsettled.
    fail_task(run)
    assert seeder.fatal_conditions(LONG_AGO) == ["task in failed"]
    assert seeder.unsettled_conditions(now + timedelta(seconds=1), LONG_AGO) == []


def fail_task(run):
    """Move a claimed task to failed through the real fenced transition."""
    with work_transaction():
        change_run(
            run_id=run.run_id,
            action="permanent_failure",
            expected_version=run.version,
            actor_id=run.worker_id,
            fence=run.fence,
            correlation_id=uuid4(),
            admit=lambda *args: True,
        )


def test_tolerated_occurrence_states_are_neither_fatal_nor_unsettled(tmp_path):
    """Phase 4's coalesced recovery rows are final: tolerated in both checks."""
    store, campaign, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.get(campaign=campaign)
    with transaction.atomic():
        now = seeder.database_now()
    with campaign_clock(inside_window(campaign)):
        kept = occurrence(
            definition, actor, target="family:1", due_at=now - timedelta(hours=1)
        )
        folded = occurrence(
            definition, actor, target="family:2", due_at=now - timedelta(hours=1)
        )
    advance(
        folded,
        actor,
        "coalesced",
        replacement_id=kept.pk,
        reason="missed_family_recovery",
    )
    later = now + timedelta(seconds=1)
    tolerate = frozenset({"coalesced"})
    assert seeder.fatal_conditions(LONG_AGO) == ["occurrence in coalesced"]
    assert seeder.fatal_conditions(LONG_AGO, tolerate=tolerate) == []
    # The kept (pending, due) occurrence is unsettled either way; the coalesced
    # one counts only without the tolerance.
    assert (
        "2 due occurrence(s) not yet succeeded or skipped"
        in seeder.unsettled_conditions(later, LONG_AGO)
    )
    assert (
        "1 due occurrence(s) not yet succeeded or skipped"
        in seeder.unsettled_conditions(later, LONG_AGO, tolerate=tolerate)
    )
    advance(kept, actor, "skipped", reason="family_submitted")
    assert not any(
        "occurrence" in r
        for r in seeder.unsettled_conditions(later, LONG_AGO, tolerate=tolerate)
    )


def test_occurrence_evidence_counts_the_planned_families(tmp_path):
    """Evidence needs every eligible Family planned at the instant, not one row."""
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.local.seed_timeline import Event

    store, campaign, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.get(campaign=campaign)
    at = definition.current_revision.due_at
    # No eligible Families yet: zero expected is met at once.
    assert (
        seeder.occurrence_evidence(Event(at, "initial"), campaign.pk, "UTC")() is True
    )
    for duid in (1, 2):
        FamilyCampaign.objects.create(
            campaign=campaign,
            family_duid=duid,
            active=True,
            portal_eligible=True,
            email_eligible=True,
            email_deliverable=True,
            status_reason="seed_test",
            deliverability_reason="seed_test",
            code_ciphertext="seed-test-ciphertext",
            first_eligible_at=at,
            first_eligible_source_generation=1,
            eligibility_changed_at=at,
            source_generation=1,
            actor_id=actor,
            correlation_id=uuid4(),
        )
    # A draft campaign admits only Testing-mode occurrences, so the test counts
    # that mode; the seed counts production ones.
    condition = seeder.occurrence_evidence(
        Event(at, "initial"), campaign.pk, "UTC", mode="testing"
    )
    instant = at.isoformat(timespec="seconds")
    assert condition() == f"occurrences due at {instant} (0 of 2 Families planned)"
    with campaign_clock(inside_window(campaign)):
        occurrence(definition, actor, target="family:1", due_at=at)
    assert "1 of 2" in condition()
    # Another mode's occurrence does not count toward this one.
    assert (
        "0 of 2"
        in seeder.occurrence_evidence(Event(at, "initial"), campaign.pk, "UTC")()
    )
    with campaign_clock(inside_window(campaign)):
        occurrence(definition, actor, target="family:2", due_at=at)
    assert condition() is True


def test_task_root_finished_follows_the_chain_to_success_or_failure(tmp_path):
    """Phase 1 waits on the refresh's own task root, whatever created it."""
    store, campaign, actor = draft_campaign(tmp_path)
    assert seeder.task_root_finished(uuid4(), "the full refresh") == (
        "the full refresh to be recorded"
    )
    run = claimed_task("report_export", uuid4(), actor)
    assert seeder.task_root_finished(run.root_id, "the full refresh") == (
        "the full refresh (running)"
    )
    with work_transaction():
        change_run(
            run_id=run.run_id,
            action="complete",
            expected_version=run.version,
            actor_id=run.worker_id,
            fence=run.fence,
            correlation_id=uuid4(),
            admit=lambda *args: True,
        )
    assert seeder.task_root_finished(run.root_id, "the full refresh") is True
    failed = claimed_task("report_facts", uuid4(), actor)
    fail_task(failed)
    with pytest.raises(seeder.SeedFailed, match="ended in failed"):
        seeder.task_root_finished(failed.root_id, "the full refresh")


def test_settle_raises_at_once_on_a_fatal_condition(tmp_path):
    store, campaign, actor = draft_campaign(tmp_path)
    fail_task(claimed_task("report_export", uuid4(), actor))
    slept = []
    with pytest.raises(seeder.SeedFailed, match="fatal work state"):
        seeder.settle(LONG_AGO, limit=5, sleep=slept.append)
    assert slept == []


def test_pending_configuration_requests_count_until_installed(tmp_path):
    store, campaign, actor = draft_campaign(tmp_path)
    assert seeder.pending_configuration_requests(LONG_AGO) == 0
    result = change(
        store,
        store.active(),
        actor,
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": {"name": "Renamed campaign"},
            }
        ],
    )
    assert result.state == "applied"
    assert seeder.pending_configuration_requests(LONG_AGO) == 0


def test_schedule_patch_installs_through_the_real_configuration_path(tmp_path):
    """The one patch the seeder records is valid and lands as real schedules."""
    store, campaign, actor = draft_campaign(tmp_path)
    cal = seed_timeline.calendar(
        datetime(2026, 10, 7, 15, 30, tzinfo=UTC),
        campaign.active_configuration.timezone,
    )
    active = store.active()
    patch = seeder.schedule_patch(active.document(), campaign.pk, cal)
    result = change(store, active, actor, patch)
    assert result.state == "applied", result
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign.pk
    )
    assert campaign.active_configuration.start_date == cal.start
    assert campaign.active_configuration.end_date == cal.end
    schedules = store.active().document()["sections"]["schedules"]
    kinds = sorted(row["values"]["kind"] for row in schedules)
    assert kinds == ["initial"] + ["reminder"] * 8
    initial = next(row for row in schedules if row["values"]["kind"] == "initial")
    assert initial["values"]["date"] == cal.start.isoformat()
    assert initial["values"]["time"] == "10:00:00"
    reminder_dates = sorted(
        row["values"]["date"]
        for row in schedules
        if row["values"]["kind"] == "reminder"
    )
    assert reminder_dates == [r.date().isoformat() for r in cal.reminders]
    assert ScheduleDefinition.objects.filter(campaign=campaign).count() == 9


def test_wizard_campaign_values_are_accepted_by_the_real_draft_owner(
    setup_service, monkeypatch, tmp_path, settings
):
    """The unattended wizard's post-load steps save and compile through the owners.

    The load itself, the Workspace credential (LOCAL's mail-catcher document
    is refused under the test profile) and the sample mail need the running
    services, so this uses the test suite's completed load and stops after the
    campaign steps: campaign values, default content, the Initial schedule and
    the logo are all admitted by the real draft owner.
    """
    from parishkit.stewardship.accounts.setup_drafts import save_sections, view_draft
    from parishkit.stewardship.local import seed_web

    from .test_runtime_auth_grants_postgresql import web_login
    from .test_setup_campaign_postgresql import completed

    media = tmp_path / "media"
    media.mkdir(mode=0o700)
    settings.STEWARDSHIP_MEDIA_ROOT = media
    request, attempt = completed(setup_service, monkeypatch)
    # Campaign dates relative to the database's date, never a fixed calendar.
    with transaction.atomic():
        today = seeder.database_now().date()
    with web_login():
        # The suite's completed load saved only the parish; the preview needs
        # the other public sections, with the unattended wizard's own values.
        status = save_sections(
            request,
            setup_service,
            attempt.pk,
            updates={
                "access": seed_web.WIZARD_ACCESS,
                "mail": seed_web.WIZARD_MAIL,
                "slack": seed_web.WIZARD_SLACK,
                "testing": seed_web.WIZARD_TESTING,
            },
            expected_version=attempt.version,
        )
        status = seed_web.wizard_campaign(
            request,
            setup_service,
            attempt.pk,
            campaign_dates=(today + timedelta(days=6), today + timedelta(days=36)),
        )
        draft = view_draft(request, setup_service, attempt.pk)
        assert draft.status.version == status.version
        campaign = draft.sections["campaign"]["campaign"]
        assert campaign["modules"] == ["census", "financial", "ministry"]
        assert campaign["financial"]["fund_duids"] == [9]
        assert len(campaign["share_options"]) >= 1
        assert draft.sections["schedules"]["records"][0]["values"]["kind"] == "initial"
        assert "email_initial" in draft.sections and "page_welcome" in draft.sections
        assert draft.sections["branding"]["bundle_id"]


def test_invariant_do_block_runs_against_the_real_schema(tmp_path):
    """Every table and column the check names exists; an empty database passes.

    A row whose timestamp is later than the seeded now (an audit event written
    after it, the "deliberately corrupted row") makes it raise, as
    does a count the data cannot satisfy: the self-verifying shape.
    """
    from datetime import date

    from django.db import connection

    now = datetime(2026, 10, 4, 5, 24, 38, tzinfo=UTC)
    with connection.cursor() as cursor:
        cursor.execute(seeder.invariant_sql(now, {"submission": 0, "midnight": 0}))
        cursor.execute(
            seeder.invariant_sql(now, {"midnight": 0}, start=date(2026, 9, 26))
        )
    with pytest.raises(Exception, match="live submissions"), connection.cursor() as c:
        c.execute(seeder.invariant_sql(now, {"submission": 3, "midnight": 0}))
    with pytest.raises(Exception, match="daily-fact row"), connection.cursor() as c:
        c.execute(seeder.invariant_sql(now, {"midnight": 2}, start=date(2026, 9, 26)))
    # The corrupted row: an audit event (one of the checked tables) written
    # after the seeded now.
    with transaction.atomic():
        later = seeder.database_now()
    AuditEvent.objects.create(
        event_type="admin_login", actor_id=uuid4(), subject_id=uuid4()
    )
    with (
        pytest.raises(Exception, match="later than the seeded now"),
        connection.cursor() as c,
    ):
        c.execute(seeder.invariant_sql(later - timedelta(minutes=5), {"midnight": 0}))
