"""Forward migration: prune long-finished runs' liveness events (#386, L2).

Its SQL is the frozen file ``schema/migrations/0024_task_event_retention.sql``,
read whole and never parsed. It replaces the task event append-only guard so
that only ``stewardship_task_event_prune_v1`` (a new SECURITY DEFINER
function, for the general worker's hourly maintenance) may delete, and only
the ``heartbeat`` and ``progress`` events of runs that finished more than its
``retain_days`` ago, and adds the partial index ``task_terminal_updated``
that keeps its batches cheap (recorded in the model state too). It ends with
a DO block that refuses to commit unless both functions and the index are
installed as declared. Reversing needs its own forward migration, so, like
the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0024_task_event_retention.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # Its app's latest migration, which also installed the previous
        # frozen file (0023).
        ("stewardship_jobs", "0013_read_guard_kills"),
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
                        fields=["updated_at"],
                        name="task_terminal_updated",
                        condition=models.Q(
                            state__in=["succeeded", "failed", "cancelled"]
                        ),
                    ),
                ),
            ],
        ),
    ]
