"""Forward migration: shared Family name SQL and report DUID sorts (#960).

Its SQL is the frozen file ``schema/migrations/0045_report_duid_sorts.sql``
(prefix 0045, assigned in the repository-wide file sequence), read whole and
never parsed. It creates the shared Family name functions
(``stewardship_name_strip_v1``, ``stewardship_family_surname_v1``,
``stewardship_family_head_part_v1``, ``stewardship_family_heads_name_v1`` and
``stewardship_family_display_name_v1``), the SQL twins of
``source/family_names.py`` and ``source/snapshot_names.py``. It replaces the
financial, Additional information and Ministry report selections and the
Ministry export capture, each with its baseline body plus the change:
``duid``/``duid_desc`` sort tokens (Family, Ministry or Member DUID) and a
Family name search and sort over the name the pages show, built with the
shared functions. Signatures, attributes and grants are unchanged, and the
file ends with a DO block that refuses to commit unless the new functions
and bodies are installed as declared. No model changes, so there is no state
operation. Reversing needs its own forward migration, so, like the baseline,
it has no reverse operation.
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
        # The migration that installed the previous frozen file (0044,
        # #984), so the files apply in prefix order.
        ("stewardship_source", "0007_corpus_evidence_from_snapshot"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
