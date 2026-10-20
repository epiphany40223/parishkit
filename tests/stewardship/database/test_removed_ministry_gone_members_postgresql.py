"""Frozen migration 0093's closing check (#1219), against 0092's bodies.

0093 adds the Member test to the removed-Ministry arm of four Ministry
selections. Its DO block must pass on the installed schema and refuse when
any selection is still on its 0092 body, when the follow-up queue lost its
Staff exception, or when a replaced selection's grants changed; and the
whole file must install over the 0092 state. Each statement here runs in a
transaction that is rolled back.
"""

import re

import pytest
from django.db import DatabaseError, connection, transaction

from .test_hide_gone_members_postgresql import SCHEMA_FILES, definition

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATION = (
    SCHEMA_FILES / "migrations/0093_removed_ministry_gone_members.sql"
).read_text(encoding="utf-8")
EARLIER = (SCHEMA_FILES / "migrations/0092_hide_gone_members.sql").read_text(
    encoding="utf-8"
)
CHECK = MIGRATION[MIGRATION.index("DO $check$") :]
# The statement recording the replaced selections' grants, which the check
# compares against (a transaction-local setting).
ACL = MIGRATION[
    MIGRATION.index("SELECT set_config('stewardship.migration_0093_acl'") : (
        MIGRATION.index(", true);", MIGRATION.index("migration_0093_acl"))
        + len(", true);")
    )
]
REPLACED = re.findall(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", MIGRATION, re.M)
# 0131 (#1684) replaced the follow-up selection again, scoping a request by
# its edited Ministry too, which rewrites the arm 0093's check reads. The
# checks below put back the body 0131 replaced (0094's) first, rolled back
# with them, so they test 0093's own result.
LATER = definition(
    (SCHEMA_FILES / "migrations/0094_source_status_names.sql").read_text(
        encoding="utf-8"
    ),
    "stewardship_ministry_followup_v1",
)
# 0140 (#1718) has since replaced the packet and report selections again,
# changing the removed-Ministry text this check reads; 0119's bodies, the
# ones before it, still hold that text.
CONSENT = (SCHEMA_FILES / "migrations/0119_leader_request_contacts.sql").read_text(
    encoding="utf-8"
)
LATER_SELECTIONS = [
    LATER,
    *(
        definition(CONSENT, name)
        for name in ("stewardship_ministry_packet_v1", "stewardship_ministry_report_v3")
    ),
]


def check_refuses(statement, message):
    """Run ``statement`` and then 0093's closing check, which must refuse it."""
    with (
        pytest.raises(DatabaseError, match=f"Migration 0093 {message}"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL check_function_bodies = false")
        for later in LATER_SELECTIONS:
            cursor.execute(later)
        cursor.execute(ACL)
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_closing_check_passes_on_the_installed_schema():
    """The check is the one the migration ran; it still holds afterwards."""
    assert sorted(REPLACED) == [
        "stewardship_ministry_followup_v1",
        "stewardship_ministry_packet_v1",
        "stewardship_ministry_report_v1",
        "stewardship_ministry_report_v3",
    ]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL check_function_bodies = false")
        for later in LATER_SELECTIONS:
            cursor.execute(later)
        cursor.execute(ACL)
        cursor.execute(CHECK)
        transaction.set_rollback(True)


@pytest.mark.parametrize("name", REPLACED)
def test_the_closing_check_refuses_each_earlier_body(name):
    """Any selection left on its 0092 body is not installed as declared."""
    earlier = definition(EARLIER, name)
    assert "f.family_duid)='current'" not in earlier
    check_refuses(earlier, "\\(removed Ministries")


def test_the_closing_check_refuses_the_staff_exception_left_out():
    """The follow-up queue hiding the removed Ministry from Staff's own views
    of those requests too is not installed as declared."""
    own = definition(MIGRATION, "stewardship_ministry_followup_v1")
    exception = (
        "              OR (operational AND (request_uuid IS NOT NULL\n"
        "                  OR coalesce(filters->>'presence','current') "
        "IN ('gone','any')))\n"
    )
    assert own.count(exception) == 1
    check_refuses(own.replace(exception, ""), "\\(removed Ministries")


def test_the_closing_check_refuses_changed_grants():
    """CREATE OR REPLACE keeps grants; a change to one is refused."""
    check_refuses(
        "REVOKE ALL ON FUNCTION public.stewardship_ministry_packet_v1"
        "(uuid, bigint[], boolean, boolean, bigint[]) FROM PUBLIC",
        "changed the Ministry selections' grants",
    )


def test_the_migration_installs_over_the_earlier_definitions():
    """Run whole over the 0092 state, the frozen file installs and checks.

    Rolled back afterwards, so later tests see the installed schema.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL check_function_bodies = false")
        for name in REPLACED:
            cursor.execute(definition(EARLIER, name))
        cursor.execute(MIGRATION)
        cursor.execute(ACL)
        cursor.execute(CHECK)
        transaction.set_rollback(True)
