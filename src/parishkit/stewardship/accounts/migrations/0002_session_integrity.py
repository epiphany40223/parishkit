"""Share operation correlation and enforce complete session chronology."""

from django.db import migrations, models

import parishkit.stewardship.observability


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0001_initial")]
    operations = [
        migrations.AlterField(
            model_name="portalsession",
            name="correlation_id",
            field=models.UUIDField(
                db_index=True,
                default=parishkit.stewardship.observability.current_correlation,
                editable=False,
            ),
        ),
        migrations.AddConstraint(
            model_name="portalsession",
            constraint=models.CheckConstraint(
                condition=models.Q(last_activity_at__lt=models.F("expires_at")),
                name="portal_session_activity_before_expiry",
            ),
        ),
        migrations.AddConstraint(
            model_name="portalsession",
            constraint=models.CheckConstraint(
                condition=models.Q(revoked_at__isnull=True)
                | models.Q(revoked_at__gte=models.F("authenticated_at")),
                name="portal_session_revoked_after_auth",
            ),
        ),
    ]
