"""Each logo bundle's Basic or Advanced mode on PostgreSQL (#1062, migration 0082).

The column admits only the two modes (or none, for campaign artwork and
earlier bundles), and the migration's closing check refuses a database
without the column or its check. Each test that changes the schema runs in a
transaction it rolls back, so later tests see the installed schema.
"""

from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0082_branding_bundle_mode.sql"
).read_text(encoding="utf-8")
CHECK = MIGRATION[MIGRATION.index("DO $check$") :]
DROP = "ALTER TABLE public.stewardship_branding_bundle DROP COLUMN mode"


def check_refuses(statement, message):
    """Run ``statement`` and then 0082's closing check, which must refuse it."""
    with (
        pytest.raises(DatabaseError, match=f"Migration 0082: {message}"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_closing_check_passes_on_the_installed_schema():
    """The check is the one the migration ran; it still holds afterwards."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CHECK)


def test_the_closing_check_refuses_a_missing_column_or_check():
    """Without the column, or with it but no check, the migration refuses."""
    check_refuses(DROP, "stewardship_branding_bundle.mode is missing")
    check_refuses(
        "ALTER TABLE public.stewardship_branding_bundle "
        "DROP CONSTRAINT branding_bundle_mode",
        "the branding bundle mode check is missing",
    )
    # A check without its NULL branch would refuse every earlier bundle.
    check_refuses(
        "ALTER TABLE public.stewardship_branding_bundle "
        "DROP CONSTRAINT branding_bundle_mode, "
        "ADD CONSTRAINT branding_bundle_mode CHECK (mode IN ('basic', 'advanced'))",
        "the branding bundle mode check is missing",
    )
    check_refuses(
        "ALTER TABLE public.stewardship_branding_bundle "
        "ALTER COLUMN mode SET DEFAULT 'basic'",
        "stewardship_branding_bundle.mode is missing",
    )


def test_the_mode_check_admits_only_the_two_modes():
    """Only basic, advanced or none: the check names exactly those."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='public.stewardship_branding_bundle'::regclass "
            "AND conname='branding_bundle_mode'"
        )
        [(definition,)] = cursor.fetchall()
    assert definition == (
        "CHECK (((mode IS NULL) OR ((mode)::text = ANY (ARRAY[('basic'::character "
        "varying)::text, ('advanced'::character varying)::text]))))"
    )


def test_the_migration_installs_over_the_earlier_table():
    """Run whole over the pre-0082 table, the frozen file installs and checks."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(DROP)
        with pytest.raises(DatabaseError, match="Migration 0082"), transaction.atomic():
            cursor.execute(CHECK)
        cursor.execute(MIGRATION)
        cursor.execute(CHECK)
        transaction.set_rollback(True)
