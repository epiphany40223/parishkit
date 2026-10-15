"""Frozen migration 0103: a new draft may start in any time zone (#1330).

0103 replaces ``stewardship_campaign_pointer_v1`` with 0091's body less the
check that a new draft's zone is the parish zone. Creating a campaign in
another zone end to end is tested in ``test_campaign_create_postgresql.py``;
this tests the file itself against the OLD (0091) definition, and that its
DO block refuses each regression.
"""

from difflib import ndiff
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
FROZEN = (MIGRATIONS / "0103_any_creation_timezone.sql").read_text(encoding="utf-8")
OLD = (MIGRATIONS / "0091_drop_ministry_activity.sql").read_text(encoding="utf-8")
POINTER = "public.stewardship_campaign_pointer_v1()"
REFUSAL = "New draft must copy the parish timezone"


def statement(text):
    """The pointer function's ``CREATE OR REPLACE`` statement in ``text``."""
    start = text.index(f"CREATE OR REPLACE FUNCTION {POINTER}")
    return text[start : text.index("END $$;", start) + len("END $$;")]


def installed(cursor):
    """The installed pointer function's body."""
    cursor.execute("SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure", [POINTER])
    return cursor.fetchone()[0]


def test_the_body_is_0091s_less_the_parish_zone_check():
    """Nothing but the one refusal (its IF and END IF) is dropped."""
    diff = list(ndiff(statement(OLD).splitlines(), statement(FROZEN).splitlines()))
    assert not [line for line in diff if line.startswith("+ ")]
    removed = [line[2:].strip() for line in diff if line.startswith("- ")]
    assert len(removed) == 3, removed
    assert "candidate.timezone<>" in removed[0] and REFUSAL in removed[1]
    assert removed[2] == "END IF;"


def test_the_file_replaces_the_old_definition():
    """Over 0091's body the file installs the new one and its check passes."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(statement(OLD))
        assert REFUSAL in installed(cursor)
        cursor.execute(FROZEN)
        assert REFUSAL not in installed(cursor)
        transaction.set_rollback(True)


def test_migration_check_refuses_the_old_body_or_a_changed_function():
    """0103's DO block passes as installed and refuses each regression."""
    capture = FROZEN[FROZEN.index("SELECT set_config(") :]
    capture = capture[: capture.index("true);") + len("true);")]
    check = FROZEN[FROZEN.index("DO $check$") :]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(capture)
        cursor.execute(check)
        for change, message in (
            (statement(OLD), "not installed as declared"),
            (f"ALTER FUNCTION {POINTER} SECURITY DEFINER", "not installed"),
            (f"ALTER FUNCTION {POINTER} RESET search_path", "not installed"),
            (
                "CREATE ROLE pk_s1330_probe NOLOGIN; "
                f"GRANT EXECUTE ON FUNCTION {POINTER} TO pk_s1330_probe",
                "grants changed",
            ),
        ):
            with (
                pytest.raises(DatabaseError, match=f"Migration 0103.*{message}"),
                transaction.atomic(),
            ):
                cursor.execute(change)
                cursor.execute(check)
        cursor.execute(check)
        transaction.set_rollback(True)
