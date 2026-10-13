"""Forward migration: requests for Members no longer in ParishSoft leave leaders' work.

Its SQL is the frozen file ``schema/migrations/0092_hide_gone_members.sql``
(prefix 0092, assigned in the repository-wide file sequence), read whole and
never parsed (#1217). It creates the Member standing function
``stewardship_member_source_status_v1`` and its per-request twin, and
replaces the follow-up, packet and Ministry report selections so a Ministry
leader never sees, counts or acts on a request whose Member ParishSoft no
longer lists in that Family, or lists as inactive or deceased; Staff and
Administrators list those only on request. Nothing is deleted or resolved.
It ends with a DO block that refuses to commit unless every function is
installed as declared with the replaced ones' grants unchanged. No model
changes, so there is no state operation. Reversing needs its own forward
migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0092_hide_gone_members.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration (it installed 0084, #1018).
        ("stewardship_reports", "0023_campaign_archive_exports"),
        # The migration that installs the previous frozen file (0091, the
        # Ministry activity table's drop), so the files apply in prefix
        # order. It follows the users swap, so `migrate --users-upgrade-base`
        # never applies this file; the two invoker functions it creates are in
        # the swap test's CLOSURE_CREATED.
        ("stewardship_accounts", "0022_drop_ministry_activity"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
