"""Migration 0122's DO block refuses every earlier or weakened body (#1620).

0122 (``schema/migrations/0122_automatic_report_views.sql``) lets the safe
context admit the boolean action key ``automatic``, which an open report's
own re-read records. Following the in-place SQL rule, its check is run here
against the body it replaced (0109's safe context) and against weakened
copies, inside a rolled-back transaction, and must refuse each.
"""

import pytest
from django.db import DatabaseError, connection, transaction

from .test_setup_sign_in_hour_postgresql import CONTEXT, MIGRATIONS, statement

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = MIGRATIONS / "0122_automatic_report_views.sql"


def test_migration_check_refuses_each_earlier_or_weakened_body():
    """0122's check passes as installed and refuses each regression."""
    text = FROZEN.read_text(encoding="utf-8")
    capture = text[text.index("SELECT set_config(") : text.index("true);") + 6]
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    context = statement(FROZEN, CONTEXT)
    changes = (
        # The body 0122 replaced.
        (
            statement(MIGRATIONS / "0109_point_in_time_exports.sql", CONTEXT),
            "does not admit automatic",
        ),
        # The key listed but no longer checked as a boolean.
        (
            context.replace(
                ",'directory_response_columns','automatic') THEN",
                ",'directory_response_columns') THEN",
            ),
            "does not admit automatic",
        ),
        (
            f"ALTER FUNCTION public.{CONTEXT}(text,jsonb) SECURITY DEFINER",
            "not installed",
        ),
        (
            "CREATE ROLE pk_s1620_probe NOLOGIN; "
            f"GRANT EXECUTE ON FUNCTION public.{CONTEXT}(text,jsonb) TO pk_s1620_probe",
            "grants",
        ),
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(capture)
        cursor.execute(check)
        for change, message in changes:
            with (
                pytest.raises(DatabaseError, match=f"Migration 0122.*{message}"),
                transaction.atomic(),
            ):
                cursor.execute(change)
                cursor.execute(check)
        cursor.execute(check)
        transaction.set_rollback(True)
