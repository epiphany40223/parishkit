"""Send statistics in Family outcome evidence (#284), on PostgreSQL.

Both transports record them; they hold no personal data; the SQL result
validator and every '{"health":...' prefix reader still accept the note;
and a database whose validator predates them gets none (never a failed
settlement). test_mail_send_report_postgresql.py runs the report over them.
"""

import json
import re

import pytest
from django.db import connection, transaction

from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import family_mail_delivery_tasks as tasks
from parishkit.stewardship.jobs import family_mail_dispatch, family_mail_results
from parishkit.stewardship.jobs.family_mail_results import event_result
from parishkit.stewardship.jobs.outbox_models import OutboxEvent

from .campaign_builders import campaign_clock
from .test_family_mail_dispatch_postgresql import prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_session_postgresql import (  # noqa: F401
    batch,
    fast_limits,
    task,
    wait_past,
)
from .test_family_mail_worker_postgresql import (  # noqa: F401
    deliver,
    dispatch_worker,
)

pytestmark = pytest.mark.django_db(transaction=True)
WORKER_KEYS = {"transport", "wait_ms", "request_ms", "submit_ms", "total_ms"}
HELPER_KEYS = {"smtp_ms", "conn_reused", "conn_seq", "conn_index", "token_refreshed"}


@pytest.fixture(autouse=True)
def fresh_admission(monkeypatch):
    """Each test sees the installed validator, not an earlier test's answer."""
    monkeypatch.setattr(
        family_mail_results,
        "_admitted",
        {"value": None, "checked": 0.0, "refused": False},
    )


def outcomes(message):
    """The message's provider outcome events, oldest first."""
    return list(
        OutboxEvent.objects.filter(
            message=message, previous_state="submitting"
        ).order_by("version")
    )


def stats_of(event):
    """The statistics stored with one outcome."""
    return json.loads(event.evidence_note)["stats"]


def validator(event):
    """What stewardship_family_smtp_result_v1 returns for the event."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_family_smtp_result_v1(%s)::text", [event.pk])
        value = cursor.fetchone()[0]
    return None if value is None else json.loads(value)


def per_message_owner(harness, path, monkeypatch, results):
    """The one-helper-per-message handler, its helper replaced by ``results``."""
    owner = tasks.delivery_handler(
        harness.service.store,
        private=harness.rings.private,
        public_origin="http://localhost:8000",
        credential_path=path,
        batched=False,
    )

    def one_helper(payload, **kwargs):
        """A one-message helper's closed result line, with its statistics."""
        return kwargs["decode"](json.dumps(results.pop(0).wire_payload()).encode())

    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private", one_helper
    )
    return owner


def test_a_batched_outcome_records_its_statistics(batch):  # noqa: F811
    """Worker, session and helper statistics, and no personal data."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(batch.harness)
        deliver(batch.harness, batch.path, message, batch.owner)
    (event,) = outcomes(message)
    stats = stats_of(event)
    assert set(stats) >= WORKER_KEYS | HELPER_KEYS
    assert stats["transport"] == "batched" and stats["helper_seq"] == 1
    assert stats["helper_index"] == 1 and stats["conn_seq"] == 1
    assert re.fullmatch(r"h[0-9a-f]{12}", stats["helper_id"])
    note = event.evidence_note
    assert "@" not in note and note.startswith('{"health":"healthy",')
    for address in (
        message.render.routed_recipients + message.render.intended_recipients
    ):
        assert address not in note
    # The SQL validator accepts it and returns the outcome without them.
    checked = validator(event)
    assert checked is not None and "stats" not in checked
    assert event_result(event).status is Status.ACCEPTED


def test_a_per_message_outcome_records_comparable_statistics(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """The fallback transport records the same helper and worker fields."""
    harness, path = dispatch_worker
    helper = {
        "smtp_ms": 40,
        "connect_ms": 20,
        "auth_ms": 10,
        "token_ms": 30,
        "conn_reused": False,
        "conn_seq": 1,
        "conn_index": 1,
        "token_refreshed": True,
    }
    owner = per_message_owner(
        harness,
        path,
        monkeypatch,
        [FamilyDeliveryResult(Status.ACCEPTED, 1, stats=helper)],
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message, owner)
    (event,) = outcomes(message)
    stats = stats_of(event)
    assert stats["transport"] == "per_message" and stats["helper_index"] == 1
    assert helper.items() <= stats.items() and set(stats) >= WORKER_KEYS
    assert validator(event) is not None


def test_outage_statistics_keep_the_prefix_inference(batch):  # noqa: F811
    """A definitely unsent outage is still recognized by its health prefix."""
    gmail = batch.gmail
    gmail.script(("connect", ["disconnect"]))
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(batch.harness)
        deliver(batch.harness, batch.path, message, batch.owner)
    (event,) = outcomes(message)
    assert event.evidence_note.startswith('{"health":"unavailable",')
    assert stats_of(event)["conn_end"] == "connect_failed"
    assert family_mail_dispatch.shared_fault(event.reason, event.evidence_note)
    # limit_history reads the same stored notes: the outage is spared.
    spared = family_mail_dispatch.limit_history(message).spared
    assert spared == 1
    assert OutboxEvent.objects.filter(
        pk=event.pk, evidence_note__startswith='{"health":"unavailable",'
    ).exists()


def replace_validator(body):
    """Install ``body`` as the result validator's definition."""
    with connection.cursor() as cursor:
        cursor.execute(body)


def installed_validator():
    """The installed validator's full CREATE statement."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'public.stewardship_family_smtp_result_v1(uuid)'::regprocedure)"
        )
        return cursor.fetchone()[0]


def test_an_older_validator_gets_no_statistics_and_still_settles(
    batch,  # noqa: F811
    monkeypatch,
):
    """Deploy order: the new release before its in-place SQL still sends mail.

    Without the gate the old validator would refuse the whole settlement.
    """
    current = installed_validator()
    start = current.index("    -- Optional send statistics (#284)")
    end = current.index("    IF jsonb_typeof(result)<>'object' OR NOT result ?&")
    replace_validator(current[:start] + current[end:])
    try:
        assert family_mail_results.stats_admitted() is False
        with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
            message = prepare(batch.harness)
            deliver(batch.harness, batch.path, message, batch.owner)
        (event,) = outcomes(message)
        assert event.state == "delivered" and '"stats"' not in event.evidence_note
    finally:
        replace_validator(current)
    monkeypatch.setattr(
        family_mail_results,
        "_admitted",
        {"value": None, "checked": 0.0, "refused": False},
    )
    assert family_mail_results.stats_admitted() is True


def test_a_validator_lacking_a_new_word_still_settles_without_stats(
    batch,  # noqa: F811
    monkeypatch,
    caplog,
):
    """A later release's word, before its in-place SQL: the outcome still stands.

    The installed validator here lacks the word "batched", as an older one
    would lack a word a later release adds. The md5 admission check alone
    already withholds statistics from it; with that check bypassed, the
    database refuses the note and the settlement is retried without them,
    so the accepted message is still recorded as delivered.
    """
    current = installed_validator()
    older = current.replace("('transport','batched'),", "", 1)
    assert older != current
    replace_validator(older)
    try:
        assert family_mail_results.stats_admitted() is False
        monkeypatch.setattr(family_mail_results, "stats_admitted", lambda: True)
        with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
            message = prepare(batch.harness)
            deliver(batch.harness, batch.path, message, batch.owner)
        (event,) = outcomes(message)
        assert event.state == "delivered" and '"stats"' not in event.evidence_note
        assert batch.gmail.data(message) == 1
        assert validator(event) is not None
        refusals = [r for r in caplog.records if "refused" in r.getMessage()]
        assert len(refusals) == 1
    finally:
        replace_validator(current)


def test_the_validator_refuses_statistics_that_could_hold_personal_data(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    caplog,
):
    """SQL, not only Python, keeps addresses out of the statistics.

    The database refuses the note, and the outcome is then recorded without
    its statistics: the address is never stored and the delivery stands.
    """
    harness, path = dispatch_worker
    smuggled = FamilyDeliveryResult(Status.ACCEPTED, 1)
    # Bypass the Python validator to prove the database's own check.
    object.__setattr__(smuggled, "stats", {"who": "valid@example.org"})
    monkeypatch.setattr(tasks, "_with_stats", lambda result, stats: result)
    owner = per_message_owner(harness, path, monkeypatch, [])
    monkeypatch.setattr(
        tasks,
        "submit_family",
        lambda *args, **kwargs: smuggled,
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message, owner)
    (event,) = outcomes(message)
    assert event.state == "delivered" and '"stats"' not in event.evidence_note
    assert "@" not in event.evidence_note
    assert any("refused" in r.getMessage() for r in caplog.records)


def validate_stats(event, stats):
    """The SQL validator's verdict on ``event`` if it carried ``stats``.

    Outcome events are immutable, so this rewrites the note only inside a
    savepoint that is always rolled back, with row triggers off for it
    (the test login is the cluster superuser). Nothing else changes.
    """
    import hashlib

    value = json.loads(event.evidence_note)
    value.pop("stats", None)
    if stats is not None:
        value["stats"] = stats
    note = json.dumps(value, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(note.encode()).hexdigest()

    class Rollback(Exception):
        pass

    verdict = []
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role = replica")
            cursor.execute(
                "UPDATE stewardship_outbox_event SET evidence_note=%s, "
                "evidence_digest=%s WHERE id=%s",
                [note, digest, event.pk],
            )
            cursor.execute(
                "SELECT stewardship_family_smtp_result_v1(%s)::text", [event.pk]
            )
            verdict.append(cursor.fetchone()[0])
            raise Rollback
    except Rollback:
        pass
    return None if verdict[0] is None else json.loads(verdict[0])


def test_the_sql_validator_agrees_with_python_on_every_case(batch):  # noqa: F811
    """Both validators accept and refuse exactly the same statistics."""
    from ..send_stats_cases import ADMITTED, REFUSED

    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(batch.harness)
        deliver(batch.harness, batch.path, message, batch.owner)
    (event,) = outcomes(message)
    plain = validate_stats(event, None)
    assert plain is not None
    for stats in ADMITTED:
        assert validate_stats(event, stats) == plain, stats
    for case, stats in REFUSED.items():
        assert validate_stats(event, stats) is None, case
    # A "stats" member that is not an object at all.
    for stats in ([1], "x", 5, True):
        assert validate_stats(event, stats) is None, stats
    # The real note is untouched by all of this.
    event.refresh_from_db()
    assert validator(event) == plain
