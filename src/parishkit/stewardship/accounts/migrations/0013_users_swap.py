"""Forward migration: sign-in reads only users (#952, slice 4).

Its SQL is the frozen file ``schema/migrations/0052_users_swap.sql``, read
whole and never parsed. It replaces the Admin session admission, export
authorization, Ministry scope, role-derived leader scope and policy
activation functions so that none reads hosted-domain rules. It ends with a
DO block that refuses to commit unless all of them are installed as declared
and the active configuration is already in the users form, so it runs only
after the offline ``upgrade-users`` conversion. ``migrate
--users-upgrade-base`` stops just before this migration (``USERS_SWAP`` in
``runtime_database``). No model changes, so there is no state operation.
Reversing needs its own forward migration, so, like the baseline, it has no
reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0052_users_swap.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # Its own app's latest migration, which also installed the previous
        # frozen file (0051, #952 slice 3), so the files apply in prefix order.
        ("stewardship_accounts", "0012_users_requests"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
