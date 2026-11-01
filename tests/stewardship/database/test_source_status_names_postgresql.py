"""Frozen migration 0094's closing check (#1216), against the earlier bodies.

0094 replaces eleven selections so a Member or Family no longer in
ParishSoft gets no name and a blank one reads "Name not recorded in
ParishSoft". Its DO block must pass on the installed schema and refuse when
any selection is still on its earlier body or when a replaced selection's
grants or settings changed; and the whole file must install over the
earlier state. Each statement here runs in a transaction that is rolled
back.
"""

import re

import pytest
from django.db import DatabaseError, connection, transaction

from .test_hide_gone_members_postgresql import SCHEMA_FILES, definition

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = SCHEMA_FILES / "migrations"
MIGRATION = (MIGRATIONS / "0094_source_status_names.sql").read_text(encoding="utf-8")
CHECK = MIGRATION[MIGRATION.index("DO $check$") :]
# The statement recording the replaced selections' grants and settings,
# which the check compares against (a transaction-local setting).
ACL = MIGRATION[
    MIGRATION.index("SELECT set_config('stewardship.migration_0094_acl'") : (
        MIGRATION.index(", true);", MIGRATION.index("migration_0094_acl"))
        + len(", true);")
    )
]
REPLACED = re.findall(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", MIGRATION, re.M)
# Where each selection's previous body is: the frozen file that last
# replaced it before 0094.
EARLIER = {
    "stewardship_ministry_followup_v1": "0093_removed_ministry_gone_members.sql",
    "stewardship_ministry_packet_v1": "0093_removed_ministry_gone_members.sql",
    "stewardship_ministry_report_v1": "0093_removed_ministry_gone_members.sql",
    "stewardship_ministry_report_v3": "0093_removed_ministry_gone_members.sql",
    "stewardship_financial_report_v1": "0066_financial_staff_entered.sql",
    "stewardship_limitations_report_v1": "0056_retire_talents.sql",
    "stewardship_information_report_v1": "0045_report_duid_sorts.sql",
    "stewardship_information_report_v2": "0064_report_local_days.sql",
    "stewardship_directory_report_v1": "0022_directory_member_search.sql",
    "stewardship_directory_report_v2": "0050_directory_response_filters.sql",
}
# The weekly observation was never replaced before, so its earlier body is
# 0094's with the former 'Family' fallback put back.
WEEKLY_NEW = (
    "''),\n"
    "                -- The weekly report has no badge, so a Family the data no\n"
    "                -- longer has is named by its ParishSoft status (#1216).\n"
    "                CASE WHEN p.id IS NULL THEN 'Not in ParishSoft'\n"
    "                    ELSE 'Name not recorded in ParishSoft' END)\n"
)
WEEKLY_OLD = "''),'Family')\n"
# 0197 (#2335) has replaced both Additional information selections since,
# without the ParishSoft active branch 0094's check names, so the check
# tests put 0094's own bodies back first, rolled back with the test.
OWN = [
    definition(MIGRATION, name)
    for name in (
        "stewardship_information_report_v1",
        "stewardship_information_report_v2",
    )
]


def earlier(name):
    """The selection's definition before 0094, as CREATE OR REPLACE."""
    if name == "stewardship_weekly_observation_v1":
        own = definition(MIGRATION, name)
        assert own.count(WEEKLY_NEW) == 1
        return own.replace(WEEKLY_NEW, WEEKLY_OLD)
    return definition((MIGRATIONS / EARLIER[name]).read_text(encoding="utf-8"), name)


def check_refuses(statement, message):
    """Run ``statement`` and then 0094's closing check, which must refuse it."""
    with (
        pytest.raises(DatabaseError, match=f"Migration 0094 {message}"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(ACL)
        for own in OWN:
            cursor.execute(own)
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_closing_check_passes_on_the_installed_schema():
    """The check is the one the migration ran; it holds for 0094's bodies."""
    assert sorted(REPLACED) == sorted([*EARLIER, "stewardship_weekly_observation_v1"])
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(ACL)
        for own in OWN:
            cursor.execute(own)
        cursor.execute(CHECK)
        transaction.set_rollback(True)


@pytest.mark.parametrize("name", REPLACED)
def test_the_closing_check_refuses_each_earlier_body(name):
    """Any selection left on its earlier body is not installed as declared."""
    body = earlier(name)
    assert "'Name not recorded in ParishSoft'" not in body
    check_refuses(body, "\\(one phrase")


def test_the_closing_check_refuses_changed_grants():
    """CREATE OR REPLACE keeps grants; a change to one is refused."""
    check_refuses(
        "REVOKE ALL ON FUNCTION public.stewardship_ministry_packet_v1"
        "(uuid, bigint[], boolean, boolean, bigint[]) FROM PUBLIC",
        "changed the selections' grants or settings",
    )


def test_the_closing_check_refuses_changed_settings():
    """CREATE OR REPLACE names the same settings; a change to one is refused."""
    check_refuses(
        "ALTER FUNCTION public.stewardship_limitations_report_v1(uuid, jsonb) "
        "SET jit TO on",
        "changed the selections' grants or settings",
    )


def test_the_migration_installs_over_the_earlier_definitions():
    """Run whole over the earlier state, the frozen file installs and checks.

    Rolled back afterwards, so later tests see the installed schema.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL check_function_bodies = false")
        for name in REPLACED:
            cursor.execute(earlier(name))
        cursor.execute(MIGRATION)
        cursor.execute(ACL)
        cursor.execute(CHECK)
        transaction.set_rollback(True)
