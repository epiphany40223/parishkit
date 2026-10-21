"""State only: the refresh check's outcome admits ``conflict`` (#1749).

``stewardship_responses.0019_published_refresh_conflicts`` installs the
frozen file (0138, provisional number) whose SQL widens the check; this
records the same constraint in Django's model state, so ``makemigrations
--check`` stays clean. It changes nothing in the database.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_workflows", "0005_census_refresh_sources"),
        ("stewardship_responses", "0019_published_refresh_conflicts"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="refreshcheck",
                    name="refresh_check_outcome",
                ),
                migrations.AddConstraint(
                    model_name="refreshcheck",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            (
                                "outcome__in",
                                (
                                    "confirmed",
                                    "unchanged",
                                    "entered_incorrectly",
                                    "ignored_now_matches",
                                    "conflict",
                                ),
                            )
                        ),
                        name="refresh_check_outcome",
                    ),
                ),
            ],
        )
    ]
