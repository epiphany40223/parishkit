"""Forward migration: an Administrator resolves a census conflict (#528).

Its SQL is the frozen file ``schema/migrations/0108_conflict_resolution.sql``
(number 0108, assigned centrally), read whole and never parsed. It adds the
accepted base (``accepted_base``, ``accepted_value``, ``accepted_source_id``
and the check over them) to ``stewardship_proposal_review``, so a review may
resolve a Conflict ("Use the Family's value anyway", ADM-09.03), and
replaces the review guard, its deferred effect check and the derived guard
to admit exactly that, ending with a DO block that refuses to commit unless
all of it is installed. The state operations record the columns and the
check in Django's model state, so ``makemigrations --check`` stays clean.
Reversing needs its own forward migration, so, like the baseline, it has no
reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0108_conflict_resolution.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The migration that installed the previous frozen file (0107), so
        # the files apply in prefix order.
        ("stewardship_responses", "0008_proposal_review_edit"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddField(
                    model_name="proposalreview",
                    name="accepted_base",
                    field=models.BooleanField(db_default=False, default=False),
                ),
                migrations.AddField(
                    model_name="proposalreview",
                    name="accepted_source_id",
                    field=models.UUIDField(null=True),
                ),
                migrations.AddField(
                    model_name="proposalreview",
                    name="accepted_value",
                    field=models.JSONField(null=True),
                ),
                migrations.AddConstraint(
                    model_name="proposalreview",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            accepted_base=False,
                            accepted_source_id__isnull=True,
                            accepted_value__isnull=True,
                        )
                        | models.Q(
                            accepted_base=True,
                            accepted_source_id__isnull=False,
                            decision="approved",
                        ),
                        name="proposal_review_accepted_shape",
                    ),
                ),
            ],
        ),
    ]
