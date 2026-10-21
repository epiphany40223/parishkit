"""Forward migration: the Ministries a user's address leads (#1925).

Its SQL is the frozen file ``schema/migrations/0167_address_leader_scope.sql``
(prefix 0167, a number assigned centrally), read whole and never parsed. It
adds ``stewardship_address_leader_scope_v1``, a read-only,
Administrator-only definer the web login calls for a user's Timeline: the
Ministries the shared leader scope would give a Google account with that
address, so a Ministry leader who never signed in still shows them. It ends
with a DO block that refuses to commit unless the function is installed as
declared. It changes no table, so there is no state operation. Reversing
needs its own forward migration, so it has no reverse operation.

It follows the users swap (#952), so its definer is granted only once it is
applied (runtime_grants.CLOSURE_FUNCTIONS).
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0167_address_leader_scope.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration (0125, #1763).
        ("stewardship_accounts", "0030_secret_request_session"),
        # The migration that installs the previous frozen file (0152,
        # #1866), so the files apply in prefix order. Re-point this when
        # the frozen files numbered between them land.
        ("stewardship_audit", "0007_audit_actor_time"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
