"""Forward migration: the Ministry requests page's Leaders column (#1594).

Its SQL is the frozen file
``schema/migrations/0168_ministry_leader_coverage.sql``, read whole and never
parsed. It adds one SECURITY DEFINER function,
``stewardship_ministry_leader_coverage_v1(text, uuid)``: for each Ministry
with a ParishSoft leader, how many leader addresses it has, how many of them
are users and how many of those users have signed in. It answers only a live
Staff or Administrator Admin session, takes no address or Ministry, and
records an audit event for each call. It ends with a DO block that refuses
to commit unless the function is installed as declared. No model changes,
so there is no state operation. Reversing needs its own forward migration,
so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0168_ministry_leader_coverage.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0032_pledge_review"),
        # The migration that installs the previous frozen file on main (0132,
        # the refresh checks), so the files apply in prefix order. 0133-0166
        # are assigned to other open changes; whichever merges last
        # re-points this. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file and its
        # granted function is registered in runtime_grants.CLOSURE_FUNCTIONS.
        ("stewardship_workflows", "0004_refresh_checks"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
