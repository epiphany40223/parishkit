"""Forward migration: schema left unused by #1547's sign-in change goes (#1550).

Its SQL is the frozen file ``schema/migrations/0147_sign_in_leftovers.sql``
(prefix 0147, provisional until the repository-wide file sequence assigns
it), read whole and never parsed. It drops
``stewardship_automation_fresh_principal_v1``, which no guard calls since
0114, with its grants, and makes an Admin session's verified sign-in instant
immutable, now that a new sign-in always issues a new session. It changes no
row, so no Admin session or Family credential ends, and it can run again
(the users swap's test reruns it). It ends with a DO block that refuses to
commit unless both are installed as declared. No model changes, so there
is no state operation. Reversing needs its own forward migration, so, like
the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0147_sign_in_leftovers.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration (0114, #1547), which left
        # the dropped function unused.
        ("stewardship_accounts", "0027_no_sign_in_age"),
        # The migration that installs the previous frozen file (0122, the
        # automatic report views), so the files apply in prefix order. It
        # follows the users swap, so `migrate --users-upgrade-base` never
        # applies this file.
        ("stewardship_audit", "0006_automatic_report_views"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
