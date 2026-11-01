"""SQL guards and Python agree on the session idle limits (#389 L9, #2366).

The idle limit is a literal in many SQL guards (a session is idle once its
``last_activity_at`` is that long ago) and a ``timedelta`` in
``accounts/session_policy.py``. They agree today; this test keeps a later edit
of one side from silently disagreeing with the other. Each literal is checked
against the limit of the session table its guard reads. The frozen migrations
are scanned too: they install the deployed function bodies, such as the
session purges.
"""

import re
from datetime import timedelta
from pathlib import Path

import pytest

from parishkit.stewardship.accounts.session_policy import ADMIN_IDLE, FAMILY_IDLE

SCHEMA = Path(__file__).parents[2] / "src/parishkit/stewardship/schema"
MIGRATIONS = SCHEMA / "migrations"
# "last_activity_at + interval '60 minutes'",
# "last_activity_at > clock_timestamp() - interval '60 minutes'" and the purge
# form "last_activity_at<=statement_timestamp()-interval '60 minutes'". The
# quote may be doubled where the guard text sits inside a SQL string literal.
IDLE = re.compile(
    r"last_activity_at\s*(?:\+\s*|[<>]=?\s*[a-z_.()]+\s*-\s*)"
    r"interval\s*'{1,2}(\d+) (minute|hour)s?'"
)
LIMITS = {
    "stewardship_portal_session": ADMIN_IDLE,
    "stewardship_family_session": FAMILY_IDLE,
}


def _idle_sites(paths):
    """(file:line, interval, session tables named by its CREATE statement)."""
    sites = []
    for path in paths:
        text = path.read_text()
        for match in IDLE.finditer(text):
            # The enclosing statement runs from its CREATE to the next one.
            start = text.rfind("\nCREATE ", 0, match.start())
            end = text.find("\nCREATE ", match.end())
            statement = text[start : end if end >= 0 else len(text)]
            count, unit = int(match.group(1)), match.group(2)
            sites.append(
                (
                    f"{path.name}:{text.count(chr(10), 0, match.start()) + 1}",
                    timedelta(**{f"{unit}s": count}),
                    {table for table in LIMITS if table in statement},
                )
            )
    return sites


SITES = _idle_sites(sorted(SCHEMA.glob("*.sql")) + sorted(MIGRATIONS.glob("*.sql")))


def test_the_scan_finds_both_kinds_of_session_guard():
    """A broken pattern must not pass vacuously."""
    assert len(SITES) >= 15
    tables = [tables for _, _, tables in SITES]
    assert {"stewardship_portal_session"} in tables
    assert {"stewardship_family_session"} in tables


def test_the_scan_reads_the_frozen_migrations():
    """Migration guards are compared too, including the Admin session purge."""
    migration_sites = _idle_sites(sorted(MIGRATIONS.glob("*.sql")))
    assert len(migration_sites) >= 80
    purge = (MIGRATIONS / "0004_automation_sessions.sql").read_text()
    line = purge[: purge.index("last_activity_at<=")].count("\n") + 1
    assert f"0004_automation_sessions.sql:{line}" in [site for site, _, _ in SITES]


def test_a_drifted_migration_literal_is_caught(tmp_path):
    """A changed literal in a migration copy no longer matches its limit."""
    copy = tmp_path / "0004_automation_sessions.sql"
    original = (MIGRATIONS / copy.name).read_text()
    copy.write_text(original.replace("interval '60 minutes'", "interval '45 minutes'"))
    drifted = _idle_sites([copy])
    assert drifted
    assert all(interval == timedelta(minutes=45) for _, interval, _ in drifted)
    assert all(interval not in LIMITS.values() for _, interval, _ in drifted)


@pytest.mark.parametrize(
    ("site", "interval", "tables"), SITES, ids=[site for site, _, _ in SITES]
)
def test_sql_idle_literal_matches_python(site, interval, tables):
    """Each guard's literal is the Python limit for the session table it reads.

    A statement that names both session tables (a Family sign-in that checks
    the Admin's portal session, as in 0188) must match both limits, so it still
    fails if the two limits ever diverge and the guard needs a closer look.
    """
    assert tables, f"{site}: name a session table"
    for table in tables:
        assert interval == LIMITS[table], f"{site}: {table} limit"
