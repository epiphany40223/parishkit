"""Name the reviewed invalidated-epoch retention exception, not generic immutability."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("stewardship_campaigns", "0029_token_rotation_time")]
    operations = [
        migrations.RunSQL(
            sql="""
ALTER FUNCTION stewardship_rehearsal_code_mac_immutable_v1()
RENAME TO stewardship_rehearsal_code_mac_retention_v1;
ALTER TRIGGER stewardship_rehearsal_code_mac_immutable_guard_v1
ON stewardship_rehearsal_code_mac
RENAME TO stewardship_rehearsal_code_mac_retention_guard_v1;
""",
            reverse_sql="""
ALTER TRIGGER stewardship_rehearsal_code_mac_retention_guard_v1
ON stewardship_rehearsal_code_mac
RENAME TO stewardship_rehearsal_code_mac_immutable_guard_v1;
ALTER FUNCTION stewardship_rehearsal_code_mac_retention_v1()
RENAME TO stewardship_rehearsal_code_mac_immutable_v1;
""",
        )
    ]
