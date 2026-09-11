"""Freeze restore fences, index cleanup and reject naive projection timestamps."""

from django.db import migrations, models

from parishkit.stewardship.storage import UTCDateTimeField
from parishkit.stewardship.storage_migrations import mutable_guard_v1

FROZEN = (
    "session_id",
    "family_id",
    "mode",
    "rehearsal_epoch_id",
    "authenticated_at",
    "expires_at",
)
PREVIOUS = mutable_guard_v1(
    "stewardship_family_session",
    frozen_fields=FROZEN,
    write_once_fields=("revoked_at",),
)


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0031_monotonic_delivery_controls")]
    operations = [
        migrations.RunSQL(PREVIOUS.reverse_sql, reverse_sql=PREVIOUS.sql),
        mutable_guard_v1(
            "stewardship_family_session",
            frozen_fields=(*FROZEN, "credential_epoch"),
            write_once_fields=("revoked_at",),
        ),
        migrations.AddIndex(
            model_name="familysession",
            index=models.Index(
                fields=["expires_at", "id"], name="family_session_expiry"
            ),
        ),
        migrations.AlterField("campaignconfiguration", "starts_at", UTCDateTimeField()),
        migrations.AlterField("campaignconfiguration", "ends_at", UTCDateTimeField()),
        migrations.AlterField(
            "schedulerevision", "due_at", UTCDateTimeField(null=True)
        ),
    ]
