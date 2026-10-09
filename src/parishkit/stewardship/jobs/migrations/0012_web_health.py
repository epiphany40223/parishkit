"""Forward migration: alert when web stops answering, record its recovery (#392 L1).

Its SQL is the frozen file ``schema/migrations/0019_web_health.sql``, read
whole and never parsed. It creates the singleton ``stewardship_web_health``
row the scheduler's web probe writes, with the trigger that counts
consecutive failed minutes and writes the CRITICAL ``web_unhealthy`` entry;
adds ``web_unhealthy`` to the operational event list and the incident kinds;
maps that entry to its incident in the log-receipt binding; gives the alert
its title and instruction; widens the context allowlist with the probe's
failure and timeout words; and ends with a DO block that refuses to commit
unless all of it is installed. The state operations record the new model and
the widened ``ops_incident_kind`` constraint (generated from
``IncidentKind``), so ``makemigrations --check`` stays clean. Reversing needs
its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations, models

import parishkit.stewardship.storage

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0019_web_health.sql"
)

# 0005_automation_incident_kinds's list plus web_unhealthy.
KINDS = (
    "database_unavailable",
    "storage_integrity",
    "task_failed",
    "system_failure",
    "source_refresh_failed",
    "source_stale",
    "source_tenant_mismatch",
    "source_destructive_change",
    "mail_provider_unavailable",
    "scheduler_lag",
    "worker_unavailable",
    "admin_abuse",
    "family_abuse",
    "limiter_unavailable",
    "limiter_state_lost",
    "publication_ambiguous",
    "production_cleanup_failed",
    "backup_rpo_breach",
    "backup_offsite_failed",
    "backup_key_changed",
    "source_retention_failing",
    "purge_inconsistency",
    "purge_cleanup_failed",
    "automation_approved",
    "automation_irreversible",
    "automation_policy_change",
    "automation_refused",
    "web_unhealthy",
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0011_log_events"),
        # The migration that installed the previous frozen file, so the
        # files apply in prefix order.
        ("stewardship_reports", "0002_download_audit_context"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))
            ],
            state_operations=[
                migrations.CreateModel(
                    name="WebHealth",
                    fields=[
                        (
                            "singleton",
                            models.BooleanField(
                                default=True, primary_key=True, serialize=False
                            ),
                        ),
                        ("healthy", models.BooleanField()),
                        (
                            "observed_at",
                            parishkit.stewardship.storage.UTCDateTimeField(),
                        ),
                        ("failures", models.PositiveIntegerField()),
                        (
                            "failing_since",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                        (
                            "passing_since",
                            parishkit.stewardship.storage.UTCDateTimeField(null=True),
                        ),
                    ],
                    options={
                        "db_table": "stewardship_web_health",
                        "constraints": [
                            models.CheckConstraint(
                                condition=models.Q(singleton=True),
                                name="web_health_singleton",
                            ),
                            models.CheckConstraint(
                                condition=models.Q(
                                    healthy=True,
                                    failures=0,
                                    failing_since__isnull=True,
                                    passing_since__isnull=False,
                                    passing_since__lte=models.F("observed_at"),
                                )
                                | models.Q(
                                    healthy=False,
                                    failures__gte=1,
                                    passing_since__isnull=True,
                                    failing_since__isnull=False,
                                    failing_since__lte=models.F("observed_at"),
                                ),
                                name="web_health_shape",
                            ),
                        ],
                    },
                ),
                migrations.RemoveConstraint(
                    model_name="operationalincident", name="ops_incident_kind"
                ),
                migrations.AddConstraint(
                    model_name="operationalincident",
                    constraint=models.CheckConstraint(
                        condition=models.Q(kind__in=KINDS), name="ops_incident_kind"
                    ),
                ),
            ],
        ),
    ]
