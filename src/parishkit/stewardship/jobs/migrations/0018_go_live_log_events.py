"""Forward migration: three reviewed go-live sequencing events (#462).

Its SQL is the frozen file ``schema/migrations/0036_go_live_log_events.sql``,
read whole and never parsed. It widens the operational log's closed event list
(``operational_event_safe``), which mirrors ``observability.Event``, with
``go_live_refresh_held``, ``go_live_step_refused`` and
``go_live_sequencing_stopped``, and ends with a DO block that refuses to
commit unless the constraint lists every name. No model changes, so there is
no state operation. Reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0036_go_live_log_events.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration.
        ("stewardship_jobs", "0017_retention_stall_wording"),
        # The migration that installed the previous frozen file (0035,
        # #866), so the files apply in prefix order.
        ("stewardship_campaigns", "0008_workgroup_recovery_skip"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
