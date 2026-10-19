"""Frozen migration 0144's closing check (#1718).

0144 replaces the Ministry packet and report selections so a request that
review's Edit (#1684) carried into another Ministry is listed, with its
Member's contact details, under that Ministry. Its behaviour is tested in
test_ministry_contacts_postgresql.py; these tests check that its closing
check holds on the installed schema and refuses 0119's bodies, a body that
lists by the Family's Ministry again, and changed grants. Each statement
runs in a transaction that is rolled back.
"""

import re

import pytest
from django.db import DatabaseError, connection, transaction

from .test_hide_gone_members_postgresql import SCHEMA_FILES, definition

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATION = (SCHEMA_FILES / "migrations/0144_edited_ministry_contacts.sql").read_text(
    encoding="utf-8"
)
EARLIER = (SCHEMA_FILES / "migrations/0119_leader_request_contacts.sql").read_text(
    encoding="utf-8"
)
CHECK = MIGRATION[MIGRATION.index("DO $check$") :]
ACL = MIGRATION[
    MIGRATION.index("SELECT set_config('stewardship.migration_0144_acl'") : (
        MIGRATION.index(", true);", MIGRATION.index("migration_0144_acl"))
        + len(", true);")
    )
]
REPLACED = re.findall(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", MIGRATION, re.M)
CURRENT = "coalesce(r.reviewed_ministry_duid,r.ministry_duid) AS ministry_duid"


def check_refuses(statement, message):
    """Run ``statement`` and then 0144's closing check, which must refuse it."""
    with (
        pytest.raises(DatabaseError, match=f"Migration 0144 {message}"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(ACL)
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_closing_check_passes_on_the_installed_schema():
    """The check is the one the migration ran; it still holds afterwards."""
    assert sorted(REPLACED) == [
        "stewardship_ministry_packet_v1",
        "stewardship_ministry_report_v3",
    ]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(ACL)
        cursor.execute(CHECK)
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE proname = ANY(%s) AND NOT prosecdef",
            [REPLACED],
        )
        assert cursor.fetchone()[0] == 2
        transaction.set_rollback(True)


@pytest.mark.parametrize("name", REPLACED)
def test_the_closing_check_refuses_each_earlier_body(name):
    """A selection left on its 0119 body (the Family's Ministry) is refused."""
    earlier = definition(EARLIER, name)
    assert "reviewed_ministry_duid" not in earlier
    check_refuses(earlier, "\\(Ministry request details")


@pytest.mark.parametrize("name", REPLACED)
def test_the_closing_check_refuses_listing_by_the_familys_ministry(name):
    """A body that lists a request under the Family's Ministry again, its
    Edit ignored, is refused."""
    own = definition(MIGRATION, name)
    assert own.count(CURRENT) == 1
    check_refuses(
        own.replace(CURRENT, "r.ministry_duid AS ministry_duid"),
        "\\(Ministry request details",
    )


def test_the_closing_check_refuses_consent_for_an_earlier_version_elsewhere():
    """A report whose earlier versions keep the latest's consent under the
    Family's Ministry after an Edit carried the latest elsewhere is refused."""
    own = definition(MIGRATION, "stewardship_ministry_report_v3")
    clause = (
        "                AND coalesce(r.reviewed_ministry_duid,r.ministry_duid)\n"
        "                    =first_value(coalesce(r.reviewed_ministry_duid,"
        "r.ministry_duid))\n"
        "                    OVER (PARTITION BY s.family_id,r.entity_kind,"
        "r.entity_key,\n"
        "                        r.ministry_duid ORDER BY s.family_version DESC,"
        "r.id)\n"
    )
    assert own.count(clause) == 1
    check_refuses(own.replace(clause, ""), "\\(Ministry request details")


def test_the_closing_check_refuses_a_correlated_chain_read():
    """The chain prefilter names the campaign by its parameter, so it runs
    once; a body that correlates it with the outer row is refused."""
    for name in REPLACED:
        own = definition(MIGRATION, name)
        changed = own.replace("es.campaign_id=campaign_uuid", "es.campaign_id=x.id")
        assert changed != own
        check_refuses(changed, "\\(Ministry request details")


def test_the_closing_check_refuses_a_definer_body():
    """A selection made SECURITY DEFINER is refused."""
    check_refuses(
        "ALTER FUNCTION public.stewardship_ministry_report_v3"
        "(uuid, jsonb, boolean, bigint[], integer, text, integer, integer) "
        "SECURITY DEFINER",
        "\\(Ministry request details",
    )


def test_the_closing_check_refuses_changed_grants():
    """CREATE OR REPLACE keeps grants; a change to one is refused."""
    check_refuses(
        "REVOKE ALL ON FUNCTION public.stewardship_ministry_packet_v1"
        "(uuid, bigint[], boolean, boolean, bigint[]) FROM PUBLIC",
        "changed the selections' grants",
    )


def test_the_migration_installs_over_the_earlier_definitions():
    """Run whole over the 0119 state, the frozen file installs and checks.

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
