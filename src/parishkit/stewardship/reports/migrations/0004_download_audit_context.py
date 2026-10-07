"""Forward migration: on-request report audits record their choices (#556).

Its SQL is the frozen file ``schema/migrations/0020_download_audit_context.sql``,
read whole and never parsed. It replaces ``stewardship_safe_context_v1`` so the
``action`` context also admits ``report_mode``, ``report_filter``,
``talent_option_id`` and ``snapshot_id``, mirroring ``audit.schemas``, and
ends with a DO block that refuses to commit unless the new allowlist is
installed with its attributes. No model changes, so there is no state
operation. Reversing needs its own forward migration, so, like the baseline,
it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0020_download_audit_context.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0003_report_local_days"),
        # The migration that installed the previous frozen file (0019,
        # #537), so the files apply in prefix order.
        ("stewardship_campaigns", "0004_restore_review"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
