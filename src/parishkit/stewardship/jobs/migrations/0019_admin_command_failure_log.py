"""Forward migration: a durable entry for a failed Admin CLI command (#617).

Its SQL is the frozen file
``schema/migrations/0038_admin_command_failure_log.sql``, read whole and never
parsed. It widens the operational log's closed event list
(``operational_event_safe``) with ``admin_command_failed``, lets the
``failure`` context carry the command's catalog name and the two
``admin_command`` failure words (``stewardship_safe_context_v1``), and lets
the web login, which the command line runs as, write that one ERROR entry
(``stewardship_operational_log_writer_v1``). It ends with a DO block that
refuses to commit unless all three are installed. No model changes, so there
is no state operation. Reversing needs its own forward migration, so, like
the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0038_admin_command_failure_log.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration.
        ("stewardship_jobs", "0018_go_live_log_events"),
        # The migration that installed the previous frozen file (0037,
        # #817), so the files apply in prefix order.
        ("stewardship_reports", "0006_family_test_names"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
