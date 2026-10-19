"""Forward migration: refresh checks of an entered move, death and opt-out (#1738).

Its SQL is the frozen file ``schema/migrations/0141_census_refresh_sources.sql``
(number 0141, provisional until it is assigned in merge order), read whole
and never parsed. It creates ``stewardship_refresh_census_source_v1`` (the
snapshot's value for a census change, now also for a Member's move and
death and the Family's email opt-out) and replaces 0136's refresh check
guard to require it. It ends with a DO block that refuses to commit unless
both bodies are installed. No model changes, so there is no state
operation. Reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0141_census_refresh_sources.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs 0136, whose refresh check guard this
        # file replaces, so the files apply in prefix order.
        ("stewardship_workflows", "0004_refresh_checks"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
