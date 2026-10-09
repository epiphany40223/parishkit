"""Admission refuses authority a login gains or lends after cluster drift (#389 L7).

Every service login is created by the superuser operator, so it is no role's
member, owns nothing and has no foreign-data USAGE; the backup login's grants
stop at its own writes. Each case here runs the real admission as the real
login name, injects one drift as the test's superuser, and expects a refusal,
then removes the drift and expects admission again.
"""

from contextlib import contextmanager

import pytest
from django.db import connection
from psycopg import sql

from parishkit.config import ConfigError
from parishkit.stewardship import backup_commands
from parishkit.stewardship.accounts.credential_database import _identity
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_grants import runtime_grants

from .role_grants import grant_runtime
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)

WORKER = "pk_stewardship_worker"
BACKUP = "pk_stewardship_backup_worker"


@contextmanager
def superuser(login):
    """Run statements as the test's superuser, then become ``login`` again."""
    with connection.cursor() as cursor:
        cursor.execute("RESET SESSION AUTHORIZATION")
        yield cursor
        cursor.execute(
            sql.SQL("SET SESSION AUTHORIZATION {}").format(sql.Identifier(login))
        )


def drifted(login, apply, undo, admit, message):
    """``admit`` passes, is refused after ``apply`` and passes again after ``undo``."""
    admit()
    with superuser(login) as cursor:
        for statement in apply:
            cursor.execute(statement)
    try:
        with pytest.raises(ConfigError, match=message):
            admit()
    finally:
        with superuser(login) as cursor:
            for statement in undo:
                cursor.execute(statement)
    admit()


# (drift, its removal): each one the identity check alone must refuse.
IDENTITY_DRIFT = {
    "a role that is a member of the login": (
        [
            "CREATE ROLE test_drift_member NOLOGIN",
            f"GRANT {WORKER} TO test_drift_member",
        ],
        ["DROP ROLE test_drift_member"],
    ),
    "a function the login owns": (
        [
            "CREATE FUNCTION public.test_drift_owned() RETURNS integer "
            "LANGUAGE sql AS 'SELECT 1'",
            f"ALTER FUNCTION public.test_drift_owned() OWNER TO {WORKER}",
        ],
        ["DROP FUNCTION public.test_drift_owned()"],
    ),
    "a schema the login owns": (
        [f"CREATE SCHEMA test_drift_schema AUTHORIZATION {WORKER}"],
        ["DROP SCHEMA test_drift_schema"],
    ),
    "a type the login owns": (
        [
            "CREATE TYPE public.test_drift_type AS ENUM ('x')",
            f"ALTER TYPE public.test_drift_type OWNER TO {WORKER}",
        ],
        ["DROP TYPE public.test_drift_type"],
    ),
    "default privileges for the login": (
        [
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {WORKER} "
            "GRANT SELECT ON TABLES TO PUBLIC"
        ],
        [
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {WORKER} "
            "REVOKE SELECT ON TABLES FROM PUBLIC"
        ],
    ),
    "foreign-data wrapper USAGE": (
        [
            "CREATE FOREIGN DATA WRAPPER test_drift_fdw",
            f"GRANT USAGE ON FOREIGN DATA WRAPPER test_drift_fdw TO {WORKER}",
        ],
        ["DROP FOREIGN DATA WRAPPER test_drift_fdw CASCADE"],
    ),
    "foreign server USAGE": (
        [
            "CREATE FOREIGN DATA WRAPPER test_drift_fdw",
            "CREATE SERVER test_drift_server FOREIGN DATA WRAPPER test_drift_fdw",
            f"GRANT USAGE ON FOREIGN SERVER test_drift_server TO {WORKER}",
        ],
        ["DROP FOREIGN DATA WRAPPER test_drift_fdw CASCADE"],
    ),
}


@pytest.mark.parametrize("case", sorted(IDENTITY_DRIFT))
def test_the_identity_check_refuses_lent_or_owned_authority(case):
    """Reverse membership, ownership of any object and foreign-data USAGE."""
    apply, undo = IDENTITY_DRIFT[case]
    with task_login(ServiceRole.WORKER):
        drifted(
            WORKER,
            apply,
            undo,
            lambda: _identity(WORKER),
            f"identity is not isolated: {IDENTITY_REFUSAL[case]}",
        )


# What each identity drift's refusal names.
IDENTITY_REFUSAL = {
    "a role that is a member of the login": "another role is a member of the login",
    "a function the login owns": "the login owns an object",
    "a schema the login owns": "the login owns an object",
    "a type the login owns": "the login owns an object",
    "default privileges for the login": "the login owns an object",
    "foreign-data wrapper USAGE": "the login has foreign-data USAGE",
    "foreign server USAGE": "the login has foreign-data USAGE",
}


@contextmanager
def backup_login():
    """The backup login as provisioning makes it: BYPASSRLS, pg_read_all_data."""
    role = sql.Identifier(BACKUP)
    tables, columns = runtime_grants(ServiceRole.BACKUP_WORKER)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT BYPASSRLS").format(role))
        cursor.execute(
            sql.SQL("GRANT pg_read_all_data TO {} WITH INHERIT TRUE").format(role)
        )
        grant_runtime(cursor, BACKUP, tables, columns, frozenset())
        # Provisioning revokes the database's PUBLIC defaults (TEMP among
        # them); the test database keeps them, so this test drops them.
        cursor.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(connection.settings_dict["NAME"])
            )
        )
        cursor.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(connection.settings_dict["NAME"]), role
            )
        )
        cursor.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(role))
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(
                sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO PUBLIC").format(
                    sql.Identifier(connection.settings_dict["NAME"])
                )
            )
            cursor.execute(sql.SQL("DROP OWNED BY {}").format(role))
            cursor.execute(sql.SQL("DROP ROLE {}").format(role))


def sequence():
    """Some application sequence (Django's migration ledger has one)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.oid::regclass::text FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relkind='S' AND n.nspname='public' ORDER BY 1 LIMIT 1"
        )
        return cursor.fetchone()[0]


def definer():
    """Some public SECURITY DEFINER function, by signature."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.oid::regprocedure::text FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.prosecdef ORDER BY 1 LIMIT 1"
        )
        return cursor.fetchone()[0]


def test_the_backup_login_refuses_grants_beyond_its_own():
    """A write it does not declare, or definer, sequence or CREATE authority.

    pg_read_all_data still reads every table and sequence, which is the
    dump's purpose and is admitted.
    """
    database = connection.settings_dict["NAME"]
    target, function = sequence(), definer()
    cases = [
        (
            f"GRANT UPDATE ON public.stewardship_family_campaign TO {BACKUP}",
            f"REVOKE UPDATE ON public.stewardship_family_campaign FROM {BACKUP}",
        ),
        (
            f"GRANT UPDATE (id) ON public.stewardship_backup_run TO {BACKUP}",
            f"REVOKE UPDATE (id) ON public.stewardship_backup_run FROM {BACKUP}",
        ),
        (
            f"GRANT USAGE ON SEQUENCE {target} TO {BACKUP}",
            f"REVOKE USAGE ON SEQUENCE {target} FROM {BACKUP}",
        ),
        (
            f"GRANT EXECUTE ON FUNCTION {function} TO {BACKUP}",
            f"REVOKE EXECUTE ON FUNCTION {function} FROM {BACKUP}",
        ),
        (
            f"GRANT CREATE ON SCHEMA public TO {BACKUP}",
            f"REVOKE CREATE ON SCHEMA public FROM {BACKUP}",
        ),
        (
            f'GRANT CREATE ON DATABASE "{database}" TO {BACKUP}',
            f'REVOKE CREATE ON DATABASE "{database}" FROM {BACKUP}',
        ),
    ]
    with backup_login():
        for apply, undo in cases:
            drifted(
                BACKUP,
                [apply],
                [undo],
                backup_commands._admit_backup_identity,
                "grants are excessive",
            )
        # Each identity drift every login is checked for applies to it too,
        # and the refusal names it.
        for (apply, undo), refusal in BACKUP_DRIFT:
            drifted(
                BACKUP,
                apply,
                undo,
                backup_commands._admit_backup_identity,
                f"requires its own database login: {refusal}",
            )


# The backup login's own query reads each ISOLATION_DRIFT column.
BACKUP_DRIFT = [
    (
        (
            [
                "CREATE ROLE test_drift_member NOLOGIN",
                f"GRANT {BACKUP} TO test_drift_member",
            ],
            ["DROP ROLE test_drift_member"],
        ),
        "another role is a member of the login",
    ),
    (
        (
            [
                "CREATE FUNCTION public.test_drift_owned() RETURNS integer "
                "LANGUAGE sql AS 'SELECT 1'",
                f"ALTER FUNCTION public.test_drift_owned() OWNER TO {BACKUP}",
            ],
            ["DROP FUNCTION public.test_drift_owned()"],
        ),
        "the login owns an object",
    ),
    (
        (
            [
                "CREATE FOREIGN DATA WRAPPER test_drift_fdw",
                "CREATE SERVER test_drift_server FOREIGN DATA WRAPPER test_drift_fdw",
                f"GRANT USAGE ON FOREIGN SERVER test_drift_server TO {BACKUP}",
            ],
            ["DROP FOREIGN DATA WRAPPER test_drift_fdw CASCADE"],
        ),
        "the login has foreign-data USAGE",
    ),
]
