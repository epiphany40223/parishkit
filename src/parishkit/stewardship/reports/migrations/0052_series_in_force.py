"""Forward migration: the participation facts count the values in force (#2202).

Its SQL is the frozen file ``schema/migrations/0185_series_in_force.sql``
(prefix 0185, assigned in the repository-wide file sequence), read whole
and never parsed. It adds the pledge review watermark to the fact set (and
to its exact-input key), the rebuild demand, the exact export request and
the report email preparation, and the pledge Edits in force that the
database records on each new fact set; creates the two narrow SECURITY
DEFINER reads (``stewardship_pledge_review_watermark_v1`` and
``stewardship_fact_pledge_edits_v1``) and the recording trigger; and
replaces the identity checks and the campaign comparison so they carry the
watermark and the Edits. It ends with a DO block that refuses to commit
unless all of it is installed as declared. The state operations record the
same fields and constraints, so ``makemigrations --check`` stays clean.
Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0185_series_in_force.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # 0184's (the campaign statistics in force, #2202, PR #2239): the
        # reports app's previous migration, so the app keeps one leaf, and
        # the one that installs the previous frozen file, so the files apply
        # in prefix order.
        ("stewardship_reports", "0051_statistics_in_force"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="campaigndailyfactset",
                    name="daily_fact_exact_inputs",
                ),
                migrations.AddField(
                    model_name="campaigndailyfactset",
                    name="pledge_edits",
                    field=models.JSONField(default=list),
                ),
                migrations.AddField(
                    model_name="campaigndailyfactset",
                    name="review_watermark",
                    field=models.PositiveBigIntegerField(default=0),
                ),
                migrations.AddField(
                    model_name="campaignfactrebuilddemand",
                    name="requested_review_watermark",
                    field=models.PositiveBigIntegerField(default=0),
                ),
                migrations.AddField(
                    model_name="exactexportrequest",
                    name="review_watermark",
                    field=models.PositiveBigIntegerField(default=0),
                ),
                migrations.AddField(
                    model_name="reportemailpreparation",
                    name="review_watermark",
                    field=models.PositiveBigIntegerField(null=True),
                ),
                migrations.AddConstraint(
                    model_name="campaigndailyfactset",
                    constraint=models.UniqueConstraint(
                        fields=(
                            "campaign",
                            "population_scope",
                            "source_generation",
                            "submission_watermark",
                            "review_watermark",
                            "timezone_configuration",
                            "through_date",
                        ),
                        name="daily_fact_exact_inputs",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="reportemailpreparation",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("review_watermark__isnull", True),
                            ("submission_watermark__isnull", False),
                            _connector="OR",
                        ),
                        name="report_email_review_inputs",
                    ),
                ),
            ],
        )
    ]
