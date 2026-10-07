"""Forward migration: start, settle and release a restore review (#537).

Its SQL is the frozen file ``schema/migrations/0019_restore_review.sql``, read
whole and never parsed. It admits two runtime transitions, ``restore_begin``
(the operator's restore command) and ``restore_release`` (a freshly signed-in
Administrator, after the held-email list is brought up to date), adds the
held-email inventory and the fresh-Administrator check, lets a "send again"
decision wait for the ordinary planner, and adds the sign-in columns
(``session_id``, ``authenticated_at``) to ``RuntimeTransition`` and
``RestoreHoldResolution``. It ends with a DO block that refuses to commit
unless all of it is installed. The state operations record the new columns
in Django's model state, so ``makemigrations --check`` stays clean.
Reversing needs its own forward migration, so, like the baseline, it has no
reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

import parishkit.stewardship.storage

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0019_restore_review.sql"
)


def _sign_in_fields(model_name):
    """The two nullable sign-in columns the frozen file adds to ``model_name``."""
    return [
        migrations.AddField(
            model_name=model_name,
            name="session_id",
            field=models.UUIDField(null=True),
        ),
        migrations.AddField(
            model_name=model_name,
            name="authenticated_at",
            field=parishkit.stewardship.storage.UTCDateTimeField(null=True),
        ),
    ]


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0003_occurrence_prepare_ahead"),
        # The migration that installed the previous frozen file (0018, #733),
        # so the files apply in prefix order.
        ("stewardship_jobs", "0012_log_events"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=_sign_in_fields("runtimetransition")
            + _sign_in_fields("restoreholdresolution"),
        )
    ]
