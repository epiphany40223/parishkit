"""Name the retained live link's prior replacement time without implying denial."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0028_rehearsal_mac_formats")]
    operations = [
        migrations.RenameField(
            model_name="familyaccesstoken", old_name="revoked_at", new_name="rotated_at"
        )
    ]
