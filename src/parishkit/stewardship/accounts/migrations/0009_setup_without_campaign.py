"""Forward migration: let system setup finish without a campaign (#142).

Its SQL is the frozen file ``schema/migrations/0034_setup_without_campaign.sql``,
read whole and never parsed. It re-creates the setup completion guard so a
completion with no campaign, no Families and the empty giving window is
accepted, while a completion that created the first campaign (an attempt
confirmed before this release) keeps every existing check. It ends with a DO
block that refuses to commit unless the new guard is installed. Reversing
needs its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0034_setup_without_campaign.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0008_portal_user_insert_guard"),
        # The migration that installed the previous frozen file (0033), so
        # the files apply in prefix order.
        ("stewardship_jobs", "0017_retention_stall_wording"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
