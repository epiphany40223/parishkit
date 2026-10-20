"""Forward migration: the refresh's last census cases (#1749).

Its SQL is the frozen file ``schema/migrations/0138_published_refresh_conflicts.sql``
(number 0138, provisional until it is assigned in merge order), read whole
and never parsed. It widens the refresh check outcome to ``conflict`` (an
Updated in ParishSoft change ParishSoft no longer holds), adds a
resolution's ParishSoft Member ID (a New Member Staff marked entered), and
replaces the census refresh source, the resolution, derived and refresh
check guards so a refresh moves such a change to Conflict, confirms a New
Member by its Member ID and the Family's home address by ParishSoft's
primary address, and the publish worker queues no Member who left the
change's Family. It ends with a DO block that refuses to commit unless all
of it is installed. The state operations record the new field and
constraint in Django's model state (the refresh check's widened outcome is
``stewardship_workflows.0006``'s), so ``makemigrations --check`` stays
clean. Reversing needs its own forward migration, so, like the baseline,
it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations, models

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0138_published_refresh_conflicts.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # This app's previous migration (0136's, after 0133's), so the app
        # keeps one leaf, and the migration that installs the previous frozen
        # file (0137); 0133's and 0137's guard bodies this file replaces and
        # joins, so the files apply in prefix order.
        ("stewardship_responses", "0018_submission_upcoming_pledge"),
        ("stewardship_workflows", "0005_census_refresh_sources"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.AddField(
                    model_name="proposalresolution",
                    name="member_duid",
                    field=models.BigIntegerField(blank=True, null=True),
                ),
                migrations.AddConstraint(
                    model_name="proposalresolution",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("member_duid__isnull", True))
                        | models.Q(("action", "entered"), ("member_duid__gt", 0)),
                        name="proposal_resolution_member",
                    ),
                ),
            ],
        )
    ]
