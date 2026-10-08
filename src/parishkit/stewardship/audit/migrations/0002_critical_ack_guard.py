"""Forward migration: guard critical-event acknowledgements (#389 L8).

Its SQL is the frozen file ``schema/migrations/0031_critical_ack_guard.sql``,
read whole and never parsed. It adds ``stewardship_critical_event_ack_insert_v1``
and its BEFORE INSERT trigger: only the web login records an acknowledgement,
only for an existing CRITICAL operational log entry and with a named actor;
the file ends with a DO block that refuses to commit unless every existing
acknowledgement already meets that rule and the trigger is installed as
declared. It changes no table, so there is no state operation. Reversing
needs its own forward migration, so it has no reverse operation.

The dependency on the migration that installed the previous frozen file
(0030) keeps the files in prefix order.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0031_critical_ack_guard.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_audit", "0001_initial"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order.
        ("stewardship_accounts", "0008_portal_user_insert_guard"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
