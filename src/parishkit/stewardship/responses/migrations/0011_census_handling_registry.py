"""Forward migration: the census handling registry from the live API test.

Its SQL is the frozen file ``schema/migrations/0113_census_handling_registry.sql``
(number 0113, assigned in merge order), read whole and never parsed. The live
ParishSoft v2 write test of 2026-10-17 (#528, #1596) showed that a death date
leaves the Member Active, that no value can be cleared and that Family
addresses cannot be read back, so the file replaces the derived guard's
expected handling (a death date, an answer that clears a value and every
Family field are manual), re-marks the still-open changes recorded before it
as manual with one count-only audit event, and ends with a DO block that
refuses to commit unless all of it is installed. No model changes, so there
is no state operation. Reversing needs its own forward migration, so, like
the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0113_census_handling_registry.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, which also installs the previous
        # frozen file (0112, #1524), so the app keeps one leaf and the files
        # apply in prefix order.
        ("stewardship_responses", "0010_upstream_at_check"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
