"""The schema leak guard names only real committed schema changes (#1489)."""

import pytest
from django.core.management import call_command
from django.db import connection, transaction

from .schema_leak_guard import (
    FIX,
    SchemaGuard,
    catalog_digest,
    failure,
    schema_connection,
)

pytestmark = pytest.mark.django_db(transaction=True)

TABLE = "stewardship_export_request"
DISABLE = f"ALTER TABLE {TABLE} DISABLE TRIGGER ALL"
ENABLE = f"ALTER TABLE {TABLE} ENABLE TRIGGER ALL"
# Installed with EXECUTE for its owner only (PUBLIC revoked).
FUNCTION = "stewardship_daily_digest_immutable_v1()"
# A column-level grant, the shape database-grants installs (#1519).
COLUMN_GRANT = f"GRANT SELECT (report) ON {TABLE} TO PUBLIC"
COLUMN_REVOKE = f"REVOKE SELECT (report) ON {TABLE} FROM PUBLIC"


def run(sql):
    """Execute one statement on the test connection (autocommit)."""
    with connection.cursor() as cursor:
        cursor.execute(sql)


def changed_triggers():
    """The trigger names whose enabled state DISABLE TRIGGER ALL changes."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "WHERE c.relname=%s AND NOT t.tgisinternal ORDER BY 1",
            [TABLE],
        )
        return [f"triggers: changed {TABLE}.{name}" for (name,) in cursor.fetchall()]


def started():
    """A guard that has seen the current schema."""
    guard = SchemaGuard()
    guard.start(connection)
    return guard


def test_a_committed_change_is_named_once_and_becomes_the_baseline():
    """Committed DDL is reported by object; the next check starts from it."""
    guard = started()
    expected = changed_triggers()
    assert expected
    run(DISABLE)
    try:
        assert guard.finish(connection) == expected
        # The drift is the new baseline: the next test is not blamed for it.
        guard.start(connection)
        assert guard.finish(connection) == []
    finally:
        run(ENABLE)
    # Restoring between tests is taken silently at the next start.
    guard.start(connection)
    assert guard.finish(connection) == []


def test_rolled_back_or_restored_changes_and_data_pass():
    """A rollback, an exact restore, a flush and ANALYZE all pass."""
    guard = started()
    with transaction.atomic():
        run(DISABLE)
        run(f"ALTER TABLE {TABLE} DROP CONSTRAINT export_report_known")
        transaction.set_rollback(True)
    run(DISABLE)
    run(ENABLE)
    run("ANALYZE stewardship_export_request")
    call_command("flush", interactive=False, verbosity=0)
    assert guard.finish(connection) == []


def test_an_identical_function_replacement_passes():
    """CREATE OR REPLACE with the same body moves xmin but not the definition."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1'"
        )
        (definition,) = cursor.fetchone()
    guard = started()
    run(definition)
    assert guard.finish(connection) == []


def test_a_revoked_grant_leaves_the_digest_alone():
    """A GRANT undone by REVOKE rewrites the catalog row but not the digest.

    Many tests grant a throwaway login role the runtime privileges and drop
    it again. Were the digest to hash row versions, each of them would cost a
    full inventory (#1498).
    """
    before = catalog_digest(connection)
    guard = started()
    run(f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO PUBLIC")
    run(f"REVOKE EXECUTE ON FUNCTION {FUNCTION} FROM PUBLIC")
    run(COLUMN_GRANT)
    run(COLUMN_REVOKE)
    assert catalog_digest(connection) == before
    assert guard.finish(connection) == []


@pytest.mark.parametrize(
    ("change", "restore", "expected"),
    [
        (
            f"REVOKE EXECUTE ON FUNCTION {FUNCTION} FROM stewardship_tests",
            f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO stewardship_tests",
            f"functions: changed {FUNCTION}",
        ),
        (
            f"ALTER TABLE {TABLE} ALTER COLUMN report SET DEFAULT 'x'",
            f"ALTER TABLE {TABLE} ALTER COLUMN report DROP DEFAULT",
            f"columns: changed {TABLE}.report",
        ),
        (COLUMN_GRANT, COLUMN_REVOKE, f"columns: changed {TABLE}.report"),
    ],
)
def test_a_committed_grant_or_default_change_is_named(change, restore, expected):
    """A committed REVOKE or column default is reported, then re-baselined."""
    guard = started()
    run(change)
    try:
        assert guard.finish(connection) == [expected]
        guard.start(connection)
        assert guard.finish(connection) == []
    finally:
        run(restore)


def test_a_committed_column_revoke_is_named():
    """Revoking a column grant the schema already held is reported too (#1519)."""
    run(COLUMN_GRANT)
    try:
        guard = started()
        run(COLUMN_REVOKE)
        assert guard.finish(connection) == [f"columns: changed {TABLE}.report"]
    finally:
        run(COLUMN_REVOKE)


def test_the_failure_names_the_change_and_the_fix():
    """The teardown message lists each change and the fix."""
    message = failure(["functions: changed stewardship_x()"])
    assert message == (
        "This test left schema changes behind:\n"
        "  functions: changed stewardship_x()\n" + FIX
    )
    assert "transaction.set_rollback(True)" in FIX


def test_a_closed_connection_is_read_through_a_short_lived_copy(monkeypatch):
    """The check never reopens a test connection that a test forbade reopening."""
    guard = started()
    connection.close()

    def forbidden(*args, **kwargs):
        """Fail if the check reopens the test's own connection."""
        pytest.fail("The schema check reopened the test connection")

    monkeypatch.setattr(connection, "get_new_connection", forbidden)
    with schema_connection(connection) as checked:
        assert checked is not connection
        assert guard.finish(checked) == []
    assert connection.connection is None
    assert checked.connection is None
