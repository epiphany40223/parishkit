"""Correlate new audit envelopes with their bound request or task operation."""

from django.db import migrations, models

import parishkit.stewardship.observability


class Migration(migrations.Migration):
    dependencies = [("stewardship_audit", "0002_append_only")]
    operations = [
        migrations.AlterField(
            model_name="auditevent",
            name="correlation_id",
            field=models.UUIDField(
                db_index=True,
                default=parishkit.stewardship.observability.current_correlation,
                editable=False,
            ),
        ),
    ]
