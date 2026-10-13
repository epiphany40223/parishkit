"""Frozen migration 0092's closing check (#1217), against earlier bodies.

0092 creates the Member standing functions and adds one filter to four
Ministry selections. Its DO block must pass on the installed schema and
refuse when any selection is still on its earlier body, when a new function
is missing or runs as its owner, or when a replaced selection's grants
changed; and the whole file must install over the earlier state. Each
statement here runs in a transaction that is rolled back.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA_FILES = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
MIGRATION = (SCHEMA_FILES / "migrations/0092_hide_gone_members.sql").read_text(
    encoding="utf-8"
)
CHECK = MIGRATION[MIGRATION.index("DO $check$") :]
# The statement recording the replaced selections' grants, which the check
# compares against (a transaction-local setting).
ACL = MIGRATION[
    MIGRATION.index("SELECT set_config('stewardship.migration_0092_acl'") : (
        MIGRATION.index(", true);", MIGRATION.index("migration_0092_acl"))
        + len(", true);")
    )
]
# The filter 0092 adds to the packet's latest requests (and to the reports'
# summary and rows).
FILTER = (
    "        -- Not a request for a Member no longer in ParishSoft (#1217).\n"
    "        AND (r.entity_kind<>'member' OR stewardship_member_source_status_v1(\n"
    "            x.source_id,r.entity_key,r.family_duid)='current')\n"
)
# Each replaced selection, with the frozen file holding its body before 0092
# (None: the baseline's, which is 0092's copy without the filter).
EARLIER = {
    "stewardship_ministry_followup_v1": "0069_member_names_last_first.sql",
    "stewardship_ministry_packet_v1": None,
    "stewardship_ministry_report_v1": "0045_report_duid_sorts.sql",
    "stewardship_ministry_report_v3": "0064_report_local_days.sql",
}
CREATED = (
    "stewardship_member_source_status_v1(uuid, text, bigint)",
    "stewardship_ministry_request_member_status_v1(uuid)",
)


def definition(source, name):
    """One function's definition in ``source``, as CREATE OR REPLACE."""
    start = re.search(
        rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(", source, re.M
    ).start()
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(source, start)
    end = source.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
    return re.sub(
        r"^CREATE (?:OR REPLACE )?FUNCTION",
        "CREATE OR REPLACE FUNCTION",
        source[start:end],
    )


def earlier_definition(name):
    """The selection's body before 0092, as CREATE OR REPLACE."""
    if EARLIER[name] is None:
        own = definition(MIGRATION, name)
        assert own.count(FILTER) == 1
        text = own.replace(FILTER, "")
    else:
        source = (SCHEMA_FILES / "migrations" / EARLIER[name]).read_text(
            encoding="utf-8"
        )
        text = definition(source, name)
    assert "stewardship_member_source_status_v1" not in text
    return text


def check_refuses(statement, message):
    """Run ``statement`` and then 0092's closing check, which must refuse it."""
    with (
        pytest.raises(DatabaseError, match=f"Migration 0092 {message}"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(ACL)
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_closing_check_passes_on_the_installed_schema():
    """The check is the one the migration ran; it still holds afterwards."""
    assert sorted(EARLIER) == sorted(
        re.findall(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", MIGRATION, re.M)
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(ACL)
        cursor.execute(CHECK)
        transaction.set_rollback(True)


@pytest.mark.parametrize("name", sorted(EARLIER))
def test_the_closing_check_refuses_each_earlier_definition(name):
    """With any one selection still on its pre-0092 body, the migration refuses."""
    check_refuses(earlier_definition(name), "\\(Members no longer in ParishSoft\\) did")


def test_the_closing_check_refuses_a_leader_rule_left_out():
    """The follow-up queue's filter letting anyone open any request by id
    (the leader rule dropped) is not installed as declared."""
    own = definition(MIGRATION, "stewardship_ministry_followup_v1")
    rule = "CASE WHEN operational AND (request_uuid IS NOT NULL"
    assert own.count(rule) == 1
    check_refuses(
        own.replace(rule, "CASE WHEN (request_uuid IS NOT NULL"),
        "\\(Members no longer in ParishSoft\\) did",
    )


@pytest.mark.parametrize("function", CREATED)
def test_the_closing_check_refuses_a_missing_or_definer_function(function):
    """Each new function is there, once, running with its caller's rights."""
    message = "\\(Members no longer in ParishSoft\\) is not installed"
    check_refuses(f"DROP FUNCTION public.{function}", message)
    check_refuses(f"ALTER FUNCTION public.{function} SECURITY DEFINER", message)


def test_the_closing_check_refuses_changed_grants():
    """CREATE OR REPLACE keeps grants; a change to one is refused."""
    check_refuses(
        "REVOKE ALL ON FUNCTION public.stewardship_ministry_packet_v1"
        "(uuid, bigint[], boolean, boolean, bigint[]) FROM PUBLIC",
        "changed the Ministry selections' grants",
    )


def test_the_migration_installs_over_the_earlier_definitions():
    """Run whole over the pre-0092 state, the frozen file installs and checks.

    Rolled back afterwards, so later tests see the installed schema.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL check_function_bodies = false")
        for name in sorted(EARLIER):
            cursor.execute(earlier_definition(name))
        for function in CREATED:
            cursor.execute(f"DROP FUNCTION public.{function}")
        cursor.execute(MIGRATION)
        cursor.execute(ACL)
        cursor.execute(CHECK)
        transaction.set_rollback(True)
