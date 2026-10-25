"""Fast SQL-shape tests of the inspection login (#2148).

The Docker provisioning test (test_database_provisioning_container.py) runs
the same functions against a real migrated PostgreSQL; these pin, without
one, what the operator's cursor is sent: a new login with no password, its
settings and its one SET FALSE membership, an existing login checked and
never altered, and grants refused on excess before any GRANT.
"""

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import inspect_login
from parishkit.stewardship.inspect_login import (
    EXCLUDED_COLUMNS,
    INSPECT_LOGIN,
    INSPECT_SETTINGS,
    REVIEWED_COLUMNS,
    ensure_inspect_login,
    grant_inspect,
)

MARKER = "parishkit-stewardship:00000000-0000-0000-0000-000000000001"


class Cursor:
    """Record rendered SQL and answer reads from a fixed sequence."""

    def __init__(self, rows=(), tables=()):
        self.rows, self.tables, self.statements = iter(rows), iter(tables), []

    def execute(self, statement, parameters=None):
        """Psycopg composables render without a connection."""
        self.statements.append(
            statement if isinstance(statement, str) else statement.as_string()
        )

    def fetchone(self):
        """An unexpected read fails instead of answering permissively."""
        return next(self.rows)

    def fetchall(self):
        """The next row set: revocable grants, then expected grants."""
        return next(self.tables)


def test_a_new_login_has_no_password_and_cannot_change_identity():
    """Created NOINHERIT, passwordless, read-only, with SET FALSE membership."""
    cursor = Cursor(rows=[(False,)])
    ensure_inspect_login(cursor, MARKER, "stewardship")
    sql = "\n".join(cursor.statements)
    create = next(s for s in cursor.statements if s.startswith("CREATE ROLE"))
    assert create == (
        f'CREATE ROLE "{INSPECT_LOGIN}" LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS '
        "NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 3"
    )
    assert "PASSWORD" not in sql
    assert f"COMMENT ON ROLE \"{INSPECT_LOGIN}\" IS '{MARKER}'" in sql
    assert (
        f'ALTER ROLE "{INSPECT_LOGIN}" SET "default_transaction_read_only" = \'on\''
        in sql
    )
    assert f'ALTER ROLE "{INSPECT_LOGIN}" SET search_path = pg_catalog, public' in sql
    assert sum(s.startswith("ALTER ROLE") for s in cursor.statements) == len(
        INSPECT_SETTINGS
    )
    assert cursor.statements[-3:] == [
        f'GRANT CONNECT ON DATABASE "stewardship" TO "{INSPECT_LOGIN}"',
        f'GRANT USAGE ON SCHEMA public TO "{INSPECT_LOGIN}"',
        f'GRANT pg_read_all_stats TO "{INSPECT_LOGIN}" WITH INHERIT TRUE, SET FALSE',
    ]


@pytest.mark.parametrize("matches", [True, False])
def test_an_existing_login_is_checked_and_never_altered(matches):
    """A matching login is left alone; a differing one is refused."""
    cursor = Cursor(rows=[(True,), (matches,)])
    if matches:
        ensure_inspect_login(cursor, MARKER, "stewardship")
    else:
        with pytest.raises(ConfigError, match="inspection login differs"):
            ensure_inspect_login(cursor, MARKER, "stewardship")
    assert len(cursor.statements) == 2
    assert MARKER in cursor.statements[1]
    assert not any(
        s.startswith(("CREATE", "ALTER", "GRANT", "COMMENT")) for s in cursor.statements
    )


def test_grants_are_refused_on_excess_before_any_grant():
    """Excess (a write, an excluded column) stops the step; nothing is granted."""
    cursor = Cursor(rows=[(True,)])
    with pytest.raises(ConfigError, match="exceed provisioning intent"):
        grant_inspect(cursor)
    assert not any(s.startswith("GRANT") for s in cursor.statements)


def test_grants_revoke_narrowed_reads_then_grant_and_recheck():
    """A no-longer-expected SELECT is revoked; then the grants; then a recheck."""
    cursor = Cursor(
        rows=[(False,), (False,)],
        tables=[
            [
                ("stewardship_secret_view", None),
                ("stewardship_secret_view", "id"),
                ("stewardship_portal_session", "x"),
            ],
            [
                ("stewardship_campaign", None),
                ("stewardship_portal_session", "principal_id"),
                ("stewardship_portal_session", "id"),
            ],
        ],
    )
    grant_inspect(cursor)
    assert [s for s in cursor.statements if s.startswith(("GRANT", "REVOKE"))] == [
        'REVOKE SELECT ("x") ON "public"."stewardship_portal_session" '
        f'FROM "{INSPECT_LOGIN}"',
        f'REVOKE SELECT ON "public"."stewardship_secret_view" FROM "{INSPECT_LOGIN}"',
        f'GRANT SELECT ON "public"."stewardship_campaign" TO "{INSPECT_LOGIN}"',
        'GRANT SELECT ("id", "principal_id") ON "public"."stewardship_portal_session" '
        f'TO "{INSPECT_LOGIN}"',
    ]
    # The last statement is the full recheck, reads included.
    assert "aclexplode" not in cursor.statements[-1]
    assert cursor.statements[-1].count("NOT EXISTS(SELECT 1 FROM expected e") == 2


def test_grants_refuse_what_remains_after_them():
    """Excess the revokes could not remove (a PUBLIC grant) is refused."""
    cursor = Cursor(rows=[(False,), (True,)], tables=[[], []])
    with pytest.raises(ConfigError, match="exceed provisioning intent"):
        grant_inspect(cursor)


def test_the_rule_names_every_exclusion_and_row_security():
    """The catalog rule carries each excluded name and the row-security test."""
    sql = inspect_login.expected_selects()
    assert "relrowsecurity" in sql and "prosecdef" in sql
    for table, columns in EXCLUDED_COLUMNS.items():
        assert f"'{table}'" in sql
        assert all(f"'{column}'" in sql for column in columns)
    for table in inspect_login.EXCLUDED_TABLES:
        assert f"'{table}'" in sql
    # A reviewed column is never also excluded.
    assert not any(
        column in EXCLUDED_COLUMNS.get(table, ()) for table, column in REVIEWED_COLUMNS
    )


def test_role_and_grant_checks_name_the_login_and_marker():
    """The upgrade check's two parts bind this login and this deployment."""
    role = inspect_login.role_matches(MARKER)
    assert f"'{INSPECT_LOGIN}'" in role and f"'{MARKER}'" in role
    assert "rolpassword IS NULL" in role and "set_option" in role
    assert "'CONNECT')" in role and "NOT has_database_privilege" in role
    assert all(f"'{setting}'" in role for setting in INSPECT_SETTINGS)
    exact = inspect_login.grants_exact()
    assert exact.startswith("(WITH expected AS (WITH RECURSIVE")
    assert f"'{INSPECT_LOGIN}'" in exact
