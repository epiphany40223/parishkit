"""Forward migration: count-only Ministry leader changes (#1034).

Its SQL is the frozen file ``schema/migrations/0070_ministry_leader_changes.sql``,
read whole and never parsed. It adds ``stewardship_ministry_leaders_at_v1``
(the leader definition evaluated at a given configuration, campaign and
snapshot; invoker, executable by no login), replaces
``stewardship_ministry_leaders_v1`` with a thin definer wrapper over it at the
current pointers (same rows and grants), and adds the SECURITY DEFINER
``stewardship_ministry_leader_changes_v1``, which writes one count-only
``ministry_leaders_changed`` audit event when the leader pairs changed since
the given earlier pointers and returns nothing. It ends with a DO block that
refuses to commit unless all three are installed as declared. No model
changes, so there is no state operation. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0070_ministry_leader_changes.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration (it installed 0057, #995).
        ("stewardship_accounts", "0017_admin_session_key_digests"),
        # The migration that installs the previous frozen file (0069, #932),
        # so the files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file and its
        # granted function is registered in runtime_grants.CLOSURE_FUNCTIONS.
        ("stewardship_reports", "0019_member_names_last_first"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
