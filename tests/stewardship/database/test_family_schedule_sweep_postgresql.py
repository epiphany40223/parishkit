"""The Family sweep plans only what changed, at its clock edges, and no more (#640).

Each test keeps one scheduler producer across loops, as the scheduler
process does, under the exact scheduler login, so the two fingerprint
reads run with the role's real grants. The change check runs on every loop
here (CHANGE_CHECK_SECONDS is 0); production reads at most every 15 s.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.campaigns import schedule_production
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    FamilyEligibilityChange,
)
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.family_schedule_planning import PREPARE_AHEAD
from parishkit.stewardship.campaigns.runtime_models import (
    ActivationCatchUpDemand,
    CampaignWorkGate,
)
from parishkit.stewardship.campaigns.schedule_models import (
    RestoreDeliveryHold,
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.schedule_production import FamilyScheduleProducer
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.responses.models import Submission

from ..configuration_factory import PARISH_TIMEZONE
from .auth_builders import unguarded
from .campaign_builders import campaign_clock, complete_empty_catchup
from .credential_builders import populate
from .plan_work import analyze_all, assert_linear_plan
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_auth_postgresql import family_service  # noqa: F401
from .test_family_schedule_planning_postgresql import add_reminders
from .test_outbox_boundaries_postgresql import control
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)
# Roughly the Production parish's Family count (#629).
REALISTIC = 2700


@pytest.fixture(autouse=True)
def every_loop_checks(monkeypatch):
    """Read what changed on every loop rather than every 15 s."""
    monkeypatch.setattr(schedule_production, "CHANGE_CHECK_SECONDS", 0)


@contextmanager
def scheduling():
    """The scheduler's own session under its exact restricted login."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        yield guard


def settle(producer, guard, loops=1000):
    """Loop until a loop plans nothing with nothing queued; return who was planned.

    Planning that writes an occurrence changes its Family's fingerprint, so
    such a Family is planned once more before the sweep goes idle.
    """
    planned = []
    for _ in range(loops):
        results = producer(guard)
        planned += [result.family_id for result in results]
        if not results and not producer.pending:
            return planned
    raise AssertionError("The Family sweep never went idle.")


def statuses(count, **changed):
    """``count`` mailable Families, with ``changed`` DUIDs given other flags."""
    return [
        changed.get(f"duid{n}", FamilyStatus(n, True, True, True, True))
        for n in range(1, count + 1)
    ]


@pytest.mark.parametrize("live", [False, True])
def test_a_response_replans_only_that_family(response_service, live):
    """A Testing or live response re-plans its Family alone, after idling.

    The campaign's first live response also records itself on the campaign
    row (a campaign-wide change), so live mode re-plans everyone once.
    """
    harness = activate_response_service(response_service) if live else response_service
    if live:
        complete_empty_catchup(harness.campaign, uuid4())
    family_id = FamilyCampaign.objects.get(family_duid=1).pk
    everyone = set(FamilyCampaign.objects.values_list("pk", flat=True))
    producer = FamilyScheduleProducer(uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        with scheduling() as guard:
            assert set(settle(producer, guard)) == everyone
            # Idle: nothing changed, so nothing is planned.
            assert producer(guard) == () and producer(guard) == ()
        form, answers = form_and_answers(harness)
        assert submit(harness, form, answers).submission is not None
        with scheduling() as guard:
            assert set(settle(producer, guard)) == (everyone if live else {family_id})
    row = ScheduleOccurrence.objects.get(target=f"family:{family_id}")
    assert row.state == "skipped" and row.reason == "family_responded"


def test_a_pause_replans_every_family(response_service):
    """A delivery pause (a campaign-wide change) re-plans every Family."""
    harness = activate_response_service(response_service)
    complete_empty_catchup(harness.campaign, uuid4())
    everyone = set(FamilyCampaign.objects.values_list("pk", flat=True))
    producer = FamilyScheduleProducer(uuid4())
    with campaign_clock(harness.campaign.active_configuration.starts_at):
        with scheduling() as guard:
            assert set(settle(producer, guard)) == everyone
        control(harness.campaign, "pause")
        with scheduling() as guard:
            assert set(settle(producer, guard)) == everyone
            assert producer(guard) == ()


def test_source_promotions_replan_only_changed_families(family_service):  # noqa: F811
    """A promotion re-plans a Family only when what planning reads changed."""
    campaign, rings = family_service.campaign, family_service.rings
    populate(campaign, rings, statuses(3), generation=2)
    duids = dict(FamilyCampaign.objects.values_list("family_duid", "pk"))
    producer = FamilyScheduleProducer(uuid4())
    with campaign_clock(campaign.active_configuration.starts_at):
        with scheduling() as guard:
            assert set(settle(producer, guard)) == set(duids.values())
        # A new generation with the same eligibility rewrites the rows but
        # changes nothing planning reads.
        populate(campaign, rings, statuses(3), generation=3)
        with scheduling() as guard:
            assert settle(producer, guard) == []
        # One Family becomes undeliverable; one new Family arrives.
        changed = FamilyStatus(2, True, True, True, False)
        populate(campaign, rings, statuses(4, duid2=changed), generation=4)
        newcomer = FamilyCampaign.objects.get(family_duid=4).pk
        with scheduling() as guard:
            assert set(settle(producer, guard)) == {duids[2], newcomer}


def test_schedule_changes_due_times_and_restarts(family_service, auth_service):  # noqa: F811
    """A schedule edit plans everyone; so does each due time, once; and a restart."""
    campaign, actor = family_service.campaign, uuid4()
    populate(campaign, family_service.rings, statuses(3), generation=2)
    everyone = set(FamilyCampaign.objects.values_list("pk", flat=True))
    initial = ScheduleDefinition.objects.get().current_revision.due_at
    producer = FamilyScheduleProducer(uuid4())
    second = timedelta(seconds=1)
    with campaign_clock(initial - second), scheduling() as guard:
        assert set(settle(producer, guard)) == everyone
        assert producer.wake == initial
    reminders = add_reminders(auth_service.store, campaign, actor)
    dues = sorted(
        ScheduleDefinition.objects.get(pk=UUID(row["id"])).current_revision.due_at
        for row in reminders
    )
    with campaign_clock(initial - second), scheduling() as guard:
        # The edit queued every Family, though none is due yet.
        assert set(settle(producer, guard)) == everyone
        assert not ScheduleOccurrence.objects.exists()
    with campaign_clock(initial), scheduling() as guard:
        # The invitation's due time: everyone, and once more for each
        # Family whose new occurrence moved its fingerprint.
        assert set(settle(producer, guard)) == everyone
        assert ScheduleOccurrence.objects.count() == len(everyone)
        assert producer.wake == dues[0]
    with campaign_clock(dues[0] - second), scheduling() as guard:
        assert settle(producer, guard) == []
    with campaign_clock(dues[0]), scheduling() as guard:
        assert set(settle(producer, guard)) == everyone
    count = ScheduleOccurrence.objects.count()
    # A restart has no memory: it plans everyone again, idempotently.
    with campaign_clock(dues[0]), scheduling() as guard:
        restarted = FamilyScheduleProducer(uuid4())
        assert set(settle(restarted, guard)) == everyone
        assert settle(restarted, guard) == []
    assert ScheduleOccurrence.objects.count() == count


def test_a_realistic_population_goes_idle_cheaply(family_service):  # noqa: F811
    """With ~2,700 Families, occurrences and fulfillments, an idle loop plans none.

    Planning every Family is what the other tests here prove; this one
    measures the idle check at scale, so it seeds one occurrence and one
    fulfillment per Family in bulk and starts the producer as if its first
    pass had just finished (every Family known, none queued). A real
    eligibility change then re-plans exactly one Family through the real
    sweep.
    """
    campaign, rings = family_service.campaign, family_service.rings
    populate(campaign, rings, statuses(REALISTIC), generation=2)
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    revision = definition.current_revision
    families = list(FamilyCampaign.objects.values_list("pk", flat=True))
    assert len(families) == REALISTIC
    with unguarded():
        ScheduleOccurrence.objects.bulk_create(
            ScheduleOccurrence(
                definition=definition,
                revision=revision,
                mode="testing",
                routing="testing_override",
                target=f"family:{family_id}",
                slot="once",
                due_at=revision.due_at,
                occurrence_key=uuid4().hex,
            )
            for family_id in families
        )
        occurrences = ScheduleOccurrence.objects.filter(definition=definition)
        ScheduleFulfillment.objects.bulk_create(
            ScheduleFulfillment(
                definition=definition,
                mode="testing",
                target=row.target,
                slot="once",
                disposition="coalesced",
                occurrence=row,
            )
            for row in occurrences
        )
    producer = FamilyScheduleProducer(uuid4())
    with campaign_clock(campaign.active_configuration.starts_at):
        with scheduling() as guard:
            producer(guard)
            # The first loop queued every Family and planned its first page;
            # consider the rest planned too.
            producer.pending.clear()
            started = monotonic()
            with CaptureQueriesContext(connection) as idle:
                for _ in range(10):
                    assert producer(guard) == ()
            elapsed = monotonic() - started
        # A handful of statements a loop, never one per Family: the runtime
        # row, the two fingerprints and (in Testing) the rehearsal-epoch
        # check. No planning and no work-order lock. (The ownership probe
        # runs on the scheduler's own raw cursor and is not captured.)
        assert len(idle) <= 10 * 5
        assert not any("pg_advisory_xact_lock" in row["sql"] for row in idle)
        assert not any("FOR UPDATE" in row["sql"] for row in idle)
        # Only a catastrophic-regression guard: about 14 ms a check on a
        # laptop, but CI packs three PostgreSQL partitions onto each runner
        # (#651), so wall-clock time here is shared-CPU noise (#688). The
        # statement count and lock assertions above, the plan's row count
        # below and test_schedule_production's idle scaling ratio (the
        # Python comparison) are the real checks (#690).
        assert elapsed < 60
        # The per-Family read stays linear in the population: one hash or
        # merge pass per input table, never a nested loop that rescans an
        # input for every Family.
        analyze_all()
        visited = assert_linear_plan(
            schedule_production.FAMILY_INPUTS,
            {"campaign": campaign.pk},
            REALISTIC,
            per_row=60,
        )
        print(f"FAMILY_INPUTS rows visited at {REALISTIC} Families: {visited}")
        middle = REALISTIC // 2
        changed = FamilyStatus(middle, True, True, True, False)
        populate(
            campaign,
            rings,
            statuses(REALISTIC, **{f"duid{middle}": changed}),
            generation=3,
        )
        expected = FamilyCampaign.objects.get(family_duid=middle).pk
        with scheduling() as guard:
            assert set(settle(producer, guard)) == {expected}


def seeded(statement, values=()):
    """Run one seeded change as the owner, past the SQL guards."""
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(statement, values)


def test_every_fingerprint_input_moves_its_fingerprint(family_service):  # noqa: F811
    """One row change in each input table changes the fingerprint that reads it."""
    campaign = family_service.campaign
    family_id = FamilyCampaign.objects.get().pk
    definition = ScheduleDefinition.objects.get()
    target = f"family:{family_id}"
    with campaign_clock(definition.current_revision.due_at), scheduling() as guard:
        settle(FamilyScheduleProducer(uuid4()), guard)
    occurrence = ScheduleOccurrence.objects.get()
    start = campaign.active_configuration.starts_at

    def hold():
        """A restore hold on the Family's invitation."""
        with unguarded():
            RestoreDeliveryHold.objects.create(
                restore_id=uuid4(),
                definition=definition,
                mode="testing",
                target=target,
                slot="once",
                backup_at=start,
                window_start=start,
                window_end=start + timedelta(days=1),
                discovery="inventory",
            )

    def fulfillment():
        """A fulfillment of the Family's invitation."""
        with unguarded():
            ScheduleFulfillment.objects.create(
                definition=definition,
                mode="testing",
                target=target,
                slot="once",
                disposition="coalesced",
                occurrence=occurrence,
            )

    def history():
        """Eligibility history alone, with the Family row untouched."""
        with unguarded():
            FamilyEligibilityChange.objects.create(
                family_id=family_id,
                source_generation=99,
                family_version=999,
                active=True,
                portal_eligible=True,
                email_eligible=True,
                email_deliverable=True,
                status_reason="active",
                deliverability_reason="",
            )

    def gate():
        """A work gate (a purge reservation) on the campaign."""
        with unguarded():
            CampaignWorkGate.objects.create(campaign=campaign, request_id=uuid4())

    def demand():
        """An unfinished activation catch-up demand."""
        with unguarded():
            ActivationCatchUpDemand.objects.create(
                campaign=campaign,
                activation_id=uuid4(),
                cutoff=start,
                configuration_id=uuid4(),
            )

    def submission():
        """A Testing response row (only its existence is read)."""
        with unguarded():
            Submission.objects.create(
                family_id=family_id,
                campaign=campaign,
                campaign_sequence=99,
                baseline_id=uuid4(),
                reviewed_source_id=uuid4(),
                validation_source_id=uuid4(),
                configuration_id=uuid4(),
                mode="test",
                family_version=1,
                submitted_at=start,
                submitted_on=start.date(),
                form_schema="synthetic",
                answers={},
            )

    one = {
        "campaign": campaign.pk,
        "occurrence": occurrence.pk,
        "family": family_id,
    }
    changes = [
        (
            "family",
            "occurrence version",
            lambda: seeded(
                "UPDATE stewardship_schedule_occurrence SET version=version+1"
                " WHERE id=%(occurrence)s",
                one,
            ),
        ),
        ("family", "restore hold", hold),
        (
            "family",
            "restore hold state",
            lambda: seeded(
                "UPDATE stewardship_restore_delivery_hold SET version=version+1,"
                " state='assumed_delivered'"
            ),
        ),
        ("family", "fulfillment", fulfillment),
        ("family", "eligibility history", history),
        (
            "campaign",
            "go-live gate",
            lambda: seeded(
                "UPDATE stewardship_campaign_credentials SET go_live_gate=true"
                " WHERE campaign_id=%(campaign)s",
                one,
            ),
        ),
        (
            "campaign",
            "rehearsal pointer",
            lambda: seeded(
                "UPDATE stewardship_campaign_credentials SET rehearsal_epoch_id=NULL"
                " WHERE campaign_id=%(campaign)s",
                one,
            ),
        ),
        (
            "campaign",
            "rehearsal epoch",
            lambda: seeded(
                "UPDATE stewardship_rehearsal_epoch SET state='invalidated',"
                " invalidated_at=now() WHERE campaign_id=%(campaign)s",
                one,
            ),
        ),
        ("campaign", "work gate", gate),
        (
            "campaign",
            "work gate state",
            lambda: seeded(
                "UPDATE stewardship_campaign_work_gate SET state='released',"
                " version=version+1 WHERE campaign_id=%(campaign)s",
                one,
            ),
        ),
        ("campaign", "catch-up demand", demand),
        (
            "campaign",
            "catch-up completion",
            lambda: seeded(
                "UPDATE stewardship_activation_catchup SET completed_at=now(),"
                " phase='succeeded' WHERE campaign_id=%(campaign)s",
                one,
            ),
        ),
        (
            "campaign",
            "schedule definition",
            lambda: seeded(
                "UPDATE stewardship_schedule_definition SET version=version+1"
                " WHERE campaign_id=%(campaign)s",
                one,
            ),
        ),
        (
            "campaign",
            "campaign row",
            lambda: seeded(
                "UPDATE stewardship_campaign SET version=version+1"
                " WHERE id=%(campaign)s",
                one,
            ),
        ),
    ]

    def read():
        """Both fingerprints, as the scheduler reads them."""
        with task_login(ServiceRole.SCHEDULER, exact=True):
            runtime = schedule_production._runtime()
            inputs = schedule_production._campaign_inputs(campaign.pk, ahead=False)
            families = schedule_production._family_inputs(campaign.pk)
        return {
            "runtime": runtime["version"],
            "campaign": inputs[1],
            "family": families[family_id],
        }

    before = read()
    for which, name, change in changes:
        change()
        after = read()
        assert after[which] != before[which], name
        before = after


@pytest.mark.parametrize("fold", [0, 1])
def test_clock_edges_are_absolute_across_a_daylight_saving_change(
    family_service,  # noqa: F811
    auth_service,
    fold,
):
    """The edges are the revision's absolute instants, even in a repeated hour.

    What this proves: _campaign_inputs returns a reminder's stored UTC due
    time as an edge, and its lead-window start exactly PREPARE_AHEAD earlier
    in absolute time, for either copy of a repeated local hour. Planning
    itself is not run (the fixture campaign ends before the November change,
    so the revision is seeded directly).
    """
    campaign = family_service.campaign
    reminder = add_reminders(auth_service.store, campaign, uuid4())[0]
    revision = ScheduleDefinition.objects.get(pk=UUID(reminder["id"])).current_revision
    zone = ZoneInfo(PARISH_TIMEZONE)
    sunday = datetime(2054, 11, 1)
    sunday += timedelta(days=(6 - sunday.weekday()) % 7)
    due = sunday.replace(hour=1, minute=30, fold=fold, tzinfo=zone).astimezone(UTC)
    seeded(
        "UPDATE stewardship_schedule_revision SET due_at=%s WHERE id=%s",
        [due, revision.pk],
    )
    with task_login(ServiceRole.SCHEDULER, exact=True):
        _, _, ahead = schedule_production._campaign_inputs(campaign.pk, ahead=True)
        _, _, plain = schedule_production._campaign_inputs(campaign.pk, ahead=False)
    assert due in plain and due - PREPARE_AHEAD not in plain
    assert {due, due - PREPARE_AHEAD} <= set(ahead)
    # The two copies of 01:30 are an hour apart in UTC.
    other = sunday.replace(hour=1, minute=30, fold=1 - fold, tzinfo=zone)
    assert abs(other.astimezone(UTC) - due) == timedelta(hours=1)
