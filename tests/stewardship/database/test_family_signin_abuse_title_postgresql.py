"""The Family sign-in abuse alert's title (#2330, frozen 0202).

0202 replaces ``stewardship_ops_content_v2`` in place, copied whole from
0043 with only the Family abuse title changed, so the SQL twin renders
"Sustained Family sign-in abuse" as ``jobs/operational_content.py`` does
(the fan-out test compares the two for every kind). These tests check that
the whole file upgrades the real 0043 body, and that its closing check
refuses the 0043 body and a change to the function's grants. Each runs in
a transaction that is rolled back.
"""

import pytest
from django.db import DatabaseError, connection, transaction

from .test_hide_gone_members_postgresql import SCHEMA_FILES, definition

pytestmark = pytest.mark.django_db(transaction=True)

NAME = "stewardship_ops_content_v2"
MIGRATION = (SCHEMA_FILES / "migrations/0202_family_signin_abuse_title.sql").read_text(
    encoding="utf-8"
)
# The body 0202 replaced, from its only earlier frozen file.
EARLIER = definition(
    (SCHEMA_FILES / "migrations/0043_alert_local_time.sql").read_text(encoding="utf-8"),
    NAME,
)
REFUSED = "\\(Family sign-in abuse title\\) did not replace"
CHECK = MIGRATION[MIGRATION.index("DO $check$") :]
ACL = MIGRATION[
    MIGRATION.index("SELECT set_config('stewardship.migration_0202_acl'") : (
        MIGRATION.index(", true);", MIGRATION.index("migration_0202_acl"))
        + len(", true);")
    )
]


def installed_source(cursor):
    """The installed content function's body."""
    cursor.execute(
        "SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure",
        [f"public.{NAME}(uuid,text,uuid)"],
    )
    return cursor.fetchone()[0]


def test_the_installed_function_has_the_new_title():
    """The migrated schema renders the sign-in title, never the login one."""
    with connection.cursor() as cursor:
        source = installed_source(cursor)
    assert "'Sustained Family sign-in abuse'" in source
    assert "Family login" not in source


def test_the_file_upgrades_the_earlier_body():
    """On the 0043 body, the whole file installs the new title and commits."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(EARLIER)
        assert "'Sustained Family login abuse'" in installed_source(cursor)
        cursor.execute(MIGRATION)
        source = installed_source(cursor)
        assert "'Sustained Family sign-in abuse'" in source
        assert "Family login" not in source
        transaction.set_rollback(True)


@pytest.mark.parametrize(
    "statement, message",
    [
        (EARLIER, REFUSED),
        (
            f"GRANT EXECUTE ON FUNCTION public.{NAME}(uuid,text,uuid) TO PUBLIC",
            "changed stewardship_ops_content_v2's grants",
        ),
        (
            f"ALTER FUNCTION public.{NAME}(uuid,text,uuid) SECURITY DEFINER",
            REFUSED,
        ),
    ],
    ids=["0043 body", "grant", "definer"],
)
def test_the_closing_check_refuses(statement, message):
    """After the recorded attributes, an old body, a new grant or definer
    rights make the closing check refuse to commit."""
    # A grant to PUBLIC is invisible if PUBLIC already holds EXECUTE (the
    # default); revoke it first so the grant changes the recorded ACL.
    prepare = (
        f"REVOKE EXECUTE ON FUNCTION public.{NAME}(uuid,text,uuid) FROM PUBLIC"
        if statement.startswith("GRANT")
        else "SELECT 1"
    )
    with (
        pytest.raises(DatabaseError, match=f"Migration 0202 {message}"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(prepare)
        cursor.execute(ACL)
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_closing_check_passes_on_the_installed_schema():
    """The installed schema satisfies the check (it is not vacuously strict)."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(ACL)
        cursor.execute(CHECK)
        transaction.set_rollback(True)
