"""Forward migration: the Daily and Weekly digest pipelines retire (#1834).

Its SQL is the frozen file ``schema/migrations/0171_retire_digests.sql``,
read whole and never parsed. It refuses to run while any schedule
definition is still a digest (the report email conversion has not run),
drops the digest kinds from the definition kind check and drops the
digest pipelines' write functions and triggers, keeping the digest tables,
their guards and the read functions as history. It ends with a DO block
that refuses to commit unless all of it is installed as declared.

The definition check's model state is recorded by the state-only
``retire_digests`` migration of the campaigns app, which depends on this
one. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0171_retire_digests.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration (0169, the Ministry leader
        # coverage). The report email conversion (#1786, 0041) it needs
        # comes before it.
        ("stewardship_reports", "0045_ministry_leader_coverage"),
        # The migration that installs the previous frozen file (0170, the
        # parish-added census value, #2008), so the files apply in prefix
        # order.
        ("stewardship_responses", "0032_parish_added_shown"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
