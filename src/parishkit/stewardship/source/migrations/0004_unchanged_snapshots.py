"""Forward migration: a quick update that changed nothing is recorded, not promoted.

Its SQL is the frozen file ``schema/migrations/0009_unchanged_snapshots.sql``
(the next prefix in the repository-wide file sequence after #633's
``0008_log_detail.sql``, which ``stewardship_jobs.0008_log_detail`` installs,
hence the dependency), read whole and never parsed. It widens the
``source_snapshot_state`` check with ``unchanged``, replaces the snapshot
guard and the refresh completion check so that a quick update whose corpus
equals the current one may end in that state (#630), and ends with a DO block
that refuses to commit unless all of it is installed. The state operations
record the same widened check in Django's model state, so
``makemigrations --check`` stays clean. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0009_unchanged_snapshots.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_source", "0003_source_drop_counts"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order.
        ("stewardship_jobs", "0008_log_detail"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="sourcesnapshot", name="source_snapshot_state"
                ),
                migrations.AddConstraint(
                    model_name="sourcesnapshot",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            state__in=(
                                "staging",
                                "ready",
                                "rejected",
                                "promoted",
                                "unchanged",
                            )
                        ),
                        name="source_snapshot_state",
                    ),
                ),
            ],
        ),
    ]
