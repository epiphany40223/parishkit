"""Forward migration: the timeout log admits ``source_load_budget`` (#834).

Its SQL is the frozen file ``schema/migrations/0032_load_budget_timeout.sql``,
read whole and never parsed. It replaces ``stewardship_safe_context_v1`` with
one more timeout word, for a full ParishSoft load stopped at its own time
bound, and ends with a DO block that refuses to commit unless the function
admits it and keeps its attributes. No model state changes. Reversing needs
its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0032_load_budget_timeout.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0015_log_writer_allowlist"),
        # The migration that installed the previous frozen file (0031), so
        # the files apply in prefix order.
        ("stewardship_audit", "0002_critical_ack_guard"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
