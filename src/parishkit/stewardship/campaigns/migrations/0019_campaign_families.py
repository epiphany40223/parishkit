"""Forward migration: a campaign's Families rule is fixed at creation (#1393).

Its SQL is the frozen file ``schema/migrations/0175_campaign_families.sql``
(prefix 0175, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces ``stewardship_campaign_pointer_v1`` with 0166's
body plus two checks: a configuration of an existing campaign must carry
exactly the ``campaign_families`` value its current configuration carries
(absent, the legacy rule, staying absent), and a configuration that creates
a campaign may carry one only in a canonical shape. It also replaces, in
place, the source window function (so a campaign with a rule can refresh)
and the Financial and Additional information report selections (so the
"No longer in this campaign" badge follows the rule). It ends with a DO
block that refuses to commit unless the functions are installed as declared
with their grants unchanged. No row changes and no model changes, so there
is no state operation. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0175_campaign_families.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The campaigns app's previous migration (0172, #1932).
        ("stewardship_campaigns", "0018_family_statistics"),
        # The migration that installs the previous frozen file (0174, the
        # ParishSoft organization choice, #1554), so the files apply in
        # prefix order.
        ("stewardship_accounts", "0035_parishsoft_organization_choice"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
