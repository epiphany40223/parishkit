"""Forward migration: index Task runs by type and domain request (#641).

Its SQL is the frozen file ``schema/migrations/0010_task_type_index.sql`` (the
next prefix in the repository-wide file sequence after #630's
``0009_unchanged_snapshots.sql``, which
``stewardship_source.0004_unchanged_snapshots`` installs, hence that
dependency), read whole and never parsed. It creates ``task_type_request`` on
``(task_type, domain_request_id)``, so the operational producers' "notices no
Task owns yet" lookup reads the index instead of the whole table, and ends with
a DO block that refuses to commit unless the index is installed as declared.
The state operation records the same index in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0010_task_type_index.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0008_log_detail"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order.
        ("stewardship_source", "0004_unchanged_snapshots"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="taskrun",
                    index=models.Index(
                        fields=["task_type", "domain_request_id"],
                        name="task_type_request",
                    ),
                ),
            ],
        ),
    ]
