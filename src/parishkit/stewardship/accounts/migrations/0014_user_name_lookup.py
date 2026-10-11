"""Forward migration: the Users page's Name lookup (#952, slice 5).

Its SQL is the frozen file ``schema/migrations/0053_user_name_lookup.sql``,
read whole and never parsed. It adds one SECURITY DEFINER function,
``stewardship_member_names_for_v1(text, text, uuid)``: the ordered, distinct
names of the current ParishSoft Members whose contact record lists one
address, which the New user page offers as the Name's default. It answers
only a live Administrator session, at most 60 times an hour for each
Administrator, and records an audit event for each lookup. It ends with a DO block that
refuses to commit unless the function is installed as declared. No model
changes, so there is no state operation. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0053_user_name_lookup.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # Its own app's latest migration, which also installed the previous
        # frozen file (0052, #952 slice 4), so the files apply in prefix order.
        ("stewardship_accounts", "0013_users_swap"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
