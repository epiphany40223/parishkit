"""Bulk preparation built outside the lock and ahead of the due time (BG-12, #447).

Each test runs the real restricted scheduler, worker and mail logins against
the bulk fixture's six mailable Families. A Production campaign first sends
its invitation to every Family; a reminder is then added and planned and
prepared ahead of its due time on the bulk path.

What these tests prove, besides each feature:

- **Exactly once.** Every Family receives exactly one reminder, a prepared
  reminder's delivery task cannot be claimed and the dispatch guard refuses
  it before its due time, and a response or pause in the lead window still
  refuses the message at its ``submitting`` commit.
- **Credentials unchanged.** Planning, building, preparing and sending ahead
  leave every Production credential row byte for byte as it was, the mail
  carries each Family's existing link token, and the emailed code and link
  still sign in.
"""

import re
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from time import sleep
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction
from django.db.models import F
from django.test import Client
from psycopg import sql

from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    CredentialKeyState,
    DeploymentCredentialState,
    FamilyAccessToken,
    FamilyAccessTokenGeneration,
    FamilyCampaign,
    FamilyCodeFingerprint,
)
from parishkit.stewardship.campaigns.family_schedule_planning import (
    PREPARE_AHEAD,
)
from parishkit.stewardship.campaigns.link_tokens import token_context
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.schedule_production import (
    FamilyScheduleProducer,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import (
    family_mail_builds,
    family_mail_bulk,
    family_mail_delivery_tasks,
    family_mail_preparation,
)
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.family_mail_builds import (
    build_preparation,
    fingerprint,
)
from parishkit.stewardship.jobs.family_mail_content import open_family_credentials
from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE as DELIVERY
from parishkit.stewardship.jobs.family_mail_models import FamilyMailPreparation
from parishkit.stewardship.jobs.family_mail_tasks import TASK_TYPE as PREPARE
from parishkit.stewardship.jobs.family_mail_tasks import owned_preparation
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import (
    OutboxEvent,
    OutboxMessage,
    OutboxRender,
)
from parishkit.stewardship.jobs.outbox_storage import _status as delivery_status
from parishkit.stewardship.jobs.outbox_validation import (
    RenderInput,
    SealedSubstitutions,
)
from parishkit.stewardship.jobs.ownership import TaskClaim, database_now
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scanning import collect_hints
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import _status, change_run
from parishkit.stewardship.source.send_hold import family_send_active

from ..campaign_factory import schedule
from ..content_factory import content
from .auth_builders import unguarded
from .campaign_builders import (
    admit_task_work,
    campaign_clock,
    change,
    claimed_task,
    complete_empty_catchup,
)
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_bulk_postgresql import (  # noqa: F401
    EXTRA,
    Provider,
    due,
    families,
    family_hint,
    mail_owner,
    plan,
    prepare_all,
    prepare_owner,
    send_all,
    single,
)
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_worker_postgresql import dispatch_worker  # noqa: F401
from .test_outbox_boundaries_postgresql import control
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)
FAMILIES = EXTRA + 1
# Just inside and just outside the two-hour lead window.
INSIDE = PREPARE_AHEAD - timedelta(minutes=1)
OUTSIDE = PREPARE_AHEAD + timedelta(minutes=1)
# How far ahead of its due time a test prepares a reminder it later sends. The
# delivery task waits that long in real time, so it is kept short.
SHORT = timedelta(seconds=4)
# The occurrence guard's prepare-ahead condition, as installed now, and the
# condition it replaced, for the side-by-side tests.
NEW_CONDITION = re.compile(
    r"OR \(NEW\.due_at>instant AND NOT \(t\.task_type='family_mail_prepare'\s+"
    r"AND NEW\.mode='production' AND NEW\.due_at<=instant\+interval '2 hours'\)\)"
)
OLD_CONDITION = "OR NEW.due_at>instant"


def add_reminder(harness, date="2054-10-03"):
    """Add one reminder schedule with its own template; return its due time."""
    template = content(
        str(harness.campaign.pk),
        kind="email",
        slot="reminder",
        html="<p>Reminder {{ family_name }}: {{ family_code }} {{ family_url }}</p>",
        text="Reminder {{ family_name }}: {{ family_code }} {{ family_url }}",
    )
    row = schedule(
        str(harness.campaign.pk),
        kind="reminder",
        date=date,
        template_version=template["id"],
        subject=template["values"]["subject"],
    )
    assert (
        change(
            harness.service.store,
            harness.service.store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "content", **template},
                {"operation": "add", "section": "schedules", **row},
            ],
        ).state
        == "applied"
    )
    return ScheduleDefinition.objects.get(pk=UUID(row["id"])).current_revision.due_at


def invite(harness, path, monkeypatch, *, production=True):
    """Send the invitation to every Family; return the harness and its provider."""
    if production:
        harness = activate_response_service(harness)
        complete_empty_catchup(harness.campaign, uuid4())
    provider = Provider()
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
        send_all(harness, path)
    assert OutboxMessage.objects.filter(state="delivered").count() == FAMILIES
    return harness, provider


@pytest.fixture
def invited(families, monkeypatch):  # noqa: F811
    """A Production campaign whose invitation reached every Family, plus a reminder.

    Returns the harness, the workspace key path, the provider (already
    holding the invitations) and the reminder's due time.
    """
    harness, path = families
    harness, provider = invite(harness, path, monkeypatch)
    return harness, path, provider, add_reminder(harness)


def reminders():
    """The reminder's occurrences."""
    return ScheduleOccurrence.objects.filter(definition__kind="reminder")


def reminder_messages():
    """The reminder's outbox messages."""
    return OutboxMessage.objects.filter(purpose="reminder")


def credential_rows():
    """Every Production credential row, column by column, for comparison."""
    return {
        model.__name__: sorted(
            tuple(sorted(row.items())) for row in model.objects.values()
        )
        for model in (
            FamilyCodeFingerprint,
            FamilyAccessToken,
            FamilyAccessTokenGeneration,
            DeploymentCredentialState,
            CredentialKeyState,
        )
    } | {
        # Activity and the version it bumps change on every Family sign-in;
        # the credential columns are what must not move.
        "FamilyCampaign": sorted(
            FamilyCampaign.objects.values_list("id", "code_ciphertext", "campaign_id")
        )
    }


@contextmanager
def old_guard():
    """Install the occurrence guard as it was before BG-12, then restore it."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'stewardship_occurrence_guard_v1()'::regprocedure)"
        )
        current = cursor.fetchone()[0]
        old, count = NEW_CONDITION.subn(OLD_CONDITION, current)
        assert count == 1, "the prepare-ahead condition is installed exactly once"
        assert "SECURITY DEFINER" in old
        cursor.execute(old)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(current)


@contextmanager
def guard(definition):
    """The new (installed) guard, or the old one, for one block."""
    if definition == "old":
        with old_guard():
            yield
    else:
        yield


class Rollback(Exception):
    """Undo one probe's claim and occurrence change."""


def admitted(row, at, claim_for):
    """Whether the occurrence guard lets ``claim_for`` claim ``row`` at ``at``.

    ``claim_for(actor)`` returns a running task claim; the occurrence is then
    moved to running under it with the update preparation itself makes.
    Everything is rolled back either way.
    """
    row.refresh_from_db()
    with campaign_clock(at):
        try:
            with transaction.atomic():
                actor = uuid4()
                claim = claim_for(actor)
                task = TaskRun.objects.get(pk=claim.run_id)
                try:
                    with transaction.atomic():
                        ScheduleOccurrence.objects.filter(
                            pk=row.pk, version=row.version
                        ).update(
                            state="running",
                            task_id=claim.run_id,
                            worker_id=actor,
                            fence=claim.fence,
                            attempts=row.attempts + 1,
                            lease_expires_at=task.lease_expires_at,
                            heartbeat_at=database_now(),
                            version=row.version + 1,
                            actor_id=actor,
                            correlation_id=claim.run_id,
                        )
                except IntegrityError as error:
                    assert "Occurrence claim requires current fenced work" in str(error)
                    raise Rollback(False) from None
                raise Rollback(True)
        except Rollback as result:
            return result.args[0]


def claim_task(run_id):
    """Claim an existing task for ``actor``, as a worker would (for probes)."""

    def claim_for(actor):
        """Claim the task now."""
        run = _status(TaskRun.objects.get(pk=run_id))
        return change_run(
            run_id=run.run_id,
            action="claim",
            expected_version=run.version,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_task_work,
            lease_seconds=300,
        )

    return claim_for


@pytest.mark.parametrize("definition", ["old", "new"])
def test_guard_admits_only_production_preparation_within_two_hours(invited, definition):
    """The one condition the migration changes, old and new side by side.

    A Production preparation claim 1 h 59 min before the due time: refused by
    the old guard, admitted by the new. 2 h 1 min before: refused by both.
    A schedule-occurrence claim before the due time: refused by both.
    """
    harness, path, provider, due_at = invited
    with campaign_clock(due_at - INSIDE):
        plan()
    ticket = FamilyMailPreparation.objects.filter(
        occurrence_id__in=reminders().values("pk")
    ).first()
    row = ScheduleOccurrence.objects.get(pk=ticket.occurrence_id)
    prepare = claim_task(ticket.task_id)
    with guard(definition):
        assert admitted(row, due_at - INSIDE, prepare) is (definition == "new")
        assert admitted(row, due_at - OUTSIDE, prepare) is False
        assert (
            admitted(
                row,
                due_at - timedelta(minutes=30),
                lambda actor: claimed_task("schedule_occurrence", row.pk, actor),
            )
            is False
        )
        # At the due time both admit it, as before.
        assert admitted(row, due_at, prepare) is True


@pytest.mark.parametrize("definition", ["old", "new"])
def test_guard_still_refuses_early_delivery_and_testing_claims(
    families,  # noqa: F811
    monkeypatch,
    definition,
):
    """Testing preparation and every delivery claim still wait for the due time."""
    harness, path = families
    harness, _ = invite(harness, path, monkeypatch, production=False)
    due_at = add_reminder(harness)
    with campaign_clock(due_at):
        plan()
    ticket = FamilyMailPreparation.objects.filter(
        occurrence_id__in=reminders().values("pk")
    ).first()
    row = ScheduleOccurrence.objects.get(pk=ticket.occurrence_id)
    with guard(definition):
        # Testing preparation 1 h 59 min early: refused by both.
        assert admitted(row, due_at - INSIDE, claim_task(ticket.task_id)) is False
        assert admitted(row, due_at, claim_task(ticket.task_id)) is True
    with campaign_clock(due_at):
        prepare_all(harness)
    row.refresh_from_db()
    message = OutboxMessage.objects.get(pk=row.outbox_id)
    with guard(definition):
        # A delivery claim before the due time: refused by both.
        delivery = claim_task(message.task_id)
        assert admitted(row, due_at - timedelta(minutes=30), delivery) is False
        assert admitted(row, due_at, delivery) is True


def test_python_lead_window_is_within_the_installed_guard(invited):
    """The installed guard's allowance is at least PREPARE_AHEAD."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT prosrc, prosecdef FROM pg_proc "
            "WHERE proname='stewardship_occurrence_guard_v1'"
        )
        source, definer = cursor.fetchone()
    # The guard keeps running as its owner after the migration replaced it.
    assert definer is True
    (allowance,) = re.findall(r"NEW\.due_at<=instant\+interval '(\d+) hours'", source)
    assert timedelta(hours=int(allowance)) >= PREPARE_AHEAD


def test_bulk_scheduler_plans_production_reminders_ahead(invited):
    """Only the bulk sweep plans a Production reminder within the lead window."""
    harness, path, provider, due_at = invited
    with campaign_clock(due_at - OUTSIDE):
        plan()
    assert not reminders().exists()
    with campaign_clock(due_at - INSIDE):
        plan(bulk=False)
        assert not reminders().exists()
        plan()
    assert reminders().count() == FAMILIES
    assert set(reminders().values_list("state", flat=True)) == {"pending"}
    assert (
        FamilyMailPreparation.objects.filter(
            occurrence_id__in=reminders().values("pk")
        ).count()
        == FAMILIES
    )


def test_a_settled_bulk_sweep_wakes_at_the_lead_window(invited, monkeypatch):
    """One long-lived bulk sweep idles, then plans at the window's start (#640).

    The scheduler keeps its producer across loops, so the lead-window start
    must wake it even though no row changed since every Family was planned.
    """
    from parishkit.stewardship.campaigns import schedule_production

    monkeypatch.setattr(schedule_production, "CHANGE_CHECK_SECONDS", 0)
    harness, path, provider, due_at = invited
    producer = FamilyScheduleProducer(uuid4(), bulk=True)
    # Every Family is planned; the mailable ones get a reminder.
    everyone = FamilyCampaign.objects.count()
    login = task_login(ServiceRole.SCHEDULER, exact=True)
    with campaign_clock(due_at - OUTSIDE), login, scheduler_session() as guard:
        for _ in range(3):
            producer(guard)
        assert not producer.pending
        assert producer.wake == due_at - PREPARE_AHEAD
        assert producer(guard) == ()
    assert not reminders().exists()
    login = task_login(ServiceRole.SCHEDULER, exact=True)
    with campaign_clock(due_at - INSIDE), login, scheduler_session() as guard:
        assert len(producer(guard)) == everyone
    assert reminders().count() == FAMILIES
    assert producer.wake == due_at


def test_testing_plans_reminders_only_at_their_due_time(
    families,  # noqa: F811
    monkeypatch,
):
    """Testing keeps planning at the due time, bulk sweep or not."""
    harness, path = families
    harness, _ = invite(harness, path, monkeypatch, production=False)
    due_at = add_reminder(harness)
    with campaign_clock(due_at - INSIDE):
        plan()
    assert not reminders().exists()
    with campaign_clock(due_at):
        plan()
    assert reminders().count() == FAMILIES


def test_reminder_is_prepared_ahead_and_not_sent_before_its_due_time(
    invited, monkeypatch
):
    """Built outside the lock, prepared ahead, and held until the due time."""
    harness, path, provider, due_at = invited
    before = credential_rows()
    built_here = []
    real = family_mail_preparation._build_here
    monkeypatch.setattr(
        family_mail_preparation,
        "_build_here",
        lambda *args, **kwargs: built_here.append(1) or real(*args, **kwargs),
    )
    timings = []
    real_timing = family_mail_bulk._timing
    monkeypatch.setattr(
        family_mail_bulk,
        "_timing",
        lambda kind, pace, **values: (
            timings.append((kind, values)) or real_timing(kind, pace, **values)
        ),
    )
    sent = len(provider.calls)
    with campaign_clock(due_at - INSIDE):
        plan()
        prepare_all(harness)
        # Every reminder is prepared, from builds made outside the lock.
        assert reminder_messages().filter(state="pending").count() == FAMILIES
        assert not built_here
        assert sum(values["prebuilt"] for kind, values in timings) == FAMILIES
        assert sum(values["rebuilt"] for kind, values in timings) == 0
        assert set(
            TaskRun.objects.filter(task_type=PREPARE).values_list("state", flat=True)
        ) == {"succeeded"}
        # Each delivery task waits for the due time, by the task clock.
        with transaction.atomic():
            now = database_now()
        tasks = TaskRun.objects.filter(
            pk__in=reminder_messages().values("task_id"), state="queued"
        )
        assert tasks.count() == FAMILIES
        assert all(
            task.not_before > now + INSIDE - timedelta(minutes=1) for task in tasks
        )
        # Neither the bulk drain nor the one-at-a-time path sends it now.
        send_all(harness, path)
        for task in tasks:
            single(harness, path, task.pk)
        # The dispatch guard refuses it before its due time...
        message = reminder_messages().first()
        assert dispatch_live(message.pk) is False
    with campaign_clock(due_at):
        # ...and admits it from then on.
        assert dispatch_live(message.pk) is True
    assert len(provider.calls) == sent
    assert set(tasks.values_list("state", flat=True)) == {"queued"}
    assert credential_rows() == before


def dispatch_live(message_id):
    """``stewardship_family_dispatch_live_v1`` for one message, now."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_family_dispatch_live_v1(%s)", [message_id]
        )
        return cursor.fetchone()[0]


def wait_for_tasks(messages, limit=20):
    """Wait until the messages' delivery tasks are due by the task clock."""
    tasks = TaskRun.objects.filter(pk__in=messages.values("task_id"))
    for _ in range(limit * 10):
        with transaction.atomic():
            now = database_now()
        if all(task.not_before <= now for task in tasks):
            return
        sleep(0.1)
    pytest.fail("The delivery tasks did not fall due.")


def test_prepared_reminder_is_sent_once_with_unchanged_credentials(
    invited, monkeypatch
):
    """Prepared ahead, then sent at the due time: once, with the same links."""
    harness, path, provider, due_at = invited
    before = credential_rows()
    sent = len(provider.calls)
    mails = []
    real = provider.__call__

    def record(value, settings, mail, **kwargs):
        """Keep each reminder's text to check its link."""
        mails.append(mail)
        return real(value, settings, mail, **kwargs)

    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", record)
    with campaign_clock(due_at - SHORT):
        plan()
        prepare_all(harness)
    assert reminder_messages().count() == FAMILIES
    wait_for_tasks(reminder_messages())
    with campaign_clock(due_at):
        send_all(harness, path)
        # Another pass, and the one-at-a-time path, find nothing left.
        send_all(harness, path)
        for message in reminder_messages():
            single(harness, path, message.task_id)
    assert len(provider.calls) - sent == FAMILIES == len(mails)
    assert len({mail.semantic_key for mail in mails}) == FAMILIES
    assert set(reminder_messages().values_list("state", flat=True)) == {"delivered"}
    assert set(reminders().values_list("state", flat=True)) == {"succeeded"}
    # No credential row moved, and each Family's mail carries its existing
    # code and the link token it was sent at go-live.
    assert credential_rows() == before
    rings = harness.rings
    for mail in mails:
        occurrence = ScheduleOccurrence.objects.get(pk=mail.semantic_key)
        family = FamilyCampaign.objects.get(
            pk=UUID(occurrence.target.removeprefix("family:"))
        )
        token = FamilyAccessToken.objects.get(family=family, destroyed_at__isnull=True)
        plaintext = rings.private.decrypt(
            token.ciphertext, context=token_context(token.pk)
        ).decode()
        assert plaintext in mail.text
    # The emailed link and code still sign Family 1 in.
    family = FamilyCampaign.objects.get(family_duid=1)
    token = FamilyAccessToken.objects.get(family=family, destroyed_at__isnull=True)
    plaintext = rings.private.decrypt(
        token.ciphertext, context=token_context(token.pk)
    ).decode()
    assert Client(enforce_csrf_checks=True).get("/access/" + plaintext).status_code == (
        302
    )
    assert harness.code in next(mail.text for mail in mails if plaintext in mail.text)


def test_prebuilt_item_writes_exactly_what_a_single_preparation_writes(
    invited, monkeypatch
):
    """Same rows, same render and the same sealed reference, either way."""
    harness, path, provider, due_at = invited
    with campaign_clock(due_at - INSIDE):
        plan()
        ticket = FamilyMailPreparation.objects.filter(
            occurrence_id__in=reminders().values("pk")
        ).first()
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            built = build_preparation(
                ticket.task_id,
                general=harness.rings.general,
                public=harness.rings.public,
                public_origin="http://localhost:8000",
            )
            assert built is not None
            prebuilt = written(harness, ticket, built)
            single = written(harness, ticket, None)
        connections.close_all()
    assert prebuilt.pop("path") is True and single.pop("path") is None
    prebuilt_sealed, single_sealed = prebuilt.pop("sealed"), single.pop("sealed")
    assert prebuilt == single
    opened = [
        open_family_credentials(
            identity=identity,
            render=render,
            sealed=sealed,
            private=harness.rings.private,
        )
        for identity, render, sealed in (prebuilt_sealed, single_sealed)
    ]
    assert opened[0].code == opened[1].code
    assert opened[0].token_id == opened[1].token_id


def written(harness, ticket, prebuilt):
    """Prepare ``ticket`` under the lock, capture its rows, and roll back.

    Returns the rows the item wrote, without identifiers or times, and how
    it was built: True for the prebuilt path, None with no build.
    """
    rows = {}
    try:
        with work_transaction():
            run = _status(TaskRun.objects.get(pk=ticket.task_id))
            worker = uuid4()
            status = change_run(
                run_id=run.run_id,
                action="claim",
                expected_version=run.version,
                actor_id=worker,
                correlation_id=uuid4(),
                admit=lambda action, status: True,
                lease_seconds=60,
            )
            claim = TaskClaim(status.run_id, status.fence, worker)
            used = []
            assert (
                family_mail_preparation.prepare_occurrence(
                    owned_preparation(status),
                    claim,
                    general=harness.rings.general,
                    mac=harness.rings.mac,
                    public=harness.rings.public,
                    public_origin="http://localhost:8000",
                    prebuilt=prebuilt,
                    report=used.append,
                )
                == "complete"
            )
            occurrence = ScheduleOccurrence.objects.get(pk=ticket.occurrence_id)
            message = OutboxMessage.objects.get(pk=occurrence.outbox_id)
            render = OutboxRender.objects.get(pk=message.render_id)
            delivery = TaskRun.objects.get(pk=message.task_id)
            status = delivery_status(message)
            rows.update(
                path=used[0] if used else None,
                occurrence=(
                    occurrence.state,
                    occurrence.reason,
                    occurrence.version,
                    occurrence.attempts,
                ),
                message=(
                    message.state,
                    message.mode,
                    message.routing,
                    message.purpose,
                    message.credential_namespace,
                    message.token_generation_id,
                    message.credential_epoch_id,
                    message.semantic_key,
                    message.family_id,
                ),
                render={
                    name: getattr(render, name)
                    for name in (*RENDER_FIELDS, "payload_digest")
                },
                events=list(
                    OutboxEvent.objects.filter(message=message).values_list(
                        "version", "action", "state"
                    )
                ),
                delivery=(delivery.state, delivery.task_type),
                sealed=(
                    status.identity,
                    family_mail_render(render),
                    family_mail_sealed(message),
                ),
            )
            raise Rollback
    except Rollback:
        pass
    return rows


RENDER_FIELDS = (
    "configuration_id",
    "template_id",
    "sender",
    "reply_to",
    "intended_recipients",
    "routed_recipients",
    "subject",
    "html",
    "text",
)


def family_mail_render(render):
    """The RenderInput a stored render was written from."""
    return RenderInput(**{name: getattr(render, name) for name in RENDER_FIELDS})


def family_mail_sealed(message):
    """The SealedSubstitutions a stored message carries."""
    return SealedSubstitutions(
        message.sealed_substitutions,
        message.token_generation_id,
        message.credential_epoch_id,
    )


@contextmanager
def as_owner():
    """Step out of a test login on this connection for a seeded change."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        role = cursor.fetchone()[0]
        cursor.execute("RESET SESSION AUTHORIZATION")
    try:
        with unguarded():
            yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SET SESSION AUTHORIZATION {}").format(sql.Identifier(role))
            )


def seed(statement, values=None):
    """Run one seeded change the runtime guards would refuse, and commit it."""
    with as_owner(), connection.cursor() as cursor:
        cursor.execute(statement, values or {})


# Each input a Production build depends on, and a change to it.
INPUT_CHANGES = {
    "occurrence version": "UPDATE stewardship_schedule_occurrence "
    "SET version=version+1 WHERE id=%(occurrence)s",
    "schedule revision": "UPDATE stewardship_schedule_definition "
    "SET current_revision_id=gen_random_uuid() WHERE id=(SELECT definition_id "
    "FROM stewardship_schedule_occurrence WHERE id=%(occurrence)s)",
    "system configuration": "UPDATE stewardship_system_configuration "
    "SET active_configuration_id=gen_random_uuid()",
    "campaign configuration": "UPDATE stewardship_campaign "
    "SET active_configuration_id=gen_random_uuid() WHERE id=%(campaign)s",
    "production cycle": "UPDATE stewardship_campaign "
    "SET production_cycle=production_cycle+1 WHERE id=%(campaign)s",
    # Eligibility alone: deliverability is cleared first (a deliverable
    # Family must be eligible), before the fingerprint is taken.
    "family eligibility": (
        "UPDATE stewardship_family_campaign "
        "SET email_deliverable=false WHERE id=%(family)s",
        "UPDATE stewardship_family_campaign "
        "SET email_eligible=false WHERE id=%(family)s",
    ),
    "family deliverability": "UPDATE stewardship_family_campaign "
    "SET email_deliverable=NOT email_deliverable WHERE id=%(family)s",
    "family response": "UPDATE stewardship_family_campaign "
    "SET effective_submission_id=gen_random_uuid() WHERE id=%(family)s",
    "family source generation": "UPDATE stewardship_family_campaign "
    "SET source_generation=source_generation+1 WHERE id=%(family)s",
    "family code": "UPDATE stewardship_family_campaign "
    "SET code_ciphertext=code_ciphertext||'x' WHERE id=%(family)s",
    "population": "UPDATE stewardship_campaign_credentials "
    "SET population_dirty=NOT population_dirty WHERE campaign_id=%(campaign)s",
    "promoted source": "UPDATE stewardship_source_current SET generation=generation+1",
    "address refusal": "INSERT INTO stewardship_recipient_refusal "
    "(id,correlation_id,family_id,event_id,address,organization_id,family_duid) "
    "SELECT gen_random_uuid(),gen_random_uuid(),f.id,gen_random_uuid(),"
    "'refused@example.org',s.organization_id,f.family_duid "
    "FROM stewardship_family_campaign f CROSS JOIN stewardship_source_current s "
    "WHERE f.id=%(family)s",
    "hosted file": "INSERT INTO stewardship_hosted_file "
    "(id,correlation_id,version,slug,original_name,kind,size,sha256,token,"
    "uploaded_by_id) VALUES (gen_random_uuid(),gen_random_uuid(),1,'flyer',"
    "'flyer.pdf','pdf',1,repeat('a',64),repeat('A',43),gen_random_uuid())",
    "branding bundle": "INSERT INTO stewardship_branding_bundle "
    "(id,correlation_id,version,owner_id,session_id,expires_at,state,base_id) "
    "VALUES (gen_random_uuid(),gen_random_uuid(),1,gen_random_uuid(),"
    "gen_random_uuid(),statement_timestamp()+interval '1 day','ready',"
    "gen_random_uuid())",
    "token generation": "UPDATE stewardship_family_token_generation "
    "SET state='superseded' WHERE campaign_id=%(campaign)s AND state='active'",
    "credential epoch": "UPDATE stewardship_credential_deployment "
    "SET family_link_epoch=gen_random_uuid()",
    "link token": "UPDATE stewardship_family_token "
    "SET generation_id=gen_random_uuid() WHERE family_id=%(family)s",
    "key inventory": "UPDATE stewardship_credential_key_state "
    "SET inventory_digest=repeat('0',64) WHERE kind='general_encryption'",
}


@pytest.mark.parametrize("change", sorted(INPUT_CHANGES))
def test_fingerprint_moves_with_every_input(invited, change):
    """A change to any input between build and write changes the fingerprint."""
    harness, path, provider, due_at = invited
    with campaign_clock(due_at - INSIDE):
        plan()
    row = reminders().first()
    family = UUID(row.target.removeprefix("family:"))
    values = {
        "occurrence": row.pk,
        "family": family,
        "campaign": harness.campaign.pk,
    }
    with transaction.atomic():
        original = fingerprint(row.pk, family)
    assert original is not None
    statement = INPUT_CHANGES[change]
    setup, statement = statement if isinstance(statement, tuple) else (None, statement)
    try:
        with unguarded(), connection.cursor() as cursor:
            if setup is not None:
                cursor.execute(setup, values)
            before = fingerprint(row.pk, family)
            cursor.execute(statement, values)
            assert cursor.rowcount >= 1
            assert fingerprint(row.pk, family) != before
            raise Rollback
    except Rollback:
        pass
    with transaction.atomic():
        assert fingerprint(row.pk, family) == original


def recorded_timings(monkeypatch):
    """Record each bulk batch's timing values, keeping the timing line."""
    timings = []
    real = family_mail_bulk._timing
    monkeypatch.setattr(
        family_mail_bulk,
        "_timing",
        lambda kind, pace, **values: (
            timings.append((kind, values)) or real(kind, pace, **values)
        ),
    )
    return timings


@pytest.mark.parametrize("stale", [False, True])
def test_carried_builds_are_rechecked_in_later_batches(invited, monkeypatch, stale):
    """Builds a batch does not reach carry over; a stale one is rebuilt.

    Each batch takes one item, and the first lookahead builds every item,
    so later batches write carried builds. With ``stale``, an input every
    build depends on changes after the first batch: every carried build is
    then rebuilt under the lock, and the result is still one prepared,
    correct message per Family.
    """
    harness, path, provider, due_at = invited
    monkeypatch.setattr(family_mail_bulk, "HOLD_SECONDS", 0.000001)
    # The hint may name an invitation's finished task, which fills a whole
    # one-item batch; the drain must then go on to the reminders.
    monkeypatch.setattr(family_mail_bulk, "IDLE_SECONDS", 1)
    monkeypatch.setattr(family_mail_bulk, "BUILD_MARGIN", FAMILIES)
    timings = recorded_timings(monkeypatch)
    seeded = []
    real = family_mail_bulk._run_batch

    def run_batch(*args, **kwargs):
        """Change a shared input once the first reminder has been written."""
        if stale and not seeded and reminder_messages().exists():
            seed(INPUT_CHANGES["hosted file"])
            seeded.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(family_mail_bulk, "_run_batch", run_batch)
    with campaign_clock(due_at - INSIDE):
        plan()
        prepare_all(harness)
    prepared = [values for kind, values in timings if kind == "prepare"]
    assert [values["items"] for values in prepared if values["items"]] == [1] * FAMILIES
    assert sum(values["prebuilt"] for values in prepared) == (1 if stale else FAMILIES)
    assert sum(values["rebuilt"] for values in prepared) == (
        FAMILIES - 1 if stale else 0
    )
    assert reminder_messages().filter(state="pending").count() == FAMILIES


def test_an_inventory_change_after_the_build_never_writes_the_build(
    invited, monkeypatch
):
    """The key inventory moves after the build (no longer current for this
    process): nothing is written until it is current again.

    A complete, valid rotation would also give the worker new keyrings; the
    fixture has no cheap way to do that, so this covers the stale-process
    half of it, and the next test the rotation's lock.
    """
    harness, path, provider, due_at = invited
    real = family_mail_builds.build_preparation
    rotated = []

    def build_then_rotate(*args, **kwargs):
        """Build, then change the general key inventory (a rotation's effect)."""
        built = real(*args, **kwargs)
        if built is not None and not rotated:
            rotated.append(
                CredentialKeyState.objects.get(
                    kind="general_encryption"
                ).inventory_digest
            )
            seed(
                "UPDATE stewardship_credential_key_state SET inventory_digest="
                "repeat('0',64) WHERE kind='general_encryption'"
            )
        return built

    monkeypatch.setattr(family_mail_builds, "build_preparation", build_then_rotate)
    with campaign_clock(due_at - INSIDE):
        plan()
        prepare_all(harness)
        # The stale build was not written, and the item could not be sealed
        # under the lock with a key set that is no longer current.
        assert not reminder_messages().exists()
        seed(
            "UPDATE stewardship_credential_key_state SET inventory_digest=%(digest)s "
            "WHERE kind='general_encryption'",
            {"digest": rotated[0]},
        )
        prepare_all(harness)
    assert reminder_messages().filter(state="pending").count() == FAMILIES


def hold_key_set_lock(ready, release, outcome=None):
    """Hold, or with ``outcome`` only try, the key-set lock exclusively.

    A rotation takes this lock exclusively and without waiting. With
    ``outcome`` (a list) the thread records whether the try succeeded and
    lets go at once, as a rotation that could not start would.
    """
    from parishkit.stewardship.campaigns.credential_keys import KEY_LOCK

    try:
        with transaction.atomic(), connection.cursor() as cursor:
            if outcome is not None:
                cursor.execute("SELECT pg_try_advisory_xact_lock(%s, %s)", KEY_LOCK)
                outcome.append(cursor.fetchone()[0])
                return
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", KEY_LOCK)
            ready.set()
            release.wait(30)
    finally:
        connections.close_all()


def rotation_could_start():
    """Whether a rotation could take the key-set lock right now."""
    from threading import Event, Thread

    outcome = []
    probe = Thread(target=hold_key_set_lock, args=(Event(), Event(), outcome))
    probe.start()
    probe.join(30)
    return outcome[0]


@contextmanager
def rotation_in_progress():
    """Hold the key-set lock exclusively on another connection, as a rotation."""
    from threading import Event, Thread

    ready, release = Event(), Event()
    holder = Thread(target=hold_key_set_lock, args=(ready, release))
    holder.start()
    assert ready.wait(10)
    try:
        yield
    finally:
        release.set()
        holder.join(30)


@pytest.mark.parametrize("when", ["during the write", "before the check"])
def test_no_rotation_lands_between_the_fingerprint_check_and_the_write(
    invited, monkeypatch, when
):
    """The prebuilt branch holds the key-set lock from the check to the commit.

    During the write, a rotation cannot take the lock, so the build written
    was checked against the key set that is still current. With a rotation
    already holding it before the check, the build is not used: the item is
    rebuilt under the lock, which refuses it as before, so nothing stale is
    written; once the rotation is done every Family is prepared.
    """
    harness, path, provider, due_at = invited
    timings = recorded_timings(monkeypatch)
    probes = []
    real_write = family_mail_preparation._write

    def write(*args, **kwargs):
        """Try to start a rotation just before the prepared rows are written."""
        probes.append(rotation_could_start())
        return real_write(*args, **kwargs)

    monkeypatch.setattr(family_mail_preparation, "_write", write)
    with campaign_clock(due_at - INSIDE):
        plan()
        if when == "during the write":
            prepare_all(harness)
            assert probes and not any(probes)
            prepared = [values for kind, values in timings if kind == "prepare"]
            assert sum(values["prebuilt"] for values in prepared) == FAMILIES
        else:
            real_run = family_mail_bulk._run_batch
            batches = []

            def run_batch(*args, **kwargs):
                """Run the first batch while a rotation holds the lock."""
                batches.append(1)
                if len(batches) > 1:
                    return real_run(*args, **kwargs)
                with rotation_in_progress():
                    return real_run(*args, **kwargs)

            monkeypatch.setattr(family_mail_bulk, "_run_batch", run_batch)
            prepare_all(harness)
            first = [values for kind, values in timings if kind == "prepare"][0]
            assert first["prebuilt"] == 0
            monkeypatch.setattr(family_mail_bulk, "_run_batch", real_run)
            prepare_all(harness)
    assert reminder_messages().filter(state="pending").count() == FAMILIES


def build(harness, ticket):
    """Build one ticket's message as the worker would."""
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        try:
            return build_preparation(
                ticket.task_id,
                general=harness.rings.general,
                public=harness.rings.public,
                public_origin="http://localhost:8000",
            )
        finally:
            connections.close_all()


@pytest.mark.parametrize(
    "failure",
    [
        "busy key set",
        "inventory not current",
        "serialization",
        "decryption",
        "unexpected",
    ],
)
def test_a_build_is_dropped_and_the_item_prepared_under_the_lock(
    invited, monkeypatch, failure
):
    """A fault in one item's build, in a real drain, drops only that build.

    The fault is injected only around the first Production build (in its
    snapshot or its seal); that item is then prepared under the lock, with
    neither a prebuilt nor a rebuilt count, and every other item is written
    from its build. Every Family ends up prepared.
    """
    from contextlib import nullcontext

    from django.db.utils import OperationalError

    from parishkit.stewardship.accounts.cryptography import (
        CryptographicError,
        GeneralKeyring,
    )
    from parishkit.stewardship.jobs import family_mail_credentials

    harness, path, provider, due_at = invited
    target, built_here, outcomes = [], [], {}
    real_build_here = family_mail_preparation._build_here
    monkeypatch.setattr(
        family_mail_preparation,
        "_build_here",
        lambda ticket, claim, row, *args, **kwargs: (
            built_here.append(row.pk)
            or real_build_here(ticket, claim, row, *args, **kwargs)
        ),
    )
    real_prepare = family_mail_preparation.prepare_occurrence

    def prepare(ticket, claim, **kwargs):
        """Record whether the item had a build and what it reported."""
        reported, report = [], kwargs.pop("report", None)

        def record(value):
            """Keep the report and pass it on."""
            reported.append(value)
            if report is not None:
                report(value)

        result = real_prepare(ticket, claim, report=record, **kwargs)
        outcomes[ticket.occurrence_id] = (kwargs.get("prebuilt") is not None, reported)
        return result

    monkeypatch.setattr(family_mail_preparation, "prepare_occurrence", prepare)
    if failure in {"serialization", "unexpected"}:
        real_snapshot = family_mail_builds._snapshot

        def snapshot(task_id, **kwargs):
            """Fail the first Production snapshot, as a conflict or bug would."""
            built = real_snapshot(task_id, **kwargs)
            if built is None or target:
                return built
            target.append(built[0].semantic_key)
            if failure == "serialization":
                raise OperationalError("could not serialize access (40001)")
            raise RuntimeError("synthetic unexpected failure")

        monkeypatch.setattr(family_mail_builds, "_snapshot", snapshot)
    else:
        real_seal = family_mail_credentials.seal_built_credentials

        def broken(self, *args, **kwargs):
            """Decryption that fails, as with a damaged ciphertext."""
            raise CryptographicError("synthetic decryption failure")

        def seal(*, identity, **kwargs):
            """Fault only the first Production seal, for real where possible.

            Sealing under the lock goes through this function too, so every
            later call, including that item's own rebuild, is left alone.
            """
            if target:
                return real_seal(identity=identity, **kwargs)
            target.append(identity.semantic_key)
            if failure == "busy key set":
                context = rotation_in_progress()
            elif failure == "inventory not current":
                # Rolled back with the build's own sealing transaction.
                seed(INPUT_CHANGES["key inventory"])
                context = nullcontext()
            else:
                context = monkeypatch.context()
            with context as patch:
                if failure == "decryption":
                    patch.setattr(GeneralKeyring, "decrypt", broken)
                return real_seal(identity=identity, **kwargs)

        monkeypatch.setattr(family_mail_credentials, "seal_built_credentials", seal)
    with campaign_clock(due_at - INSIDE):
        plan()
        prepare_all(harness)
    assert target
    (faulted,) = target
    assert faulted in built_here
    assert outcomes[faulted] == (False, [])
    assert all(
        outcome == (True, [True])
        for occurrence, outcome in outcomes.items()
        if occurrence != faulted
    )
    assert reminder_messages().filter(state="pending").count() == FAMILIES


def test_an_unsent_invitation_never_absorbs_a_reminder_early(
    families,  # noqa: F811
    monkeypatch,
):
    """A Family still owed its invitation plans exactly as before.

    Inside a reminder's lead window, the bulk sweep must not coalesce the
    reminder into an invitation that is due but not yet sent: it would
    never be sent. Once the invitation is delivered, the reminder-only
    group is planned and prepared ahead.
    """
    from parishkit.stewardship.campaigns.schedule_models import ScheduleFulfillment

    harness, path = families
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    provider = Provider()
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    due_at = add_reminder(harness)
    with campaign_clock(due_at - INSIDE):
        # The invitation (due two days ago) is unsent; the reminder is in
        # its window. Only the invitation is planned.
        plan()
        assert not reminders().exists()
        assert not ScheduleFulfillment.objects.filter(
            definition__kind="reminder"
        ).exists()
        prepare_all(harness)
        send_all(harness, path)
        assert (
            OutboxMessage.objects.filter(purpose="initial", state="delivered").count()
            == FAMILIES
        )
        # Now each group is reminder-only, and the reminder is prepared.
        plan()
        prepare_all(harness)
    assert reminder_messages().filter(state="pending").count() == FAMILIES
    assert set(reminders().values_list("state", flat=True)) == {"pending"}


def test_testing_items_are_never_built_outside_the_lock(
    families,  # noqa: F811
    monkeypatch,
):
    """Testing preparation writes rehearsal credentials, so it stays inside."""
    harness, path = families
    harness, _ = invite(harness, path, monkeypatch, production=False)
    due_at = add_reminder(harness)
    timings = recorded_timings(monkeypatch)
    with campaign_clock(due_at):
        plan()
        ticket = FamilyMailPreparation.objects.filter(
            occurrence_id__in=reminders().values("pk")
        ).first()
        assert build(harness, ticket) is None
        prepare_all(harness)
    assert reminder_messages().filter(state="pending").count() == FAMILIES
    prepared = [values for kind, values in timings if kind == "prepare"]
    assert sum(values["prebuilt"] + values["rebuilt"] for values in prepared) == 0
    # Prepared at the due time: each delivery task is due at once.
    with transaction.atomic():
        now = database_now()
    assert all(
        task.not_before <= now
        for task in TaskRun.objects.filter(pk__in=reminder_messages().values("task_id"))
    )


def test_turning_the_bulk_send_off_mid_window_still_completes_preparation(invited):
    """Queued preparation planned ahead completes on the one-at-a-time path."""
    harness, path, provider, due_at = invited
    with campaign_clock(due_at - INSIDE):
        plan()
        owner = prepare_owner(harness, None)
        assert owner.bulk is None
        queued = list(
            TaskRun.objects.filter(task_type=PREPARE, state="queued").values_list(
                "pk", flat=True
            )
        )
        assert len(queued) == FAMILIES
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            for run_id in queued:
                assert execute_hint(
                    run_id,
                    queue=WorkQueue.GENERAL,
                    worker_id=uuid4(),
                    handlers={PREPARE: owner},
                )
        connections.close_all()
    assert reminder_messages().filter(state="pending").count() == FAMILIES
    with transaction.atomic():
        now = database_now()
    assert all(
        task.not_before > now
        for task in TaskRun.objects.filter(pk__in=reminder_messages().values("task_id"))
    )


def prepare_shortly_before(harness, due_at):
    """Plan and prepare the reminder just before its due time; wait for it."""
    with campaign_clock(due_at - SHORT):
        plan()
        prepare_all(harness)
    assert reminder_messages().count() == FAMILIES
    wait_for_tasks(reminder_messages())


def test_a_response_in_the_lead_window_cancels_that_familys_reminder(
    invited, monkeypatch
):
    """A Family that responds after preparation is not sent the reminder."""
    harness, path, provider, due_at = invited
    sent = len(provider.calls)
    prepare_shortly_before(harness, due_at)
    responder = FamilyCampaign.objects.get(family_duid=1)
    with campaign_clock(due_at - SHORT):
        form, answers = form_and_answers(harness)
        assert submit(harness, form, answers).submission is not None
    with campaign_clock(due_at):
        send_all(harness, path)
        send_all(harness, path)
        for message in reminder_messages().filter(family_id=responder.pk):
            single(harness, path, message.task_id)
    assert len(provider.calls) - sent == FAMILIES - 1
    assert (
        not reminder_messages()
        .filter(family_id=responder.pk, state="delivered")
        .exists()
    )
    assert reminder_messages().filter(state="delivered").count() == FAMILIES - 1
    assert len(provider.keys) == len(set(provider.keys))


def test_a_pause_in_the_lead_window_holds_every_prepared_reminder(invited, monkeypatch):
    """Pausing after preparation: nothing is sent at the due time."""
    harness, path, provider, due_at = invited
    sent = len(provider.calls)
    prepare_shortly_before(harness, due_at)
    with campaign_clock(due_at - SHORT):
        control(harness.campaign, "pause")
    with campaign_clock(due_at):
        send_all(harness, path)
        for message in reminder_messages():
            single(harness, path, message.task_id)
    assert len(provider.calls) == sent
    assert not reminder_messages().filter(state="delivered").exists()


def test_a_schedule_edit_in_the_lead_window_sends_each_family_once(
    invited, monkeypatch
):
    """Moving the reminder after preparation: the old messages are never sent."""
    harness, path, provider, due_at = invited
    sent = len(provider.calls)
    prepare_shortly_before(harness, due_at)
    definition = ScheduleDefinition.objects.get(kind="reminder")
    with campaign_clock(due_at - SHORT):
        assert (
            change(
                harness.service.store,
                harness.service.store.active(),
                uuid4(),
                [
                    {
                        "operation": "update",
                        "section": "schedules",
                        "id": str(definition.pk),
                        "values": {"time": "10:00:00"},
                    }
                ],
            ).state
            == "applied"
        )
    definition.refresh_from_db()
    moved = definition.current_revision.due_at
    assert moved == due_at + timedelta(hours=1)
    with campaign_clock(due_at):
        # At the old time the old revision's messages are not sent.
        send_all(harness, path)
        for message in reminder_messages():
            single(harness, path, message.task_id)
    assert len(provider.calls) == sent
    with campaign_clock(moved):
        plan()
        prepare_all(harness)
        send_all(harness, path)
        send_all(harness, path)
    delivered = reminder_messages().filter(state="delivered")
    assert len(provider.calls) - sent == FAMILIES == delivered.count()
    assert delivered.values("family_id").distinct().count() == FAMILIES


def test_deltas_wait_only_for_reminders_being_prepared_or_due(invited):
    """Messages not yet due do not hold refreshes; preparation and due mail do.

    A promotion during preparation leaves the population dirty, and
    preparation waits until it is rebuilt.
    """
    harness, path, provider, due_at = invited
    with campaign_clock(due_at - INSIDE):
        plan()
        with transaction.atomic():
            assert family_send_active(minimum=FAMILIES) is True
        CampaignCredentialState.objects.update(
            population_dirty=True, version=F("version") + 1
        )
        prepare_all(harness)
        assert not reminder_messages().exists()
        assert set(
            TaskRun.objects.filter(
                root_id__in=FamilyMailPreparation.objects.filter(
                    occurrence_id__in=reminders().values("pk")
                ).values("task_id")
            ).values_list("state", flat=True)
        ) == {"queued"}
        CampaignCredentialState.objects.update(
            population_dirty=False, version=F("version") + 1
        )
        prepare_all(harness)
        assert reminder_messages().count() == FAMILIES
        with transaction.atomic():
            assert family_send_active(minimum=1) is False
    with campaign_clock(due_at), transaction.atomic():
        assert family_send_active(minimum=FAMILIES) is True


def test_pages_and_health_ignore_reminders_not_yet_due(invited):
    """A prepared reminder is upcoming, not a send in progress or late work."""
    from parishkit.stewardship.campaigns.work_locks import read_transaction
    from parishkit.stewardship.jobs import send_progress
    from parishkit.stewardship.jobs.due_work_health import DueWorkScan

    harness, path, provider, due_at = invited
    campaign = harness.campaign
    with campaign_clock(due_at - timedelta(minutes=30)):
        plan()
        prepare_all(harness)
        assert reminder_messages().count() == FAMILIES
        with read_transaction():
            counts = send_progress.read_send(
                campaign.pk, "production", 0, due_at - timedelta(minutes=30)
            )
            assert counts.kind == "initial" and not counts.in_progress
            assert send_progress.upcoming(
                campaign, "production", 0, due_at - timedelta(minutes=30)
            )
        scan = DueWorkScan()
        scan.begin()
        handlers = {DELIVERY: mail_owner(harness, path, None)}
        with task_login(ServiceRole.SCHEDULER, exact=True):
            hints, _ = collect_hints(handlers=handlers, health=scan)
        assert not hints and not scan.late
    with campaign_clock(due_at), read_transaction():
        counts = send_progress.read_send(campaign.pk, "production", 0, due_at)
        assert counts.kind == "reminder" and counts.in_progress
        assert not send_progress.upcoming(campaign, "production", 0, due_at)


@pytest.mark.parametrize(
    "overrides,warned",
    [
        ({}, False),
        ({"nightly_time": "07:30", "full_refresh_times": ("07:30",)}, True),
        # Another configured full refresh time (#465) inside the window.
        ({"full_refresh_times": ("02:00", "07:00")}, True),
        ({"frequency": "hourly"}, True),
    ],
)
def test_a_full_refresh_inside_a_lead_window_is_warned_once(
    invited, monkeypatch, caplog, overrides, warned
):
    """The bulk scheduler warns once when a full refresh hits a lead window."""
    from parishkit.stewardship.observability import Event, FailureKind
    from parishkit.stewardship.source import cadence

    harness, path, provider, due_at = invited
    real = cadence.refresh_settings
    monkeypatch.setattr(
        cadence, "refresh_settings", lambda settings: real(settings) | overrides
    )
    producer = FamilyScheduleProducer(uuid4(), bulk=True)
    with (
        caplog.at_level("WARNING", logger="parishkit.stewardship"),
        campaign_clock(due_at - timedelta(days=1)),
        task_login(ServiceRole.SCHEDULER, exact=True),
    ):
        for _ in range(2):
            with scheduler_session() as session:
                producer(session)
    warnings = [
        record
        for record in caplog.records
        if record.getMessage() == Event.REFRESH_LEAD_WINDOW_CONFLICT
        and record.levelname == "WARNING"
        and FailureKind.REFRESH_IN_LEAD_WINDOW in str(record.__dict__)
    ]
    assert len(warnings) == (1 if warned else 0)
    # A reminder already past on the campaign clock has no window to warn
    # about.
    caplog.clear()
    with (
        caplog.at_level("WARNING", logger="parishkit.stewardship"),
        campaign_clock(due_at + timedelta(minutes=1)),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session() as session,
    ):
        FamilyScheduleProducer(uuid4(), bulk=True)(session)
    assert not [
        record
        for record in caplog.records
        if FailureKind.REFRESH_IN_LEAD_WINDOW in str(record.__dict__)
    ]


def test_the_migrations_own_check_refuses_an_old_or_weakened_guard():
    """The frozen file's DO block passes only on the new, owner-run guard."""
    from parishkit.stewardship.campaigns.migrations import (
        __file__ as migrations_file,
    )

    frozen = (
        Path(migrations_file).parents[1].parent
        / "schema"
        / "migrations"
        / "0005_occurrence_prepare_ahead.sql"
    ).read_text(encoding="utf-8")
    check = frozen[frozen.index("DO $check$") :]

    def verified():
        """Whether the DO block commits."""
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(check)
        except Exception as error:  # noqa: BLE001
            assert "was not replaced" in str(error)
            return False
        return True

    assert verified()
    with old_guard():
        assert not verified()
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "ALTER FUNCTION public.stewardship_occurrence_guard_v1() "
                "SECURITY INVOKER"
            )
            assert not verified()
            raise Rollback
    except Rollback:
        pass
    assert verified()
