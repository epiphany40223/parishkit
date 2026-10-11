"""Forward migration: the directory gains the response lists' filters (#933).

Its SQL is the frozen file ``schema/migrations/0050_directory_response_filters.sql``
(prefix 0050, assigned in the repository-wide file sequence), read whole and
never parsed. It adds ``stewardship_family_response_v1`` (the response
funnel's per-Family rows, which ``reports.response_metrics`` reads instead of
its former inline statement) and ``stewardship_directory_report_v2`` (the
directory selection with the Response and ParishSoft data to check filters,
the response columns and their sort orders). It replaces
``stewardship_directory_export_capture_v1`` so captures run through v2 and
keep the envelope number in the Family-code file, and
``stewardship_safe_context_v1`` so the audit context admits the directory's
new closed values. It ends with a DO block that refuses to commit unless all
four are installed as declared and the replaced ones keep their grants. No
model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0050_directory_response_filters.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which also installs the
        # previous frozen file (0049, #966), so the files apply in prefix
        # order. This file is 0012 because 0010 was reserved for it before
        # main took 0011; the gap is harmless to Django.
        ("stewardship_reports", "0011_family_test_names_formats"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
