"""Forward migration: the chosen-Family test names export in every format (#966).

Its SQL is the frozen file ``schema/migrations/0049_family_test_names_formats.sql``
(prefix 0049 in the repository-wide file sequence, after #983's 0047 and
#988's 0048), read whole and never parsed. It
replaces ``export_report_known`` with 0037's definition with the
``family_test_names`` arm widened from CSV only to CSV, XLSX and PDF, and
ends with a DO block that refuses to commit unless the widened constraint is
installed and validated. The state operations record the same constraint
change in Django's state, so ``makemigrations --check`` stays clean.
Reversing needs its own forward migration, so, like the baseline, it has no
reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0049_family_test_names_formats.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration on main. This file is 0011
        # because #945 holds reports 0010.
        ("stewardship_reports", "0009_report_duid_sorts"),
        # The migration that installs the previous frozen file (0048, #988),
        # so the files apply in prefix order.
        ("stewardship_campaigns", "0010_restore_holds_after_release"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="exportrequest",
                    name="export_report_known",
                ),
                migrations.AddConstraint(
                    model_name="exportrequest",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", False),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "participation"),
                            ),
                            models.Q(
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", False),
                                ("ministry_snapshot__isnull", True),
                                ("report", "additional_information"),
                            ),
                            models.Q(
                                ("directory_snapshot__isnull", False),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report__in", ("family_directory", "postal_outreach")),
                            ),
                            models.Q(
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", False),
                                ("report", "ministry"),
                            ),
                            models.Q(
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", False),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "financial"),
                            ),
                            models.Q(
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", False),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "family_test_names"),
                            ),
                            _connector="OR",
                        ),
                        name="export_report_known",
                    ),
                ),
            ],
        ),
    ]
