"""Forward migration: a live campaign's share options may change their short
labels (#1569).

Its SQL is the frozen file ``schema/migrations/0167_share_short_labels.sql``
(prefix 0167, assigned in the repository-wide file sequence), read whole and
never parsed. It replaces ``stewardship_campaign_pointer_v1`` with 0103's
body, changing only the locked-structure check: a structurally locked
campaign's share options are compared without their ``short_label`` keys,
so a short label (Admin-only wording, outside the Family form and its
digest) can change on a live campaign and nothing else about the options
can. It ends with a DO block that refuses to commit unless the function is
installed as declared with its grants unchanged. No model changes, so there
is no state operation. Reversing needs its own forward migration, so, like
the baseline, it has no reverse operation.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0167_share_short_labels.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_campaigns", "0014_digest_skip_dates"),
        # The migration that installs the previous frozen file on main (0132,
        # the refresh checks), so the files apply in prefix order. 0133-0166
        # are assigned to other open changes; whichever merges last
        # re-points this.
        ("stewardship_workflows", "0004_refresh_checks"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
