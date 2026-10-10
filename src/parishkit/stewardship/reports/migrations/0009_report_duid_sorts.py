"""Forward migration: the SQL-ordered report tables sort by their DUIDs (#960).

Its SQL is the frozen file ``schema/migrations/0045_report_duid_sorts.sql``
(prefix 0045, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces the financial, Additional information, talents and
Ministry report selections and the Ministry export capture, each with its
baseline body plus the change: ``duid``/``duid_desc`` sort tokens (Family,
Ministry or Member DUID), the Talents Member DUID, and a Family name search
and sort over the name the pages show. Signatures, attributes and grants are
unchanged, and the file ends with a DO block that refuses to commit unless
the new bodies are installed with them. No model changes, so there is no
state operation. Reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0045_report_duid_sorts.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration.
        ("stewardship_reports", "0008_ministry_report_actor_scope"),
        # The migration that installed the previous frozen file on main
        # (0041). Files 0042-0044 belong to open pull requests; on rebase this
        # names the migration that installs 0044 instead.
        ("stewardship_jobs", "0020_task_type_creators"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
