"""Forward migration: an edited Ministry request belongs to one Ministry (#2331).

Its SQL is the frozen file ``schema/migrations/0198_ministry_edit_once.sql``
(prefix 0198, assigned in the repository-wide file sequence), read whole
and never parsed. It creates nothing and changes no table: it replaces the
follow-up, Ministry report and packet selections in place, each copied
whole from its newest definition, so a request review's Edit carried into
another Ministry is counted, listed and scoped under that Ministry, its
Ministry in force, alone (Administrator decision, 2026-11-01). It ends with
a DO block that refuses to commit unless all of it is installed as declared.
Reversing needs its own forward migration.

Numbering: frozen 0198 follows 0197 (financial eligibility, #2335,
reports 0056), the previous file in the repository-wide sequence, so the
files apply in prefix order.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0198_ministry_edit_once.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration (frozen 0197, #2335), so the
        # app keeps one leaf; it also installs the previous frozen file, so
        # the files apply in prefix order.
        ("stewardship_reports", "0056_financial_eligibility"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
