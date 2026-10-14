"""Forward migration: a durable entry for an unrecorded retry refusal (#1201).

Its SQL is the frozen file ``schema/migrations/0089_retry_refusal_log.sql``,
read whole and never parsed. It widens the operational log's closed event
list (``operational_event_safe``) with ``retry_refusal_unrecorded``, lets the
``failure`` context carry the ``retry_refusal`` failure word
(``stewardship_safe_context_v1``), and lets the mail dispatch login, which
settles address follow-ups, write that one CRITICAL entry
(``stewardship_operational_log_writer_v1``). It ends with a DO block that
refuses to commit unless all three are installed. No model changes, so there
is no state operation. Reversing needs its own forward migration, so, like
the baseline, it has no reverse operation.

It lives in the audit app, which owns the operational log, rather than the
jobs app, so it adds no second leaf beside another open change's jobs
migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0089_retry_refusal_log.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The audit app's previous migration.
        ("stewardship_audit", "0002_critical_ack_guard"),
        # The migration that installs the previous frozen file (0088, Allow
        # debug logging again), so the files apply in prefix order.
        ("stewardship_jobs", "0031_debug_allow"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
