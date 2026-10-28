"""Forward migration: clearer Family names for mixed-surname heads (#2320).

Its SQL is the frozen file ``schema/migrations/0194_mixed_surname_names.sql``
(prefix 0194, assigned in the repository-wide file sequence), read whole and
never parsed. It creates ``stewardship_name_series_v1`` and replaces the
shared Family name functions (``stewardship_family_head_part_v1`` and
``stewardship_family_heads_name_v1``) and both directory selections, so a
Family whose heads have different surnames reads "Kelly / Thieneman,
Maureen / Linda" in SQL search and sort exactly as
``family_names.family_heads_name`` shows it. Signatures, attributes and
grants are unchanged, and the file ends with a DO block that refuses to
commit unless the new functions and bodies are installed as declared. No
model changes, so there is no state operation. Reversing needs its own
forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0194_mixed_surname_names.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, so the app keeps one leaf.
        ("stewardship_reports", "0052_series_in_force"),
        # The migration that installs the previous frozen file on main (0191,
        # #2300), so the files apply in prefix order.
        ("stewardship_jobs", "0040_settle_leader_emails"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
