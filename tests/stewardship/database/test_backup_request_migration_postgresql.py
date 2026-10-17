"""The Take a backup now request migration's frozen file checks itself (#530).

``0062_backup_request.sql`` creates ``stewardship_backup_request`` and its
guard. Its closing DO block must pass on an installed database and refuse
one where any part is missing or changed: the database as it was before
(no table), a guard given definer's rights, a guard open to PUBLIC, or a
dropped trigger. Each case runs in a rolled-back transaction, which first
puts back 0062's own guard body: 0113 (#1547) later replaces it.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
TEXT = (MIGRATIONS / "0062_backup_request.sql").read_text(encoding="utf-8")
CHECK = re.search(r"^DO \$check\$.*?^\$check\$;", TEXT, re.M | re.S)[0]
GUARD = "public.stewardship_backup_request_guard_v1()"
BODY = re.search(
    r"^CREATE FUNCTION public\.stewardship_backup_request_guard_v1\(\)"
    r".*?^END \$\$;",
    TEXT,
    re.M | re.S,
)[0]
# 0062's own guard, put back inside each rolled-back transaction: the live
# schema has 0113's body (#1547), which this file's check rightly refuses.
OWN = BODY.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")


def refused(change, message):
    """``change`` (SQL) then the check, refused with ``message``; rolled back."""
    with (
        connection.cursor() as cursor,
        pytest.raises(DatabaseError, match=message),
        transaction.atomic(),
    ):
        cursor.execute(OWN)
        cursor.execute(change)
        cursor.execute(CHECK)


def test_the_check_passes_on_an_installed_database():
    """Everything the file installs is there as declared."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(OWN)
        cursor.execute(CHECK)
        transaction.set_rollback(True)


def test_the_check_refuses_the_database_before_it():
    """Without the table (every database before 0062) the file cannot commit."""
    refused(
        "DROP TABLE public.stewardship_backup_request CASCADE",
        "table was not created",
    )


def test_the_check_refuses_a_definer_guard():
    """The guard runs with the caller's rights, never the owner's."""
    refused(f"ALTER FUNCTION {GUARD} SECURITY DEFINER", "wrong security")


def test_the_check_refuses_a_public_guard():
    """PUBLIC never executes the guard."""
    refused(f"GRANT EXECUTE ON FUNCTION {GUARD} TO PUBLIC", "PUBLIC may execute")


def test_the_check_refuses_a_missing_trigger():
    """The guard is attached to every insert, update and delete."""
    refused(
        "DROP TRIGGER stewardship_backup_request_guard "
        "ON public.stewardship_backup_request",
        "trigger was not created",
    )


def test_the_check_refuses_a_guard_without_the_automation_gate():
    """A guard that admits only a five-minute sign-in (#706's first body,
    before automation sessions stood in for one) fails the check."""
    gate = "OR public.stewardship_automation_fresh_v1(login.id,NEW.actor_id)"
    assert gate in OWN
    refused(OWN.replace(gate, ""), "not the one this file installs")
