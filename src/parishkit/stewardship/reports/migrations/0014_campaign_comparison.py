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
        # The migration that installs 0059, the archive tables these functions
        # read (#1048, slice 2). Files 0060 to 0066 are assigned to other open
        # pull requests; when any of them lands first, this moves to the
        # migration that installs the highest of them, so the frozen files
        # apply in prefix order.
        ("stewardship_reports", "0013_campaign_archives"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
