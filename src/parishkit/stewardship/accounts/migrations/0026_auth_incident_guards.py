"""Protect incident identity/evidence while permitting delivery and resolution."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0025_oauthstateconsumption")]
    operations = [
        mutable_guard_v1(
            "stewardship_auth_incident",
            frozen_fields=(
                "kind",
                "window",
                "level",
                "attempts",
                "sources",
                "identities",
                "candidates",
            ),
            write_once_fields=("resolved_at",),
        ),
    ]
