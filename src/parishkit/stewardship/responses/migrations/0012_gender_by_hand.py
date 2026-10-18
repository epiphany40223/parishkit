"""Forward migration: census gender is worked by hand (#1603).

Its SQL is the frozen file ``schema/migrations/0118_gender_by_hand.sql``
(number 0118, assigned in merge order), read whole and never parsed. The live
ParishSoft v2 gender test of 2026-10-17 showed that a gender PUT is accepted
but never changes the Member, so the file adds gender to the derived guard's
manual Member fields, re-marks the still-open gender changes recorded before
it as manual with one count-only audit event, and ends with a DO block that
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
    / "0118_gender_by_hand.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, which installs the previous derived
        # guard (0113, #1596), so the app keeps one leaf and the files apply
        # in prefix order.
        ("stewardship_responses", "0011_census_handling_registry"),
        # The migration that installs the previous frozen file (0117,
        # #765), so the files apply in prefix order.
        ("stewardship_audit", "0005_log_search_indexes"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
