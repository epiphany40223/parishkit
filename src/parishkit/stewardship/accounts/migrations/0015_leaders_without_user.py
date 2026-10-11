"""Forward migration: the Users page's ParishSoft leaders table (#952, slice 6).

Its SQL is the frozen file ``schema/migrations/0054_leaders_without_user.sql``,
read whole and never parsed. It adds one SECURITY DEFINER function,
``stewardship_leaders_without_user_v1(text, uuid)``: the ParishSoft Ministry
leaders whose valid addresses are not users yet, each with the Name, Member
DUID and Ministries the Users page shows, and the count of leaders with no
usable address. It answers only a live Administrator session, takes no
address, and records an audit event for each call. It ends with a DO block
that refuses to commit unless the function is installed as declared. No
model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0054_leaders_without_user.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # Its own app's latest migration, which also installed the previous
        # frozen file of this stack (0053, #952 slice 5), so the files apply
        # in prefix order.
        ("stewardship_accounts", "0014_user_name_lookup"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
