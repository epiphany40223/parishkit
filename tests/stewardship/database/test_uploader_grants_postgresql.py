"""The off-site uploader's login holds exactly its registry, as itself (#235).

Operations specification, "Database login": its outcome rows (SELECT and
INSERT), three run columns, the runtime row's id, timeout log entries, the
destination definer and the debug-off read, and nothing else; its admission
refuses any excess. The test login is the real login name, granted exactly
what ``database-grants`` grants from the registry.
"""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from psycopg import sql

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_grants import (
    login_name,
    runtime_functions,
    runtime_grants,
)
from parishkit.stewardship.uploader_boundaries import admit_uploader_identity

from .role_grants import grant_runtime

pytestmark = pytest.mark.django_db(transaction=True)

UPLOADER = login_name(ServiceRole.BACKUP_UPLOADER)


@contextmanager
def uploader_session(*extra):
    """Act as the uploader login with exactly its registry, plus ``extra`` SQL.

    Each extra statement has ``{role}`` for the quoted login, so a test can
    add one excess grant and see admission refuse it. The login and all it
    was granted are dropped afterwards.
    """
    role = sql.Identifier(UPLOADER)
    database = sql.Identifier(connection.settings_dict["NAME"])
    tables, columns = runtime_grants(ServiceRole.BACKUP_UPLOADER)
    functions = runtime_functions(ServiceRole.BACKUP_UPLOADER)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT").format(role))
        grant_runtime(cursor, UPLOADER, tables, columns, functions)
        # Provisioning revokes the database's PUBLIC defaults (TEMP among
        # them); the test database keeps them, so this drops them meanwhile.
        cursor.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database)
        )
        cursor.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role)
        )
        for statement in extra:
            cursor.execute(statement.format(role=f'"{UPLOADER}"'))
        cursor.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(role))
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(
                sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO PUBLIC").format(
                    database
                )
            )
            cursor.execute(sql.SQL("DROP OWNED BY {}").format(role))
            cursor.execute(sql.SQL("DROP ROLE {}").format(role))


class Undo(Exception):
    """Raised inside a probe's transaction so its effects are rolled back."""


def denied(statement, parameters=None):
    """The SQLSTATE a statement fails with as the current login, or None.

    Each probe runs in its own transaction, which is always rolled back, so
    an allowed write leaves nothing behind.
    """
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            raise Undo
    except Undo:
        return None
    except DatabaseError as error:
        return getattr(error.__cause__, "sqlstate", None)


def test_admission_admits_exactly_the_registry():
    """The real login with exactly its grants passes its own admission."""
    with uploader_session():
        admit_uploader_identity()


@pytest.mark.parametrize(
    "extra",
    [
        "GRANT SELECT ON public.stewardship_backup_run TO {role}",
        "GRANT SELECT (size_bytes) ON public.stewardship_backup_run TO {role}",
        "GRANT UPDATE ON public.stewardship_backup_upload TO {role}",
        "GRANT SELECT ON public.stewardship_system_configuration TO {role}",
        "GRANT INSERT ON public.stewardship_backup_run TO {role}",
        "GRANT TEMP ON DATABASE {database} TO {role}",
        "GRANT EXECUTE ON FUNCTION public.stewardship_task_event_prune_v1"
        "(integer, integer, integer) TO {role}",
    ],
)
def test_admission_refuses_any_excess_grant(extra):
    """One privilege beyond the registry stops the uploader before any copy."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        (database,) = cursor.fetchone()
    statement = extra.replace("{database}", f'"{database}"')
    if "size_bytes" in statement:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT attname FROM pg_attribute WHERE attrelid="
                "'public.stewardship_backup_run'::regclass AND attnum>0 "
                "AND NOT attisdropped AND attname NOT IN "
                "('manifest_digest','started_at','completed_at') ORDER BY attnum"
            )
            other = cursor.fetchone()[0]
        statement = statement.replace("size_bytes", other)
    with uploader_session(statement), pytest.raises(ConfigError):
        admit_uploader_identity()


def test_the_login_reads_and_writes_only_what_it_needs():
    """Its run columns and outcome rows yes; other columns and tables no."""
    with uploader_session():
        assert (
            denied(
                "SELECT manifest_digest, started_at, completed_at "
                "FROM public.stewardship_backup_run"
            )
            is None
        )
        assert denied("SELECT * FROM public.stewardship_backup_run") == "42501"
        assert denied("SELECT id FROM public.stewardship_system_configuration") is None
        assert (
            denied("SELECT * FROM public.stewardship_system_configuration") == "42501"
        )
        assert denied("SELECT * FROM public.stewardship_backup_upload") is None
        assert (
            denied(
                "INSERT INTO public.stewardship_backup_upload "
                "(id, state, folder_id, failure_kind) "
                "VALUES (%s, 'failed', '1AbCdEfGhIjKlMnOp', 'unavailable')",
                [uuid4()],
            )
            is None
        )
        assert denied("DELETE FROM public.stewardship_backup_upload") == "42501"
        assert denied("SELECT * FROM public.stewardship_backup_request") == "42501"
        assert denied("SELECT * FROM public.stewardship_operational_log") == "42501"
        assert denied("SELECT public.stewardship_debug_logging_off_v1()") is None
