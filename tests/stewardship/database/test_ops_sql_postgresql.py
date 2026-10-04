"""The operator scripts' own SQL runs against the real schema (#459).

tools/stewardship-ops/predeploy.sql and send-monitor.sql are run here as
psql would run them: the psql variables substituted, the psql-only lines
(\\pset) and the read-only transaction brackets left out (the test runs in
its own rolled-back transaction). This catches a renamed table, column or
state literal that the scripts' stand-in tests cannot see.
"""

import re
from pathlib import Path

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db

OPS = Path(__file__).resolve().parents[3] / "tools" / "stewardship-ops"


def run_sql(name, **values):
    """Run one of the scripts' SQL files; return each statement's rows."""
    text = (OPS / name).read_text()
    text = "\n".join(line for line in text.splitlines() if not line.startswith("\\"))
    results = []
    with connection.cursor() as cursor:
        for statement in re.split(r";\s*\n", text):
            body = (
                "\n".join(
                    line for line in statement.splitlines() if not line.startswith("--")
                )
                .strip()
                .rstrip(";")
            )
            if not body or body in ("BEGIN READ ONLY", "ROLLBACK"):
                continue
            for key, value in values.items():
                body = body.replace(f":'{key}'", "'" + value.replace("'", "''") + "'")
            assert ":'" not in body, body
            cursor.execute(body)
            results.append(cursor.fetchall())
    return results


def test_predeploy_sql_runs_and_ends_with_the_blocking_count():
    """Every check runs, and the last line is the count the script reads."""
    results = run_sql("predeploy.sql", tz="America/New_York")
    assert len(results) == 5
    assert re.fullmatch(r"predeploy: blocking=\d+", results[-1][0][0])


def test_send_monitor_sql_runs_and_reports_an_empty_send():
    """One line; nothing created since a future time gives the empty values."""
    results = run_sql(
        "send-monitor.sql",
        purpose="initial",
        mode="production",
        since="2999-01-01 00:00Z",
        tz="UTC",
    )
    assert len(results) == 1 and len(results[0]) == 1
    _, states, accepted, first, last = results[0][0]
    assert (states, accepted, first, last) == (
        "none",
        "accepted_last_min=0",
        "first=-",
        "last=-",
    )
