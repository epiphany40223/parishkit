"""Frozen file 0083: the daily digest ready row's funnel chart columns (#477).

The compile path that fills them (a real Production digest, the real server
renderer and the fan-out) is in ``test_daily_digest_as_of_postgresql``. Here:
the columns' check keeps the two images together, the replaced comparison
check lets a section have no images (charts withheld), and 0083's DO block
refuses a database without any of it.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
PNG = b"\x89PNG\r\n\x1a\n"


def check_block():
    """0083's grant capture, then its closing DO block, each whole."""
    text = (MIGRATIONS / "0083_digest_funnel_charts.sql").read_text(encoding="utf-8")
    start = text.index("SELECT set_config('stewardship.migration_0083_acl'")
    capture = text[start : text.index("), true);", start) + len("), true);")]
    block = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    return capture, block


def insert_ready(
    cursor, funnel, activity, comparison=None, families=None, pledges=None
):
    """Insert a bare ready row with the given chart columns.

    Replica mode skips the row's guard and foreign-key triggers, which would
    refuse an unowned row first, so only the table's checks are exercised.
    """
    cursor.execute("SET LOCAL session_replication_role = replica")
    cursor.execute(
        "INSERT INTO stewardship_daily_digest_ready (id, correlation_id, "
        "snapshot_id, fact_set_id, recipient_configuration_id, recipients, "
        "subject, html, text, chart, run_id, fence, worker_id, funnel_chart, "
        "activity_chart, comparison, comparison_families_chart, "
        "comparison_pledges_chart) VALUES (%s, %s, %s, %s, %s, '[]', 's', 'h', "
        "'t', %s, %s, 0, %s, %s, %s, %s, %s, %s)",
        [
            *(uuid4() for _ in range(5)),
            PNG,
            uuid4(),
            uuid4(),
            funnel,
            activity,
            comparison,
            families,
            pledges,
        ],
    )


@pytest.mark.parametrize(
    ("funnel", "activity", "accepted"),
    [
        (None, None, True),
        (PNG, PNG, True),
        (PNG, None, False),
        (None, PNG, False),
    ],
)
def test_the_funnel_charts_are_kept_together(funnel, activity, accepted):
    """Both images or neither; a lone one is refused."""
    with transaction.atomic(), connection.cursor() as cursor:
        if accepted:
            insert_ready(cursor, funnel, activity)
        else:
            with (
                pytest.raises(IntegrityError, match="daily_digest_funnel_charts"),
                transaction.atomic(),
            ):
                insert_ready(cursor, funnel, activity)
        transaction.set_rollback(True)


@pytest.mark.parametrize(
    ("comparison", "families", "pledges", "accepted"),
    [
        (None, None, None, True),
        ('{"charts": []}', None, None, True),
        ('{"charts": [1]}', PNG, None, True),
        ('{"charts": [1, 2]}', PNG, PNG, True),
        (None, PNG, None, False),
        ('{"charts": [1]}', None, PNG, False),
    ],
)
def test_a_section_may_be_kept_without_its_images(
    comparison, families, pledges, accepted
):
    """No images without a section, no pledges image without the Families one."""
    with transaction.atomic(), connection.cursor() as cursor:
        if accepted:
            insert_ready(cursor, None, None, comparison, families, pledges)
        else:
            with (
                pytest.raises(IntegrityError, match="daily_digest_comparison_charts"),
                transaction.atomic(),
            ):
                insert_ready(cursor, None, None, comparison, families, pledges)
        transaction.set_rollback(True)


@pytest.mark.parametrize(
    "undo",
    [
        "ALTER TABLE stewardship_daily_digest_ready "
        "DROP CONSTRAINT daily_digest_funnel_charts",
        "ALTER TABLE stewardship_daily_digest_ready DROP COLUMN activity_chart",
        "ALTER TABLE stewardship_daily_digest_ready "
        "ALTER COLUMN funnel_chart SET NOT NULL",
        # 0072's stricter comparison check, put back.
        "ALTER TABLE stewardship_daily_digest_ready "
        "DROP CONSTRAINT daily_digest_comparison_charts; "
        "ALTER TABLE stewardship_daily_digest_ready "
        "ADD CONSTRAINT daily_digest_comparison_charts CHECK ("
        "comparison IS NULL OR comparison_families_chart IS NOT NULL)",
        # 0072's context check, without the withheld chart words.
        "replaced-context",
    ],
)
def test_the_0083_check_refuses_a_database_without_the_columns(undo):
    """The DO block passes as installed and fails once any part is undone."""
    check = check_block()
    if undo == "replaced-context":
        text = (MIGRATIONS / "0072_digest_comparison.sql").read_text(encoding="utf-8")
        start = text.index(
            "CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1("
        )
        undo = text[start : text.index("$_$;", start) + len("$_$;")]
    capture, check = check
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(capture)
        cursor.execute(check)  # As installed, it passes.
        with (
            pytest.raises(DatabaseError, match="Migration 0083: "),
            transaction.atomic(),
        ):
            cursor.execute(undo)
            cursor.execute(check)
        cursor.execute(check)  # Rolled back: it passes again.
