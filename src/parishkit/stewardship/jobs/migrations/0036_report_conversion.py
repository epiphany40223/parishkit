"""Model state for the report email conversion (#1786).

It admits the outbox purposes ``admin_report`` and
``admin_report_test``. The frozen file 0191
(``stewardship_reports.0034_report_conversion``) changes the constraints
in the database; this records them in Django's model state only, so
``makemigrations --check`` stays clean.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0034_report_conversion"),
        ("stewardship_campaigns", "0015_report_conversion"),
        ("stewardship_jobs", "0035_leader_emails"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="outboxmessage",
                    name="outbox_purpose",
                ),
                migrations.RemoveConstraint(
                    model_name="outboxmessage",
                    name="outbox_family_purpose",
                ),
                migrations.AddConstraint(
                    model_name="outboxmessage",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            (
                                "purpose__in",
                                (
                                    "initial",
                                    "reminder",
                                    "receipt",
                                    "family_test",
                                    "daily_digest",
                                    "weekly_digest",
                                    "operational",
                                    "security_event",
                                    "leader_email",
                                    "admin_report",
                                    "admin_report_test",
                                ),
                            )
                        ),
                        name="outbox_purpose",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="outboxmessage",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("family__isnull", False),
                                (
                                    "purpose__in",
                                    ["initial", "reminder", "receipt", "family_test"],
                                ),
                            ),
                            models.Q(
                                ("family", None),
                                (
                                    "purpose__in",
                                    [
                                        "daily_digest",
                                        "weekly_digest",
                                        "operational",
                                        "security_event",
                                        "leader_email",
                                        "admin_report",
                                        "admin_report_test",
                                    ],
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="outbox_family_purpose",
                    ),
                ),
            ],
        )
    ]
