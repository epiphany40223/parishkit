"""Forward migration: Entered incorrectly's two fixes (#1737).

Its SQL is the frozen file ``schema/migrations/0140_entered_incorrectly_fixes.sql``
(number 0140, provisional until it is assigned in merge order), read whole
and never parsed. It replaces the census resolution guard, the census
derived guard and the refresh check guard so Staff and Administrators can
mark a change Entered incorrectly Entered by hand again, or send it Back to
review, each with its paired resolution row, and so a census refresh check
agrees with the value recorded at the change's mark. It ends with a DO block
that refuses to commit unless the new bodies are installed. No model
changes, so there is no state operation. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0140_entered_incorrectly_fixes.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, so the app keeps one leaf, and the
        # migration that installs the previous frozen file (0136), whose
        # refresh check guard this file replaces.
        ("stewardship_responses", "0013_census_pipeline_review"),
        ("stewardship_workflows", "0004_refresh_checks"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
