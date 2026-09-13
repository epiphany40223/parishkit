"""Admit closed wizard credential receipts, never their settings or ciphertext."""

from importlib import import_module

from django.db import migrations

_previous = import_module(
    "parishkit.stewardship.audit.migrations.0011_handoff_mismatch_event"
)
PREVIOUS = (*_previous.PREVIOUS, "credential_handoff_key_mismatch")
ADDED = ("setup_credential_staged", "setup_credential_scrubbed")


class Migration(migrations.Migration):
    dependencies = [("stewardship_audit", "0011_handoff_mismatch_event")]
    operations = [
        migrations.RunSQL(
            _previous._previous.constraint((*PREVIOUS, *ADDED)),
            _previous._previous.constraint(PREVIOUS),
        )
    ]
