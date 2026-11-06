"""The verified-address check's audit context (#2343, frozen migration 0203).

``stewardship_safe_context_v1`` admits two more action keys: the check's
closed outcome word (``address_check``) and the match's ID (``match_id``).
Sign-in through another verified address was removed (#2387), so nothing
records them now; these tests keep the frozen file covered until the
forward migration that drops the match schema. They prove SQL's closed set,
and that the file's closing DO block refuses the definition it replaces
(0174's), rights it should not have and changed grants.
"""

from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = (MIGRATIONS / "migrations/0203_address_check_context.sql").read_text(
    encoding="utf-8"
)
SNAPSHOT_START = FROZEN.index("SELECT set_config('stewardship.migration_0203_acl'")
# The file's grants snapshot and its closing DO block, run together.
SNAPSHOT = FROZEN[SNAPSHOT_START : FROZEN.index(";\n", SNAPSHOT_START) + 1]
CHECK = FROZEN[FROZEN.index("DO $check$") :]


@pytest.mark.parametrize(
    "context",
    ['{"address_check": "matched"}', '{"match_id": "person@example.org"}'],
)
def test_sql_refuses_other_values(context):
    """SQL holds the same closed set as Python, and IDs only."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_safe_context_v1('action', %s::jsonb)", [context]
        )
        assert cursor.fetchone()[0] is False


def test_the_do_block_passes_installed_and_refuses_each_missing_part():
    """Installed it passes; 0174's body, definer rights or a grant change fail."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(SNAPSHOT)
        cursor.execute(CHECK)
    old = (MIGRATIONS / "migrations/0174_parishsoft_organization_choice.sql").read_text(
        encoding="utf-8"
    )
    start = old.index("CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1(")
    before = old[start : old.index("END $_$;", start) + len("END $_$;")]
    for change in (
        before,
        "ALTER FUNCTION public.stewardship_safe_context_v1(text,jsonb) "
        "SECURITY DEFINER",
        "ALTER FUNCTION public.stewardship_safe_context_v1(text,jsonb) VOLATILE",
        "ALTER FUNCTION public.stewardship_safe_context_v1(text,jsonb) "
        "RESET search_path",
    ):
        with (
            pytest.raises(DatabaseError, match="Migration 0203"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(SNAPSHOT)
            cursor.execute(change)
            cursor.execute(CHECK)
    with (
        pytest.raises(DatabaseError, match="Migration 0203: .* grants"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(SNAPSHOT)
        cursor.execute(
            "REVOKE ALL ON FUNCTION public.stewardship_safe_context_v1(text,jsonb) "
            "FROM PUBLIC"
        )
        cursor.execute(CHECK)
