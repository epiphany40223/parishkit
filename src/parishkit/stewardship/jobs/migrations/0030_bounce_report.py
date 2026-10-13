"""Forward migration: an Administrator's report that an address bounced (#806).

Its SQL is the frozen file ``schema/migrations/0081_bounce_report.sql`` (prefix
0081 in the repository-wide file sequence), read whole and never parsed. It
adds ``kind`` and ``evidence_note`` to ``stewardship_recipient_refusal`` with
the check that ties them together, replaces
``stewardship_recipient_refusal_guard_v1`` with the baseline's final
definition (which also admits a reported bounce from the web login), creates
the web-only definer ``stewardship_report_recipient_bounce_v1`` that records
one, and ends with a DO block that refuses to commit unless all of it is
installed as declared. The state operations record the columns and the check
in Django's model state, so ``makemigrations --check`` stays clean. Reversing
needs its own forward migration.

It follows the users swap (#952), so its definer is granted only once it is
applied (runtime_grants.CLOSURE_FUNCTIONS).
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0081_bounce_report.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0029_retry_refusal"),
        # The migration that installed the previous frozen file (0080), so
        # the files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file.
        ("stewardship_source", "0008_drop_acceptance"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddField(
                    model_name="recipientrefusal",
                    name="kind",
                    field=models.CharField(
                        db_default="provider", default="provider", max_length=32
                    ),
                ),
                migrations.AddField(
                    model_name="recipientrefusal",
                    name="evidence_note",
                    field=models.CharField(
                        blank=True, db_default="", default="", max_length=2000
                    ),
                ),
                migrations.AddConstraint(
                    model_name="recipientrefusal",
                    constraint=models.CheckConstraint(
                        condition=models.Q(kind="provider", evidence_note="")
                        | (
                            models.Q(kind="reported_bounce", actor_id__isnull=False)
                            & ~models.Q(evidence_note="")
                        ),
                        name="recipient_refusal_kind",
                    ),
                ),
            ],
        ),
    ]
