"""Guard every SQL session update, not only calls to the mutation helper."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0002_session_integrity")]
    operations = [
        mutable_guard_v1(
            "stewardship_portal_session",
            frozen_fields=("principal_id", "session_id", "authenticated_at"),
        )
    ]
