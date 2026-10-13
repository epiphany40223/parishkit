"""Forward migration: the campaign archive download (#1018 part (a), slice A2).

Its SQL is the frozen file ``schema/migrations/0084_campaign_archive_exports.sql``
(number 0084, the next free number when the train was built), read whole
and never parsed. It creates
the general worker's definer read ``stewardship_campaign_archive_answers_v1``
(the archive's parts of each live answer under a fact set's pins), re-adds
``export_report_known`` with a ``campaign_archive`` arm (XLSX, a fact set,
no capture), and re-creates the export request, publication and fact-pin
guards and the request authorization with an archive branch. It ends with a
DO block that refuses to commit unless all of that is installed. The state
operations record the same constraint, so ``makemigrations --check`` stays
clean. Reversing needs its own forward migration, so it has no reverse
operation.

It depends on the migration that installs the previous frozen file on main
(0076, ``stewardship_jobs.0026_daily_send_resume``, #530) and on the reports
app's previous migration (0021). Numbers 0077 to 0079 are reserved by open
pull requests (#1174, #1177, #1180; #1180's is also this app's 0022): the
one of them that merges last before this must become a dependency here (and
this file's number may then need renaming), so the files apply in prefix
order and Django keeps a single leaf. Like 0071, it is in the users swap's
closure (#952): its definer function is registered in
``runtime_grants.CLOSURE_FUNCTIONS``, and the users swap test puts 0071's
export guards and ``export_report_known`` back while the swap is pending.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0084_campaign_archive_exports.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration, which also installs the
        # previous frozen file (0083, #477), so the files apply in prefix
        # order.
        ("stewardship_reports", "0022_digest_funnel_charts"),
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
                                ("comparison_snapshot__isnull", True),
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", False),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "participation"),
                                ("timeline_snapshot__isnull", True),
                            ),
                            models.Q(
                                ("comparison_snapshot__isnull", True),
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", False),
                                ("ministry_snapshot__isnull", True),
                                ("report", "additional_information"),
                                ("timeline_snapshot__isnull", True),
                            ),
                            models.Q(
                                ("comparison_snapshot__isnull", True),
                                ("directory_snapshot__isnull", False),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report__in", ("family_directory", "postal_outreach")),
                                ("timeline_snapshot__isnull", True),
                            ),
                            models.Q(
                                ("comparison_snapshot__isnull", True),
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", False),
                                ("report", "ministry"),
                                ("timeline_snapshot__isnull", True),
                            ),
                            models.Q(
                                ("comparison_snapshot__isnull", True),
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", False),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "financial"),
                                ("timeline_snapshot__isnull", True),
                            ),
                            models.Q(
                                ("comparison_snapshot__isnull", True),
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", False),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "family_test_names"),
                                ("timeline_snapshot__isnull", True),
                            ),
                            models.Q(
                                ("comparison_snapshot__isnull", True),
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "family_timeline"),
                                ("timeline_snapshot__isnull", False),
                            ),
                            models.Q(
                                ("comparison_snapshot__isnull", False),
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", True),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format__in", ("csv", "xlsx", "pdf")),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "campaign_comparison"),
                                ("timeline_snapshot__isnull", True),
                            ),
                            models.Q(
                                ("comparison_snapshot__isnull", True),
                                ("directory_snapshot__isnull", True),
                                ("fact_set__isnull", False),
                                ("family_test_names_snapshot__isnull", True),
                                ("financial_snapshot__isnull", True),
                                ("format", "xlsx"),
                                ("information_snapshot__isnull", True),
                                ("ministry_snapshot__isnull", True),
                                ("report", "campaign_archive"),
                                ("timeline_snapshot__isnull", True),
                            ),
                            _connector="OR",
                        ),
                        name="export_report_known",
                    ),
                ),
            ],
        ),
    ]
