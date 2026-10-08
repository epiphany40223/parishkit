"""Forward migration: Financial stewardship detail marks Staff entry (#794).

Its SQL is the frozen file ``schema/migrations/0066_financial_staff_entered.sql``
(prefix 0066, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces ``stewardship_financial_report_v1`` so each row of
the page's read and of every new export capture carries a boolean
``staff_entered`` for the effective response (#529's
``stewardship_submission.entered_by_id``), and ends with a DO block that
refuses to commit unless the body is installed with its attributes and
grants. No model changes, so there is no state operation. Reversing needs its
own forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0066_financial_staff_entered.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration on this branch. This file is
        # 0014 because #1048 reserves reports 0013 (campaign archives); when
        # that lands first, this depends on it instead.
        ("stewardship_reports", "0012_directory_response_filters"),
        # The migration that installed the previous frozen file (0065, #529),
        # which adds the entered_by_id column this projection reads, so the
        # files apply in prefix order.
        ("stewardship_responses", "0002_staff_entered"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
