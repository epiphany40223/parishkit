"""Migration 0074: a menu logo may be a wide wordmark (#1062, slice 2).

The branding asset insert guard bounded every ``menu`` file within 128 x 128
pixels; 0074 lets it be up to 512 wide (still at most 128 tall), leaves every
other label's bounds alone and pins the guard's ``search_path``. These tests
run the frozen file against the guard as it was before (byte for byte the
baseline at 2038075e5, ``pre_wide_menu_logo_function.sql``) and prove its
closing check refuses that earlier state and anything else not installed as
declared.
"""

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from .campaign_builders import initialized
from .test_branding_postgresql import asset, bundle

pytestmark = pytest.mark.django_db(transaction=True)

ROOT = Path(__file__).resolve().parents[3]
FROZEN = (
    ROOT / "src/parishkit/stewardship/schema/migrations/0074_wide_menu_logo.sql"
).read_text(encoding="utf-8")
# The closing check, and the statement before it recording the guard's owner
# and grants, which the check compares against.
CHECK = FROZEN[FROZEN.index("DO $check$") :]
RECORD = FROZEN[
    FROZEN.index("SELECT set_config('stewardship.migration_0074_acl'") : FROZEN.index(
        "CREATE OR REPLACE FUNCTION"
    )
]
EARLIER_FILE = Path(__file__).with_name("pre_wide_menu_logo_function.sql")
EARLIER = EARLIER_FILE.read_text(encoding="utf-8")
GUARD = "public.stewardship_branding_asset_insert_v1()"


def test_the_earlier_guard_fixture_is_unchanged():
    """The fixture is the pre-0074 baseline body, so the tests below prove the
    migration on exactly what Production ran before it."""
    digest = hashlib.sha256(EARLIER.encode()).hexdigest()
    assert digest == "8b794a051ff3b1a1a83aba246277c0b8bb02f40ad4319dc46a474191e7fc9f47"
    assert "ELSE 128 END;" in EARLIER and "widest" not in EARLIER


def accepts(row, label, width, height):
    """Whether the guard admits a ``label`` file of ``width`` x ``height``."""
    try:
        with transaction.atomic():
            asset(row, label, id=uuid4(), width=width, height=height)
            transaction.set_rollback(True)
    except DatabaseError:
        return False
    return True


@pytest.mark.parametrize(
    ("label", "width", "height", "accepted"),
    [
        # A 4:1 wordmark at the widest stored menu size (#1062).
        ("menu", 512, 128, True),
        ("menu", 128, 128, True),
        ("menu", 513, 128, False),
        ("menu", 128, 129, False),
        # Every other label keeps its square bound.
        ("icon", 128, 128, True),
        ("icon", 129, 32, False),
        ("favicon", 32, 32, True),
        ("favicon", 33, 8, False),
        ("large", 1024, 1024, True),
        ("large", 1025, 512, False),
        ("section", 256, 256, True),
        ("section", 257, 64, False),
        ("banner", 1024, 512, True),
        ("banner", 1025, 512, False),
    ],
)
def test_each_label_keeps_its_bounds(tmp_path, label, width, height, accepted):
    """Only the menu label gains width; its height and the rest are unchanged."""
    _, version, actor = initialized(tmp_path)
    assert accepts(bundle(version, actor), label, width, height) is accepted


def test_the_file_widens_the_menu_bound_on_the_earlier_guard(tmp_path):
    """On the guard as it was, a 512-pixel menu file is refused; once the
    frozen file runs in the same transaction, it is accepted."""
    _, version, actor = initialized(tmp_path)
    row = bundle(version, actor)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(EARLIER)
        assert not accepts(row, "menu", 512, 128)
        assert accepts(row, "menu", 128, 128)
        with connection.cursor() as cursor:
            cursor.execute(FROZEN)
        assert accepts(row, "menu", 512, 128)
        assert not accepts(row, "menu", 513, 128)
        transaction.set_rollback(True)


def check_refuses(statement, message):
    """Record the guard's grants, run ``statement``, then 0074's closing check,
    which must refuse."""
    with (
        pytest.raises(DatabaseError, match=message),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(RECORD)
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_closing_check_passes_on_the_installed_schema():
    """The check is the one the migration ran; it still holds afterwards."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(RECORD)
        cursor.execute(CHECK)
        transaction.set_rollback(True)


@pytest.mark.parametrize(
    "statement",
    [
        EARLIER,
        # The new body without the pinned search_path.
        f"ALTER FUNCTION {GUARD} RESET search_path",
        f"ALTER FUNCTION {GUARD} SECURITY DEFINER",
        f"ALTER FUNCTION {GUARD} STABLE",
    ],
    ids=["earlier-body", "no-search-path", "definer", "stable"],
)
def test_the_closing_check_refuses_an_undeclared_guard(statement):
    """The earlier body, or a changed attribute, is refused."""
    check_refuses(statement, "is not installed as declared")


def test_the_closing_check_refuses_changed_grants():
    """CREATE OR REPLACE keeps the guard's grants; a change is refused."""
    check_refuses(f"REVOKE EXECUTE ON FUNCTION {GUARD} FROM PUBLIC", "owner or grants")


def test_the_closing_check_refuses_a_missing_trigger():
    """The asset table's insert trigger must still call the guard."""
    check_refuses(
        "DROP TRIGGER stewardship_branding_asset_insert_v1 "
        "ON public.stewardship_branding_asset",
        "insert trigger is missing",
    )
