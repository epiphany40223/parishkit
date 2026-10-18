"""An Administrator edits an Automatic change's value under the real web role (#528).

Migration 0107 (``schema/migrations/0107_proposal_review_edit.sql``) widens
0096's review guard so a review row may be an edited value or a cleared edit
(ADM-09.04) instead of a decision. These tests run the service and the SQL
guard as installed, the worklist's Edit value over HTTP, and the file's own
DO block against 0096's guard body.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.census_review import (
    InvalidEdit,
    Unchanged,
    edit_census_change,
)
from parishkit.stewardship.responses.models import ProposalReview, ProposedChange
from parishkit.stewardship.storage import StaleRecordError

from .test_background_grants_postgresql import task_login
from .test_census_resolution_postgresql import leader, prepared, resubmit
from .test_census_review_postgresql import fresh, review
from .test_publication_plans_postgresql import owner, ready  # noqa: F401
from .test_publication_worker_postgresql import (  # noqa: F401 (fixture)
    _replaced,
    confirmed,
    results,
    run,
)

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = MIGRATIONS / "migrations/0107_proposal_review_edit.sql"
ROUTE = "/admin/reports/census/"


def edit(harness, actor, proposal, value, *, version=None, key=None, note=""):
    """Edit (or, with ``value`` None, clear) through the service as web."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return edit_census_change(
            harness.service.store,
            actor,
            proposal.pk,
            expected_version=proposal.version if version is None else version,
            request_key=key or uuid4(),
            value=value,
            note=note,
        )


def test_an_edit_keeps_the_decision_and_the_familys_answer(response_service):
    """Edit value records the value to publish, keeps the decision and the
    Family's answer, advances the version and is audited; a repeated request
    is one row, a rebound key is refused, and Clear edit removes it."""
    harness, admin, _, _, name = prepared(response_service)
    review(harness, admin, name, "approved")
    name = fresh(name)
    key = uuid4()
    first = edit(harness, admin, name, "  Corrected ", key=key, note="Typo")
    again = edit(harness, admin, name, "  Corrected ", key=key, note="Typo")
    assert again.pk == first.pk
    with pytest.raises(ValueError):
        edit(harness, admin, name, "Other", key=key, note="Typo")
    edited = fresh(name)
    assert (edited.decision, edited.execution) == ("approved", "pending")
    assert (edited.admin_value_set, edited.admin_value) == (True, "Corrected")
    assert edited.submitted_value == "Requested"
    assert edited.version == name.version + 1
    assert (first.decision, first.admin_value_set, first.note) == (
        "approved",
        True,
        "Typo",
    )
    assert AuditEvent.objects.filter(
        event_type="census_change_updated", subject_id=first.pk
    ).exists()
    cleared = edit(harness, admin, edited, None)
    assert (cleared.admin_value_set, cleared.admin_value) == (False, None)
    edited = fresh(name)
    assert (edited.admin_value_set, edited.admin_value, edited.decision) == (
        False,
        None,
        "approved",
    )
    # A decision after an edit keeps the edit.
    edit(harness, admin, edited, "Corrected")
    review(harness, admin, fresh(name), "ignored")
    assert (fresh(name).decision, fresh(name).admin_value) == ("ignored", "Corrected")


def test_an_edit_equal_to_the_familys_answer_is_no_edit(response_service):
    """The Family's own answer (compared as the census compares it) clears an
    edit, and with no edit set it changes nothing."""
    harness, admin, _, _, name = prepared(response_service)
    with pytest.raises(Unchanged):
        edit(harness, admin, name, " Requested ")
    with pytest.raises(Unchanged):
        edit(harness, admin, name, None)
    edit(harness, admin, name, "Corrected")
    with pytest.raises(Unchanged):
        edit(harness, admin, fresh(name), "Corrected")
    cleared = edit(harness, admin, fresh(name), "Requested")
    assert cleared.admin_value_set is False
    assert ProposalReview.objects.count() == 2


@pytest.mark.parametrize(
    "value",
    ["", "   ", "x" * 31, "Two\nlines"],
)
def test_a_value_publication_would_refuse_is_refused(response_service, value):
    """Blank, longer than ParishSoft's first-name limit (30) or not one line."""
    harness, admin, _, _, name = prepared(response_service)
    with pytest.raises(InvalidEdit):
        edit(harness, admin, name, value)
    assert ProposalReview.objects.count() == 0


def test_leaders_stale_malformed_and_queued_edits_are_refused(response_service):
    """Staff edit as an Administrator does (#1683), a Ministry leader never;
    a stale version, an address given as one line and a queued change are
    refused; a Conflict may be edited."""
    harness, admin, staff, home, name = prepared(response_service)
    with pytest.raises(PermissionError):
        edit(harness, leader(harness, admin), name, "Corrected")
    edit(harness, staff, name, "Staff value")
    assert fresh(name).admin_value == "Staff value"
    name = fresh(name)
    with pytest.raises(StaleRecordError):
        edit(harness, admin, name, "Corrected", version=name.version + 1)
    with pytest.raises(ValueError):
        edit(harness, admin, home, "Corrected")
    for execution, allowed in (("queued", False), ("conflict", True)):
        owner(
            "UPDATE stewardship_proposed_change SET execution=%s WHERE id=%s",
            [execution, name.pk],
        )
        if allowed:
            edit(harness, admin, fresh(name), "Corrected")
            assert fresh(name).admin_value == "Corrected"
        else:
            with pytest.raises(ValueError):
                edit(harness, admin, fresh(name), "Corrected")
            with pytest.raises(
                (IntegrityError, DatabaseError), match="current authority"
            ):
                forge(fresh(name), admin, '"Corrected"')


def test_an_edit_after_confirmation_leaves_the_plan_stale(confirmed):  # noqa: F811
    """An edit advances the version, so the confirmed plan no longer matches:
    the worker writes nothing and the change stays approved and pending."""
    state = confirmed
    edit(state.harness, state.admin, state.first, "Edited")
    assert run(state).invalidated == 1
    assert state.gateway.writes == []
    assert results(state.entity) == [("invalidated", "")]
    first = fresh(state.first)
    assert (first.execution, first.decision, first.admin_value) == (
        "pending",
        "approved",
        "Edited",
    )


def forge(proposal, actor, value, *, decision=None, value_set=True):
    """Insert an edit review row and apply it directly as the web login,
    bypassing the service and its audit, inside one transaction."""
    decision = decision or proposal.decision
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_proposal_review (id,correlation_id,actor_id,"
            "proposal_id,expected_version,request_key,decision,admin_value_set,"
            "admin_value,note) VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,"
            "%s,gen_random_uuid(),%s,%s,%s::jsonb,'')",
            [actor, proposal.pk, proposal.version, decision, value_set, value],
        )
        cursor.execute(
            "UPDATE stewardship_proposed_change SET decision=%s,"
            " admin_value_set=%s, admin_value=%s::jsonb, version=version+1"
            " WHERE id=%s",
            [decision, value_set, value, proposal.pk],
        )


@pytest.mark.parametrize(
    ("value", "decision", "value_set", "message"),
    [
        # A decision other than Accept and an edit at once (Edit is Accept
        # with a value, #1683).
        ('"Corrected"', "ignored", True, "current authority"),
        # Not the field's shape: a number, an object, JSON null.
        ("7", None, True, "field's shape"),
        ('{"display": "x"}', None, True, "field's shape"),
        ("null", None, True, "field's shape"),
        # Oversized.
        (json.dumps("x" * 1100), None, True, "field's shape"),
        # Blank, by the comparison's whitespace (a tab, a no-break and an
        # ideographic space too), or the Family's own answer.
        ('"   "', None, True, "differ from the Family"),
        ('"\\t"', None, True, "differ from the Family"),
        (json.dumps("\u00a0"), None, True, "differ from the Family"),
        (json.dumps("\u3000"), None, True, "differ from the Family"),
        ('" Requested"', None, True, "differ from the Family"),
        # Clearing when no edit is set changes nothing.
        (None, None, False, "current authority"),
    ],
)
def test_sql_refuses_a_forged_edit(
    response_service, value, decision, value_set, message
):
    """The guard checks the edit's kind, shape, size and difference itself."""
    harness, admin, _, _, name = prepared(response_service)
    with pytest.raises((IntegrityError, DatabaseError), match=message):
        forge(name, admin, value, decision=decision, value_set=value_set)
    name = fresh(name)
    assert (name.admin_value_set, name.decision) == (False, "unreviewed")


@pytest.mark.parametrize("edited_first", [False, True])
def test_the_replacement_path_cannot_change_the_edit(
    response_service, monkeypatch, edited_first
):
    """The web login's edit grant is not usable on the replacement path: a
    later response's supersession that also sets, changes or clears the
    edit is refused by the edit guard, while the same supersession leaving
    the edit as it is (an edit set before included) is accepted."""
    from django.db.models import QuerySet

    harness, admin, _, _, name = prepared(response_service)
    if edited_first:
        edit(harness, admin, name, "Corrected")
    forged_edit = (
        {"admin_value_set": False, "admin_value": None}
        if edited_first
        else {"admin_value_set": True, "admin_value": {"x": "forged" * 50}}
    )
    original = QuerySet.update

    def forged(self, **values):
        """Also change the edit when a proposal's execution changes."""
        if self.model is ProposedChange and "execution" in values:
            values = values | forged_edit
        return original(self, **values)

    with monkeypatch.context() as patch:
        patch.setattr(QuerySet, "update", forged)
        with pytest.raises(DatabaseError, match="edit requires its review"):
            resubmit(harness)
    name = fresh(name)
    assert name.execution == "pending" and name.admin_value_set is edited_first
    resubmit(harness)
    name = fresh(name)
    assert name.execution == "superseded" and name.admin_value_set is edited_first
    assert name.admin_value == ("Corrected" if edited_first else None)


def test_sql_refuses_a_forged_edit_without_its_review(response_service):
    """The web login cannot change the edit fields with no review row at all,
    even with the version and decision left as they are."""
    harness, admin, _, _, name = prepared(response_service)
    with (
        pytest.raises(
            (IntegrityError, DatabaseError), match="edit requires its review"
        ),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_proposed_change SET admin_value_set=true,"
            " admin_value='\"Forged\"'::jsonb, version=version+1 WHERE id=%s",
            [name.pk],
        )
    assert fresh(name).admin_value_set is False


def test_sql_refuses_a_forged_edit_without_its_audit(response_service):
    """A well-formed edit row and its result still need the audit event."""
    harness, admin, _, _, name = prepared(response_service)
    with pytest.raises((IntegrityError, DatabaseError), match="result and audit"):
        forge(name, admin, '"Corrected"')
    assert fresh(name).admin_value_set is False


def test_do_block_refuses_the_earlier_guard():
    """The file's closing check passes as installed and raises once the
    review guard is put back to 0096's body, or the edit guard's trigger is
    gone; the file installs both again."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    earlier = _replaced(
        (MIGRATIONS / "migrations/0096_proposal_review.sql").read_text(
            encoding="utf-8"
        ),
        "stewardship_proposal_review_guard_v1",
    )
    own = _replaced(text, "stewardship_proposal_review_guard_v1")

    class Rollback(Exception):
        """Ends a check that must leave the database as it found it."""

    # 0131 (#1683) replaced the review guard since: each check runs on this
    # file's own body for it, rolled back, so it tests this file alone.
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(own)
        cursor.execute(check)
        raise Rollback
    with (
        pytest.raises(DatabaseError, match="lacks the edit rules"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(earlier)
        cursor.execute(check)

    with (
        pytest.raises(DatabaseError, match="lacks its edit guard"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(own)
        cursor.execute(
            "DROP TRIGGER stewardship_proposal_edit_guard"
            " ON stewardship_proposed_change"
        )
        cursor.execute(check)

    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(earlier)
        cursor.execute("DROP FUNCTION stewardship_proposal_edit_guard_v1() CASCADE")
        cursor.execute(text)
        cursor.execute(
            "SELECT prosrc LIKE '%%OR decided=edited%%', prosecdef FROM pg_proc"
            " WHERE proname='stewardship_proposal_review_guard_v1'"
        )
        assert cursor.fetchone() == (True, False)
        cursor.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname="
            "'stewardship_proposal_edit_guard'"
        )
        assert cursor.fetchone() == (1,)
        raise Rollback


def test_the_worklist_edits_in_place_for_an_administrator_only(
    response_service, google
):
    """An Administrator's Review has Edit value; Save value answers with the
    page, its notice and the Edited value beside the Family's answer; a value
    ParishSoft would refuse is a typed refusal; Staff see no Edit value and
    are denied; Clear edit removes it."""
    from .auth_builders import signed_in
    from .test_information_followup_postgresql import search

    harness, admin, staff, _, name = prepared(response_service)
    google[0]["email"] = "staff@example.org"
    staff_browser, login = signed_in()
    assert login.status_code == 302
    _, body = search(staff_browser, ROUTE, {"status": "all"})
    assert b'value="edit"' not in body
    form = {
        "status": "all",
        "resolve": "edit",
        "proposal": str(name.pk),
        "version": str(name.version),
        "request_key": str(uuid4()),
        "note": "",
        "value": "Corrected",
    }
    response, _ = search(staff_browser, ROUTE, form)
    assert response.status_code == 403
    google[0]["email"] = "admin@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    _, body = search(browser, ROUTE, {"status": "all"})
    assert b'value="edit"' in body and b"resolve=clear_edit&amp;" not in body
    assert b'maxlength="30"' in body and b'value="Requested"' in body
    response, body = search(browser, ROUTE, form | {"value": "x" * 31})
    assert response.status_code == 400
    assert json.loads(body)["refusal"]["message"] == (
        "ParishSoft takes at most 30 characters in this field."
    )
    assert ProposalReview.objects.count() == 0
    response, body = search(browser, ROUTE, form)
    assert response.status_code == 200
    assert b"now has an edited value." in body
    assert b"<dt>Edited value</dt><dd>Corrected</dd>" in body
    assert b"<dd>Requested</dd>" in body
    assert b"resolve=clear_edit&amp;" in body
    name = fresh(name)
    response, body = search(
        browser,
        ROUTE,
        form
        | {
            "resolve": "clear_edit",
            "version": str(name.version),
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 200
    assert b"no longer has an edited value." in body
    assert fresh(name).admin_value_set is False
    # Only Edit value's form sends a value.
    response, _ = search(
        browser,
        ROUTE,
        form
        | {
            "resolve": "approve",
            "version": str(fresh(name).version),
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 400
