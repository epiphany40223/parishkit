"""Forward migration: a menu logo may be a wide wordmark (#1062, slice 2).

Its SQL is the frozen file ``schema/migrations/0074_wide_menu_logo.sql``,
read whole and never parsed. It replaces the branding asset insert guard,
``stewardship_branding_asset_insert_v1``, so a ``menu`` file may be up to
512 pixels wide (its height stays at most 128), and pins the guard's
``search_path``. It ends with a DO block that refuses to commit unless the
guard is installed as declared, with its owner, grants and trigger kept. No
model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0074_wide_menu_logo.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration (0070, #1034).
        ("stewardship_accounts", "0018_leader_changes"),
        # The migration that installs the previous frozen file (0073, #389
        # L1), so the files apply in prefix order. It follows the users swap,
        # so `migrate --users-upgrade-base` never applies this file; it
        # replaces one ungranted trigger function in place, so nothing is
        # registered in runtime_grants' closure lists.
        ("stewardship_jobs", "0025_dispatch_token_access"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
