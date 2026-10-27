"""Frozen 0187 admits ``consumer_readmission`` and refuses to commit without it.

The installed check admitting every Python event is
``test_review_round2_postgresql``'s job; these check 0187's own DO block
against 0106's older event list, and that its file may run again, as the
users swap test does. Each test rolls its changes back.
"""

from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)
MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = (MIGRATIONS / "0187_consumer_readmission_log.sql").read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
OLDER = (MIGRATIONS / "0106_admin_input_refusal_log.sql").read_text(encoding="utf-8")
# 0106's two statements that re-create the event check with its older list.
OLDER_CHECK = OLDER[
    OLDER.index("ALTER TABLE public.stewardship_operational_log DROP") : OLDER.index(
        "CREATE OR REPLACE FUNCTION"
    )
]


def test_the_check_refuses_the_older_event_list():
    """0106's list, without the new event, cannot pass 0187's DO block."""
    assert "consumer_readmission" not in OLDER_CHECK
    with (
        pytest.raises(DatabaseError, match="does not admit: consumer_readmission"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(OLDER_CHECK)
        cursor.execute(CHECK)


def test_the_check_refuses_a_missing_constraint():
    """A dropped event check cannot pass the DO block either."""
    with (
        pytest.raises(DatabaseError, match="0187: operational_event_safe is not"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "ALTER TABLE public.stewardship_operational_log "
            "DROP CONSTRAINT operational_event_safe"
        )
        cursor.execute(CHECK)


def test_the_file_runs_again_over_the_older_list():
    """The whole file, run after 0106's older list, installs the new event."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(OLDER_CHECK)
        cursor.execute(FROZEN)
        cursor.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname='operational_event_safe'"
        )
        assert "'consumer_readmission'" in cursor.fetchone()[0]
        transaction.set_rollback(True)
