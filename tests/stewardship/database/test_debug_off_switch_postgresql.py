"""System health's debug-off switch under the real logins and guard (#530).

The web login sets a switch only for a freshly signed-in Administrator, one
at a time; only the schema owner (the admin-recovery login's definer) and, in
Testing, the web login for a fresh Administrator (0088) clear it, and only
once; nobody deletes it. Every runtime login reads it
through its one-boolean definer, the backup login through pg_read_all_data,
and a login with neither (the download login) cannot tell; only web, for
System health's panel, reads the table itself. A process started with debug
logging honors it on a new connection and on its service status record, and
the operator's offline ``debug-off-clear`` clears it, recording
``debug_logging_allowed`` as the operator in the same transaction.
"""

import logging
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship import (
    debug_switch,
    observability,
    operator_commands,
    runtime_database,
)
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.debug_switch_models import DebugOffSwitch

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_backup_postgresql import backup_login, now

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
TEXT = (MIGRATIONS / "0078_debug_off_switch.sql").read_text(encoding="utf-8")
CHECK = re.search(r"^DO \$check\$.*?^\$check\$;", TEXT, re.M | re.S)[0]
WEB = "pk_stewardship_web"


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch):
    """Each test starts as a process with no switch applied."""
    monkeypatch.setattr(debug_switch, "_held", None)
    monkeypatch.setattr(debug_switch, "_raised", [])
    monkeypatch.setattr(debug_switch, "_present", [])
    monkeypatch.delenv(observability.DEBUG_LOGGING_VARIABLE, raising=False)


def session():
    """The signed-in Administrator's live session."""
    return PortalSession.objects.order_by("-authenticated_at").first()


@contextmanager
def web_with(*privileges):
    """The web login with ``privileges`` on the switch table added.

    Web holds SELECT and INSERT (System health's button, ADM-13 PR 6b); the
    guard is exercised under any further grants added here, as the backup
    request tests widen a grant, so it is the boundary even then.
    """
    with task_login(ServiceRole.WEB, exact=True):
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            for privilege in privileges:
                cursor.execute(
                    f"GRANT {privilege} ON stewardship_debug_off_switch TO {WEB}"
                )
            cursor.execute(f"SET SESSION AUTHORIZATION {WEB}")
        yield


def web_set(**values):
    """Set the switch as the web would, for the signed-in Administrator."""
    current = session()
    row = {
        "id": uuid4(),
        "actor_id": current.principal_id,
        "session_id": current.pk,
        "authenticated_at": current.authenticated_at,
    } | values
    with transaction.atomic():
        return DebugOffSwitch.objects.create(**row)


def owner_set():
    """A set switch written directly as the schema owner."""
    instant = now()
    return DebugOffSwitch.objects.create(
        id=uuid4(), actor_id=uuid4(), session_id=uuid4(), authenticated_at=instant
    )


@contextmanager
def refused(message):
    """A change refused with ``message`` and rolled back."""
    with pytest.raises(DatabaseError, match=message), transaction.atomic():
        yield


def owner_clear():
    """Clear every set switch as the operator's definer would."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.stewardship_debug_off_clear_v1()")
        return cursor.fetchone()[0]


def test_the_web_sets_one_switch_for_a_fresh_administrator(auth_service, google):
    """Fresh sign-in, one at a time, set time the database's."""
    signed_in()
    with web_with("SELECT", "INSERT"):
        first = web_set()
        with pytest.raises(DatabaseError, match="already set"):
            web_set()
    first.refresh_from_db()
    assert first.cleared_at is None and first.set_at is not None
    assert owner_clear() == 1
    current = session()
    with web_with("SELECT", "INSERT"):
        # A stale sign-in, another Administrator's id or a made-up session
        # is refused.
        for values in (
            {"authenticated_at": current.authenticated_at.replace(year=2000)},
            {"actor_id": uuid4()},
            {"session_id": uuid4()},
        ):
            with pytest.raises(DatabaseError, match="fresh Administrator"):
                web_set(**values)
        # A cleared switch does not block a new one.
        assert web_set().cleared_at is None
    assert DebugOffSwitch.objects.filter(cleared_at__isnull=True).count() == 1


def test_no_other_login_sets_and_the_web_never_clears_as_operator(auth_service, google):
    """Even with every grant added, the guard keeps the boundary: the web
    clears only as an Administrator (Allow debug logging again, 0088, tested
    in test_debug_allow_postgresql), never as the operator."""
    signed_in()
    with task_login(ServiceRole.WORKER, exact=True), refused("permission denied"):
        web_set()
    switch = owner_set()
    with web_with("SELECT", "INSERT", "UPDATE", "DELETE"):
        with refused("only as an Administrator"):
            DebugOffSwitch.objects.filter(pk=switch.pk).update(
                cleared_at=now(), cleared_by="operator"
            )
        with refused("never deleted"):
            DebugOffSwitch.objects.filter(pk=switch.pk).delete()
    with (
        web_with(),
        refused("permission denied"),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT public.stewardship_debug_off_clear_v1()")


def test_a_switch_keeps_who_set_it_and_is_cleared_once(auth_service):
    """Even the owner cannot rewrite who set it, clear twice or delete."""
    switch = owner_set()
    for field, value in (
        ("actor_id", uuid4()),
        ("session_id", uuid4()),
        ("authenticated_at", now()),
        ("set_at", now()),
    ):
        with refused("keeps who set it"):
            DebugOffSwitch.objects.filter(pk=switch.pk).update(**{field: value})
    assert owner_clear() == 1
    switch.refresh_from_db()
    assert (switch.cleared_by, switch.cleared_actor_id) == ("operator", None)
    assert switch.cleared_at is not None
    assert owner_clear() == 0
    with refused("cleared once"):
        DebugOffSwitch.objects.filter(pk=switch.pk).update(cleared_at=now())
    with refused("never deleted"):
        DebugOffSwitch.objects.filter(pk=switch.pk).delete()


@pytest.mark.parametrize(
    "service",
    [
        ServiceRole.WEB,
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.MAIL_DISPATCH,
        ServiceRole.CONFIG_INSTALLER,
        ServiceRole.ADMIN_RECOVERY,
    ],
)
def test_each_runtime_login_reads_the_switch_and_nothing_else(service):
    """One boolean through the definer; only web reads the table itself
    (System health's panel says who set the switch and when)."""
    with task_login(service, exact=True):
        assert debug_switch.read_switch(connection) is False
        if service is ServiceRole.WEB:
            assert list(DebugOffSwitch.objects.all()) == []
        else:
            with refused("permission denied"):
                list(DebugOffSwitch.objects.all())
    owner_set()
    with task_login(service, exact=True):
        assert debug_switch.read_switch(connection) is True
    owner_clear()
    with task_login(service, exact=True):
        assert debug_switch.read_switch(connection) is False


def test_the_backup_login_reads_the_table_and_another_login_cannot_tell():
    """pg_read_all_data stands in for the definer; no grant means no answer."""
    owner_set()
    with backup_login():
        assert debug_switch.read_switch(connection) is True
    # The download login has neither the definer grant nor the table.
    with task_login("download"):
        assert debug_switch.read_switch(connection) is None


def test_admin_recovery_clears_the_switch_as_the_operator():
    """The definer is admin recovery's alone, and clears exactly once."""
    switch = owner_set()
    with (
        task_login(ServiceRole.ADMIN_RECOVERY, exact=True),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT public.stewardship_debug_off_clear_v1()")
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT public.stewardship_debug_off_clear_v1()")
        assert cursor.fetchone()[0] == 0
    switch.refresh_from_db()
    assert switch.cleared_by == "operator" and switch.cleared_at is not None


@pytest.fixture
def offline(monkeypatch):
    """Run ``debug-off-clear`` in this process; returns its configuration."""
    monkeypatch.setattr(
        operator_commands, "admit_offline_service", lambda _: ServiceRole.ADMIN_RECOVERY
    )
    monkeypatch.setattr(
        operator_commands, "configure_operator_database", lambda _: None
    )
    monkeypatch.setattr(operator_commands, "StartupLease", lambda *a, **k: _nothing())
    monkeypatch.setattr(
        operator_commands, "RuntimeLayout", lambda _: SimpleNamespace(interlock=None)
    )
    # Process capacity and TEMP checks are covered elsewhere; the identity,
    # grant admission and current schema checks run for real.
    for name in (
        "require_no_temporary_authority",
        "require_capacity",
        "require_role_capacity",
    ):
        monkeypatch.setattr(runtime_database, name, lambda *a, **k: None)
    return SimpleNamespace(service_role=ServiceRole.ADMIN_RECOVERY)


def test_the_operator_command_clears_the_switch(offline):
    """``debug-off-clear`` admits the offline login, then clears and reports.

    The clear is audited as the operator (``debug_logging_allowed``, actor
    kind ``operator``, no actor), once: a second run clears nothing and
    records nothing.
    """
    configuration = offline
    owner_set()
    with task_login(ServiceRole.ADMIN_RECOVERY, exact=True, reconnect=True):
        assert operator_commands.debug_off_clear_command(configuration) == {
            "debug_off_cleared": True
        }
        assert operator_commands.debug_off_clear_command(configuration) == {
            "debug_off_cleared": False
        }
    assert DebugOffSwitch.objects.get().cleared_by == "operator"
    event = AuditEvent.objects.get(event_type="debug_logging_allowed")
    assert event.actor_id is None and event.subject_id is None
    context = AuditContext.objects.get(event=event)
    assert (context.actor_kind, context.schema) == ("operator", "action")
    assert context.context == {"outcome": "succeeded"}


def test_the_operator_clear_and_its_audit_commit_together(offline, monkeypatch):
    """A failed audit write leaves the switch set: one transaction."""
    from parishkit.stewardship.audit import services

    def broken(*args, **kwargs):
        """The audit write fails after the clear ran."""
        raise DatabaseError("audit unavailable")

    monkeypatch.setattr(services, "record_operator_action", broken)
    owner_set()
    with (
        task_login(ServiceRole.ADMIN_RECOVERY, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="audit unavailable"),
    ):
        operator_commands.debug_off_clear_command(offline)
    assert DebugOffSwitch.objects.get().cleared_at is None
    assert not AuditEvent.objects.filter(event_type="debug_logging_allowed").exists()


def test_the_operator_command_names_a_missing_grant(offline):
    """Without the clear's EXECUTE it says to run database-grants, clearing nothing."""
    from parishkit.config import ConfigError

    owner_set()
    login = "pk_stewardship_admin_recovery"
    with task_login(ServiceRole.ADMIN_RECOVERY, exact=True, reconnect=True):
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(
                "REVOKE EXECUTE ON FUNCTION public.stewardship_debug_off_clear_v1() "
                f"FROM {login}"
            )
            cursor.execute(f"SET SESSION AUTHORIZATION {login}")
        with pytest.raises(ConfigError, match="run database-grants"):
            operator_commands.debug_off_clear_command(offline)
    assert DebugOffSwitch.objects.get().cleared_at is None


@contextmanager
def _nothing():
    """A stand-in for the offline startup lease."""
    yield


@pytest.fixture
def debug_handler():
    """A root log handler at the DEBUG threshold, as debug logging installs."""
    handler = logging.NullHandler()
    handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)
    yield handler
    logging.getLogger().removeHandler(handler)


def test_a_process_with_debug_logging_honors_the_switch(
    monkeypatch, caplog, debug_handler
):
    """Off on a new connection, back on after the clear; once each."""
    handler = debug_handler
    monkeypatch.setenv(observability.DEBUG_LOGGING_VARIABLE, "1")
    owner_set()
    # A new connection reads the switch (Django's connection_created).
    connection.close()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    assert not observability.debug_logging_enabled()
    assert handler.level == logging.INFO
    switched = [r for r in caplog.records if r.msg == "debug_logging_switched_off"]
    assert len(switched) == 1 and switched[0].levelno == logging.WARNING
    # Reading again changes nothing and logs nothing more.
    debug_switch.refresh(connection)
    assert (
        len([r for r in caplog.records if r.msg == "debug_logging_switched_off"]) == 1
    )
    owner_clear()
    debug_switch.refresh(connection)
    assert observability.debug_logging_enabled()
    assert handler.level == logging.DEBUG
    assert [r.msg for r in caplog.records].count("debug_logging_enabled") == 1


def test_a_process_without_debug_logging_never_reads_it(monkeypatch):
    """No setting, no query: the switch can only turn debug logging off."""
    owner_set()

    def unexpected(database):
        """Fail if the switch is read at all."""
        raise AssertionError("read without debug logging")

    monkeypatch.setattr(debug_switch, "read_switch", unexpected)
    connection.close()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    debug_switch.refresh(connection)
    assert not observability.debug_logging_enabled()


def test_the_service_status_record_reports_the_effective_state(monkeypatch):
    """The record reads the switch first, so it reports debug logging off."""
    from parishkit.stewardship.jobs.service_status_models import ServiceStatus
    from parishkit.stewardship.service_status import ServiceStatusReporter

    monkeypatch.setenv(observability.DEBUG_LOGGING_VARIABLE, "1")
    reporter = ServiceStatusReporter(ServiceRole.WORKER.value)
    with task_login(ServiceRole.WORKER, exact=True):
        assert reporter.report()
    assert ServiceStatus.objects.get(pk=reporter.id).debug_logging is True
    owner_set()
    reporter.due = 0.0
    with task_login(ServiceRole.WORKER, exact=True):
        assert reporter.report()
    assert ServiceStatus.objects.get(pk=reporter.id).debug_logging is False


def test_the_migration_check_passes_and_refuses_drift():
    """The closing DO block passes as installed and refuses each drift."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CHECK)
        transaction.set_rollback(True)
    for change, message in (
        ("DROP TABLE public.stewardship_debug_off_switch CASCADE", "not installed"),
        (
            "ALTER FUNCTION public.stewardship_debug_off_switch_guard_v1() "
            "SECURITY DEFINER",
            "guard is not the one",
        ),
        (
            "GRANT EXECUTE ON FUNCTION public.stewardship_debug_logging_off_v1() "
            "TO PUBLIC",
            "read is not installed",
        ),
        (
            "ALTER FUNCTION public.stewardship_debug_logging_off_v1() SECURITY INVOKER",
            "read is not installed",
        ),
        (
            "GRANT EXECUTE ON FUNCTION public.stewardship_debug_off_clear_v1() "
            "TO PUBLIC",
            "clear is not installed",
        ),
        (
            "GRANT SELECT ON public.stewardship_debug_off_switch TO PUBLIC",
            "PUBLIC holds a privilege",
        ),
        (
            "DROP TRIGGER stewardship_debug_off_switch_guard "
            "ON public.stewardship_debug_off_switch",
            "trigger was not created",
        ),
        (
            "ALTER TABLE public.stewardship_operational_log "
            "DROP CONSTRAINT operational_event_safe",
            "does not admit",
        ),
    ):
        with (
            connection.cursor() as cursor,
            pytest.raises(DatabaseError, match=message),
            transaction.atomic(),
        ):
            cursor.execute(change)
            cursor.execute(CHECK)
