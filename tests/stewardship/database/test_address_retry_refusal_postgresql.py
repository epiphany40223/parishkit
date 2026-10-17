"""A follow-up's permanent refusal becomes a recipient refusal (#806, migration 0079).

The real mail worker sends a Production invitation to a Family with two
addresses and the fake provider refuses the second only for now, which opens
0077's follow-up. The real follow-up handler then sends the same email to
that address alone and the provider refuses it permanently: the same
transaction records a ``RecipientRefusal`` naming the accepted attempt's
event, so the address is set aside for the Family, while nothing is sent
again, rendered again or changed in the Family's credentials. These tests
also prove the replaced refusal guard admits no other follow-up evidence,
that a refusal that cannot be recorded never loses the follow-up's outcome,
and run the file's DO block.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import (
    address_retry,
    family_mail_delivery_tasks,
    recipient_suppressions,
)
from parishkit.stewardship.jobs.address_retry_models import AddressRetry
from parishkit.stewardship.jobs.delivery_reads import (
    email_refusals,
    with_refusal_counts,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxEvent
from parishkit.stewardship.jobs.recipient_models import RecipientRefusal

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service, response_source
from .test_address_retry_postgresql import (
    ADDRESSES,
    follow_up_owner,
    partly_accepted,
    run_follow_up,
    tokens,
)
from .test_family_mail_dispatch_postgresql import prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_worker_postgresql import (  # noqa: F401
    deliver,
    dispatch_worker,
)
from .test_recipient_suppressions_postgresql import refresh

pytestmark = pytest.mark.django_db(transaction=True)
FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations"
    / "0079_retry_refusal.sql"
).read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
REPLACE = FROZEN[
    FROZEN.index("CREATE OR REPLACE FUNCTION") : FROZEN.index("DO $check$")
]
INSERT_REFUSAL = (
    "INSERT INTO stewardship_recipient_refusal(id,actor_id,correlation_id,"
    "family_id,event_id,address,organization_id,family_duid)"
    " VALUES(%s,%s,%s,%s,%s,%s,%s,%s)"
)


def refused_permanently(dispatch, monkeypatch):
    """Run the follow-up once with the provider refusing it permanently.

    Returns the message, the follow-up after its outcome, and what was
    observed just before the follow-up ran (tokens, message version and
    outbox event count).
    """
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        harness, path, message, _ = partly_accepted(dispatch, monkeypatch)
        before = (tokens(), message.version, OutboxEvent.objects.count())
        sent = []

        def provider(value, settings, mail, **kwargs):
            sent.append(mail.recipients)
            return FamilyDeliveryResult(Status.PERMANENT, 1, permanent=(0,))

        monkeypatch.setattr(address_retry, "submit_family", provider)
        retry = run_follow_up(
            follow_up_owner(harness, path), AddressRetry.objects.get()
        )
    assert sent == [(ADDRESSES[1],)]
    return message, retry, before


def test_the_follow_up_sets_a_permanently_refused_address_aside(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """The follow-up's permanent refusal is a recipient refusal of the
    accepted attempt, by the follow-up's worker and run; Sent mail marks
    that email, and nothing about the email or credentials changes."""
    message, retry, before = refused_permanently(dispatch_worker, monkeypatch)
    assert retry.state == "finished" and retry.refused == [1] and retry.remaining == []
    assert TaskRun.objects.get(pk=retry.task_id).state == "succeeded"
    (refusal,) = RecipientRefusal.objects.all()
    family = FamilyCampaign.objects.get(pk=message.family_id)
    assert (
        refusal.event_id,
        refusal.address,
        refusal.family_id,
        refusal.family_duid,
        refusal.actor_id,
        refusal.correlation_id,
    ) == (
        retry.source_event_id,
        ADDRESSES[1],
        message.family_id,
        family.family_duid,
        retry.actor_id,
        retry.correlation_id,
    )
    assert with_refusal_counts([{"id": message.pk}]) == [
        {"id": message.pk, "refused": 1}
    ]
    assert [row["address"] for row in email_refusals(message.pk)] == [ADDRESSES[1]]
    # The other address still accepts mail, so the Family stays deliverable.
    assert family.email_deliverable
    message.refresh_from_db()
    assert before == (tokens(), message.version, OutboxEvent.objects.count())


def insert_refusal(message, address, actor_id, correlation_id, *, keep=True):
    """The SQLSTATE of one refusal insert against the message's accepted
    attempt, as the owner; ``keep=False`` rolls an admitted insert back."""
    event = OutboxEvent.objects.get(message=message, previous_state="submitting")
    family = FamilyCampaign.objects.get(pk=message.family_id)
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT s.organization_id FROM stewardship_campaign_credentials k"
                " JOIN stewardship_source_snapshot s ON s.id=k.source_snapshot_id"
                " WHERE k.campaign_id=%s",
                [family.campaign_id],
            )
            (organization_id,) = cursor.fetchone()
            cursor.execute(
                INSERT_REFUSAL,
                [
                    uuid4(),
                    actor_id,
                    correlation_id,
                    message.family_id,
                    event.pk,
                    address,
                    organization_id,
                    family.family_duid,
                ],
            )
            transaction.set_rollback(not keep)
    except DatabaseError as error:
        return error.__cause__.sqlstate
    return None


def without_recording(monkeypatch):
    """Run follow-ups without their own recording, to insert by hand."""
    monkeypatch.setattr(
        recipient_suppressions, "record_retry_refusal", lambda **kwargs: None
    )


def test_the_guard_admits_no_other_follow_up_evidence(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """Refused for now is not evidence, nor is a follow-up's outcome under
    another worker or run, nor an address its latest outcome did not refuse
    permanently, nor the same refusal twice."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        _, _, message, _ = partly_accepted(dispatch_worker, monkeypatch)
    retry = AddressRetry.objects.get()
    event = OutboxEvent.objects.get(pk=retry.source_event_id)

    def insert(address, actor_id, correlation_id):
        """The SQLSTATE of one kept refusal insert."""
        return insert_refusal(message, address, actor_id, correlation_id)

    # Before the follow-up ran, the attempt refused that address only for
    # now: not evidence, whoever claims it.
    assert insert(ADDRESSES[1], event.actor_id, event.correlation_id) == "23514"
    without_recording(monkeypatch)
    retry = refused_permanently_again(dispatch_worker, monkeypatch, retry)
    assert not RecipientRefusal.objects.exists()
    # Another worker or run, or the address that accepted, is not evidence.
    assert insert(ADDRESSES[1], uuid4(), retry.correlation_id) == "23514"
    assert insert(ADDRESSES[1], retry.actor_id, uuid4()) == "23514"
    assert insert(ADDRESSES[0], retry.actor_id, retry.correlation_id) == "23514"
    # The follow-up's own evidence is, once.
    assert insert(ADDRESSES[1], retry.actor_id, retry.correlation_id) is None
    assert insert(ADDRESSES[1], retry.actor_id, retry.correlation_id) == "23505"


def test_a_follow_up_refused_for_now_again_is_not_evidence(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """The latest outcome refused the address only for now: its own worker
    and run still cannot record a refusal of it."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        harness, path, message, _ = partly_accepted(dispatch_worker, monkeypatch)
        without_recording(monkeypatch)

        def provider(value, settings, mail, **kwargs):
            return FamilyDeliveryResult(Status.TRANSIENT, 1, transient=(0,))

        monkeypatch.setattr(address_retry, "submit_family", provider)
        retry = run_follow_up(
            follow_up_owner(harness, path), AddressRetry.objects.get()
        )
    assert retry.state == "waiting" and retry.outcome["transient"] == [1]
    assert retry.run_id == retry.correlation_id
    assert (
        insert_refusal(message, ADDRESSES[1], retry.actor_id, retry.correlation_id)
        == "23514"
    )


THREE = ("valid@example.org", "xother@example.org", "zother@example.org")


def test_a_stale_permanent_outcome_is_not_a_later_runs_evidence(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """A follow-up to two addresses refuses one permanently and keeps the
    other: that outcome is evidence only for the run that recorded it. A
    later run that ends the row without a send (the other address left the
    Family) keeps the outcome under its own actor and run, and neither that
    run nor the earlier one may then record the refusal."""
    harness, path = dispatch_worker
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    source = response_source()
    source.members[3]["emailAddress"] = "; ".join(THREE)
    refresh(harness, source)
    without_recording(monkeypatch)
    monkeypatch.setattr(address_retry, "RECIPIENT_RETRY_SECONDS", (1, 1, 1, 1))
    results = iter(
        (
            FamilyDeliveryResult(Status.ACCEPTED, 3, transient=(1, 2)),
            FamilyDeliveryResult(Status.TRANSIENT, 2, permanent=(0,), transient=(1,)),
        )
    )
    sent = []

    def provider(value, settings, mail, **kwargs):
        sent.append(mail.recipients)
        return next(results)

    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    monkeypatch.setattr(address_retry, "submit_family", provider)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message)
        message.refresh_from_db()
        owner = follow_up_owner(harness, path)
        first = run_follow_up(owner, AddressRetry.objects.get())
        assert sent == [THREE, THREE[1:]]
        assert (first.state, first.refused, first.remaining) == ("waiting", [1], [2])
        # The run that recorded it may (rolled back here).
        assert (
            insert_refusal(
                message, THREE[1], first.actor_id, first.correlation_id, keep=False
            )
            is None
        )
        # run_follow_up refreshes the row in place: keep what the first run
        # recorded.
        recorded = (first.actor_id, first.correlation_id, first.run_id, first.outcome)
        # A later run ends it unsent: no send, no new outcome.
        source.members[3]["emailAddress"] = THREE[0]
        refresh(harness, source)
        later = run_follow_up(owner, first)
    assert (later.state, later.reason) == ("cancelled", "recipients_changed")
    # A retry keeps the task run's id (the row's run) and claims it under a
    # new fence; here a new worker, too.
    assert later.outcome == recorded[3] and later.correlation_id == recorded[1]
    assert later.run_id == recorded[2] and later.actor_id != recorded[0]
    assert TaskRun.objects.get(pk=later.run_id).fence > later.task_fence
    assert len(sent) == 2
    for actor_id, correlation_id in (
        (later.actor_id, later.correlation_id),
        recorded[:2],
    ):
        assert insert_refusal(message, THREE[1], actor_id, correlation_id) == "23514"
    assert not RecipientRefusal.objects.exists()


def refused_permanently_again(dispatch, monkeypatch, retry):
    """Run an already opened follow-up with the provider refusing it for good."""
    harness, path = dispatch
    sent = []

    def provider(value, settings, mail, **kwargs):
        sent.append(mail.recipients)
        return FamilyDeliveryResult(Status.PERMANENT, 1, permanent=(0,))

    monkeypatch.setattr(address_retry, "submit_family", provider)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        retry = run_follow_up(follow_up_owner(harness, path), retry)
    assert sent == [(ADDRESSES[1],)] and retry.refused == [1]
    return retry


def test_a_refusal_that_cannot_be_recorded_keeps_the_outcome(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    caplog,
):
    """Recording runs in a savepoint: a failure is logged at ERROR and the
    follow-up still ends with its outcome, never resent."""

    def fail(**kwargs):
        raise PermissionError(
            "Recipient refusal requires definitive delivery evidence."
        )

    monkeypatch.setattr(recipient_suppressions, "record_retry_refusal", fail)
    _, retry, _ = refused_permanently(dispatch_worker, monkeypatch)
    assert retry.state == "finished" and retry.refused == [1]
    assert TaskRun.objects.get(pk=retry.task_id).state == "succeeded"
    assert not RecipientRefusal.objects.exists()
    assert any(
        record.levelname == "ERROR" and "recipient refusal" in record.getMessage()
        for record in caplog.records
    )


def test_the_migration_check_passes_installed_and_fails_otherwise():
    """The DO block accepts the installed guard and refuses one without the
    follow-up's evidence, as a definer, with another search_path, or with
    its trigger disabled or detached."""
    with connection.cursor() as cursor:
        cursor.execute(CHECK)
    without = REPLACE.replace(
        "FROM public.stewardship_outbox_address_retry a",
        "FROM public.stewardship_outbox_message a",
    )
    assert without != REPLACE
    guard = "public.stewardship_recipient_refusal_guard_v1()"
    for statement in (
        without,
        f"ALTER FUNCTION {guard} SECURITY DEFINER",
        f"ALTER FUNCTION {guard} SET search_path TO public",
        "ALTER TABLE public.stewardship_recipient_refusal"
        " DISABLE TRIGGER stewardship_recipient_refusal_guard",
        "DROP TRIGGER stewardship_recipient_refusal_guard"
        " ON public.stewardship_recipient_refusal",
    ):
        with (
            pytest.raises(DatabaseError, match="0079"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
            cursor.execute(CHECK)
