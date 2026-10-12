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

It depends on the responses app's migration that installs the previous
frozen file (0065), so the files apply in prefix order and Django keeps a
single leaf. That puts it in the users swap's closure (#952):
`migrate --users-upgrade-base` never applies it. It creates no table or
routine and alters no table, so nothing is registered in ``runtime_grants``
and the swap tests need no entry: the full migration replaces the function
again in place.
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
        # The reports app's previous migration (0064, #558).
        ("stewardship_reports", "0016_report_local_days"),
        # The migration that installs the previous frozen file (0065, #529),
        # which adds the entered_by_id column this projection reads, so the
        # files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file.
        ("stewardship_responses", "0003_staff_entered"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
