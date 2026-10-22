"""Frozen file 0072's DO block, run against the bodies it replaced (#1018).

Kept from the Daily digest comparison suite when report emails replaced the
digests (#1786): the migration check guards the comparison functions and
the chart timeout context the report pipeline still uses, whatever sends
them.
"""

from pathlib import Path

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)


# 0072's own comparison chart check, which 0083 (#477) relaxed so a section
# may be kept without images; 0072's DO block checks for this one.
CHECK_0072 = (
    "ALTER TABLE public.stewardship_daily_digest_ready DROP CONSTRAINT "
    "daily_digest_comparison_charts; "
    + next(
        line
        for line in (
            Path(__file__).resolve().parents[3]
            / "src/parishkit/stewardship/schema/migrations/0072_digest_comparison.sql"
        )
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith(
            "ALTER TABLE public.stewardship_daily_digest_ready ADD CONSTRAINT"
        )
    )
)


def replaced(name, function):
    """One function's definition from a frozen file, as CREATE OR REPLACE."""
    from ..test_schema_migration_files import function_bodies

    text = (MIGRATIONS / name).read_text(encoding="utf-8")
    bodies = function_bodies(text, replace=False) | function_bodies(text, replace=True)
    return "CREATE OR REPLACE FUNCTION" + bodies[function]


@pytest.mark.parametrize(
    ("installs", "refusal"),
    [
        (
            [("0065_staff_entered.sql", "stewardship_safe_context_v1")],
            "does not admit the chart timeout",
        ),
        (
            [
                ("0067_campaign_comparison.sql", "stewardship_campaign_comparison_v1"),
                (
                    "0067_campaign_comparison.sql",
                    "stewardship_campaign_archive_renewal_v1",
                ),
            ],
            "comparison functions are not installed",
        ),
    ],
)
def test_the_0072_check_refuses_the_old_bodies(installs, refusal):
    """Each replaced body, put back as it was, fails 0072's DO block."""
    from django.db import DatabaseError, transaction

    from .test_campaign_comparison_postgresql import ACL_NOW

    text = (MIGRATIONS / "0072_digest_comparison.sql").read_text(encoding="utf-8")
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(ACL_NOW)
        # The table as 0072 left it (rolled back below); 0083 changes it.
        cursor.execute(CHECK_0072)
        cursor.execute(check)  # The installed bodies pass.
        with (
            pytest.raises(DatabaseError, match=f"Migration 0072: .*{refusal}"),
            transaction.atomic(),
        ):
            for name, function in installs:
                cursor.execute(replaced(name, function))
            cursor.execute(check)
        cursor.execute(check)  # Rolled back: the installed bodies pass again.
        transaction.set_rollback(True)


def test_the_0072_check_refuses_a_body_without_0065s_sorts():
    """0072's body built on 0059's (a stale restack) fails its DO block.

    The chart words alone are not enough: the body must keep 0065's Entered
    by Staff sort tokens, or a restacked file would silently revert them.
    """
    from django.db import DatabaseError, transaction

    from .test_campaign_comparison_postgresql import ACL_NOW

    text = (MIGRATIONS / "0072_digest_comparison.sql").read_text(encoding="utf-8")
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    body = replaced("0072_digest_comparison.sql", "stewardship_safe_context_v1")
    tokens = ",'staff_entered','-staff_entered'"
    assert body.count(tokens) == 1
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(ACL_NOW)
        cursor.execute(CHECK_0072)  # As 0072 left the table; rolled back below.
        with (
            pytest.raises(DatabaseError, match="Migration 0072: .*chart timeout"),
            transaction.atomic(),
        ):
            cursor.execute(body.replace(tokens, ""))
            cursor.execute(check)
        cursor.execute(check)  # Rolled back: the installed body passes again.
        transaction.set_rollback(True)
