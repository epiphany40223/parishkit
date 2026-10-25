"""Forward migration: definer functions write their jsonb operators qualified.

Its SQL is the frozen file ``schema/migrations/0179_definer_operators.sql``
(prefix 0179, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces each of the fourteen SECURITY DEFINER functions
that used ``?``, ``?|``, ``?&``, ``@>`` or ``<@`` unqualified with its latest
body, unchanged except that each such operator is written
``OPERATOR(pg_catalog.x)``, so no look-alike operator on the search path can
be chosen in a function that runs with its owner's rights (#2166). It ends
with a DO block that refuses to commit unless each is installed as declared.
No row changes and no model changes, so there is no state operation.
Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0179_definer_operators.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration (0177, the setup load
        # definer, #2163), so the app keeps one leaf.
        ("stewardship_accounts", "0036_setup_load_definer"),
        # The migration that installs the previous frozen file (0178, the
        # review run, #2154), so the files apply in prefix order.
        ("stewardship_reports", "0048_review_run"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
