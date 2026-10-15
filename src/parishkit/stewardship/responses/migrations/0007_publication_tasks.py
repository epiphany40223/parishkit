"""Forward migration: the census publication task types (#528, BG-09).

Its SQL is the frozen file ``schema/migrations/0101_publication_tasks.sql``
(prefix 0101, assigned in the repository-wide file sequence), read whole and
never parsed. It adds ``census_publication_preflight`` and
``census_publication`` to the task execution map (run by the worker) and the
creator map (created by web), and ends with a DO block that refuses to
commit unless both maps are installed as declared. It changes no table, so
there is no state operation. Reversing needs its own forward migration, so,
like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0101_publication_tasks.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installed the previous frozen file (0096), so
        # the files apply in prefix order. Files 0097-0100 are held by an
        # open stack (#1293 -> #1326); once it merges, this must depend on
        # the migration installing 0100 instead.
        ("stewardship_responses", "0006_proposal_review"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
