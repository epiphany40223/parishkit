"""SQL guards and Python agree on the session idle limits (#389 L9).

The idle limit is a literal in many SQL guards (a session is idle once its
``last_activity_at`` is that long ago) and a ``timedelta`` in
``accounts/session_policy.py``. They agree today; this test keeps a later edit
of one side from silently disagreeing with the other. Each literal is checked
against the limit of the session table its guard reads.
"""

import re
from datetime import timedelta
from pathlib import Path

import pytest

from parishkit.stewardship.accounts.session_policy import ADMIN_IDLE, FAMILY_IDLE

SCHEMA = Path(__file__).parents[2] / "src/parishkit/stewardship/schema"
# "last_activity_at + interval '60 minutes'" and
# "last_activity_at > clock_timestamp() - interval '60 minutes'".
IDLE = re.compile(
    r"last_activity_at\s*(?:\+\s*|>\s*[a-z_.()]+\s*-\s*)"
    r"interval\s*'(\d+) (minute|hour)s?'"
)
LIMITS = {
    "stewardship_portal_session": ADMIN_IDLE,
    "stewardship_family_session": FAMILY_IDLE,
}


def _idle_sites():
    """(file:line, interval, session tables named by its CREATE statement)."""
    sites = []
    for path in sorted(SCHEMA.glob("*.sql")):
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


SITES = _idle_sites()


def test_the_scan_finds_both_kinds_of_session_guard():
    """A broken pattern must not pass vacuously."""
    assert len(SITES) >= 15
    tables = [tables for _, _, tables in SITES]
    assert {"stewardship_portal_session"} in tables
    assert {"stewardship_family_session"} in tables


@pytest.mark.parametrize(
    ("site", "interval", "tables"), SITES, ids=[site for site, _, _ in SITES]
)
def test_sql_idle_literal_matches_python(site, interval, tables):
    """Each guard's literal is the Python limit for the session table it reads."""
    assert len(tables) == 1, f"{site}: name exactly one session table"
    assert interval == LIMITS[next(iter(tables))]
