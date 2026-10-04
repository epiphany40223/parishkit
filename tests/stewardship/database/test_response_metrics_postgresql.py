"""Response funnel metrics against PostgreSQL (#477): real paths, read as the web.

Five eligible Families go through the real paths that leave the durable
evidence the funnel reads: rehearsal and live sign-ins (the engagement
record), form issuance and presence heartbeats, final submissions, the real
scheduler and preparation worker, and the restricted dispatch owner that
delivers, or skips for an already-responded Family, each invitation and
reminder. The metrics are then read under the restricted web login in a
read-only snapshot and checked stage by stage, at as-of cutoffs between the
steps, in one rehearsal against Production and against another epoch, and
again after later activity at the same cutoff.

Submissions are dated by the campaign clock, so each is submitted with that
clock pinned to the database's real instant; every instant the funnel reads
then lies on one timeline and the cutoffs between steps are exact. For that
instant to fall inside the campaign (the shared fixture campaign starts on
2054-10-01, far after the real date; issue #421), the campaign's dates are
derived from the database clock rather than taken from the shared fixture.
"""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import connection

from parishkit.stewardship.accounts.family_authentication import FamilyRuntime
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.campaigns.credential_keys import initialize_key_inventories
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.family_identity import (
    code_context as live_code_context,
)
from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.rehearsals import code_context, prepare_rehearsals
from parishkit.stewardship.campaigns.schedule_models import (
    OccurrenceTransition,
    ScheduleDefinition,
)
from parishkit.stewardship.campaigns.schedule_production import FamilyScheduleProducer
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.family_mail_dispatch import (
    begin_submission,
    finish_submission,
)
from parishkit.stewardship.jobs.family_mail_models import FamilyMailPreparation
from parishkit.stewardship.jobs.family_mail_tasks import TASK_TYPE as PREPARE
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.reports.response_metrics import (
    LINK_FOLLOWED_NOTE,
    STAGES,
    ResponseScope,
    response_metrics,
)
from parishkit.stewardship.responses.baselines import issue_baseline
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease

from ..campaign_factory import campaign as campaign_record
from ..campaign_factory import schedule
from ..configuration_factory import PARISH_TIMEZONE
from ..content_factory import content
from .campaign_builders import campaign_clock, change, complete_empty_catchup
from .credential_builders import keys
from .response_builders import (
    ResponseHarness,
    activate_response_service,
    response_source,
)
from .test_background_grants_postgresql import task_login
from .test_family_auth_postgresql import login
from .test_family_engagement_postgresql import age_presence, beat
from .test_family_mail_dispatch_postgresql import claim
from .test_family_mail_preparation_postgresql import handler
from .test_response_submission_postgresql import form_and_answers, submit
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)

# The corpus Family (DUID 1) plus four more eligible Families; DUID 2 is the
# corpus' memberless Family, which is never Portal-eligible.
EXTRA = (11, 12, 13, 14)
EVERYONE = (1, *EXTRA)
# The campaign starts this many days before the database's local date, so the
# invitation (start, 09:00) and the reminder (the day after) both fall due
# before the real instant the submissions are dated by, and the campaign is
# open at that instant; it ends well after it.
START_DAYS_AGO = 2
END_DAYS_AHEAD = 20


def campaign_dates():
    """(start, reminder, end) dates around the database's current parish date."""
    today = database_now().astimezone(ZoneInfo(PARISH_TIMEZONE)).date()
    start = today - timedelta(days=START_DAYS_AGO)
    return start, start + timedelta(days=1), today + timedelta(days=END_DAYS_AHEAD)


def funnel_source():
    """The corpus with four more eligible Families, each one active head."""
    data = response_source()
    for duid in EXTRA:
        data.families[duid] = dict(
            data.families[1],
            familyDUID=duid,
            familyID=100 + duid,
            lastName=f"Family{duid}",
            eMailAddress=f"family{duid}@example.org",
        )
        data.members[100 + duid] = dict(
            data.members[3],
            memberDUID=100 + duid,
            familyDUID=duid,
            firstName=f"Head{duid}",
            emailAddress=f"head{duid}@example.org",
        )
    return data


@pytest.fixture
def funnel(auth_service, settings):
    """A Testing campaign of five eligible Families, an invitation and a reminder.

    As ``response_service``, but dated from the database clock (see
    ``campaign_dates``), with the larger source, the invitation content
    ``family_mail`` adds and one reminder the day after the start, signed in
    as the corpus Family's rehearsal credential. Yields the harness and the
    rehearsal epoch.
    """
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    store = auth_service.store
    start, reminder_date, end = campaign_dates()
    # As add_draft, with the invitation on the campaign's own start date.
    row = campaign_record(start_date=start.isoformat(), end_date=end.isoformat())
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "campaigns", **row},
                {
                    "operation": "add",
                    "section": "schedules",
                    **schedule(row["id"], date=start.isoformat()),
                },
            ],
        ).state
        == "applied"
    )
    campaign = Campaign.objects.get(pk=UUID(row["id"]))
    definition = ScheduleDefinition.objects.get()
    template = content(
        str(campaign.pk),
        kind="email",
        slot="initial",
        html="<p>Hello {{ family_name }}: {{ family_code }} {{ family_url }}</p>",
        text="Hello {{ family_name }}: {{ family_code }} {{ family_url }}",
    )
    reminder = content(
        str(campaign.pk),
        kind="email",
        slot="reminder",
        html="<p>Reminder {{ family_name }}: {{ family_code }} {{ family_url }}</p>",
        text="Reminder {{ family_name }}: {{ family_code }} {{ family_url }}",
    )
    patch = [
        {"operation": "add", "section": "content", **template},
        {"operation": "add", "section": "content", **reminder},
        {
            "operation": "add",
            "section": "schedules",
            **schedule(
                str(campaign.pk),
                kind="reminder",
                date=reminder_date.isoformat(),
                template_version=reminder["id"],
                subject=reminder["values"]["subject"],
            ),
        },
        {
            "operation": "update",
            "section": "schedules",
            "id": str(definition.pk),
            "values": {
                "template_version": template["id"],
                "subject": template["values"]["subject"],
            },
        },
    ]
    if not any(
        item["values"]["kind"] == "email"
        for item in store.active().document()["sections"]["integrations"]
    ):
        patch.append(
            {
                "operation": "add",
                "section": "integrations",
                "id": str(uuid4()),
                "values": {
                    "kind": "email",
                    "settings": {
                        "sender": "sender@example.org",
                        "reply_to": "reply@example.org",
                    },
                    "credential_fingerprint": None,
                },
            }
        )
    assert change(store, store.active(), uuid4(), patch).state == "applied"
    campaign.refresh_from_db()
    rings = keys()
    snapshot, claim_ = prepare(funnel_source())
    snapshot = promote(snapshot, claim_, campaign, rings)
    service = FamilyRuntime(
        store, auth_service.limiter, rings.general, rings.mac, rings.public
    )
    settings.STEWARDSHIP_FAMILY_RUNTIME = service
    settings.STEWARDSHIP_PUBLIC_ORIGIN = "https://parish.example.org"
    with campaign_clock(campaign.active_configuration.starts_at):
        prepare_rehearsals(
            campaign_id=campaign.pk,
            family_ids=list(
                FamilyCampaign.objects.filter(portal_eligible=True).values_list(
                    "pk", flat=True
                )
            ),
            general=rings.general,
            mac=rings.mac,
            public=rings.public,
            purpose=CampaignWorkKind.REHEARSAL,
            admit=lambda *args: True,
        )
        credential = RehearsalCredential.objects.get(family__family_duid=1)
        code = rings.general.decrypt(
            credential.code_ciphertext, context=code_context(credential.pk)
        ).decode()
        client, response = login(code)
        assert response.status_code == 302
        yield (
            ResponseHarness(
                campaign, rings, service, snapshot, client, response.wsgi_request, code
            ),
            credential.epoch_id,
        )


def live_login(harness, duid):
    """Sign one Family in with its live code, as its personal link would."""
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=duid)
    code = harness.rings.general.decrypt(
        family.code_ciphertext, context=live_code_context(family.pk)
    ).decode()
    client, response = login(code)
    assert response.status_code == 302
    return replace(harness, code=code, client=client, request=response.wsgi_request)


def open_form(harness):
    """Issue the Family's form, as the first form page does."""
    assert issue_baseline(harness.request, harness.service).baseline.state == "open"


def progress_to(harness, section):
    """Heartbeats from the first step to ``section``, past the 30-second bound."""
    assert beat(harness.client, "welcome").status_code == 200
    if section != "welcome":
        age_presence(31)
        assert beat(harness.client, section).status_code == 200


def respond(harness):
    """One real final submission, dated by the campaign clock at this instant."""
    with campaign_clock(database_now()):
        form, answers = form_and_answers(harness)
        assert submit(harness, form, answers).submission is not None


def prepare_all(harness, mode, purpose="initial"):
    """Plan and prepare every owed email with the real scheduler and worker.

    Returns the ``purpose`` messages of ``mode`` prepared by this call, by
    Family. The campaign clock decides which schedules are due.
    """
    initialize_key_inventories(harness.rings.private)
    before = set(FamilyMailPreparation.objects.values_list("pk", flat=True))
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        FamilyScheduleProducer(uuid4())(guard)
    owner = handler(harness)
    tickets = FamilyMailPreparation.objects.exclude(pk__in=before)
    with task_login(ServiceRole.WORKER, exact=True):
        for ticket in tickets:
            execution = claim_hint(
                ticket.task_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={PREPARE: owner},
            )
            with maintain_execution(execution):
                owner.execute(execution)
    return {
        message.family_id: message
        for message in OutboxMessage.objects.filter(
            purpose=purpose,
            mode=mode,
            semantic_key__in=tickets.values_list("occurrence_id", flat=True),
        )
    }


def dispatch(harness, message):
    """Hand one prepared email to the real dispatch owner; accepted if sent.

    Returns the message's final state: delivered, or cancelled when the
    owner found the Family had already responded.
    """
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        started = begin_submission(
            message.pk,
            execution.claim,
            private=harness.rings.private,
            public_origin="http://localhost:8000",
        )
        if started is not None:
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
            )
    message.refresh_from_db()
    return message.state


def dispatch_all(harness, messages, families, due):
    """Dispatch every Family's message an hour after ``due``, by DUID.

    The campaign clock is pinned there, not at the real instant, so the
    owner's own recheck of overdue reminders sees the same schedule state on
    every run; the delivery instants still come from the database clock.
    Returns the final states.
    """
    with campaign_clock(due + timedelta(hours=1)):
        return {
            duid: dispatch(harness, messages[families[duid]])
            for duid in sorted(families)
            if families[duid] in messages
        }


def read(harness, as_of, mode="production", epoch=None, **options):
    """Read the funnel as the report page will: web login, read-only snapshot."""
    scope = ResponseScope(harness.campaign.pk, mode, epoch)
    with task_login(ServiceRole.WEB, exact=True), read_transaction():
        metrics = response_metrics(scope, as_of, **options)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND locktype='advisory'"
            )
            assert cursor.fetchone()[0] == 0
    return metrics


def counts(metrics):
    """The five stage counts plus the three separate figures, as one tuple.

    (invited, link followed, form opened, progressed, submitted; skipped
    because already responded, submitted without a delivered invitation,
    submitted more than once).
    """
    return (
        *(stage.count for stage in metrics.stages),
        metrics.skipped_responded,
        metrics.submitted_uninvited,
        metrics.submitted_again,
    )


def marks(metrics):
    """The send markers as (kind, name, delivered) tuples."""
    return [(send.kind, send.name, send.delivered) for send in metrics.sends]


def by_duid(metrics):
    """The per-Family rows keyed by DUID."""
    return {family.family_duid: family for family in metrics.families}


def skipped_for(message):
    """Whether the real owner skipped the message's occurrence as already responded."""
    return OccurrenceTransition.objects.filter(
        occurrence_id=message.semantic_key,
        after_state="skipped",
        reason="family_responded",
    ).exists()


def test_funnel_from_real_paths_as_of_cutoffs_modes_and_parity(funnel):
    """The whole funnel, read as the web, from the evidence the real paths leave."""
    harness, epoch = funnel
    families = {
        duid: pk
        for duid, pk in FamilyCampaign.objects.values_list("family_duid", "pk")
        if duid in EVERYONE
    }
    initial = ScheduleDefinition.objects.get(kind="initial")
    due = initial.current_revision.due_at

    # Testing: the rehearsal's invitations are prepared, the corpus Family
    # responds before dispatch (so its invitation is skipped) and the other
    # four are delivered. All of it is this rehearsal's funnel: Production
    # sees none of it, and neither does another epoch.
    with campaign_clock(due):
        rehearsal = prepare_all(harness, "testing")
    assert set(rehearsal) == set(families.values())
    open_form(harness)
    respond(harness)
    states = dispatch_all(harness, rehearsal, families, due)
    assert states == {1: "cancelled", 11: "delivered", 12: "delivered"} | {
        13: "delivered",
        14: "delivered",
    }
    assert skipped_for(rehearsal[families[1]])
    testing = read(harness, database_now(), "testing", epoch)
    assert counts(testing) == (4, 1, 1, 1, 1, 1, 1, 0)
    assert marks(testing) == [("initial", "Invitation", 4)]
    assert (testing.sends[0].key.mode, testing.sends[0].key.cycle) == ("testing", 0)
    assert [stage.key for stage in testing.stages] == list(STAGES)
    assert testing.stages[1].note == LINK_FOLLOWED_NOTE
    assert testing.timezone == "America/New_York" and testing.grain == "hour"
    production = read(harness, database_now())
    assert counts(production) == (0,) * 8 and production.sends == ()
    other = read(harness, database_now(), "testing", uuid4())
    assert counts(other) == (0,) * 8 and other.sends == ()

    # Production: the real activation signs the corpus Family in live.
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    assert counts(read(harness, database_now())) == (0, 1, 0, 0, 0, 0, 0, 0)
    with campaign_clock(due):
        messages = prepare_all(harness, "production")
    assert set(messages) == set(families.values())
    # Family 14 responds between preparation and dispatch, so dispatch skips
    # its invitation: responded, never invited.
    early = live_login(harness, 14)
    open_form(early)
    respond(early)
    before_dispatch = database_now()
    states = dispatch_all(harness, messages, families, due)
    assert states == {1: "delivered", 11: "delivered", 12: "delivered"} | {
        13: "delivered",
        14: "cancelled",
    }
    assert skipped_for(messages[families[14]])
    # Reminder 1 falls due: planned for the four invited Families that have
    # not responded, and delivered.
    first = ScheduleDefinition.objects.get(kind="reminder")
    with campaign_clock(first.current_revision.due_at):
        reminders = prepare_all(harness, "production", purpose="reminder")
    assert set(reminders) == {families[duid] for duid in (1, 11, 12, 13)}
    delivered = dispatch_all(
        harness, reminders, families, first.current_revision.due_at
    )
    assert set(delivered.values()) == {"delivered"}
    after_dispatch = database_now()

    # Family 1 goes all the way; 11 progresses and submits; 12 only follows
    # its link (as a mail scanner would); 13 opens the form and stops.
    open_form(harness)
    progress_to(harness, "ministry")
    second = live_login(harness, 11)
    open_form(second)
    progress_to(second, "census")
    live_login(harness, 12)
    before_fourth = database_now()
    fourth = live_login(harness, 13)
    open_form(fourth)
    progress_to(fourth, "welcome")
    respond(harness)
    respond(second)
    settled = database_now()

    metrics = read(harness, settled)
    # Family 14 submitted without a recorded step: its submission implies it
    # progressed (see FamilyResponse.progressed_at).
    assert counts(metrics) == (4, 5, 4, 3, 3, 1, 1, 0)
    rows = by_duid(metrics)
    assert sorted(rows) == [1, 2, 11, 12, 13, 14]
    assert rows[1].stages == STAGES and rows[1].submissions == 1
    assert rows[11].stages == STAGES
    assert rows[12].stages == ("invited", "link_followed")
    assert rows[13].stages == ("invited", "link_followed", "form_opened")
    assert rows[14].stages == (
        "link_followed",
        "form_opened",
        "progressed",
        "submitted",
    )
    assert rows[14].progress_at is None
    assert rows[14].progressed_at == rows[14].submitted_at
    assert rows[14].skipped_responded and rows[14].submitted_uninvited
    assert rows[2].stages == () and rows[2].submissions == 0
    assert rows[1].invited_at <= rows[1].form_at <= rows[1].progress_at
    assert rows[1].progress_at <= rows[1].submitted_at <= settled
    # The series is the same first instants by local hour; the two sends
    # each delivered four emails, keyed by their own occurrences' cycle.
    assert sum(bucket.links for bucket in metrics.activity) == 5
    assert sum(bucket.forms for bucket in metrics.activity) == 4
    assert sum(bucket.submissions for bucket in metrics.activity) == 3
    assert all(
        bucket.start.tzinfo.key == "America/New_York" for bucket in metrics.activity
    )
    assert all(bucket.start.minute == 0 for bucket in metrics.activity)
    assert marks(metrics) == [
        ("initial", "Invitation", 4),
        ("reminder", "Reminder 1", 4),
    ]
    invitation, reminder = metrics.sends
    harness.campaign.refresh_from_db()
    assert (invitation.scheduled, reminder.scheduled) == (
        due,
        first.current_revision.due_at,
    )
    assert {send.key.mode for send in metrics.sends} == {"production"}
    assert {send.key.cycle for send in metrics.sends} == {
        harness.campaign.production_cycle
    }
    assert before_dispatch < invitation.first_delivered_at
    assert invitation.last_delivered_at < reminder.first_delivered_at
    assert reminder.last_delivered_at < after_dispatch
    daily = read(harness, settled, grain="day")
    assert daily.families == metrics.families and daily.grain == "day"
    assert all(bucket.start.hour == 0 for bucket in daily.activity)

    # Cutoffs: before dispatch nothing was invited or skipped, only the early
    # Family had responded and the invitation send had delivered nothing;
    # after dispatch but before the fourth Family signed in, three Families
    # had followed their link.
    assert counts(read(harness, before_dispatch)) == (0, 2, 1, 1, 1, 0, 1, 0)
    assert marks(read(harness, before_dispatch)) == [("initial", "Invitation", 0)]
    assert counts(read(harness, after_dispatch)) == (4, 2, 1, 1, 1, 1, 1, 0)
    assert read(harness, after_dispatch).sends == metrics.sends
    assert counts(read(harness, before_fourth)) == (4, 4, 3, 3, 1, 1, 1, 0)
    # A cutoff before anything happened: no evidence at all.
    assert counts(read(harness, settled - timedelta(days=7))) == (0,) * 8

    # Later activity never changes an earlier as-of: Family 1 submits again
    # and Family 12 opens its form, and the read at ``settled`` is identical
    # (the daily digest's parity rule), while the current read moves on.
    again = live_login(harness, 1)
    respond(again)
    twelfth = live_login(harness, 12)
    open_form(twelfth)
    assert read(harness, settled) == metrics
    later = read(harness, database_now())
    assert counts(later) == (4, 5, 5, 3, 3, 1, 1, 1)
    assert by_duid(later)[1].submissions == 2
    assert by_duid(later)[1].submitted_at == rows[1].submitted_at
    assert by_duid(later)[12].stages == ("invited", "link_followed", "form_opened")

    # The finished rehearsal's scope sees none of the live activity, and the
    # Production-transition cleanup removed the rehearsal's own evidence
    # (responses, engagement and its mail alike), so it now reads empty: the
    # parity rule holds only while the evidence is retained.
    finished = read(harness, database_now(), "testing", epoch)
    assert counts(finished) == (0,) * 8 and finished.sends == ()
