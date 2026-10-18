"""Census gender is worked by hand (#1603).

The live ParishSoft v2 gender test of 2026-10-17 found that a one-character
``gender`` PUT is accepted but never changes the Member in any read, so
migration 0118 (``schema/migrations/0118_gender_by_hand.sql``) adds gender to
the derived guard's manual Member fields and re-marks open gender changes
recorded before it. These tests run the real response path and SQL guard,
Staff's worklist, the file's one-time correction against rows in the old
state, and its DO block against the body it replaces (0113's).
"""

from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.responses.member_census import GENDERS
from parishkit.stewardship.responses.models import ProposedChange

from .response_builders import activate_response_service
from .test_census_changes_postgresql import STAFF, submit_live, worklist
from .test_census_handling_registry_postgresql import (
    SEEDED_FAMILY,
    SEEDED_MEMBER,
    corrected_events,
    installed,
    seed,
)
from .test_ministry_responses_postgresql import respond, revisit, start
from .test_publication_worker_postgresql import _replaced
from .test_response_http_postgresql import answers_for
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = MIGRATIONS / "migrations/0118_gender_by_hand.sql"
GUARD = "stewardship_response_derived_guard_v1"


def correction():
    """The file's one-time correction: from disabling the guard to enabling it."""
    text = FROZEN.read_text(encoding="utf-8")
    start_at = text.index("ALTER TABLE public.stewardship_proposed_change DISABLE")
    return text[start_at : text.index("DO $check$")]


def check():
    """The file's closing DO block."""
    text = FROZEN.read_text(encoding="utf-8")
    return text[text.index("DO $check$") :]


def previous_guard():
    """0113's derived guard, the body this file replaces."""
    text = (MIGRATIONS / "migrations/0113_census_handling_registry.sql").read_text(
        encoding="utf-8"
    )
    return _replaced(text, GUARD)


def change_gender(harness):
    """Send a later response changing the fixture Member's gender, and return
    its gender proposal."""
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        member = answers["members"]["3"]
        member["gender"] = next(g for g in GENDERS if g != member.get("gender"))
        respond(harness, form, answers)
    return ProposedChange.objects.get(field="gender", execution="pending")


def test_the_replaced_guard_is_0113s_plus_only_the_gender_rule():
    """0118's guard is 0113's with 'gender' added to the manual Member fields
    and its comment, nothing else, and the fresh-install baseline matches."""
    current = _replaced(FROZEN.read_text(encoding="utf-8"), GUARD)
    old = previous_guard()
    fields = "('prefix','suffix','marital_status',"
    assert old.count(fields) == 1
    rebuilt = old.replace(fields, "('prefix','suffix','gender','marital_status',")
    rebuilt = rebuilt.replace(
        '-- removes one (JSON null or "") is By hand too.\n',
        '-- removes one (JSON null or "") is By hand too. The live\n'
        "                -- gender test (#1603): a gender PUT is accepted but never\n"
        "                -- changes the Member, so gender is By hand.\n",
        1,
    )
    assert rebuilt == current


def test_a_new_gender_change_is_by_hand_to_do(response_service):
    """A Family's gender change is stored By hand and shows in Staff's By hand
    work as To do; the response path and the SQL guard agree."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    gender = change_gender(harness)
    assert gender.handling == "manual"
    rows = worklist(harness, STAFF, status="all", route="by_hand")["rows"]
    shown = [row for row in rows if "Gender" in row["label"]]
    assert [(row["route_label"], row["status"]) for row in shown] == [
        ("By hand", "to_do")
    ]


def test_the_correction_re_marks_only_open_gender_rows(response_service):
    """Open Automatic gender rows stored before 0118 are re-marked By hand at
    the next version with their decision, edit and execution kept; queued
    and failed gender rows and other Automatic changes are left as they are,
    and one event records only the count. A second run changes nothing."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    base = ProposedChange.objects.get(field="first_name")
    member = {"entity_kind": "member", "field": "gender", "submitted_value": "Male"}
    pending = seed(base, **member, entity_key=SEEDED_MEMBER, decision="ignored")
    conflict = seed(
        base,
        **member,
        entity_key=SEEDED_FAMILY,
        decision="approved",
        admin_value_set=True,
        admin_value="Female",
        execution="conflict",
    )
    queued = seed(
        base,
        **member,
        entity_key="00000000-0000-4000-8000-000000000120",
        decision="approved",
        execution="queued",
    )
    failed = seed(
        base,
        **member,
        entity_key="00000000-0000-4000-8000-000000000121",
        execution="failed",
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(correction())
    for row in (pending, conflict):
        after = ProposedChange.objects.get(pk=row.pk)
        assert (after.handling, after.version) == ("manual", row.version + 1)
        assert (
            after.decision,
            after.admin_value_set,
            after.admin_value,
            after.execution,
        ) == (row.decision, row.admin_value_set, row.admin_value, row.execution)
    for row in (queued, failed, base):
        after = ProposedChange.objects.get(pk=row.pk)
        assert (after.handling, after.version, after.execution) == (
            "api",
            row.version,
            row.execution,
        )
    assert corrected_events() == [{"outcome": "changed", "count": 2}]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check())
        cursor.execute(correction())
    assert len(corrected_events()) == 1


def test_do_block_refuses_the_old_definition_and_leftover_rows(response_service):
    """The closing check passes as installed, raises once the derived guard is
    put back to 0113's body or its trigger is left disabled, and raises while
    an open gender row is still marked Automatic."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check())
    for statement in (
        previous_guard(),
        "ALTER TABLE public.stewardship_proposed_change"
        " DISABLE TRIGGER stewardship_proposed_change_guard",
    ):
        with (
            pytest.raises(DatabaseError, match="lacks the gender handling"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
            cursor.execute(check())
    base = ProposedChange.objects.get(field="first_name")
    seed(base, field="gender", entity_key=SEEDED_MEMBER, submitted_value="Male")
    with (
        pytest.raises(DatabaseError, match="still marked Automatic"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(check())


def test_the_old_guard_refused_a_by_hand_gender(response_service):
    """Under 0113's guard a gender change had to be stored as Automatic, so
    the registry change needs this migration: the response is refused."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    current = installed(GUARD)
    with connection.cursor() as cursor:
        cursor.execute(previous_guard())
    try:
        with pytest.raises(Exception, match="Proposal differs from its immutable"):
            change_gender(harness)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(current)
    assert not ProposedChange.objects.filter(field="gender").exists()
