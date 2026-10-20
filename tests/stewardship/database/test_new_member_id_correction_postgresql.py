"""Correcting a New Member's ParishSoft Member ID (#1773), in PostgreSQL.

A New Member Staff mark Entered by hand names the ParishSoft Member ID the
refresh confirms it by (#1749). While no refresh has confirmed it, Staff
correct a mistyped ID, or give one the mark lacked, by marking it Entered
by hand again with another ID (review pipeline spec, "Refresh checks").
Migration 0139 (provisional number) admits that resolution; the changes are
made through the real service and page on the web login and re-evaluated by
real promotions.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.reports.census_worklist import worklist_rows
from parishkit.stewardship.responses.census_resolution import InvalidMemberId
from parishkit.stewardship.responses.models import ProposalResolution, ProposedChange

from ..census_factory import member
from .test_background_grants_postgresql import task_login
from .test_census_refresh_sources_postgresql import answered, refresh
from .test_census_review_postgresql import fresh
from .test_census_worklist_page_postgresql import ROUTE, decide, staff_browser
from .test_entered_incorrectly_postgresql import forge
from .test_information_followup_postgresql import search
from .test_published_refresh_postgresql import (
    ADDED,
    _body,
    mark,
    new_member,
    with_added,
)
from .test_refresh_checks_postgresql import (  # noqa: F401 (started: fixture)
    census_row,
    checks,
    started,
)

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0139_new_member_id_correction.sql"
)
# A mistyped ParishSoft Member ID: no Member the data lists.
WRONG = 8


def marks(change):
    """The Member IDs of ``change``'s marks, oldest first."""
    return list(
        ProposalResolution.objects.filter(proposal=change)
        .order_by("expected_version")
        .values_list("member_duid", flat=True)
    )


def test_a_mistyped_member_id_is_corrected_and_then_confirmed(
    response_service,
    started,  # noqa: F811
):
    """A wrong ID is unchanged and Not seen after two full refreshes; the
    correction is a new mark at the change's version, which keeps it Entered
    by hand, clears Not seen (its count starts from the new mark), names the
    new ID under the stage, and the refresh listing that Member confirms
    it."""
    harness, staff, rows = answered(response_service, new_member)
    change = mark(harness, staff, rows["new_member"], str(WRONG))
    refresh(harness)
    refresh(harness)
    row = census_row(harness, change)
    assert row["stage"] == Stage.ENTERED_BY_HAND and row["not_seen"]
    corrected = mark(harness, staff, fresh(change), f" {ADDED} ")
    assert marks(change) == [WRONG, ADDED]
    assert (corrected.execution, corrected.version) == (
        "resolved_external",
        change.version + 1,
    )
    assert corrected.decision == change.decision
    row = census_row(harness, corrected)
    assert row["stage"] == Stage.ENTERED_BY_HAND and not row["not_seen"]
    (shown,) = worklist_rows([row])
    assert shown["stage_note"] == f"ParishSoft Member ID {ADDED}."
    with_added(harness)
    assert checks(proposal=change)[-1] == "confirmed"
    assert census_row(harness, change)["stage"] == Stage.CONFIRMED


def test_a_mark_made_without_an_id_gains_one(response_service):
    """A mark with no ID has no check; giving one lets the refresh confirm
    it."""
    harness, staff, rows = answered(response_service, new_member)
    change = mark(harness, staff, rows["new_member"], "")
    with_added(harness)
    assert checks(proposal=change) == []
    change = mark(harness, staff, fresh(change), str(ADDED))
    assert marks(change) == [None, ADDED]
    with_added(harness)
    assert checks(proposal=change) == ["confirmed"]


def test_the_correction_needs_a_new_valid_id(response_service):
    """The same ID again, a blank one, or one the first mark would refuse
    (a Member the Family listed, not a whole number) records nothing."""
    harness, staff, rows = answered(response_service, new_member)
    change = mark(harness, staff, rows["new_member"], str(WRONG))
    with pytest.raises(InvalidMemberId, match="already recorded"):
        mark(harness, staff, change, f"000{WRONG}")
    with pytest.raises(
        InvalidMemberId, match="Enter the ParishSoft Member ID"
    ) as blank:
        mark(harness, staff, change, "  ")
    assert blank.value.correcting
    for member_id in ("3", "x"):
        with pytest.raises(InvalidMemberId):
            mark(harness, staff, change, member_id)
    assert marks(change) == [WRONG]
    assert fresh(change).version == change.version


def test_a_confirmed_new_member_is_not_corrected(response_service):
    """Once a refresh confirmed the Member, the mark is final: the service
    and SQL refuse another."""
    harness, staff, rows = answered(response_service, new_member)
    change = mark(harness, staff, rows["new_member"], str(ADDED))
    with_added(harness)
    assert census_row(harness, change)["stage"] == Stage.CONFIRMED
    with pytest.raises(ValueError, match="cannot be resolved that way now"):
        mark(harness, staff, fresh(change), str(WRONG))
    with pytest.raises((IntegrityError, DatabaseError), match="lacks current"):
        forge_mark(fresh(change), staff, WRONG)
    assert marks(change) == [ADDED]


def forge_mark(proposal, actor, member_duid, action="entered"):
    """A resolution row naming ``member_duid``, straight to the table as the
    web login, bypassing the service."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_proposal_resolution (id,correlation_id,"
            "actor_id,proposal_id,expected_version,request_key,action,note,"
            "member_duid) VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,%s,"
            "gen_random_uuid(),%s,'',%s)",
            [actor, proposal.pk, proposal.version, action, member_duid],
        )


@pytest.mark.parametrize(
    ("member_duid", "action"),
    [
        # The same ID as the mark in force: no correction.
        (WRONG, "entered"),
        # No ID: the mark in force stays.
        (None, "entered"),
        # A Member the Family's response listed.
        (3, "entered"),
        # Only a mark, never a reopen or an Ignore, of an entered New Member.
        (None, "reopened"),
        (None, "ignored"),
    ],
)
def test_sql_refuses_a_forged_correction(response_service, member_duid, action):
    """The resolution guard holds with the service bypassed."""
    harness, staff, rows = answered(response_service, new_member)
    change = mark(harness, staff, rows["new_member"], str(WRONG))
    with pytest.raises((IntegrityError, DatabaseError), match="lacks current"):
        forge_mark(change, staff, member_duid, action)
    assert marks(change) == [WRONG]


def test_sql_refuses_another_new_members_id(response_service):
    """A correction may not name the ID another New Member of the Family is
    entered as."""

    def edit(answers):
        """Two New Members."""
        new_member(answers)
        new_member(answers)

    harness, staff, _ = answered(response_service, edit)
    first, second = ProposedChange.objects.filter(field="new_member").order_by("pk")
    mark(harness, staff, first, str(ADDED))
    second = mark(harness, staff, second, str(WRONG))
    with pytest.raises(InvalidMemberId, match="Another New Member"):
        mark(harness, staff, second, str(ADDED))
    with pytest.raises((IntegrityError, DatabaseError), match="lacks current"):
        forge_mark(second, staff, ADDED)
    assert marks(second) == [WRONG]


def test_an_entered_member_field_takes_no_correction(response_service):
    """Only a New Member's mark is made again this way: an entered Member
    field not found Entered incorrectly takes no second mark, with or
    without an ID."""
    from .test_census_pipeline_review_postgresql import decide as census_decide
    from .test_refresh_checks_postgresql import prepared

    harness, staff, rows = prepared(response_service)
    census_decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    prefix = fresh(rows["prefix"])
    with pytest.raises((IntegrityError, DatabaseError), match="lacks current"):
        forge(prefix, staff, "entered")
    with pytest.raises((IntegrityError, DatabaseError), match="lacks current"):
        forge_mark(prefix, staff, ADDED)


def test_a_replaced_new_member_is_not_corrected(response_service):
    """A later response that answers the New Member again makes a change of
    its own; the earlier one is history and takes no correction."""
    from .test_ministry_responses_postgresql import respond, revisit
    from .test_response_http_postgresql import answers_for
    from .test_runtime_auth_grants_postgresql import web_login

    harness, staff, rows = answered(response_service, new_member)
    old = mark(harness, staff, rows["new_member"], str(WRONG))
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        # The same New Member (its key), answered with another first name.
        answers["proposed_members"][old.entity_key] = member(first_name="Renamed")
        answers["ministries"]["proposed_members"][old.entity_key] = {"join": []}
        respond(harness, form, answers)
    assert ProposedChange.objects.filter(
        field="new_member", entity_key=old.entity_key
    ).exclude(pk=old.pk)
    row = census_row(harness, old)
    assert row["replaced"] and row["stage"] == Stage.ENTERED_BY_HAND
    (shown,) = worklist_rows([row])
    assert shown["remark"] is None
    with pytest.raises(ValueError, match="cannot be resolved that way now"):
        mark(harness, staff, fresh(old), str(ADDED))
    with pytest.raises((IntegrityError, DatabaseError), match="lacks current"):
        forge_mark(fresh(old), staff, ADDED)


def test_the_page_offers_and_records_the_correction(response_service, google):
    """Over HTTP: the row draws the correction icon and its dialog; the same
    ID is refused at the field (a 400 naming value_member); a new one is
    recorded with its notice; a bulk Entered by hand of the row is refused
    (one row at a time)."""
    harness, _, rows = answered(response_service, new_member)
    browser = staff_browser(google)
    change = rows["new_member"]
    response, body = search(
        browser, ROUTE, decide(change, "entered_by_hand", value_member=str(WRONG))
    )
    assert response.status_code == 200
    change = fresh(change)
    response, body = search(browser, ROUTE, {"stage": "all"})
    page = body.decode()
    assert "Correct the ParishSoft Member ID of" in page
    assert f'id="census-entered-dialog-{change.pk}"' in page
    response, body = search(
        browser, ROUTE, decide(change, "entered_by_hand", value_member=str(WRONG))
    )
    assert response.status_code == 400
    refusal = json.loads(body)
    assert [error["field_id"] for error in refusal["errors"]] == ["value_member"]
    assert refusal["refusal"]["message"] == (
        "That is the ParishSoft Member ID already recorded. Enter the corrected ID."
    )
    bulk = {
        "stage": "all",
        "bulk": "entered_by_hand",
        "bulk_key": str(uuid4()),
        "bulk_note": "",
        "item": [f"{change.pk}:{change.version}"],
    }
    assert search(browser, ROUTE, bulk)[0].status_code == 400
    # A blank correction is refused at the field too.
    response, body = search(
        browser, ROUTE, decide(change, "entered_by_hand", value_member=" ")
    )
    assert response.status_code == 400
    blank = json.loads(body)
    assert [error["field_id"] for error in blank["errors"]] == ["value_member"]
    assert blank["refusal"]["fix"] == "Correct the Member ID, then record it again."
    # The census guard admits a final change's update only from the web
    # login (as Production's web runs), so this post runs on it.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(
            browser, ROUTE, decide(change, "entered_by_hand", value_member=str(ADDED))
        )
    assert response.status_code == 200
    assert b"had its ParishSoft Member ID corrected." in body
    assert marks(change) == [WRONG, ADDED]
    assert refusal["refusal"]["fix"] == "Correct the Member ID, then record it again."


def test_the_id_is_corrected_again_and_freed_for_another_new_member(
    response_service,
):
    """A second correction reads the mark now in force (the version before
    the change's current one); an ID corrected away is free for another New
    Member of the Family."""

    def edit(answers):
        """Two New Members."""
        new_member(answers)
        new_member(answers)

    harness, staff, _ = answered(response_service, edit)
    first, second = ProposedChange.objects.filter(field="new_member").order_by("pk")
    first = mark(harness, staff, first, str(ADDED))
    first = mark(harness, staff, first, str(WRONG))
    with pytest.raises(InvalidMemberId, match="already recorded"):
        mark(harness, staff, first, str(WRONG))
    first = mark(harness, staff, first, "9")
    assert marks(first) == [ADDED, WRONG, 9]
    second = mark(harness, staff, second, str(ADDED))
    assert marks(second) == [ADDED]


def test_sql_refuses_a_stale_or_unpaired_correction(response_service):
    """A correction at another version is refused at once; one with no
    paired update and audit event is refused when the work commits (0060's
    deferred effect check)."""
    from parishkit.stewardship.campaigns.work_locks import work_transaction

    harness, staff, rows = answered(response_service, new_member)
    change = mark(harness, staff, rows["new_member"], str(WRONG))
    for version in (change.version - 1, change.version + 1):
        stale = ProposedChange(pk=change.pk, version=version)
        with pytest.raises((IntegrityError, DatabaseError), match="lacks current"):
            forge_mark(stale, staff, ADDED)
    with (
        pytest.raises((IntegrityError, DatabaseError)),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_proposal_resolution (id,correlation_id,"
            "actor_id,proposal_id,expected_version,request_key,action,note,"
            "member_duid) VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,%s,"
            "gen_random_uuid(),'entered','',%s)",
            [staff, change.pk, change.version, ADDED],
        )
    assert marks(change) == [WRONG]
    assert fresh(change).version == change.version


def _check():
    """0139's closing DO block, as the file has it."""
    text = FROZEN.read_text(encoding="utf-8")
    return text[text.index("DO $check$") :]


def test_do_block_refuses_the_old_definition(db):
    """The closing check passes as installed and raises once the guard is
    put back to 0138's body."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(_check())
    with (
        pytest.raises(DatabaseError, match="lacks the New Member ID correction"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            _body(
                "0138_published_refresh_conflicts.sql",
                "stewardship_proposal_resolution_guard_v1",
            )
        )
        cursor.execute(_check())
