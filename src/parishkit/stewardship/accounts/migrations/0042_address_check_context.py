"""Forward migration: the audit context of verified-address sign-in (#2343).

Its SQL is the frozen file ``schema/migrations/0203_address_check_context.sql``
(prefix 0203 in the repository-wide file sequence; renumbered at train time
if the sequence moved), read whole and never parsed. It replaces
``stewardship_safe_context_v1`` (0174's body) so the action context admits
``address_check``, the closed outcome word of a check of a Google account's
other verified addresses, and ``match_id``, the verified-address match a
re-check or a matched sign-in used. It ends with a DO block that refuses to
commit unless it is installed as declared with its grants unchanged. No
model changes, so there is no state operation. Reversing needs its own
forward migration, so, like the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0203_address_check_context.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The accounts app's previous migration (0200, the verified-address
        # matches), and the jobs app's last, which installs 0202 (the frozen
        # file just before this one's 0203).
        ("stewardship_accounts", "0041_address_match"),
        ("stewardship_jobs", "0043_family_signin_abuse_title"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
