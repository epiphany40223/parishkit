"""Forward migration: a new campaign draft may start in any time zone (#1330).

Its SQL is the frozen file ``schema/migrations/0103_any_creation_timezone.sql``
(prefix 0103, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces ``stewardship_campaign_pointer_v1`` with 0091's
body less the check that a new draft's time zone is the parish zone: Create
the campaign defaults to the browser's zone and accepts any catalog zone
(Administrator, 2026-10-15). It ends with a DO block that refuses to commit
unless the function is installed as declared with its grants unchanged. No
model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0103_any_creation_timezone.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0012_workgroup_rename_keep"),
        # The migration that installs the previous frozen file (0102, #1353),
        # so the files apply in prefix order.
        ("stewardship_accounts", "0023_setup_load_void"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
