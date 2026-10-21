"""Model state for retiring the digest kinds (#1834).

The schedule definition check admits only ``initial``, ``reminder`` and
``report``. The frozen file 0162 (``stewardship_reports.0035_retire_digests``)
changes the constraint in the database; this records it in Django's model
state only, so ``makemigrations --check`` stays clean.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0035_retire_digests"),
        ("stewardship_campaigns", "0015_report_conversion"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="scheduledefinition",
                    name="schedule_definition_kind",
                ),
                migrations.AddConstraint(
                    model_name="scheduledefinition",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("kind__in", ["initial", "reminder", "report"])
                        ),
                        name="schedule_definition_kind",
                    ),
                ),
            ],
        )
    ]
