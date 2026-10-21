"""Forward migration: Back to review undoes a pledge's terminal stage (#1840).

Its SQL is the frozen file ``schema/migrations/0149_pledge_terminal_undo.sql``
(prefix 0149 in the repository-wide file sequence), read whole and never
parsed. It widens
0127's ``pledge_review_action_state`` check so a Back to review row may keep
an accepted decision, and replaces 0135's pledge review guard in place so
Back to review on Entered by hand, Entered incorrectly or Confirmed needs a
note and keeps the decision in force (none for a pledge ParishSoft already
held). It ends with a DO block that refuses to commit unless all of it is
installed. The state operation records the same check in Django's model
state, so ``makemigrations --check`` stays clean. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.

It depends on the reports app's previous migration, which installs the
earlier frozen file, and on the workflows migration that installs the
frozen file before it (0148), so the files apply in prefix order.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0149_pledge_terminal_undo.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration.
        ("stewardship_reports", "0037_staff_timeline_export"),
        # The migration that installs the previous frozen file (0148,
        # #1840), so the files apply in prefix order.
        ("stewardship_workflows", "0007_ministry_terminal_undo"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="pledgereview",
                    name="pledge_review_action_state",
                ),
                migrations.AddConstraint(
                    model_name="pledgereview",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            action="accept",
                            decision="accepted",
                            decision__isnull=False,
                            entered=False,
                        )
                        | models.Q(
                            action="edit",
                            decision="accepted_edited",
                            decision__isnull=False,
                            entered=False,
                        )
                        | models.Q(
                            action="ignore",
                            decision="ignored",
                            decision__isnull=False,
                            entered=False,
                        )
                        | models.Q(
                            action="back_to_review",
                            decision__isnull=True,
                            entered=False,
                        )
                        | models.Q(
                            action="back_to_review",
                            decision__isnull=False,
                            decision__in=("accepted", "accepted_edited"),
                            entered=False,
                        )
                        | models.Q(
                            action="entered_by_hand",
                            decision__isnull=False,
                            decision__in=("accepted", "accepted_edited"),
                            entered=True,
                        ),
                        name="pledge_review_action_state",
                    ),
                ),
            ],
        ),
    ]
