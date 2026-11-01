"""The verified-address check's audit context (#2343, frozen migration 0203).

``stewardship_safe_context_v1`` admits two more action keys: the check's
closed outcome word (``address_check``) and the match's ID (``match_id``).
These tests prove the stored audit rows take them, and that the file's
closing DO block refuses the definition it replaces (0174's), rights it
should not have and changed grants.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.audit.schemas import Action, ActorKind
from parishkit.stewardship.audit.services import record_action

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = (MIGRATIONS / "migrations/0203_address_check_context.sql").read_text(
    encoding="utf-8"
)
SNAPSHOT_START = FROZEN.index("SELECT set_config('stewardship.migration_0203_acl'")
# The file's grants snapshot and its closing DO block, run together.
SNAPSHOT = FROZEN[SNAPSHOT_START : FROZEN.index(";\n", SNAPSHOT_START) + 1]
CHECK = FROZEN[FROZEN.index("DO $check$") :]


def test_audit_rows_take_the_outcome_and_the_match():
    """A re-check's outcome and match ID, as the sign-in records them."""
    match = uuid4()
    with transaction.atomic():
        event = record_action(
            Action.ADMIN_ADDRESS_CHECKED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=uuid4(),
            context={"address_check": "rechecked", "match_id": match},
        )
    assert AuditContext.objects.get(event=event).context == {
        "address_check": "rechecked",
        "match_id": str(match),
    }


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
