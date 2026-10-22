"""A report send skips the recipients that already have its slots (#1835).

Frozen file 0162's comparison (``stewardship_report_email_prior_v1``), the
recipient guard that holds every recipient row to it, and the completion
view that counts a covered recipient as delivered. Until the conversion
admits report schedules no report send can run end to end, so the earlier
sends here are fixture rows inserted without their guards (in the test's
own transaction, which is rolled back); the rows the tests check are
inserted with the guard in force.

The two rules: a recipient whose message for the send's slots the provider
already accepted is never sent it again, and one whose earlier message was
never accepted is never skipped. An earlier message that may still be, or
have been, accepted (still being submitted, or unknown after a crash
between the provider's acceptance and its record) blocks both until it
settles.
"""

import json
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from .test_scheduler_cheap_reads_postgresql import insert, loosen, scope

pytestmark = pytest.mark.django_db

ADDRESS = "staff@example.org"
OTHER = "other@example.org"
MEMBER = {"address": ADDRESS, "kind": "outside", "ref": None}
FIXTURES = (
    "stewardship_outbox_message",
    "stewardship_schedule_occurrence",
    "stewardship_report_email_preparation",
    "stewardship_report_email_snapshot",
    "stewardship_task_run",
    "stewardship_task_event",
    "stewardship_outbox_event",
)


def q(value):
    """One SQL literal for a fixture value (None is NULL)."""
    if value is None:
        return "NULL"
    if isinstance(value, (list, dict)):
        return "'" + json.dumps(value).replace("'", "''") + "'::jsonb"
    return "'" + str(value).replace("'", "''") + "'"


class Sends:
    """Fixture sends of one report, inserted with their guards switched off.

    The recipient table keeps its own check, so every recipient row here
    has a disposition the schema admits.
    """

    def __init__(self, cursor):
        self.cursor = cursor
        self.campaign = uuid4()
        self.definition = uuid4()
        cursor.execute("SET LOCAL session_replication_role = replica")
        for table in FIXTURES:
            loosen(cursor, table)

    def send(self, slots, *, occurrence="coalesced", mode="production", **scope):
        """One send's occurrence, preparation and snapshot; returns the snapshot.

        ``occurrence`` is its occurrence's state (an earlier send that a
        recovery send replaced is ``coalesced``; None for none); ``scope``
        overrides the definition, campaign or Testing epoch.
        """
        occurrence_id, preparation, snapshot = uuid4(), uuid4(), uuid4()
        if occurrence is None:
            occurrence_id = None
        else:
            insert(
                self.cursor,
                "stewardship_schedule_occurrence",
                {
                    "id": q(occurrence_id),
                    "occurrence_key": q(uuid4()),
                    "state": q(occurrence),
                    "mode": q(mode),
                },
            )
        insert(
            self.cursor,
            "stewardship_report_email_preparation",
            {
                "id": q(preparation),
                "campaign_id": q(scope.get("campaign", self.campaign)),
                "definition_id": q(scope.get("definition", self.definition)),
                "mode": q(mode),
                "rehearsal_epoch_id": q(scope.get("epoch")),
                "occurrence_id": q(occurrence_id),
                "covered_slots": q(slots),
                "phase": q("fanout"),
            },
        )
        insert(
            self.cursor,
            "stewardship_report_email_snapshot",
            {
                "id": q(snapshot),
                "preparation_id": q(preparation),
                "campaign_id": q(self.campaign),
                "recipients": q([MEMBER, {**MEMBER, "address": OTHER}]),
            },
        )
        return snapshot

    def message(self, snapshot, state, address=ADDRESS):
        """An earlier send's queued recipient and its message in ``state``."""
        message = uuid4()
        insert(
            self.cursor,
            "stewardship_outbox_message",
            {"id": q(message), "state": q(state)},
        )
        insert(
            self.cursor,
            "stewardship_report_email_recipient",
            {
                "snapshot_id": q(snapshot),
                "address": q(address),
                "kind": q("outside"),
                "ref": "NULL",
                "disposition": q("queued"),
                "outbox_id": q(message),
                "covered_messages": q([]),
            },
        )
        return message

    def unknown_before(self, message):
        """Record that the message's outcome was once unknown (its history)."""
        insert(
            self.cursor,
            "stewardship_outbox_event",
            {"message_id": q(message), "state": q("delivery_unknown")},
        )

    def settle(self, message, state):
        """Move an earlier message to ``state``, as mail dispatch would."""
        self.cursor.execute(
            "UPDATE stewardship_outbox_message SET state=%s WHERE id=%s",
            [state, message],
        )

    def prior(self, snapshot, address=ADDRESS):
        """The comparison's answer for one address of a send."""
        self.cursor.execute(
            "SELECT stewardship_report_email_prior_v1(%s,%s)", [snapshot, address]
        )
        value = self.cursor.fetchone()[0]
        return json.loads(value) if isinstance(value, str) else value


@pytest.fixture
def sends():
    """A rolled-back transaction holding one report's fixture sends."""
    with transaction.atomic(), connection.cursor() as cursor:
        yield Sends(cursor)
        transaction.set_rollback(True)


def covered(*messages):
    """The comparison's covered answer, with its messages sorted."""
    return {"state": "covered", "messages": sorted(str(m) for m in messages)}


NONE = {"state": "none"}


def uncertain(*messages):
    """The comparison's uncertain answer, naming the messages it waits on (#1872)."""
    return {"state": "uncertain", "waiting": sorted(str(m) for m in messages)}


def test_an_accepted_message_for_every_slot_covers_the_address(sends):
    """Delivered messages that together hold every slot cover it; others do not."""
    first = sends.send(["2026-10-05", "2026-10-06"])
    accepted = sends.message(first, "delivered")
    sends.message(first, "permanent_failure", address=OTHER)
    recovery = sends.send(["2026-10-05", "2026-10-06"], occurrence="pending")
    assert sends.prior(recovery) == covered(accepted)
    # The other address's earlier message was never accepted: it is sent.
    assert sends.prior(recovery, OTHER) == NONE
    # A wider recovery send holds a slot no accepted message had.
    wider = sends.send(["2026-10-05", "2026-10-06", "2026-10-07"], occurrence="pending")
    assert sends.prior(wider) == NONE
    # Until a later accepted message holds that slot too.
    later = sends.send(["2026-10-07"])
    second = sends.message(later, "delivered")
    assert sends.prior(wider) == covered(accepted, second)


def test_messages_never_accepted_never_cover(sends):
    """Unsent or refused messages of an ended send leave the address to be sent.

    An ended send's occurrence is final (succeeded, skipped or coalesced),
    so mail dispatch can never send or retry its messages.
    """
    for state, occurrence in (
        ("permanent_failure", "succeeded"),
        ("cancelled", "failed"),
        ("pending", "skipped"),
        ("retry_wait", "coalesced"),
    ):
        earlier = sends.send(["2026-10-05"], occurrence=occurrence)
        sends.message(earlier, state)
    recovery = sends.send(["2026-10-05"], occurrence="pending")
    assert sends.prior(recovery) == NONE


@pytest.mark.parametrize(
    "state,occurrence",
    [
        ("submitting", "coalesced"),
        ("delivery_unknown", "coalesced"),
        ("pending", "pending"),
        ("retry_wait", "running"),
        ("permanent_failure", "pending"),
        # An occurrence whose outcome is unknown, or that failed, can return
        # to pending, so its messages could still be sent.
        ("pending", "delivery_unknown"),
        ("permanent_failure", "failed"),
        # With no occurrence at all, nothing proves the send ended.
        ("pending", None),
    ],
)
def test_a_message_that_may_still_be_accepted_blocks_the_decision(
    sends, state, occurrence
):
    """Still in flight, unknown, or retryable while its send may run: uncertain.

    A pending, retry_wait or failed message whose own occurrence is closed
    can never be submitted again, so it is not uncertain.
    """
    earlier = sends.send(["2026-10-05"], occurrence=occurrence)
    waiting = sends.message(earlier, state)
    recovery = sends.send(["2026-10-05"], occurrence="pending")
    assert sends.prior(recovery) == uncertain(waiting)
    # Once its send has ended for good, an unsent or refused message never
    # will be sent; one being submitted or unknown stays uncertain.
    if occurrence is not None and state not in {"submitting", "delivery_unknown"}:
        sends.cursor.execute(
            "UPDATE stewardship_schedule_occurrence SET state='coalesced' "
            "WHERE id=(SELECT occurrence_id FROM stewardship_report_email_preparation "
            "p JOIN stewardship_report_email_snapshot s ON s.preparation_id=p.id "
            "WHERE s.id=%s)",
            [earlier],
        )
        assert sends.prior(recovery) == NONE
    # An accepted message for every slot decides it whatever else is open.
    other = sends.send(["2026-10-05"])
    accepted = sends.message(other, "delivered")
    assert sends.prior(recovery) == covered(accepted)


@pytest.mark.parametrize("outcome", ["delivered", "permanent_failure"])
def test_a_crash_between_acceptance_and_its_record_waits_for_the_outcome(
    sends, outcome
):
    """The provider accepted a message but its record was lost: never guess.

    The message stays submitting, then is marked delivery_unknown; the
    recovery send's decision waits through both. Reconciliation then
    records the real outcome: accepted covers the address (never a second
    copy), refused leaves it to be sent (never skipped).
    """
    earlier = sends.send(["2026-10-05"])
    message = sends.message(earlier, "submitting")
    recovery = sends.send(["2026-10-05"], occurrence="pending")
    assert sends.prior(recovery) == uncertain(message)
    sends.settle(message, "delivery_unknown")
    assert sends.prior(recovery) == uncertain(message)
    sends.settle(message, outcome)
    assert sends.prior(recovery) == (
        covered(message) if outcome == "delivered" else NONE
    )


@pytest.mark.parametrize(
    "state,expected",
    [
        ("pending", "uncertain"),
        ("retry_wait", "uncertain"),
        ("cancelled", "uncertain"),
        ("permanent_failure", "none"),
        ("delivered", "covered"),
    ],
)
def test_an_unknown_outcome_in_the_history_stays_uncertain(sends, state, expected):
    """A resend or idempotent retry after an unknown outcome is not proof.

    The earlier attempt may have been accepted, so only delivery or a final
    refusal decides, even after the earlier send ended.
    """
    earlier = sends.send(["2026-10-05"])
    message = sends.message(earlier, state)
    sends.unknown_before(message)
    recovery = sends.send(["2026-10-05"], occurrence="pending")
    answer = sends.prior(recovery)
    assert answer["state"] == expected
    if expected == "covered":
        assert answer == covered(message)


def test_only_the_same_reports_earlier_sends_count(sends):
    """Another report, campaign, mode, Testing epoch or address never covers."""
    for elsewhere_scope in (
        {"definition": uuid4()},
        {"campaign": uuid4()},
        {"mode": "testing", "epoch": uuid4()},
    ):
        earlier = sends.send(["2026-10-05"], **elsewhere_scope)
        sends.message(earlier, "delivered")
    elsewhere = sends.send(["2026-10-05"])
    sends.message(elsewhere, "delivered", address=OTHER)
    recovery = sends.send(["2026-10-05"], occurrence="pending")
    assert sends.prior(recovery) == NONE
    # A rehearsal compares only within its own Testing epoch.
    epoch = uuid4()
    rehearsed = sends.send(["2026-10-05"], mode="testing", epoch=epoch)
    accepted = sends.message(rehearsed, "delivered")
    again = sends.send(
        ["2026-10-05"], mode="testing", epoch=epoch, occurrence="pending"
    )
    assert sends.prior(again) == covered(accepted)


def test_send_now_covers_nothing_and_is_never_covered(sends):
    """A manual send has no slot: it always sends, and never covers a slot."""
    manual = sends.send([])
    sends.message(manual, "delivered")
    scheduled = sends.send(["2026-10-05"], occurrence="pending")
    assert sends.prior(scheduled) == NONE
    earlier = sends.send(["2026-10-05"])
    sends.message(earlier, "delivered")
    again = sends.send([], occurrence="pending")
    assert sends.prior(again) == NONE


def recipient(
    snapshot, disposition, messages=(), outbox=None, guarded=True, **ownership
):
    """Insert one recipient row, with the guard in force unless not ``guarded``."""
    identifier = uuid4()
    with connection.cursor() as cursor:
        role = "origin" if guarded else "replica"
        cursor.execute(f"SET LOCAL session_replication_role = {role}")
        cursor.execute(
            "INSERT INTO stewardship_report_email_recipient(id,correlation_id,"
            "actor_id,snapshot_id,address,kind,ref,disposition,outbox_id,"
            "covered_messages) VALUES(%s,%s,%s,%s,%s,'outside',NULL,%s,%s,%s::jsonb)",
            [
                identifier,
                ownership.get("correlation", uuid4()),
                ownership.get("worker"),
                snapshot,
                ADDRESS,
                disposition,
                outbox,
                json.dumps([str(m) for m in messages]),
            ],
        )
    return identifier


def refused(message, *arguments, **keywords):
    """The guarded insert is refused with exactly ``message``."""
    with pytest.raises(DatabaseError, match=message), transaction.atomic():
        recipient(*arguments, **keywords)


def test_the_guard_holds_each_row_to_the_comparison(sends):
    """A covered row needs exactly the accepted messages; a queued one none.

    Every refusal comes before the ownership checks, so each is its own;
    a row that passes them is then refused for lacking a live owner, as
    every report row is until the conversion.
    """
    first = sends.send(["2026-10-05"])
    accepted = sends.message(first, "delivered")
    recovery = sends.send(["2026-10-05"], occurrence="pending")
    refused("must reuse its accepted coverage", recovery, "queued", outbox=uuid4())
    refused("lacks exact accepted coverage", recovery, "covered", [uuid4()])
    refused("lacks its exact cohort message", recovery, "covered", [accepted])
    # Uncovered: a covered row is a forgery, and a queued one needs an owner.
    fresh = sends.send(["2026-10-06"], occurrence="pending")
    refused("lacks exact accepted coverage", fresh, "covered", [accepted])
    refused("lacks its exact cohort message", fresh, "queued", outbox=uuid4())
    # Uncertain: neither is admitted until the earlier message settles.
    pending = sends.send(["2026-10-07"])
    inflight = sends.message(pending, "submitting")
    waiting = sends.send(["2026-10-07"], occurrence="pending")
    refused("waits for an earlier message to settle", waiting, "queued", outbox=uuid4())
    refused("waits for an earlier message to settle", waiting, "covered", [inflight])
    # The table's own check (the guard aside): a covered row has messages
    # and no outbox; a revoked or queued one has no messages.
    check = "report_email_recipient_coverage"
    refused(check, recovery, "covered", guarded=False)
    refused(check, recovery, "covered", [accepted], outbox=uuid4(), guarded=False)
    refused(check, fresh, "revoked", [accepted], guarded=False)
    refused(check, fresh, "queued", [accepted], outbox=uuid4(), guarded=False)


def owned(sends, snapshot, *, current=True):
    """Give a fixture send a live claim; returns (worker, claim event).

    The live check answers true inside the rolled-back transaction, and the
    recheck answers ``current``, so only the coverage rule and the recheck
    decide what the guard admits.
    """
    cursor = sends.cursor
    scope(cursor, "stewardship_report_email_live_v1", True)
    scope(cursor, "stewardship_report_email_current_v1", current)
    worker, run, event = uuid4(), uuid4(), uuid4()
    cursor.execute(
        "SELECT p.task_id FROM stewardship_report_email_preparation p "
        "JOIN stewardship_report_email_snapshot s ON s.preparation_id=p.id "
        "WHERE s.id=%s",
        [snapshot],
    )
    (task,) = cursor.fetchone()
    cursor.execute("SET LOCAL session_replication_role = replica")
    insert(
        cursor,
        "stewardship_task_run",
        {"id": q(run), "root_id": q(task), "worker_id": q(worker), "fence": "7"},
    )
    insert(
        cursor,
        "stewardship_task_event",
        {
            "id": q(event),
            "run_id": q(run),
            "action": q("claim"),
            "fence": "7",
            "worker_id": q(worker),
            "state": q("running"),
        },
    )
    return {"worker": worker, "correlation": event}


def test_a_live_fanout_records_a_covered_recipient_once(sends):
    """With a live owner the guard admits the covered row, and only once.

    A replayed page cannot add a second row for the address (one per
    snapshot and address), so a retry never sends the covered recipient a
    message either.
    """
    first = sends.send(["2026-10-05"])
    accepted = sends.message(first, "delivered")
    recovery = sends.send(["2026-10-05"], occurrence="pending")
    ownership = owned(sends, recovery)
    refused(
        "must reuse its accepted coverage",
        recovery,
        "queued",
        outbox=uuid4(),
        **ownership,
    )
    with transaction.atomic():
        recipient(recovery, "covered", [accepted], **ownership)
    refused("report_email_recipient_once", recovery, "covered", [accepted], **ownership)


def test_a_covered_recipient_must_still_be_a_recipient(sends):
    """The recheck comes before coverage: a removed address is revoked.

    The guard refuses a covered row for an address that is no longer a
    recipient, so an accepted earlier message never stands in for a
    revocation.
    """
    first = sends.send(["2026-10-05"])
    accepted = sends.message(first, "delivered")
    recovery = sends.send(["2026-10-05"], occurrence="pending")
    ownership = owned(sends, recovery, current=False)
    refused(
        "lacks its exact cohort message", recovery, "covered", [accepted], **ownership
    )
    with transaction.atomic():
        recipient(recovery, "revoked", **ownership)


def completion(cursor, snapshot):
    """The completion view's disposition for a send, None while unsettled."""
    cursor.execute(
        "SELECT c.disposition FROM stewardship_report_email_completion_ready c "
        "JOIN stewardship_report_email_snapshot s ON s.preparation_id=c.preparation_id "
        "WHERE s.id=%s",
        [snapshot],
    )
    row = cursor.fetchone()
    return row and row[0]


def test_a_covered_recipient_settles_as_delivered(sends):
    """A covered address is settled and counts as delivered; others still wait."""
    first = sends.send(["2026-10-05"])
    accepted = sends.message(first, "delivered")
    recovery = sends.send(["2026-10-05"], occurrence="pending")
    cursor = sends.cursor
    cursor.execute(
        "UPDATE stewardship_report_email_preparation SET phase='complete' "
        "WHERE id=(SELECT preparation_id FROM stewardship_report_email_snapshot "
        "WHERE id=%s)",
        [recovery],
    )
    insert(
        cursor,
        "stewardship_report_email_recipient",
        {
            "snapshot_id": q(recovery),
            "address": q(ADDRESS),
            "kind": q("outside"),
            "ref": "NULL",
            "disposition": q("covered"),
            "covered_messages": q([str(accepted)]),
        },
    )
    other = sends.message(recovery, "pending", address=OTHER)
    assert completion(cursor, recovery) is None
    sends.settle(other, "cancelled")
    cursor.execute(
        "UPDATE stewardship_outbox_message SET reason='recipient_revoked' WHERE id=%s",
        [other],
    )
    # One covered recipient and one revoked: the send was delivered.
    assert completion(cursor, recovery) == "delivered"
