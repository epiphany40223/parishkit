"""Forward migration: the Mail message page's recipient read (#1024).

Its SQL is the frozen file ``schema/migrations/0058_mail_message_recipients.sql``
(prefix 0058 in the repository-wide file sequence), read whole and never
parsed. It creates ``stewardship_delivery_recipients_v1(message, actor)``, a
SECURITY DEFINER read that returns one email's recipient addresses (and
nothing else from its rendering) to the web login for an Administrator, and
ends with a DO block that refuses to commit unless the function is installed
as declared. No model changes, so there is no state operation. Reversing
needs its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0058_mail_message_recipients.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_jobs", "0021_alert_local_time"),
        # The migration that installed the previous frozen file (0057,
        # #995), so the files apply in prefix order. It follows the users
        # swap, so `migrate --users-upgrade-base` never applies this file
        # and its routine is registered in runtime_grants.CLOSURE_FUNCTIONS.
        ("stewardship_accounts", "0017_admin_session_key_digests"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
