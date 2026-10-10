"""Forward migration: settle held emails after a restore review (#757).

Its SQL is the frozen file
``schema/migrations/0048_restore_holds_after_release.sql``, read whole and
never parsed. It replaces ``stewardship_restore_resolution_v1`` with its
latest body plus two changes: a held-email decision is no longer refused once
the review is released (a hold of a campaign that is no longer current is
refused instead), and "send again" on a held reminder is refused once a later
reminder for the same Family reached it or is on its way. It also replaces
``stewardship_family_dispatch_live_v1`` so such a resent reminder is never
sent after a later one. Every other rule (undecided holds only, a fresh
Administrator, no resend while another restore's hold keeps the email back)
stays. Signatures, attributes and grants are unchanged, and the file ends
with a DO block that refuses to commit unless the new bodies are installed
as declared. No model changes, so there is no state operation. Reversing needs
its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0048_restore_holds_after_release.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0009_restore_begin_keeps_review"),
        # The migration that installed the previous frozen file (0047, #952),
        # so the files apply in prefix order.
        ("stewardship_accounts", "0011_portal_users"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
