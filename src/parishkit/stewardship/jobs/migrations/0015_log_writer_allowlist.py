"""Forward migration: a per-login allow-list in the operational log writer.

Its SQL is the frozen file ``schema/migrations/0029_log_writer_allowlist.sql``
(#389 L2), read whole and never parsed. It replaces the body of
``stewardship_operational_log_writer_v1`` so that web, worker and scheduler,
like mail dispatch and the backup login before them, may record only the
(schema, event, level) entries their own code writes, with no actor and the
database's own time; the file ends with a DO block that refuses to commit
unless the new body is installed behind the same trigger. It changes no
table, so there is no state operation. Reversing needs its own forward
migration, so it has no reverse operation.

The dependency on the migration that installed the previous frozen file
(0028) keeps the files in prefix order.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0029_log_writer_allowlist.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0014_task_event_retention"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order.
        ("stewardship_campaigns", "0007_drop_initial_invitation_state"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
