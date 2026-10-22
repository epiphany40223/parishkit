"""Forward migration: the audit index the user timeline reads by (#1866).

Its SQL is the frozen file ``schema/migrations/0152_audit_actor_time.sql``
(prefix 0152, a number assigned centrally at merge), read whole
and never parsed. It creates ``audit_event_actor_time``, a B-tree on the
audit records' ``(actor_id, created_at)``, so a user's timeline reads the
records of the user's Google accounts without scanning the whole audit
table, and ends with a DO block that refuses to commit unless the index is
installed as declared. The state operation records the same index on the
model, so ``makemigrations --check`` stays clean. Reversing needs its own
forward migration, so it has no reverse operation.

It lives in the audit app, which owns the audit table, and depends on the
migration that installs the previous frozen file, so the files apply in
prefix order. Its statement is safe to repeat, so the users swap test,
which may run it again, needs no registration for it.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0152_audit_actor_time.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The audit app's previous migration (0122, #1620).
        ("stewardship_audit", "0006_automatic_report_views"),
        # The migration that installs the previous frozen file (0151,
        # #1840), so the files apply in prefix order.
        ("stewardship_responses", "0026_limitation_handled_undo"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="auditevent",
                    index=models.Index(
                        fields=["actor_id", "created_at"],
                        name="audit_event_actor_time",
                    ),
                ),
            ],
        )
    ]
