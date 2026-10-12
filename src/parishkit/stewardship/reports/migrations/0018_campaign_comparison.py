"""Forward migration: the campaign comparison report's SQL figures (#1018).

Its SQL is the frozen file ``schema/migrations/0067_campaign_comparison.sql``
(prefix 0067, assigned in the repository-wide file sequence), read whole and
never parsed. It adds two definer functions that return aggregates only:
``stewardship_campaign_comparison_v1`` (the current campaign's pledging
Families, quartiles, pledge bands and renewal at the end of one day) and
``stewardship_campaign_archive_renewal_v1`` (one imported year's renewal of
another's Families). No table or model changes, so there are no state
operations. Reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.

It depends on the reports app's migration that installs the previous frozen
file (0066), so the files apply in prefix order and Django keeps a single
leaf. That puts it in the users swap's closure (#952): `migrate
--users-upgrade-base` never applies it, so both functions are registered in
``runtime_grants.CLOSURE_FUNCTIONS`` and granted only once it is applied. It
alters no table.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0067_campaign_comparison.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs the previous frozen file (0066, #794).
        # The archive tables these functions read (0059, #1048) come before
        # it. It follows the users swap, so `migrate --users-upgrade-base`
        # never applies this file.
        ("stewardship_reports", "0017_financial_staff_entered"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
