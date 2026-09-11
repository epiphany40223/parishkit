"""Admit the closed installer diagnostic event without weakening log validation."""

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
)


def constraint(events):
    """Freeze literal migration vocabulary; never import the live event registry."""
    values = ",".join("'" + event + "'" for event in events)
    return (
        "ALTER TABLE stewardship_operational_log "
        "DROP CONSTRAINT operational_event_safe;"
        "ALTER TABLE stewardship_operational_log ADD CONSTRAINT operational_event_safe "
        f"CHECK (event IN ({values}));"
    )


class Migration(migrations.Migration):
    dependencies = [("stewardship_audit", "0008_context_guards")]
    operations = [
        migrations.RunSQL(
            sql=constraint((*PREVIOUS, "installer_request_failed")),
            # Existing new-event history makes ADD CONSTRAINT fail atomically;
            # a downgrade may not discard or silently relabel those records.
            reverse_sql=constraint(PREVIOUS),
        )
    ]
