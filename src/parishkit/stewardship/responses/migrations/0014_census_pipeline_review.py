"""Forward migration: census changes adopt the review pipeline (#1683).

Its SQL is the frozen file ``schema/migrations/0128_census_pipeline_review.sql``
(number 0128, provisional until it is assigned in merge order), read whole and
never parsed. It replaces the census review guard so Staff and Administrators
review every census change the worklist lists, By hand ones too, record Edit
as Accept with a value (also on a Conflict, with the accepted base), edit a
Family address, and say why when they send an ignored change back to review.
It ends with a DO block that refuses to commit unless the new body is
installed. No model changes, so there is no state operation. Reversing needs
its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0128_census_pipeline_review.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, so the app keeps one leaf, and the
        # migration that installs main's latest frozen file (0124), so the
        # files apply in prefix order.
        ("stewardship_responses", "0012_gender_by_hand"),
        ("stewardship_accounts", "0029_guard_wording"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
