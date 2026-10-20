"""Seeing and ending a report send's wait on an earlier message (#1872).

Frozen file 0260's coverage decision (an Administrator's "counts as
delivered" or "counts as not delivered" about an earlier report message
that was once unknown and then cancelled), its insert guard, and the
comparison that reads it and names what an uncertain answer waits on;
frozen file 0261's Administrator-only read of the sends that wait. As in
#1835's tests (``test_report_recovery_coverage_postgresql``), no report
send can run end to end before the conversion, so the earlier sends are
fixture rows inserted without their guards, in the test's own transaction,
which is rolled back; the rows under test go through the guard.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from .test_report_recovery_coverage_postgresql import (
    ADDRESS,
    MEMBER,
    NONE,
    OTHER,
    Sends,
    covered,
    q,
    uncertain,
)
from .test_scheduler_cheap_reads_postgresql import insert, loosen, scope

pytestmark = pytest.mark.django_db

WEB = "pk_stewardship_web"
ADMIN = uuid4()
MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)


class Decisions(Sends):
    """Fixture sends whose earlier messages are report emails, as the guard reads.

    Each message is an ``admin_report`` message of a campaign, whose
    semantic key is its recipient row, so the decision guard's ownership
    checks hold; the evidence note is a fixture row too, since the
    resolution guard admits report emails only after the conversion. The
    Administrator and campaign admissions answer true unless a test says
    otherwise.
    """

    def __init__(self, cursor):
        super().__init__(cursor)
        loosen(cursor, "stewardship_delivery_resolution")
        # The fixture campaign has no parish row for the audit event to name.
        loosen(cursor, "stewardship_audit_event")
        scope(cursor, "stewardship_export_authorized_v1", True)
        scope(cursor, "stewardship_export_admitted_v1", True)
        scope(cursor, "stewardship_report_email_current_v1", True)

    def message(self, snapshot, state, address=ADDRESS, *, version=3):
        """An earlier send's queued recipient and its report message in ``state``."""
        message, row = uuid4(), uuid4()
        insert(
            self.cursor,
            "stewardship_outbox_message",
            {
                "id": q(message),
                "state": q(state),
                "purpose": q("admin_report"),
                "semantic_key": q(row),
                "campaign_id": q(self.campaign),
                "version": str(version),
            },
        )
        insert(
            self.cursor,
            "stewardship_report_email_recipient",
            {
                "id": q(row),
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

    def note(self, message, *, version=3, actor=ADMIN, action="note", at=None):
        """The evidence note a decision is recorded with; returns its id.

        ``at`` is a SQL instant for an older note; by default it is made in
        the test's transaction, as the command makes it.
        """
        identifier = uuid4()
        insert(
            self.cursor,
            "stewardship_delivery_resolution",
            {
                **({"created_at": at} if at else {}),
                "id": q(identifier),
                "correlation_id": q(identifier),
                "actor_id": q(actor),
                "message_id": q(message),
                "expected_version": str(version),
                "action": q(action),
                "evidence_note": q("Checked the Sent folder."),
                "duplicate_acknowledged": "false",
            },
        )
        return identifier


@pytest.fixture
def decisions():
    """A rolled-back transaction holding one report's fixture sends."""
    with transaction.atomic(), connection.cursor() as cursor:
        yield Decisions(cursor)
        transaction.set_rollback(True)


def decide(message, decision, note, *, actor=ADMIN, as_web=True, correlation=None):
    """Record one decision with the guard in force, as web does.

    The web login is a disposable role inside the rolled-back transaction,
    granted only the decision table, as runtime_grants grants it. A refused
    insert's savepoint rollback (``refused``) also undoes the role and
    replication settings, so they are reset here only after a success.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = origin")
        if as_web:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", [WEB])
            if cursor.fetchone() is None:
                cursor.execute(f'CREATE ROLE "{WEB}" LOGIN NOINHERIT')
            cursor.execute(
                "GRANT SELECT, INSERT ON stewardship_report_email_coverage_decision "
                f'TO "{WEB}"'
            )
            cursor.execute(f'SET LOCAL SESSION AUTHORIZATION "{WEB}"')
        cursor.execute(
            "INSERT INTO stewardship_report_email_coverage_decision"
            "(id,correlation_id,actor_id,message_id,decision) "
            "VALUES(%s,%s,%s,%s,%s)",
            [note, correlation or note, actor, message, decision],
        )
        cursor.execute("RESET SESSION AUTHORIZATION")
        cursor.execute("SET LOCAL session_replication_role = replica")


def refused(message, *arguments, **keywords):
    """The guarded decision is refused with exactly ``message``."""
    with pytest.raises(DatabaseError, match=message), transaction.atomic():
        decide(*arguments, **keywords)


def stuck(decisions, address=ADDRESS):
    """An earlier message once unknown, then cancelled, and the send it holds.

    Returns ``(message, recovery snapshot)``.
    """
    earlier = decisions.send(["2026-10-05"])
    message = decisions.message(earlier, "cancelled", address)
    decisions.unknown_before(message)
    return message, decisions.send(["2026-10-05"], occurrence="pending")


def test_an_uncertain_answer_names_every_message_it_waits_on(decisions):
    """The comparison lists, sorted, each earlier message still in doubt."""
    first = decisions.send(["2026-10-05"])
    submitting = decisions.message(first, "submitting")
    second = decisions.send(["2026-10-06"])
    unknown = decisions.message(second, "delivery_unknown")
    # A refused message of an ended send is not in doubt.
    third = decisions.send(["2026-10-05"], occurrence="succeeded")
    decisions.message(third, "permanent_failure")
    recovery = decisions.send(["2026-10-05", "2026-10-06"], occurrence="pending")
    assert decisions.prior(recovery) == uncertain(submitting, unknown)


@pytest.mark.parametrize("decision", ["delivered", "unsent"])
def test_a_decision_settles_a_message_nothing_else_can(decisions, decision):
    """Once unknown, then cancelled: it waits until an Administrator decides.

    Counted as delivered, it covers its slots and is named among the
    covered messages; counted as not delivered, the address gets its own.
    """
    message, recovery = stuck(decisions)
    assert decisions.prior(recovery) == uncertain(message)
    decide(message, decision, decisions.note(message))
    assert decisions.prior(recovery) == (
        covered(message) if decision == "delivered" else NONE
    )
    # Every audit event names the decision.
    decisions.cursor.execute(
        "SELECT count(*) FROM stewardship_audit_event WHERE event_type=%s",
        [f"report_email_coverage_{decision}"],
    )
    assert decisions.cursor.fetchone()[0] == 1


def test_a_delivered_decision_covers_with_other_accepted_messages(decisions):
    """A decided message holds its slots alongside delivered ones."""
    message, _ = stuck(decisions)
    later = decisions.send(["2026-10-06"])
    accepted = decisions.message(later, "delivered")
    decide(message, "delivered", decisions.note(message))
    both = decisions.send(["2026-10-05", "2026-10-06"], occurrence="pending")
    assert decisions.prior(both) == covered(message, accepted)
    # The other address had no such message: nothing covers it.
    assert decisions.prior(both, OTHER) == NONE


def test_the_guard_admits_only_an_exact_current_decision(decisions):
    """Each refusal is the guard's own; the table's checks come first."""
    message, _ = stuck(decisions)
    reason = "lacks current exact Admin authority"
    # Only web records a decision.
    refused(reason, message, "delivered", decisions.note(message), as_web=False)
    # With a note that is not this message's, not a note, by someone else,
    # or at an earlier version.
    refused(reason, message, "delivered", uuid4())
    other, _ = stuck(decisions, OTHER)
    refused(reason, message, "delivered", decisions.note(other))
    refused(reason, message, "delivered", decisions.note(message, action="accept"))
    refused(reason, message, "delivered", decisions.note(message, actor=uuid4()))
    refused(reason, message, "delivered", decisions.note(message, version=2))
    # A note recorded before this transaction cannot be reused.
    old = decisions.note(message, at="transaction_timestamp() - interval '1 day'")
    refused(reason, message, "delivered", old)
    note = decisions.note(message)
    refused(reason, message, "delivered", note, correlation=uuid4())
    # A message that is not cancelled, or never was unknown.
    for state in ("delivered", "permanent_failure", "pending"):
        earlier = decisions.send(["2026-10-07"])
        open_message = decisions.message(earlier, state)
        decisions.unknown_before(open_message)
        refused(reason, open_message, "unsent", decisions.note(open_message))
    earlier = decisions.send(["2026-10-08"])
    never_unknown = decisions.message(earlier, "cancelled")
    refused(reason, never_unknown, "unsent", decisions.note(never_unknown))
    # Not a report email.
    decisions.cursor.execute(
        "UPDATE stewardship_outbox_message SET purpose='daily_digest' WHERE id=%s",
        [never_unknown],
    )
    decisions.unknown_before(never_unknown)
    refused(reason, never_unknown, "unsent", decisions.note(never_unknown))
    # Not an Administrator now, or the campaign accepts no Admin change.
    for function in (
        "stewardship_export_authorized_v1",
        "stewardship_export_admitted_v1",
    ):
        scope(decisions.cursor, function, False)
        refused(reason, message, "delivered", decisions.note(message))
        scope(decisions.cursor, function, True)
    # An unknown decision is the table's check; then one decision only.
    refused("report_email_coverage_decision_known", message, "maybe", note)
    decide(message, "unsent", note)
    refused("message_id", message, "delivered", decisions.note(message))


def test_a_decision_is_never_changed(decisions):
    """The decision row is immutable, as the resolution journal is."""
    message, _ = stuck(decisions)
    note = decisions.note(message)
    decide(message, "delivered", note)
    for statement in (
        "UPDATE stewardship_report_email_coverage_decision SET decision='unsent'",
        "DELETE FROM stewardship_report_email_coverage_decision",
    ):
        with pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
            decisions.cursor.execute("SET LOCAL session_replication_role = origin")
            decisions.cursor.execute(statement)


def waiting(actor=ADMIN):
    """The waiting read's rows, as (preparation, message) pairs."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT preparation_id, message_id "
            "FROM stewardship_report_email_waiting_v1(%s)",
            [actor],
        )
        return cursor.fetchall()


def preparation(decisions, snapshot):
    """The preparation of a fixture send's snapshot."""
    decisions.cursor.execute(
        "SELECT preparation_id FROM stewardship_report_email_snapshot WHERE id=%s",
        [snapshot],
    )
    return decisions.cursor.fetchone()[0]


def test_the_waiting_read_names_each_waiting_send_and_message(decisions):
    """Sends in fanout whose unhandled current recipients wait, never addresses."""
    message, recovery = stuck(decisions)
    send = preparation(decisions, recovery)
    assert waiting() == [(send, message)]
    # The read returns ids only: no address column.
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM stewardship_report_email_waiting_v1(%s)", [ADMIN])
        assert [column.name for column in cursor.description] == [
            "preparation_id",
            "due_at",
            "message_id",
        ]
    # An address no longer a recipient is revoked, never compared.
    scope(decisions.cursor, "stewardship_report_email_current_v1", False)
    assert waiting() == []
    scope(decisions.cursor, "stewardship_report_email_current_v1", True)
    # Once the address has its row, the send no longer waits on it.
    insert(
        decisions.cursor,
        "stewardship_report_email_recipient",
        {
            "snapshot_id": q(recovery),
            "address": q(ADDRESS),
            "kind": q("outside"),
            "ref": "NULL",
            "disposition": q("revoked"),
            "outbox_id": "NULL",
            "covered_messages": q([]),
        },
    )
    assert waiting() == []
    # A decision ends the wait of a new send too.
    again = decisions.send(["2026-10-05"], occurrence="pending")
    assert waiting() == [(preparation(decisions, again), message)]
    decide(message, "unsent", decisions.note(message))
    assert waiting() == []


def test_the_waiting_read_skips_sends_not_in_fanout(decisions):
    """Only a send fanning out waits; one complete or capturing does not."""
    message, recovery = stuck(decisions)
    decisions.cursor.execute(
        "UPDATE stewardship_report_email_preparation SET phase='complete' WHERE id=%s",
        [preparation(decisions, recovery)],
    )
    assert waiting() == []


def test_the_waiting_read_requires_an_administrator(decisions):
    """Refused (42501) for an actor who is not an Administrator now."""
    stuck(decisions)
    scope(decisions.cursor, "stewardship_export_authorized_v1", False)
    with (
        pytest.raises(DatabaseError, match="require an Administrator") as raised,
        transaction.atomic(),
    ):
        waiting()
    assert raised.value.__cause__.sqlstate == "42501"


@pytest.mark.parametrize(
    "name", ["0260_report_coverage_decisions.sql", "0261_report_waiting_read.sql"]
)
def test_each_files_check_passes_as_installed(name):
    """The DO block of each file passes on the migrated database."""
    text = (MIGRATIONS / name).read_text(encoding="utf-8")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(text[text.index("DO $check$") :])
        transaction.set_rollback(True)


def test_the_files_check_refuses_an_older_comparison():
    """0260's check fails when 0250's comparison is back in place."""
    old = (MIGRATIONS / "0250_report_recovery_coverage.sql").read_text(encoding="utf-8")
    start = old.index("CREATE FUNCTION public.stewardship_report_email_prior_v1(")
    body = old[start : old.index("$$;", old.index("AS $$", start)) + 3]
    text = (MIGRATIONS / "0260_report_coverage_decisions.sql").read_text(
        encoding="utf-8"
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(body.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
        with (
            pytest.raises(DatabaseError, match="coverage comparison"),
            transaction.atomic(),
        ):
            cursor.execute(text[text.index("DO $check$") :])
        transaction.set_rollback(True)


def test_a_member_listed_twice_is_one_waiting_row(decisions):
    """Distinct rows: one per send and message, however the cohort reads."""
    message, recovery = stuck(decisions)
    decisions.cursor.execute(
        "UPDATE stewardship_report_email_snapshot SET recipients=%s::jsonb WHERE id=%s",
        [json.dumps([MEMBER, MEMBER]), recovery],
    )
    assert waiting() == [(preparation(decisions, recovery), message)]


def test_the_worker_can_run_the_comparison_with_its_own_grants(decisions):
    """The fanout and the recipient guard run the comparison as the worker.

    A disposable role holding exactly the worker's runtime grants reads
    every table the comparison names, the coverage decisions too.
    """
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.runtime_grants import runtime_grants

    from .role_grants import grant_runtime

    message, recovery = stuck(decisions)
    decide(message, "delivered", decisions.note(message))
    role = "pk_test_1872_worker"
    cursor = decisions.cursor
    cursor.execute(f'CREATE ROLE "{role}" NOLOGIN NOINHERIT')
    tables, columns = runtime_grants(ServiceRole.WORKER)
    grant_runtime(cursor, role, tables, columns, frozenset())
    cursor.execute(f'SET LOCAL SESSION AUTHORIZATION "{role}"')
    answer = decisions.prior(recovery)
    cursor.execute("RESET SESSION AUTHORIZATION")
    assert answer == covered(message)
