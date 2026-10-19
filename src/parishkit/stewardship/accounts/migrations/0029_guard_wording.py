"""Forward migration: the guards stop asking for fresh authentication (#1762).

Its SQL is the frozen file ``schema/migrations/0124_guard_wording.sql``
(prefix 0124 in the repository-wide file sequence), read whole and never
parsed. Since #1547 no guard checks a sign-in's
age, so it rewords ten guard refusals that still said a write "requires
fresh ... authentication" to name the live session they check, and four
comments that still described a sign-in age or a step-up sign-in. Each
body is otherwise its latest earlier copy: no rule changes and no row
changes, so no Admin session or Family credential ends, and it can run
again (the users swap's test reinstalls some bodies). It ends with a DO
block that refuses to commit unless every body is installed as declared.
No model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0124_guard_wording.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installs the previous frozen file (0123, #1550),
        # so the files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file.
        ("stewardship_accounts", "0028_sign_in_leftovers"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
