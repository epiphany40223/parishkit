"""Forward migration: mail dispatch reads one claimed message's token (#389 L1).

Its SQL is the frozen file ``schema/migrations/0073_dispatch_token_access.sql``,
read whole and never parsed. It adds one SECURITY DEFINER function,
``stewardship_dispatch_family_token_v1(uuid, uuid, uuid, bigint, uuid)``,
which answers the ciphertext and digest of one unsent Family message's own
token only while the caller's delivery task for that message is live, and
revokes the mail-dispatch login's column SELECT on the Family token and
rehearsal token ciphertext. It ends with a DO block that refuses to commit
unless both are as declared. No model changes, so there is no state
operation. Reversing needs its own forward migration, so, like the baseline,
it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0073_dispatch_token_access.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration.
        ("stewardship_jobs", "0024_mail_message_address_outcomes"),
        # The migration that installs the previous frozen file (0072, #1018
        # slice 7), so the files apply in prefix order. It follows the users
        # swap, so `migrate --users-upgrade-base` never applies this file.
        ("stewardship_reports", "0021_digest_comparison"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
