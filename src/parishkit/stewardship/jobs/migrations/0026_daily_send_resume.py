"""Forward migration: when Family email resumes after the daily limit (#530).

Its SQL is the frozen file ``schema/migrations/0076_daily_send_resume.sql``
(prefix 0076 in the repository-wide file sequence), read whole and never
parsed. It creates ``stewardship_family_daily_resume_v1()``, a SECURITY
DEFINER read that returns, to the web login only, the earliest instant at
which the mail consumers' rolling 24-hour Family mail count falls below the
Family limit (NULL when it is already below), without the web reading any
recipient address, and ends with a DO block that refuses to commit unless
the function is installed as declared. No model changes, so there is no
state operation. Reversing needs its own forward migration.

It follows the users swap (#952), so its function is granted only once it
is applied. It alters no table.
"""

from pathlib import Path

from django.db import migrations

FROZEN_SQL = (
    Path(__file__).resolve().parents[2]
    / "schema"
    / "migrations"
    / "0076_daily_send_resume.sql"
)


class Migration(migrations.Migration):
    dependencies = [
        # The jobs app's previous migration.
        ("stewardship_jobs", "0025_dispatch_token_access"),
        # The migration that installs the previous frozen file (0075, #1115),
        # so the files apply in prefix order. It follows the users swap, so
        # `migrate --users-upgrade-base` never applies this file and its
        # function is registered in runtime_grants.CLOSURE_FUNCTIONS.
        ("stewardship_accounts", "0020_campaign_series"),
    ]

    operations = [migrations.RunSQL(FROZEN_SQL.read_text(encoding="utf-8"))]
