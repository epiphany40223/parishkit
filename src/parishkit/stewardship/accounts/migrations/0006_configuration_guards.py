"""Protect each new canonical/projection table independently of its ORM manager."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1


class Migration(migrations.Migration):
    """Use the deployed frozen builder without changing older table semantics."""

    dependencies = [
        (
            "stewardship_accounts",
            "0005_appliedconfigurationversion_appliedintegration_and_more",
        ),
    ]
    operations = [
        immutable_guard_v1("stewardship_configuration_version"),
        immutable_guard_v1("stewardship_parish"),
        immutable_guard_v1("stewardship_applied_integration"),
    ]
