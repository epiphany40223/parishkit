"""Model state for the report email conversion (#1786).

It admits the ``report`` schedule kind beside the digest kinds and the
report email cleanup categories. The frozen file 0161
(``stewardship_reports.0041_report_conversion``) changes the constraints
in the database; this records them in Django's model state only, so
``makemigrations --check`` stays clean.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_reports", "0041_report_conversion"),
        ("stewardship_accounts", "0031_report_conversion"),
        ("stewardship_campaigns", "0014_digest_skip_dates"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="productioncleanuptarget",
                    name="production_target_category",
                ),
                migrations.RemoveConstraint(
                    model_name="scheduledefinition",
                    name="schedule_definition_kind",
                ),
                migrations.RemoveConstraint(
                    model_name="schedulerevision",
                    name="schedule_revision_due_kind",
                ),
                migrations.AddConstraint(
                    model_name="productioncleanuptarget",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            (
                                "category__in",
                                [
                                    "baselines",
                                    "session_data",
                                    "family_sessions",
                                    "ministry_requests",
                                    "occurrences",
                                    "occurrence_events",
                                    "outbox_events",
                                    "outbox_messages",
                                    "outbox_renders",
                                    "proposals",
                                    "rehearsal_credentials",
                                    "rehearsal_macs",
                                    "schedule_fulfillments",
                                    "source_pins",
                                    "submission_receipts",
                                    "submissions",
                                    "prior_inventory_targets",
                                    "daily_digest_recipients",
                                    "daily_digest_ready",
                                    "daily_digest_snapshots",
                                    "daily_digest_fact_pins",
                                    "recovery_replacements",
                                    "weekly_digest_recipients",
                                    "weekly_digest_snapshots",
                                    "engagement",
                                    "report_email_recipients",
                                    "report_email_snapshots",
                                    "report_email_fact_pins",
                                ],
                            )
                        ),
                        name="production_target_category",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="scheduledefinition",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            (
                                "kind__in",
                                [
                                    "initial",
                                    "reminder",
                                    "daily_digest",
                                    "weekly_digest",
                                    "report",
                                ],
                            )
                        ),
                        name="schedule_definition_kind",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="schedulerevision",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("due_at__isnull", False),
                                ("kind__in", ["initial", "reminder"]),
                            ),
                            models.Q(
                                ("due_at__isnull", True),
                                (
                                    "kind__in",
                                    ["daily_digest", "weekly_digest", "report"],
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="schedule_revision_due_kind",
                    ),
                ),
            ],
        )
    ]
