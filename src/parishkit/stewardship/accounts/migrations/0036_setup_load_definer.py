"""Forward migration: the setup load's organization check as SECURITY DEFINER.

Its SQL is the frozen file ``schema/migrations/0177_setup_load_definer.sql``
(prefix 0177, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces 0174's ``stewardship_setup_load_organization_v1``
trigger function with the same checks, run with its owner's rights, so a
scheduler or worker update of a setup attempt no longer needs SELECT on the
ParishSoft organization lookup table once PostgreSQL uses a generic plan
(#2163). It ends with a DO block that refuses to commit unless the function
is installed as declared. No row changes and no model changes, so there is
no state operation. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0177_setup_load_definer.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration (0174, the ParishSoft
        # organization choice, #1554), which created the function.
        ("stewardship_accounts", "0035_parishsoft_organization_choice"),
        # The migration that installs the previous frozen file (0176, the
        # backup Test access re-check, #269), so the files apply in prefix
        # order.
        ("stewardship_jobs", "0038_probe_settings_changed"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
