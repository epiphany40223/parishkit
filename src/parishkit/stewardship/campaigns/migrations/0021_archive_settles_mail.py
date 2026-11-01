"""Forward migration: archive waits for every campaign email to settle (#2305).

Its SQL is the frozen file ``schema/migrations/0201_archive_settles_mail.sql``
(prefix 0201 in the repository-wide file sequence), read whole and never
parsed. It replaces the campaign transition guard in place so that archive
is also refused while any outbox message of the campaign is pending,
retry_wait, submitting or delivery_unknown: the delivery resolution guard
refuses an archived campaign's email, so such a message could otherwise
never be settled. It ends with a DO block that refuses to commit unless the
new check is installed as declared. No model state changes. Reversing needs
its own forward migration.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0201_archive_settles_mail.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The campaigns app's previous migration, so the app keeps one leaf.
        ("stewardship_campaigns", "0020_staff_open_after_close"),
        # The migration that installs the previous frozen file (0200).
        ("stewardship_accounts", "0041_address_match"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
