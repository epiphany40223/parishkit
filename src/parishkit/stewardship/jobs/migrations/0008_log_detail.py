"""Forward migration: WARNING-or-above log entries say what went wrong (#633).

Its SQL is the frozen file ``schema/migrations/0008_log_detail.sql`` (the next
prefix in the repository-wide file sequence after #622's
``0007_system_health_records.sql``, which this app's previous migration
installs), read whole and never parsed. It widens the operational context
allowlist (``stewardship_safe_context_v1``) with the closed ``failure`` and
``recovery`` schemas, mirrored by ``audit.schemas``; replaces the SQL
producers that logged an empty context with ones that say what failed; adds
the ``incident_recovered`` event and the trigger that writes it when an
operational incident resolves; and ends with a DO block that refuses to commit
unless all of it is installed. No table or column changes, so there is no
model state to record. Reversing needs its own forward migration restoring the
old bodies, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0008_log_detail.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # #622's migration, which installs frozen file 0007, so the files
        # apply in prefix order.
        ("stewardship_jobs", "0007_system_health_records"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
