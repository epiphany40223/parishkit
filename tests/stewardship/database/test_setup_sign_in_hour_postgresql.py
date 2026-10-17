"""Migration 0104's DO block refuses every earlier or weakened body (#1402).

0104 (``schema/migrations/0104_setup_sign_in_hour.sql``) widens the setup
credential guard's sign-in window to an hour, inclusive, makes the staged
credential's audit record the sign-in age, and lets the safe context admit
that key. Following the in-place SQL rule, its check is run here against
the bodies it replaced (0055's guard, 0102's safe context) and against
weakened copies, inside a rolled-back transaction, and must refuse each.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0104_setup_sign_in_hour.sql"
GUARD = "stewardship_setup_secret_guard_v1"
AUDIT = "stewardship_setup_secret_audit_v1"
CONTEXT = "stewardship_safe_context_v1"


def statement(path, name):
    """One ``CREATE OR REPLACE FUNCTION`` statement of a frozen file, whole."""
    text = path.read_text(encoding="utf-8")
    found = re.search(
        rf"^CREATE OR REPLACE FUNCTION public\.{name}\(.*?^END \$_?\$;$",
        text,
        re.M | re.S,
    )
    assert found and len(found[0]) > 500, name
    return found[0]


def test_migration_check_refuses_each_earlier_or_weakened_body():
    """0104's check passes as installed and refuses each regression."""
    text = FROZEN.read_text(encoding="utf-8")
    capture = text[text.index("SELECT set_config(") : text.index("true);") + 6]
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    guard = statement(FROZEN, GUARD)
    audit = statement(FROZEN, AUDIT)
    changes = (
        # The bodies 0104 replaced.
        (statement(MIGRATIONS / "0055_setup_users.sql", GUARD), "guard is not"),
        (statement(MIGRATIONS / "0102_setup_load_void.sql", CONTEXT), "safe context"),
        # A strict hour (the web's check is inclusive) and the old five minutes.
        (
            guard.replace(
                "authenticated_at>=stamp-interval '60 minutes'",
                "authenticated_at>stamp-interval '60 minutes'",
            ),
            "guard is not",
        ),
        (
            guard.replace(
                "authenticated_at>=stamp-interval '60 minutes'",
                "authenticated_at>stamp-interval '5 minutes'",
            ),
            "guard is not",
        ),
        # An audit that no longer records the age.
        (
            audit.replace(
                "context:=context||jsonb_build_object('sign_in_age_seconds',age);",
                "NULL;",
            ),
            "audit is not",
        ),
        (f"ALTER FUNCTION public.{GUARD}() SECURITY DEFINER", "replaced function"),
        (
            "CREATE ROLE pk_s1402_probe NOLOGIN; "
            f"GRANT EXECUTE ON FUNCTION public.{AUDIT}() TO pk_s1402_probe",
            "grants",
        ),
        (
            "ALTER TABLE public.stewardship_setup_sealed_credential "
            "DISABLE TRIGGER stewardship_setup_secret_audit",
            "trigger is missing",
        ),
    )
    with transaction.atomic(), connection.cursor() as cursor:
        # 0113 (#1547) later drops the hour, so 0104's own guard goes back
        # first; the transaction rolls back.
        cursor.execute(guard)
        cursor.execute(capture)
        cursor.execute(check)
        for change, message in changes:
            with (
                pytest.raises(DatabaseError, match=f"Migration 0104.*{message}"),
                transaction.atomic(),
            ):
                cursor.execute(change)
                cursor.execute(check)
        cursor.execute(check)
        transaction.set_rollback(True)
