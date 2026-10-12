"""Frozen migration 0058's self-check for the Mail message recipient read (#1024).

The file ends with a DO block that refuses to commit unless
``stewardship_delivery_recipients_v1`` is installed exactly as declared: a
stable SECURITY DEFINER set-returning function with the fixed search path
that PUBLIC cannot execute. These tests run that block against the
installed database (it passes) and against databases where the function is
missing or installed differently (it raises). Each change is made inside a
transaction that is rolled back. What the function returns, and whom it
refuses, is tested with the Mail message page in
``test_delivery_views_postgresql.py``.
"""

from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0058_mail_message_recipients.sql"
).read_text(encoding="utf-8")
CHECK = FROZEN[FROZEN.index("DO $check$") :]
FUNCTION = "public.stewardship_delivery_recipients_v1(uuid, uuid)"

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_check_passes_on_the_installed_function():
    """A migrated database has the function exactly as 0058 declares it."""
    with connection.cursor() as cursor:
        cursor.execute(CHECK)


@pytest.mark.parametrize(
    "change",
    [
        f"DROP FUNCTION {FUNCTION}",
        f"ALTER FUNCTION {FUNCTION} SECURITY INVOKER",
        f"ALTER FUNCTION {FUNCTION} SET search_path TO public",
        f"ALTER FUNCTION {FUNCTION} VOLATILE",
        f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO PUBLIC",
    ],
)
def test_the_check_refuses_a_missing_or_different_function(change):
    """Without the function, or with any declared attribute changed, the
    DO block raises, so the migration would not commit."""
    with (
        pytest.raises(DatabaseError, match="0058"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(change)
        cursor.execute(CHECK)
