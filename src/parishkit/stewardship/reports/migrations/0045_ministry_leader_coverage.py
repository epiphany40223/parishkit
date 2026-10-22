"""Forward migration: the Ministry requests page's Leaders column (#1594).

Its SQL is the frozen file
``schema/migrations/0169_ministry_leader_coverage.sql``, read whole and never
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
    / "0169_ministry_leader_coverage.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration (the report waiting read).
        ("stewardship_reports", "0044_report_waiting_read"),
        # The migration that installs the previous frozen file (0168, the
        # setup test binding), so the files apply in prefix order. It
        # follows the users swap, so `migrate --users-upgrade-base` never
        # applies this file and its granted function is registered in
        # runtime_grants.CLOSURE_FUNCTIONS.
        ("stewardship_accounts", "0034_setup_test_binding"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
