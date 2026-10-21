"""Forward migration: the Daily and Weekly digest pipelines retire (#1834).

Its SQL is the frozen file ``schema/migrations/0162_retire_digests.sql``,
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
    / "0162_retire_digests.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which installs the report
        # email conversion (#1786) this file follows; the frozen files apply
        # in that order whatever their prefixes until they are renumbered.
        ("stewardship_reports", "0034_report_conversion"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
