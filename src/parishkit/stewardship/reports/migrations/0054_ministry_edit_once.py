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

Numbering: frozen 0198 follows the previous file in the repository-wide
sequence on main when it was written, 0192 (late report totals, #2298,
reports 0053), so the files apply in prefix order; a release train that
lands 0193 to 0197 first re-points the second dependency at the migration
that installs 0197.
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
        # The reports app's previous migration (frozen 0192, #2298), so the
        # app keeps one leaf; it also installs the previous frozen file, so
        # the files apply in prefix order.
        ("stewardship_reports", "0053_late_report_totals"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
