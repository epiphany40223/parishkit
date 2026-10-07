"""Forward migration: four reviewed operational events (#541, #546, #584).

Its SQL is the frozen file ``schema/migrations/0018_log_events.sql``, read
whole and never parsed. It widens the operational log's closed event list
(``operational_event_safe``), which mirrors ``observability.Event``, with
``startup_waiting``, ``startup_wait_ended``, ``debug_logging_enabled`` and
``refresh_lead_window_conflict``, and ends with a DO block that refuses to
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
    / "0018_log_events.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration (#706, frozen file 0016).
        ("stewardship_jobs", "0011_backup_request"),
        # The migration that installed the previous frozen file (0017,
        # #711), so the files apply in prefix order.
        ("stewardship_reports", "0003_report_local_days"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
