"""Census changes on the review pipeline: the worklist's decisions (#1683).

Staff and Administrators review every census change, By hand ones too:
Accept, Edit (Accept with a value, a Family address included), Ignore, Back
to review (a note to reopen an ignored change) and Entered by hand, one by
one or in bulk, all or none (``census_review.decide_census_changes``). The
frozen file 0131 widens the review guard for them; its own rules are tested
here with the service bypassed.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.census_review import (
    BulkStale,
    InvalidEdit,
    decide_census_changes,
)
from parishkit.stewardship.responses.models import (
    ProposalResolution,
    ProposalReview,
    ProposedChange,
)

from ..census_factory import address
from ..policy_factory import address as rule
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_census_conflict_postgresql import conflicted
from .test_census_resolution_postgresql import leader
from .test_census_review_postgresql import fresh
from .test_ministry_responses_postgresql import respond, start
from .test_policy_postgresql import user
from .test_publication_plans_postgresql import owner
from .test_publication_worker_postgresql import _replaced
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0131_census_pipeline_review.sql"
)


def prepared(harness):
    """A live response with Automatic and By hand changes, and a Staff member.

    Member 3's first name (Automatic) and prefix (By hand), and the Family's
    home address and email opt-out (By hand).
    """
    start(harness)
    harness = activate_response_service(harness)
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"]["first_name"] = "Requested"
        answers["members"]["3"]["prefix"] = "Dr."
        answers["family"]["home_address"] = address(line1="9 Census Way")
        answers["family"]["email_opt_out"] = True
        respond(harness, form, answers)
    admin = user("admin@example.org")
    store = harness.service.store
    change(
        store,
        store.active(),
        admin.pk,
        [
            {"operation": "add", "section": "login_rules", **item}
            for item in (rule("staff@example.org", ("staff",)),)
        ],
    )
    rows = {row.field: row for row in ProposedChange.objects.all()}
    return harness, admin.pk, user("staff@example.org").pk, rows


def decide(harness, actor, rows, action, *, note="", value=None, bulk=None, key=None):
    """One worklist decision through the service on the restricted web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return decide_census_changes(
            harness.service.store,
            actor,
            action,
            {row.pk: row.version for row in rows},
            note=note,
            request_key=key or uuid4(),
            bulk=len(rows) > 1 if bulk is None else bulk,
            value=value,
        )


def state(row):
    """The stored (decision, execution, admin_value_set, admin_value)."""
    row = fresh(row)
    return (row.decision, row.execution, row.admin_value_set, row.admin_value)


def test_staff_accept_a_by_hand_change_then_enter_it(response_service):
    """Accept moves a By hand change to To enter by hand (approved, pending);
    Entered by hand then makes it final (decision 2)."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "accept", note="Checked")
    assert state(rows["prefix"]) == ("approved", "pending", False, None)
    decide(harness, staff, [fresh(rows["prefix"])], "entered_by_hand")
    assert state(rows["prefix"])[:2] == ("approved", "resolved_external")
    assert ProposalResolution.objects.get().actor_id == staff


def test_entered_by_hand_on_a_to_review_row_accepts_and_marks(response_service):
    """On a To review By hand change, Entered by hand is one step: the change
    is final with no review row first."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["email_opt_out"]], "entered_by_hand")
    assert state(rows["email_opt_out"])[:2] == ("unreviewed", "resolved_external")
    assert ProposalReview.objects.count() == 0


def test_edit_is_accept_with_a_value(response_service):
    """Edit approves and records the reviewer's value in one review row: a
    Member's text, a By hand prefix and a Family address. A value equal to
    the Family's answer is a plain Accept."""
    harness, admin, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["first_name"]], "edit", value="Corrected")
    assert state(rows["first_name"]) == ("approved", "pending", True, "Corrected")
    decide(harness, staff, [rows["prefix"]], "edit", value="Rev.")
    assert state(rows["prefix"]) == ("approved", "pending", True, "Rev.")
    parts = address(line1="9 Census Way", line2="Apt 2")
    decide(harness, admin, [rows["home_address"]], "edit", value=parts)
    assert state(rows["home_address"])[:3] == ("approved", "pending", True)
    assert fresh(rows["home_address"]).admin_value["line2"] == "Apt 2"
    reviews = ProposalReview.objects.filter(proposal=rows["prefix"])
    assert reviews.count() == 1
    # Back to review keeps the value; Edit with the Family's answer clears it.
    decide(harness, staff, [fresh(rows["prefix"])], "back_to_review")
    decide(harness, staff, [fresh(rows["prefix"])], "edit", value="Dr.")
    assert state(rows["prefix"]) == ("approved", "pending", False, None)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("first_name", "  ", "Enter the value"),
        ("home_address", address(line1=""), "Complete this field"),
        (
            "home_address",
            address(line1="", city="", region="", postal_code="", country=""),
            "Enter the value",
        ),
    ],
)
def test_an_edit_the_change_cannot_take_is_refused(
    response_service, field, value, message
):
    """A blank value or an address the Family form would refuse is refused
    with a static reason, and nothing is recorded."""
    harness, _, staff, rows = prepared(response_service)
    with pytest.raises(InvalidEdit, match=message):
        decide(harness, staff, [rows[field]], "edit", value=value)
    assert ProposalReview.objects.count() == 0


@pytest.mark.parametrize("field", ["email_opt_out"])
def test_a_yes_or_no_answer_takes_no_edit(response_service, field):
    """The email opt-out (and moved, deceased, a New Member) is accepted or
    ignored as the Family gave it."""
    harness, _, staff, rows = prepared(response_service)
    with pytest.raises(ValueError):
        decide(harness, staff, [rows[field]], "edit", value="No")
    decide(harness, staff, [rows[field]], "accept")
    assert state(rows[field])[:2] == ("approved", "pending")


def test_accept_and_edit_on_a_conflict_accept_parishsofts_value(response_service):
    """On a Conflict, Accept and Edit approve against ParishSoft's current
    value (the accepted base), so the change is pending again."""
    harness, _, staff, rows = prepared(response_service)
    name = conflicted(rows["first_name"])
    decide(harness, staff, [name], "edit", value="Corrected")
    review = ProposalReview.objects.get(proposal=name)
    assert review.accepted_base and review.accepted_value == "Parish value"
    assert state(name) == ("approved", "pending", True, "Corrected")
    prefix = conflicted(rows["prefix"], "Mr.")
    decide(harness, staff, [prefix], "accept")
    assert state(prefix)[:2] == ("approved", "pending")


def test_back_to_review_reopens_an_ignored_change_only_with_a_note(response_service):
    """Sending an ignored change back to review says why."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "ignore")
    with pytest.raises(ValueError):
        decide(harness, staff, [fresh(rows["prefix"])], "back_to_review", note=" ")
    decide(harness, staff, [fresh(rows["prefix"])], "back_to_review", note="Asked")
    assert state(rows["prefix"])[0] == "unreviewed"


def test_bulk_decisions_are_all_or_none(response_service):
    """Bulk Accept covers Automatic and By hand changes; a bulk Entered by
    hand that includes an Automatic change, a stale change and a Ministry
    leader each refuse the whole request."""
    harness, admin, staff, rows = prepared(response_service)
    chosen = [rows["first_name"], rows["prefix"], rows["home_address"]]
    with pytest.raises(PermissionError):
        decide(harness, leader(harness, admin), chosen, "accept")
    with pytest.raises(ValueError):
        decide(harness, staff, chosen, "entered_by_hand")
    stale = [rows["prefix"], rows["home_address"]]
    decide(harness, staff, [rows["home_address"]], "ignore")
    with pytest.raises(BulkStale) as refused:
        decide(harness, staff, stale, "accept")
    assert refused.value.proposal_id == rows["home_address"].pk
    assert ProposalResolution.objects.count() == 0
    assert ProposalReview.objects.count() == 1
    key = uuid4()
    decide(harness, staff, chosen[:2], "accept", key=key)
    # A repeated bulk post records nothing more.
    decide(harness, staff, chosen[:2], "accept", key=key)
    assert ProposalReview.objects.count() == 3
    assert {state(row)[0] for row in chosen[:2]} == {"approved"}
    decide(harness, staff, [fresh(rows["prefix"])], "entered_by_hand", bulk=True)
    assert state(rows["prefix"])[1] == "resolved_external"


def forge(proposal, actor, *, decision, value_set=False, value=None, note=""):
    """Insert a review row and its result as the web login, bypassing the
    service and the audit, inside one transaction."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_proposal_review (id,correlation_id,actor_id,"
            "proposal_id,expected_version,request_key,decision,admin_value_set,"
            "admin_value,note) VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,"
            "%s,gen_random_uuid(),%s,%s,%s::jsonb,%s)",
            [actor, proposal.pk, proposal.version, decision, value_set, value, note],
        )
        cursor.execute(
            "UPDATE stewardship_proposed_change SET decision=%s,"
            " admin_value_set=%s, admin_value=%s::jsonb, version=version+1"
            " WHERE id=%s",
            [decision, value_set, value, proposal.pk],
        )


@pytest.mark.parametrize(
    ("field", "decision", "value", "message"),
    [
        # Edit is Accept with a value: no other decision takes one.
        ("prefix", "ignored", '"Rev."', "current authority"),
        # No value can be typed for a yes-or-no answer.
        ("email_opt_out", "approved", "false", "not offered for this field"),
        # An address is an object of its six parts, with a street line.
        ("home_address", "approved", '"9 Census Way"', "field's shape"),
        (
            "home_address",
            "approved",
            json.dumps(address(line1=" ")),
            "differ from the Family",
        ),
        ("home_address", "approved", '{"line1": "1 Main"}', "address components"),
    ],
)
def test_sql_refuses_a_forged_pipeline_edit(
    response_service, field, decision, value, message
):
    """The 0131 guard holds every writer to the edit rules."""
    harness, _, staff, rows = prepared(response_service)
    with pytest.raises((IntegrityError, DatabaseError), match=message):
        forge(rows[field], staff, decision=decision, value_set=True, value=value)
    assert state(rows[field])[:3] == ("unreviewed", "pending", False)


def test_sql_admits_staff_on_a_by_hand_change_only_with_its_audit(response_service):
    """A well-formed Staff Accept with a value on a By hand change passes the
    guard; only the missing audit event then refuses it, and a reopen without
    a note is refused by the guard itself."""
    harness, _, staff, rows = prepared(response_service)
    with pytest.raises((IntegrityError, DatabaseError), match="result and audit"):
        forge(
            rows["home_address"],
            staff,
            decision="approved",
            value_set=True,
            value=json.dumps(address(line1="10 Census Way")),
        )
    owner(
        "UPDATE stewardship_proposed_change SET decision='ignored' WHERE id=%s",
        [rows["prefix"].pk],
    )
    with pytest.raises((IntegrityError, DatabaseError), match="current authority"):
        forge(fresh(rows["prefix"]), staff, decision="unreviewed", note="  ")


def test_do_block_refuses_the_earlier_guard_and_installs_it():
    """The file's closing check passes as installed and raises once the
    review guard is put back to 0108's body; on that state the file
    installs its own again."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    earlier = _replaced(
        (FROZEN.parent / "0108_conflict_resolution.sql").read_text(encoding="utf-8"),
        "stewardship_proposal_review_guard_v1",
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)
    with (
        pytest.raises(DatabaseError, match="lacks the review pipeline rules"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(earlier)
        cursor.execute(check)

    class Rollback(Exception):
        """Ends a check that must leave the database as it found it."""

    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(earlier)
        cursor.execute(text)
        cursor.execute(
            "SELECT prosrc LIKE '%%stewardship_export_authorized_v1(NEW.actor_id,"
            "false)%%', prosecdef FROM pg_proc"
            " WHERE proname='stewardship_proposal_review_guard_v1'"
        )
        assert cursor.fetchone() == (True, False)
        raise Rollback
