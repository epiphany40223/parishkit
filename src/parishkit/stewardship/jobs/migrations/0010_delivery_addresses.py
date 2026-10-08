"""Forward migration: count the addresses each accepted attempt reached (#806).

Its SQL is the frozen file ``schema/migrations/0048_delivery_addresses.sql``,
read whole and never parsed. It creates the definer function
``stewardship_delivery_addresses_v1``, which returns, for one message's
accepted attempts, only how many envelope positions were refused for now and
for good, so Outgoing mail's message page can say "Delivered to 1 of 2
addresses" without the web reading delivery evidence. It ends with a DO block
that refuses to commit unless the function is installed as declared. No model
changes. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0048_delivery_addresses.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The previous frozen file on this branch (0010). Restacked onto the
        # migration that installs 0047 at merge time (see the pull request).
        ("stewardship_jobs", "0009_task_type_index"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
