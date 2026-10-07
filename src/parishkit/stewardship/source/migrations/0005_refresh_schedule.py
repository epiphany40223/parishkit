"""Forward migration: the integrated refresh schedule's tick guard (#632).

Its SQL is the frozen file ``schema/migrations/0011_refresh_schedule.sql``
(the next prefix in the repository-wide file sequence after #641's
``0010_task_type_index.sql``, which ``stewardship_jobs.0009_task_type_index``
installs, hence the dependency), read
whole and never parsed. It creates ``stewardship_refresh_schedule_v1``, which
normalizes stored schedule settings as ``cadence.refresh_settings`` does;
widens the ``source_refresh_command_cause`` check with ``catch_up``, the
schedule-change catch-up; and replaces the refresh-tick guard so that it
admits a quick tick only at a listed local quick time when the schedule
lists them, and a catch-up tick only at the instant the current schedule
took effect. It ends with a DO block that refuses to commit unless all of it
is installed. The state operations record the widened check in Django's
model state, so ``makemigrations --check`` stays clean. Reversing needs its
own forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0011_refresh_schedule.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_source", "0004_unchanged_snapshots"),
        # The migration that installed the previous frozen file (0010), so
        # the files apply in prefix order.
        ("stewardship_jobs", "0009_task_type_index"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="sourcerefreshcommand",
                    name="source_refresh_command_cause",
                ),
                migrations.AddConstraint(
                    model_name="sourcerefreshcommand",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            cause__in=(
                                "manual",
                                "nightly",
                                "initial",
                                "delta",
                                "fallback",
                                "catch_up",
                            )
                        ),
                        name="source_refresh_command_cause",
                    ),
                ),
            ],
        ),
    ]
