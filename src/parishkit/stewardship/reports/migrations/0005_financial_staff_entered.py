"""Forward migration: Financial stewardship detail marks Staff entry (#794).

Its SQL is the frozen file ``schema/migrations/0041_financial_staff_entered.sql``,
read whole and never parsed. It replaces ``stewardship_financial_report_v1``
so each row of the page's read and of every new export capture carries a
boolean ``staff_entered`` for the effective response (#529's
``stewardship_submission.entered_by_id``), and ends with a DO block that
refuses to commit unless the body is installed with its attributes. No model
changes, so there is no state operation. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0041_financial_staff_entered.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0004_download_audit_context"),
        # The migration that installed the previous frozen file on this
        # branch (0021, #779), so the files apply in prefix order; restacked
        # onto 0040's at merge time (see the pull request).
        ("stewardship_responses", "0002_staff_entered"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
