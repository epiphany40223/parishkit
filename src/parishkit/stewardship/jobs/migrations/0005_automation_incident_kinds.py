"""Model state for the four Admin automation incident kinds (ADM-11 PR 2).

The database change is made by ``stewardship_accounts.0004_automation_sessions``,
whose frozen SQL file drops and re-adds the ``ops_incident_kind`` constraint
with the new kinds (the constraint's baseline text is in
``schema/operational_incidents.sql``). The constraint is generated from
``IncidentKind``, and CI runs ``makemigrations --check``, so this migration
records the same change in Django's state only; it runs no SQL and therefore
depends on the migration that does.
"""

from django.db import migrations, models

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
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0004_family_mail_test"),
        ("stewardship_accounts", "0004_automation_sessions"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="operationalincident", name="ops_incident_kind"
                ),
                migrations.AddConstraint(
                    model_name="operationalincident",
                    constraint=models.CheckConstraint(
                        condition=models.Q(kind__in=KINDS), name="ops_incident_kind"
                    ),
                ),
            ]
        )
    ]
