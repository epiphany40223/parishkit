"""Timeout entries are durable, reviewed and survive the stopped work (#293)."""

import json
from datetime import timedelta
from threading import Event as Signal
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, OperationalError, connection, transaction
from django.utils import timezone

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.audit.timeouts import TIMEOUT_EVENTS, record_timeout
from parishkit.stewardship.campaigns.read_guards import CampaignReadGuard, ReadLimits
from parishkit.stewardship.jobs.broker import record_sql_timeout
from parishkit.stewardship.observability import Event

from .source_builders import running_source_task
from .test_read_guards_postgresql import family_campaign

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("event", sorted(TIMEOUT_EVENTS))
def test_each_timeout_event_is_accepted_and_names_its_task(event):
    """The recorder looks up the task's type and attempt; SQL admits the row."""
    task = running_source_task()
    record_timeout(
        event,
        what="lease",
        level="WARNING",
        task_id=task["task_id"],
        limit_seconds=60,
        elapsed_seconds=61.4,
    )
    entry = OperationalLog.objects.get()
    assert (entry.event, entry.level, entry.schema) == (event, "WARNING", "timeout")
    assert entry.context == {
        "what": "lease",
        "task_id": str(task["task_id"]),
        "task_type": "source_probe",
        "attempt": 1,
        "limit_seconds": 60,
        "elapsed_seconds": 61,
    }


def test_a_killed_helper_is_named_with_the_bound_task():
    """A helper entry says which helper and, inside a worker, which task."""
    from parishkit.stewardship.observability import task_scope

    task = running_source_task()
    with task_scope(task["task_id"]):
        record_timeout(
            Event.HELPER_TIMED_OUT,
            what="mail_helper",
            helper="family_delivery_worker",
            limit_seconds=30,
            elapsed_seconds=30.1,
        )
    assert OperationalLog.objects.get().context == {
        "what": "mail_helper",
        "helper": "family_delivery_worker",
        "task_id": str(task["task_id"]),
        "task_type": "source_probe",
        "attempt": 1,
        "limit_seconds": 30,
        "elapsed_seconds": 30,
    }


def test_the_entry_survives_the_callers_rollback():
    """Written on its own connection, the entry outlives the stopped work."""
    with pytest.raises(RuntimeError), transaction.atomic():
        record_timeout(Event.TASK_TIMED_OUT, what="statement_timeout")
        raise RuntimeError("synthetic rollback")
    assert OperationalLog.objects.get().context == {"what": "statement_timeout"}


@pytest.mark.parametrize(
    "event,context",
    [
        ("task_timed_out", {"what": "free text"}),
        ("task_timed_out", {"what": "lease", "task_type": "Private Name"}),
        ("task_timed_out", {"what": "lease", "detail": "x"}),
        ("task_took_long", {"what": "lease"}),
        ("helper_timed_out", {"what": "mail_helper", "helper": "other_worker"}),
        ("task_timed_out", {"what": "control_lock", "count": -1}),
        ("task_timed_out", {"what": "control_lock", "count": "7"}),
        ("helper_timed_out", {"what": "web_drains"}),
        ("helper_timed_out", {"what": "Web_Drain"}),
        ("helper_timed_out", {"what": "web_drain", "limit_seconds": "345"}),
        ("helper_timed_out", {"what": "web_drain", "count": -1}),
        ("helper_timed_out", {"what": "web_heartbeat", "worker": 1}),
        ("task_timed_out", {"what": "configuration_activations"}),
    ],
)
def test_sql_refuses_unreviewed_timeout_entries(event, context):
    """The database repeats the Python review, so no path can bypass it."""
    with pytest.raises(IntegrityError), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_operational_log "
            "(id,correlation_id,event,level,schema,context) "
            "VALUES (%s,%s,%s,'ERROR','timeout',%s::jsonb)",
            [uuid4(), uuid4(), event, json.dumps(context)],
        )


def test_read_guard_deadline_is_recorded_before_the_abort(tmp_path, monkeypatch):
    """The guard writes what it stopped, then stops it (the export hard stop)."""
    from parishkit.stewardship.campaigns import read_guards

    # A generous wait here, so a loaded CI host still sees the write land
    # before the abort; production waits RECORD_SECONDS at most.
    monkeypatch.setattr(read_guards, "RECORD_SECONDS", 10)
    identifier = family_campaign(tmp_path)
    task = uuid4()
    seen = []
    aborted = Signal()

    def abort():
        """Observe whether the entry was already committed when stopping."""
        seen.append(OperationalLog.objects.using("default").count())
        aborted.set()

    guard = CampaignReadGuard(
        [identifier],
        authorize=lambda _: None,
        abort=abort,
        limits=ReadLimits(interactive_seconds=2, lock_seconds=1),
        timeout_task=task,
    )
    with guard:
        assert aborted.wait(timeout=6)
    assert seen == [1]
    entry = OperationalLog.objects.get()
    assert entry.event == "task_timed_out" and entry.level == "ERROR"
    assert entry.context["what"] == "read_guard"
    assert entry.context["task_id"] == str(task)
    assert entry.context["limit_seconds"] == 2
    assert 2 <= entry.context["elapsed_seconds"] <= 4
    with pytest.raises(TypeError):
        CampaignReadGuard(
            [identifier],
            authorize=lambda _: None,
            abort=abort,
            timeout_task=str(task),
        )


def test_statement_timeout_is_recorded_for_its_task():
    """A task stopped by PostgreSQL's statement timeout is named in the log."""
    task = running_source_task()
    with (
        pytest.raises(OperationalError) as caught,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL statement_timeout = '10ms'")
        cursor.execute("SELECT pg_sleep(1)")
    record_sql_timeout(caught.value, (str(task["task_id"]),))
    entry = OperationalLog.objects.get()
    assert entry.event == "task_timed_out"
    assert entry.context["what"] == "statement_timeout"
    assert UUID(entry.context["task_id"]) == task["task_id"]
    assert entry.context["task_type"] == "source_probe"


@pytest.mark.parametrize("service", ["mail-dispatch", "backup-worker"])
def test_append_only_logins_record_timeouts_but_cannot_forge_events(service):
    """Mail and backup logins append timeout entries and nothing else."""
    from django.db import ProgrammingError

    from parishkit.stewardship.deployment import ServiceRole

    from .test_background_grants_postgresql import task_login

    statement = (
        "INSERT INTO stewardship_operational_log "
        "(id,correlation_id,event,level,schema,context) "
        "VALUES (gen_random_uuid(),gen_random_uuid(),%s,'ERROR',%s,%s::jsonb)"
    )
    admin = uuid4()
    with task_login(ServiceRole(service), exact=True):
        for event, schema, context in (
            ("mail_provider_failed", "none", "{}"),
            ("task_failed", "timeout", '{"what":"lease"}'),
        ):
            with (
                pytest.raises(ProgrammingError) as denied,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement, [event, schema, context])
            assert denied.value.__cause__.sqlstate == "42501"
        # No paging level, and no impersonated actor.
        for extra, values in (
            ("", ["CRITICAL"]),
            (",actor_id", ["ERROR", admin]),
        ):
            with (
                pytest.raises(ProgrammingError) as denied,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    "INSERT INTO stewardship_operational_log "
                    f"(id,correlation_id,event,schema,context,level{extra}) "
                    "VALUES (gen_random_uuid(),gen_random_uuid(),'task_timed_out',"
                    "'timeout','{\"what\":\"lease\"}',"
                    + ",".join(["%s"] * len(values))
                    + ")",
                    values,
                )
            assert denied.value.__cause__.sqlstate == "42501"
        with connection.cursor() as cursor:
            cursor.execute(
                statement, ["helper_timed_out", "timeout", '{"what":"mail_helper"}']
            )
            # A supplied time is replaced by the database's own.
            cursor.execute(
                "INSERT INTO stewardship_operational_log "
                "(id,correlation_id,event,level,schema,context,created_at) "
                "VALUES (gen_random_uuid(),gen_random_uuid(),'task_timed_out',"
                "'ERROR','timeout','{\"what\":\"lease\"}',"
                "now() + interval '10 years')"
            )
    entries = OperationalLog.objects.order_by("event")
    assert [entry.event for entry in entries] == ["helper_timed_out", "task_timed_out"]
    assert all(
        entry.created_at < timezone.now() + timedelta(minutes=1) for entry in entries
    )


def test_a_control_lock_summary_with_its_count_is_accepted():
    """SQL admits a process-local lock summary and its occurrence count."""
    record_timeout(
        Event.TASK_TIMED_OUT,
        what="control_lock",
        level="WARNING",
        limit_seconds=2,
        count=7,
    )
    assert OperationalLog.objects.get().context == {
        "what": "control_lock",
        "limit_seconds": 2,
        "count": 7,
    }


@pytest.mark.parametrize(
    "what", ["drive_retry_budget", "drive_request", "drive_probe_wait"]
)
def test_backup_drive_limits_are_accepted_by_sql(what):
    """SQL admits each off-site backup Drive limit name (#324)."""
    record_timeout(Event.WORK_BUDGET_REACHED, what=what, level="WARNING")
    assert OperationalLog.objects.get().context == {"what": what}


def test_grants_accept_exactly_the_installed_writer_guard():
    """The grant check's digest matches the body a fresh install creates."""
    from parishkit.config import ConfigError
    from parishkit.stewardship import database_provisioning

    with connection.cursor() as cursor:
        database_provisioning._admit_writer_guard(cursor)
        cursor.execute(
            "ALTER TABLE stewardship_operational_log "
            "DISABLE TRIGGER stewardship_operational_log_writer_v1"
        )
        try:
            with pytest.raises(ConfigError, match="writer guard"):
                database_provisioning._admit_writer_guard(cursor)
        finally:
            cursor.execute(
                "ALTER TABLE stewardship_operational_log "
                "ENABLE TRIGGER stewardship_operational_log_writer_v1"
            )


@pytest.mark.parametrize("what,count", [("web_drain", 2), ("web_heartbeat", None)])
def test_the_web_master_records_a_killed_worker_durably(what, count):
    """The Gunicorn master's own writer (no Django) lands a reviewed entry (#374)."""
    from parishkit.stewardship.web_supervisor import record_web_kill

    settings = dict(connection.settings_dict)
    record_web_kill(
        lambda: settings,
        what=what,
        limit_seconds=345,
        elapsed_seconds=345.4,
        count=count,
    )
    entry = OperationalLog.objects.get()
    assert (entry.event, entry.level, entry.schema) == (
        "helper_timed_out",
        "ERROR",
        "timeout",
    )
    expected = {"what": what, "limit_seconds": 345, "elapsed_seconds": 345}
    if count is not None:
        expected["count"] = count
    assert entry.context == expected


def test_the_web_master_never_raises_when_its_write_is_refused():
    """A refused write (here: a login that may not insert) leaves no row."""
    from parishkit.stewardship.web_supervisor import record_web_kill

    settings = dict(connection.settings_dict, PASSWORD="wrong", USER="nobody")
    record_web_kill(
        lambda: settings, what="web_drain", limit_seconds=345, elapsed_seconds=346
    )
    assert not OperationalLog.objects.exists()


def test_an_abandoned_activation_wait_is_recorded_durably(monkeypatch):
    """The 15-second hold's give-up is a durable entry (#429)."""
    from parishkit.stewardship import activation_hold
    from parishkit.stewardship.accounts.authority import AuthorityChanging

    monkeypatch.setattr(activation_hold, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(activation_hold, "installation_running", lambda: True)

    def step():
        """The change never finishes activating."""
        raise AuthorityChanging("synthetic")

    with pytest.raises(AuthorityChanging):
        activation_hold.wait_out_activation(step, limit=1)
    entry = OperationalLog.objects.get()
    assert (entry.event, entry.level) == ("task_timed_out", "WARNING")
    assert entry.context["what"] == "configuration_activation"
    assert entry.context["limit_seconds"] == 1
    assert entry.context["elapsed_seconds"] >= 1
