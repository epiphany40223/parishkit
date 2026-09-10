"""Protect the persistent health namespace without retaining authentication data."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0027_limiterstorehealth_and_more")]
    operations = [
        mutable_guard_v1(
            "stewardship_limiter_health",
            frozen_fields=("namespace_fingerprint", "marker"),
        ),
    ]
