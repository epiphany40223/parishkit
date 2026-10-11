"""Forward migration: Admin session keys are stored only as digests (#995).

Its SQL is the frozen file ``schema/migrations/0057_admin_session_key_digests.sql``
(prefix 0057, assigned in the repository-wide file sequence), read whole and
never parsed. It adds ``stewardship_admin_session_key_v1`` (the stored form
of an Admin session key, as ``accounts/admin_session_store.digest`` computes
it) and an insert guard that admits only such a digest as a
``stewardship_portal_session.session_id``; replaces the Users page's two
session definers (0053, 0054) so they compare the digest of the key they
are handed; and ends every live Admin session stored under an undigested
key with an ``admin_revoked`` audit event. It
ends with a DO block that refuses to commit unless all of it is installed as
declared. Family sessions are untouched. No model changes, so there is no
state operation. Reversing needs its own forward migration, so, like the
baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0057_admin_session_key_digests.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # Its own app's latest migration (it installed 0055, #993).
        ("stewardship_accounts", "0016_setup_users"),
        # The migration that installed the previous frozen file (0056, #970),
        # so the files apply in prefix order.
        ("stewardship_reports", "0013_retire_talents"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
