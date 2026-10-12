"""Forward migration: the Ministry follow-up queue names Members "Last, first" (#932).

Its SQL is the frozen file ``schema/migrations/0069_member_names_last_first.sql``
(prefix 0069, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces ``stewardship_ministry_followup_v1`` so each
request's Member name is "Last, first" and the Member sort orders by surname;
the search still matches the "first last" form. It ends with a DO block that
refuses to commit unless the function is installed as declared with its
grants unchanged. No model changes, so there is no state operation. Reversing
needs its own forward migration, so, like the baseline, it has no reverse
operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0069_member_names_last_first.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The reports app's previous migration (it installed 0067, #1018).
        ("stewardship_reports", "0018_campaign_comparison"),
        # The migration that installs the previous frozen file (0068, #806),
        # so the files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file; it creates
        # no function or table, so nothing is registered for the swap.
        ("stewardship_jobs", "0024_mail_message_address_outcomes"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
