"""Forward migration: the publication check marks Already in ParishSoft (#528).

Its SQL is the frozen file ``schema/migrations/0112_upstream_at_check.sql``
(number 0112, assigned centrally), read whole and never parsed. It replaces
the derived guard so the worker may move an approved Automatic Member change
whose value a Production-mode plan, recorded in the same transaction, found
already in ParishSoft from pending or failed to ``resolved_upstream``, and
the confirmation guard so such a plan can still be confirmed (ADM-09, the
data spec's preflight step 4). It ends with a DO block that refuses to
commit unless both are installed. No model state changes. Reversing needs
its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0112_upstream_at_check.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration, so the app keeps one leaf.
        ("stewardship_responses", "0009_conflict_resolution"),
        # The migration that installs the newest frozen file on main (0110),
        # so the files apply in prefix order. 0111 (#1502) is not merged
        # yet; once it is, depend on stewardship_accounts.0026_setup_slack_off.
        ("stewardship_reports", "0030_financial_local_days"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
