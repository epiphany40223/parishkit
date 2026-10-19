"""Forward migration: an edited census address meets the Family's rules (#1719).

Its SQL is the frozen file ``schema/migrations/0138_census_address_guard.sql``
(number 0138, provisional until it is assigned in merge order), read whole and
never parsed. It replaces the census review guard so a reviewer's edited
Family address is also held to the address rules the Family's own answer
meets (``stewardship_response_address_guard_v1``). It ends with a DO block
that refuses to commit unless the new body is installed. No model changes,
so there is no state operation. Reversing needs its own forward migration,
so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0138_census_address_guard.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, which installs the previous frozen
        # file that replaces this guard (0131), so the files apply in order.
        ("stewardship_responses", "0013_census_pipeline_review"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
