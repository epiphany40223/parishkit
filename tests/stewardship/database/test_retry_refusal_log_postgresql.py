"""A follow-up refusal that cannot be recorded is flagged (#1201, migration 0089).

When an address follow-up's permanent refusal cannot be set aside as a
recipient refusal, the real mail dispatch login leaves one CRITICAL
``retry_refusal_unrecorded`` entry naming only the email and how many
addresses. System health lists it (and links the email) until the
critical-problems banner's acknowledgement, and a failure to write the entry
still never loses the follow-up's outcome. The last test runs the file's DO
block against the installed objects and against each object's earlier body.
"""

from pathlib import Path

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.audit import schemas
from parishkit.stewardship.audit.models import (
    CriticalEventAcknowledgement,
    OperationalLog,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import recipient_suppressions
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.recipient_models import RecipientRefusal
from parishkit.stewardship.system_health import _facts, find_problems

from .test_address_retry_refusal_postgresql import refused_permanently
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_worker_postgresql import dispatch_worker  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = (MIGRATIONS / "0089_retry_refusal_log.sql").read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
# What the DO block says when one of its three objects is not installed.
REFUSED = "0089|operational_event_safe|safe_context"


def refusal_fails(monkeypatch):
    """Make every follow-up refusal record fail as a refused write would."""

    def fail(**kwargs):
        raise IntegrityError("Recipient refusal requires definitive evidence.")

    monkeypatch.setattr(recipient_suppressions, "record_retry_refusal", fail)


def unrecorded():
    """System health's facts about unrecorded refusals, read as the web login."""
    with task_login(ServiceRole.WEB, exact=True):
        return _facts().unrecorded


def test_an_unrecorded_refusal_is_flagged_until_acknowledged(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """One closed CRITICAL entry from mail dispatch, then a System health
    problem linking the email, gone once the entry is acknowledged."""
    refusal_fails(monkeypatch)
    message, retry, _ = refused_permanently(dispatch_worker, monkeypatch)
    assert retry.state == "finished" and retry.refused == [1]
    assert not RecipientRefusal.objects.exists()
    entry = OperationalLog.objects.get(event="retry_refusal_unrecorded")
    assert entry.level == "CRITICAL" and entry.schema == "failure"
    assert entry.context == {
        "failure": "retry_refusal",
        "failure_kind": "database_write_refused",
        "message_id": str(message.pk),
        "count": 1,
        "outcome": "failed",
    }
    found = unrecorded()
    assert (found.count, found.message_id) == (1, str(message.pk))
    assert found.since == entry.created_at
    (problem,) = [
        problem
        for problem in find_problems(
            mode="production",
            processes=(),
            missing=(),
            schema_current=True,
            incidents={},
            refresh=None,
            backup_at=None,
            unrecorded=found,
        )
        if problem.kind == "refusal_unrecorded"
    ]
    assert (problem.count, problem.message_id) == (1, str(message.pk))
    CriticalEventAcknowledgement.objects.create(log_id=entry.pk)
    assert unrecorded().count == 0


def test_an_entry_that_cannot_be_written_keeps_the_outcome(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    caplog,
):
    """If the entry fails too, the follow-up still ends with its outcome."""
    refusal_fails(monkeypatch)

    def broken(kind, values):
        raise ValueError("refused")

    monkeypatch.setattr(schemas, "sanitize", broken)
    _, retry, _ = refused_permanently(dispatch_worker, monkeypatch)
    assert retry.state == "finished" and retry.refused == [1]
    assert TaskRun.objects.get(pk=retry.task_id).state == "succeeded"
    assert not OperationalLog.objects.filter(event="retry_refusal_unrecorded")
    assert any(
        record.levelname == "ERROR" and "#1201" in record.getMessage()
        for record in caplog.records
    )


def test_only_mail_dispatch_writes_the_entry_and_only_at_critical():
    """The writer admits the one tuple from mail dispatch, nothing else."""
    insert = (
        "INSERT INTO stewardship_operational_log"
        "(id,correlation_id,event,level,schema,context)"
        " VALUES(gen_random_uuid(),gen_random_uuid(),'retry_refusal_unrecorded',"
        '%s,\'failure\',\'{"failure": "retry_refusal", "count": 1}\'::jsonb)'
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(insert, ["CRITICAL"])
            transaction.set_rollback(True)
        with (
            pytest.raises(DatabaseError, match="may not record"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(insert, ["ERROR"])
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(DatabaseError, match="may not record"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(insert, ["CRITICAL"])


def _body(text, name, end):
    """``name``'s CREATE OR REPLACE statement in a frozen file's ``text``."""
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    return text[start : text.index(end, start) + len(end)]


def test_the_migration_check_passes_installed_and_fails_otherwise():
    """The DO block accepts the installed objects and refuses the event
    check, the safe context or the writer as they were before 0089."""
    with connection.cursor() as cursor:
        cursor.execute(CHECK)
    earlier = {
        name: (MIGRATIONS / name).read_text(encoding="utf-8")
        for name in ("0078_debug_off_switch.sql", "0083_digest_funnel_charts.sql")
    }
    writer = (MIGRATIONS / "0038_admin_command_failure_log.sql").read_text(
        encoding="utf-8"
    )
    events = earlier["0078_debug_off_switch.sql"]
    last = "'admin_command_failed','debug_logging_switched_off'));"
    start = events.index("ALTER TABLE public.stewardship_operational_log DROP")
    events = events[start : events.index(last) + len(last)]
    for statement in (
        events,
        _body(
            earlier["0083_digest_funnel_charts.sql"],
            "stewardship_safe_context_v1",
            "$_$;",
        ),
        _body(writer, "stewardship_operational_log_writer_v1", "$$;"),
    ):
        with (
            pytest.raises(DatabaseError, match=REFUSED),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
            cursor.execute(CHECK)
