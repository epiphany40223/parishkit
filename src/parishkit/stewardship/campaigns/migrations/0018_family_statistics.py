"""Forward migration: refresh the Family campaign tables' statistics after a
new campaign's first load (#1932).

Its SQL is the frozen file
``schema/migrations/0172_family_campaign_statistics.sql`` (prefix 0172 in
the repository-wide file sequence), read whole and never parsed. It creates
the worker-only definer ``stewardship_family_campaign_analyze_v1``, which
analyzes ``stewardship_family_campaign`` and
``stewardship_family_eligibility`` for a campaign that can still receive
Family effects, so the scheduler's first reads after a first load plan with
statistics that include the new campaign. It ends with a DO block that
refuses to commit unless the definer is installed as declared. Nothing
changes in Django's model state. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0172_family_campaign_statistics.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The campaigns app's previous migration, the digest retirement's
        # model state (#1834).
        ("stewardship_campaigns", "0017_retire_digests"),
        # The migration that installs the previous frozen file (0171, the
        # digest retirement, #1834), so the files apply in prefix order. It
        # follows the users swap, so `migrate --users-upgrade-base` never
        # applies this file and its granted function is registered in
        # runtime_grants.CLOSURE_FUNCTIONS.
        ("stewardship_reports", "0046_retire_digests"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
