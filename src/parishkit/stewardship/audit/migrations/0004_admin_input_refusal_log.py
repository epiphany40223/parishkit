"""Forward migration: a durable entry for every refused Admin input (#1403).

Its SQL is the frozen file ``schema/migrations/0106_admin_input_refusal_log.sql``
(number 0106, assigned centrally), read whole and never parsed. It widens the
operational log's closed event list (``operational_event_safe``) with
``admin_input_refused``, adds the closed ``refusal`` context
(``stewardship_safe_context_v1``), lets the web login write that event at
INFO and WARNING, and only with the refused user as its actor
(``stewardship_operational_log_writer_v1``), and adds the partial index on
(``actor_id``, ``created_at``) that the writer's per-actor cap counts. It ends
with a DO block that refuses to commit unless all four are installed. The
state operation records the same index on the model, so ``makemigrations
--check`` stays clean. Reversing needs its own forward migration, so, like
the baseline, it has no reverse operation.

It lives in the audit app, which owns the operational log, and depends on
the migration that installs the previous frozen file, so the files apply in
prefix order. That keeps it in the users swap's closure, as 0089's is: the
swap test runs this file again after the files it reinstalls bring back an
older safe context (``CLOSURE_REPLACED``). Its safe context is 0104's body
plus the refusal schema, so it keeps 0102's and 0104's action keys.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0106_admin_input_refusal_log.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The audit app's previous migration (0089, #1201).
        ("stewardship_audit", "0003_retry_refusal_log"),
        # The migration that installs the previous frozen file (0105, the
        # campaign setup review), so the files apply in prefix order.
        ("stewardship_accounts", "0025_campaign_setup_review"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="operationallog",
                    index=models.Index(
                        condition=models.Q(event="admin_input_refused"),
                        fields=["actor_id", "created_at"],
                        name="operational_refusal_actor_time",
                    ),
                ),
            ],
        )
    ]
