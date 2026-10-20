"""Forward migration: Sent mail names emails to people by a Member (#1562).

Its SQL is the frozen file ``schema/migrations/0220_page_member_names.sql``
(prefix 0220, provisional until the repository-wide file sequence assigns
it), read whole and never parsed. It adds the Administrator-only definer
read ``stewardship_delivery_page_member_names_v1``: for a page's emails to
people, the ParishSoft Member name of each one's address, in the Users
page's default-Name order. It ends with a DO block that refuses to commit
unless the function is installed as declared. It changes no table, so it
has no state operation. Reversing needs its own forward migration.

It follows the users swap (#952), so its definer is granted only once it
is applied (runtime_grants.CLOSURE_FUNCTIONS).
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0220_page_member_names.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration (0120, #1592), so the app keeps
        # one leaf, and the migration that installs the latest earlier
        # frozen file (0125, #1763), so the files apply in prefix order.
        ("stewardship_jobs", "0035_leader_emails"),
        ("stewardship_accounts", "0030_secret_request_session"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
