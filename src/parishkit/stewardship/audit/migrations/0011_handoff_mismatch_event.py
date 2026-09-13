"""Keep the private-key mismatch diagnostic in the durable closed vocabulary."""

from importlib import import_module

from django.db import migrations

_previous = import_module(
    "parishkit.stewardship.audit.migrations.0010_source_failure_events"
)
PREVIOUS = (*_previous.PREVIOUS, *_previous.ADDED)


class Migration(migrations.Migration):
    dependencies = [("stewardship_audit", "0010_source_failure_events")]
    operations = [
        migrations.RunSQL(
            _previous.constraint((*PREVIOUS, "credential_handoff_key_mismatch")),
            _previous.constraint(PREVIOUS),
        )
    ]
