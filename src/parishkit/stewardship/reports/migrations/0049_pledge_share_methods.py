"""Forward migration: a pledge Edit carries the reviewer's share methods (#2177).

Its SQL is the frozen file ``schema/migrations/0181_pledge_share_methods.sql``
(prefix 0181, assigned in the repository-wide file sequence), read whole
and never parsed. It adds ``stewardship_pledge_review.reviewed_shares``
with its check, and replaces 0158's pledge review guard and 0155's pledge
item selection in place, so the mark and the undo of a terminal step carry
an Edit's share methods and a reviewed form that sent them back as shown
keeps its decision. It ends with a DO block that refuses to commit unless
all of it is installed as declared. The state operations record the column
and the check in Django's model state, so ``makemigrations --check`` stays
clean. Reversing needs its own forward migration.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0181_pledge_share_methods.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration (0178, the review run,
        # #2154), so the app keeps one leaf.
        ("stewardship_reports", "0048_review_run"),
        # The migration that installs the previous frozen file (0180, the
        # added undo, #2173), so the files apply in prefix order.
        ("stewardship_workflows", "0009_added_undo_to_review"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddField(
                    model_name="pledgereview",
                    name="reviewed_shares",
                    field=models.JSONField(null=True),
                ),
                migrations.AddConstraint(
                    model_name="pledgereview",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("reviewed_shares__isnull", True),
                            models.Q(
                                ("decision", "accepted_edited"),
                                ("reviewed_annual_pledge__gt", 0),
                            ),
                            _connector="OR",
                        ),
                        name="pledge_review_shares_shape",
                    ),
                ),
            ],
        ),
    ]
