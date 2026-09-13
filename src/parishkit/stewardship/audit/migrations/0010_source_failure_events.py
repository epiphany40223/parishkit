"""Admit closed source failure categories without admitting provider text."""

from django.db import migrations

PREVIOUS = (
    "configuration_rejected",
    "configuration_digest_mismatch",
    "startup_rejected",
    "startup_validated",
    "request_completed",
    "task_started",
    "task_completed",
    "task_failed",
    "unstructured_log_suppressed",
    "authentication_limits_weakened",
    "installer_request_failed",
)
ADDED = (
    "source_refresh_invalid",
    "source_refresh_held",
    "source_credential_failed",
    "source_provider_failed",
)


def constraint(events):
    """Freeze migration vocabulary independently of the evolving event registry."""
    values = ",".join("'" + event + "'" for event in events)
    return (
        "ALTER TABLE stewardship_operational_log "
        "DROP CONSTRAINT operational_event_safe;"
        "ALTER TABLE stewardship_operational_log ADD CONSTRAINT operational_event_safe "
        f"CHECK (event IN ({values}));"
    )


class Migration(migrations.Migration):
    dependencies = [("stewardship_audit", "0009_installer_failure_event")]
    operations = [
        migrations.RunSQL(
            sql=constraint((*PREVIOUS, *ADDED)),
            # Retained new-event history rejects downgrade atomically. Never
            # discard or relabel audit records merely to make rollback succeed.
            reverse_sql=constraint(PREVIOUS),
        )
    ]
