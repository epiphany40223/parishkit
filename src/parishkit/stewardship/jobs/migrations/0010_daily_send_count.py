"""Forward migration: the 24-hour Family mail count for System health (#530).

Its SQL is the frozen file ``schema/migrations/0012_daily_send_count.sql``
(the next prefix in the repository-wide file sequence after #632's
``0011_refresh_schedule.sql``, which ``stewardship_source.0005_refresh_schedule``
installs, hence that dependency), read whole and never parsed. It creates the
``outbox_event_daily`` index on ``(previous_state, created_at)`` (#382 L9) and
``stewardship_family_daily_sends_v1()``, a SECURITY DEFINER count that lets
the web login show the mail consumers' 24-hour count without reading any
recipient address, and ends with a DO block that refuses to commit unless
both are installed as declared. The state operation records the index in
Django's model state, so ``makemigrations --check`` stays clean. Reversing
needs its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0012_daily_send_count.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0009_task_type_index"),
        # The migration that installed the previous frozen file (0011), so
        # the files apply in prefix order.
        ("stewardship_source", "0005_refresh_schedule"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="outboxevent",
                    index=models.Index(
                        fields=["previous_state", "created_at"],
                        name="outbox_event_daily",
                    ),
                ),
            ],
        ),
    ]
