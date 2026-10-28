"""The off-site uploader's contract migration, frozen file 0195 (#235).

The file revokes the backup login's INSERT on the off-site copy outcomes and
its table INSERT on the run record, and INSERT on both from PUBLIC; its
closing DO block refuses to commit while any of them, or an INSERT on the
run's completed_at, is left. The backup login's insert under the grants
that follow is in test_backup_postgresql.py; the users upgrade's base, which
holds the earlier grants until the full migration, in
test_users_swap_postgresql.py.
"""

from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction
from psycopg import sql

from parishkit.stewardship import backup_probes
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_grants import runtime_functions, runtime_grants

from .role_grants import grant_runtime

pytestmark = pytest.mark.django_db(transaction=True)
BACKUP = "pk_stewardship_backup_worker"
CONTRACT_SQL = (
    Path(backup_probes.__file__).with_name("schema")
    / "migrations"
    / "0195_backup_uploader_contract.sql"
).read_text(encoding="utf-8")
CONTRACT_CHECK = CONTRACT_SQL[CONTRACT_SQL.index("DO $check$") :]


def privileges():
    """The backup login's INSERTs that 0195 removes, and PUBLIC's."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_table_privilege(%(b)s,"
            "'public.stewardship_backup_run','INSERT'),"
            "has_table_privilege(%(b)s,'public.stewardship_backup_upload','INSERT'),"
            "has_column_privilege(%(b)s,'public.stewardship_backup_run',"
            "'completed_at','INSERT'),"
            "has_any_column_privilege('public','public.stewardship_backup_run',"
            "'INSERT')",
            {"b": BACKUP},
        )
        return cursor.fetchone()


@pytest.fixture
def backup_login():
    """The backup login with the earlier release's grants, then dropped."""
    role = sql.Identifier(BACKUP)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT").format(role))
        cursor.execute(
            sql.SQL(
                "GRANT SELECT, INSERT ON public.stewardship_backup_run TO {0}; "
                "GRANT SELECT, INSERT ON public.stewardship_backup_upload TO {0}"
            ).format(role)
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP OWNED BY {}").format(role))
            cursor.execute(sql.SQL("DROP ROLE {}").format(role))


def test_the_file_revokes_the_earlier_inserts_and_checks_itself(backup_login):
    """Run whole: the earlier INSERTs go; the registry's column grant follows."""
    with connection.cursor() as cursor:
        cursor.execute("GRANT INSERT ON public.stewardship_backup_run TO PUBLIC")
    assert privileges() == (True, True, True, True)
    with connection.cursor() as cursor:
        cursor.execute(CONTRACT_SQL)
    assert privileges() == (False, False, False, False)
    # database-grants then gives the run row's other columns, and SELECT stays.
    tables, columns = runtime_grants(ServiceRole.BACKUP_WORKER)
    with connection.cursor() as cursor:
        grant_runtime(
            cursor,
            BACKUP,
            tables,
            columns,
            runtime_functions(ServiceRole.BACKUP_WORKER),
        )
        cursor.execute(
            "SELECT has_column_privilege(%s,'public.stewardship_backup_run',"
            "'started_at','INSERT'), has_table_privilege(%s,"
            "'public.stewardship_backup_upload','SELECT')",
            [BACKUP, BACKUP],
        )
        assert cursor.fetchone() == (True, True)
    assert privileges() == (False, False, False, False)
    # And it runs again harmlessly (as the users swap's closure may run it).
    with connection.cursor() as cursor:
        cursor.execute(CONTRACT_SQL)


def test_a_database_without_the_backup_login_migrates():
    """Roles come from database-roles: before them there is nothing to revoke."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_roles WHERE rolname=%s", [BACKUP])
        assert cursor.fetchone() == (0,)
        with transaction.atomic():
            cursor.execute(CONTRACT_SQL)
            transaction.set_rollback(True)


@pytest.mark.parametrize(
    "left",
    [
        "GRANT INSERT ON public.stewardship_backup_upload TO {b}",
        "GRANT INSERT (state) ON public.stewardship_backup_upload TO {b}",
        "GRANT INSERT ON public.stewardship_backup_run TO {b}",
        "GRANT INSERT (completed_at) ON public.stewardship_backup_run TO {b}",
        "GRANT INSERT ON public.stewardship_backup_upload TO PUBLIC",
        "GRANT INSERT (id) ON public.stewardship_backup_run TO PUBLIC",
    ],
    ids=[
        "upload-table",
        "upload-column",
        "run-table",
        "run-completed-at",
        "public-upload",
        "public-run-column",
    ],
)
def test_the_check_refuses_every_insert_left_behind(backup_login, left):
    """0195's closing DO block passes once revoked and refuses each remainder."""
    with connection.cursor() as cursor:
        cursor.execute(CONTRACT_SQL)
        cursor.execute(CONTRACT_CHECK)
        with pytest.raises(DatabaseError, match="0195"), transaction.atomic():
            cursor.execute(left.format(b=BACKUP))
            cursor.execute(CONTRACT_CHECK)


def test_the_check_admits_the_registry_column_grant(backup_login):
    """The run row's other columns are the backup's own insert: admitted."""
    with connection.cursor() as cursor:
        cursor.execute(CONTRACT_SQL)
        cursor.execute(
            sql.SQL(
                "GRANT INSERT (id, started_at, manifest_digest) "
                "ON public.stewardship_backup_run TO {}"
            ).format(sql.Identifier(BACKUP))
        )
        cursor.execute(CONTRACT_CHECK)
