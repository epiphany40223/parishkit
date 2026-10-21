"""Forward migration: report emails replace the digests (#1786, delivery item 4).

Its SQL is the frozen file ``schema/migrations/0161_report_conversion.sql``
(prefix 0161 in the repository-wide file sequence), read whole and never
parsed. It admits report schedules, the ``campaign-series-v12``
configuration schema, the conversion's request format and the report
outbox purposes, and replaces every shared function
and view that named a digest so report emails run end to end. It refuses
to run while a digest send is in progress. The rows are converted after it,
by the offline ``pk-stewardship upgrade-reports``, because the configuration
lives in YAML as well (the file's header gives the order). It ends with a
DO block that refuses to commit unless all of it is installed as declared.

The constraint changes' model state is recorded by the state-only
``report_conversion`` migrations of the accounts, campaigns and jobs apps,
which depend on this one. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0161_report_conversion.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which installs the previous
        # frozen file (0156, the report pipeline), so the files apply in
        # prefix order.
        ("stewardship_reports", "0040_report_pipeline"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
