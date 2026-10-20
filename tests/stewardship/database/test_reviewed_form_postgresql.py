"""The reviewed Family form (#1705): what a live form shows and how it is judged.

A live form draws each item's shown value (review pipeline spec, "Reviewed
Family form") and records the shown items it drew; the submit compares each
answer with what the form showed (rules 1 to 4 of "Family resubmission").
These tests run the real form issue, the HTTP submit and the SQL guards
(frozen files 0216 to 0218) on the restricted web login where a Family
signs in.
"""

from contextlib import contextmanager
from decimal import Decimal

import pytest

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses import baselines
from parishkit.stewardship.responses.models import (
    ProposedChange,
    Submission,
)
from parishkit.stewardship.workflows.models import MinistryRequest

from .test_background_grants_postgresql import task_login
from .test_census_pipeline_review_postgresql import decide, prepared
from .test_census_review_postgresql import fresh
from .test_ministry_responses_postgresql import respond, revisit
from .test_response_http_postgresql import answers_for, post
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def rows_for(field):
    """Every census change of ``field``, oldest first."""
    return list(
        ProposedChange.objects.filter(field=field).order_by(
            "submission__family_version"
        )
    )


def member_field(form, name, member="3"):
    """One Member field of a form payload, as the browser receives it."""
    (found,) = [
        field
        for entry in form["members"]
        if entry["id"] == member
        for field in entry["fields"]
        if field["name"] == name
    ]
    return found


def household_field(form, name):
    """One household field of a form payload."""
    (found,) = [field for field in form["household"]["fields"] if field["name"] == name]
    return found


def again(harness, change=None):
    """The Family signs in again and sends the form as drawn, then ``change``d.

    Returns the form payload it was drawn with and the new response.
    """
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        if change is not None:
            change(answers)
        return form, respond(harness, form, answers)


def first_name(value):
    """A change that sets Member 3's first name in the answers."""

    def apply(answers):
        answers["members"]["3"]["first_name"] = value

    return apply


@contextmanager
def drawn_before_the_release():
    """Issue forms inside as a form drawn before #1705's last slice was."""
    original = baselines._shown
    baselines._shown = lambda *args: (False, None)
    try:
        yield
    finally:
        baselines._shown = original


# --- Census ----------------------------------------------------------------


def test_a_live_form_records_what_it_shows(response_service):
    """A live form is drawn as the reviewed form and names each change in force."""
    harness, _, _, rows = prepared(response_service)
    form, _ = again(harness)
    baseline = Submission.objects.order_by("-family_version").first()
    assert baseline.reviewed_view is True
    entries = {entry["field"]: entry for entry in baseline.shown_items}
    for field in ("first_name", "prefix", "home_address", "email_opt_out"):
        entry = entries[field]
        assert entry["stream"] == "census"
        assert entry["item"] == str(rows[field].pk)
        assert (entry["shown"], entry["decision"]) == ("family", None)
    # The Family's own answers, marked changed, as before the reviewed form.
    shown = member_field(form, "first_name")
    assert (shown["value"], shown["changed"], shown["conflict"]) == (
        "Requested",
        True,
        False,
    )


def test_an_unchanged_answer_keeps_its_change_and_makes_no_row(response_service):
    """Rule 1: no new row, however many times the Family answers again."""
    harness, _, _, rows = prepared(response_service)
    before = fresh(rows["first_name"])
    again(harness)
    again(harness)
    found = rows_for("first_name")
    assert [row.pk for row in found] == [before.pk]
    assert (found[0].execution, found[0].version) == ("pending", before.version)
    assert ProposedChange.objects.count() == len(rows)


def test_an_edited_change_shows_the_reviewers_value_and_keeps_it(response_service):
    """Accepted (edited): the form shows the reviewer's value, marked changed;
    sending it back keeps the edit and the Family's answer; changing it again
    reopens it To review against ParishSoft's value, carrying no decision."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["first_name"]], "edit", value="Edited")
    form, response = again(harness)
    shown = member_field(form, "first_name")
    assert (shown["value"], shown["changed"]) == ("Edited", True)
    assert response.answers["members"]["3"]["first_name"] == "Edited"
    kept = fresh(rows["first_name"])
    assert (kept.decision, kept.admin_value, kept.submitted_value) == (
        "approved",
        "Edited",
        "Requested",
    )
    form, _ = again(harness)
    assert member_field(form, "first_name")["value"] == "Edited"
    again(harness, first_name("Changed again"))
    old, new = rows_for("first_name")
    assert (old.execution, old.superseded_by_id) == ("superseded", new.pk)
    assert (new.decision, new.admin_value_set, new.execution) == (
        "unreviewed",
        False,
        "pending",
    )
    assert new.baseline_value == new.current_value == "Member"


def test_an_ignored_change_shows_parishsoft_and_stays_ignored(response_service):
    """Ignored: a Member's field shows ParishSoft's value, unmarked, and stays
    Ignored when sent back; re-entering the Family's value reopens it To
    review. A Family address, whose ParishSoft value the form cannot show,
    shows the Family's own answer, unmarked."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["first_name"], rows["home_address"]], "ignore")
    form, _ = again(harness)
    name = member_field(form, "first_name")
    assert (name["value"], name["changed"]) == ("Member", False)
    home = household_field(form, "home_address")
    assert home["value"] == rows["home_address"].submitted_value
    assert home["changed"] is False
    for field in ("first_name", "home_address"):
        (row,) = rows_for(field)
        assert (row.decision, row.execution) == ("ignored", "pending")
    again(harness)
    (row,) = rows_for("first_name")
    assert (row.decision, row.execution) == ("ignored", "pending")
    again(harness, first_name("Requested"))
    old, new = rows_for("first_name")
    assert (old.execution, old.superseded_by_id) == ("superseded", new.pk)
    assert (new.decision, new.submitted_value) == ("unreviewed", "Requested")


def test_a_decision_recorded_while_the_form_is_open_is_kept(response_service):
    """A decision while the Family has the form open never refuses the submit,
    and an untouched item keeps it."""
    harness, _, staff, rows = prepared(response_service)
    with web_login():
        form = revisit(harness)
    decide(harness, staff, [rows["first_name"]], "edit", value="Edited meanwhile")
    with web_login():
        respond(harness, form, answers_for(form))
    kept = fresh(rows["first_name"])
    assert (kept.decision, kept.admin_value) == ("approved", "Edited meanwhile")
    assert len(rows_for("first_name")) == 1


def test_an_answer_set_back_to_parishsoft_withdraws_the_change(response_service):
    """Rule 3: the open change is withdrawn and no row is made."""
    harness, _, _, rows = prepared(response_service)
    again(harness, first_name("Member"))
    (row,) = rows_for("first_name")
    assert row.execution == "cancelled"


def test_an_entered_change_is_shown_and_left_as_it_is(response_service):
    """An Entered by hand change is shown carried forward; leaving it is rule 1."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    form, _ = again(harness)
    assert member_field(form, "prefix")["value"] == "Dr."
    again(harness)
    (row,) = rows_for("prefix")
    assert row.execution == "resolved_external"


def test_a_moved_request_entered_by_hand_is_still_shown_and_kept(
    live_response_service,
):
    """A move request is shown in its state, Entered by hand included, until
    Confirmed; sending it back makes no row (rule 1)."""
    from django.db.models import F

    from parishkit.stewardship.campaigns.work_locks import work_transaction

    from .test_response_http_postgresql import load_form

    harness = live_response_service
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {"moved_household": True, "confirmed": True}
        respond(harness, form, answers)
    with work_transaction():
        ProposedChange.objects.filter(field="moved_household").update(
            execution="resolved_external", version=F("version") + 1
        )
    form, _ = again(harness)
    assert form["members"][0]["request"] == {
        "moved_household": True,
        "confirmed": True,
    }
    (row,) = rows_for("moved_household")
    assert row.execution == "resolved_external"


def test_a_form_drawn_before_the_release_keeps_the_earlier_rule(response_service):
    """A form open across the deploy shows the Family's own answers and is
    judged by the rule before the reviewed form (a same-answer successor
    carries the decision); it is never refused for the deploy."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["first_name"]], "edit", value="Edited")
    with web_login():
        with drawn_before_the_release():
            form = revisit(harness)
        assert member_field(form, "first_name")["value"] == "Requested"
        response = respond(harness, form, answers_for(form))
    assert (response.reviewed_view, response.shown_items) == (False, None)
    old, new = rows_for("first_name")
    assert (old.execution, old.superseded_by_id) == ("superseded", new.pk)
    assert (new.decision, new.admin_value) == ("approved", "Edited")


def test_the_database_refuses_an_absence_the_form_did_not_show(
    response_service, monkeypatch
):
    """The completeness check admits a missing row only for the value the form
    showed: a derivation that treats a changed answer as left as shown is
    refused, not committed."""
    from django.db import IntegrityError

    from parishkit.stewardship.responses import reviewed

    harness, _, _, rows = prepared(response_service)
    monkeypatch.setattr(reviewed, "left_as_shown", lambda *args: True)
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        answers["members"]["3"]["first_name"] = "Something else"
        with pytest.raises(IntegrityError, match="atomic derived work"):
            post(
                harness.client,
                "/family/submit",
                {"baseline": form["baseline"], "answers": answers},
            )
    assert len(rows_for("first_name")) == 1


# --- Ministry --------------------------------------------------------------


def ministry_setup(response_service):
    """Member 3 asks to join Food pantry (9) and leave Ministry 4; Choir (11)
    is offered too, for an Edit to carry a join into."""
    from .response_builders import activate_response_service
    from .test_ministry_reports_postgresql import actor
    from .test_ministry_responses_postgresql import configure, ministry_source
    from .test_response_http_postgresql import load_form
    from .test_source_families_postgresql import prepare, promote

    harness = response_service
    data = ministry_source()
    data.ministry_types[11] = {"id": 11, "name": "Choir"}
    data.ministry_type_memberships[11] = {"membership": []}
    snapshot, claim = prepare(data)
    harness.snapshot = promote(snapshot, claim, harness.campaign, harness.rings)
    configure(harness, selected=(4, 9, 11))
    harness = activate_response_service(harness)
    form = load_form(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [9], "leave": [4]}
    respond(harness, form, answers)
    return harness, actor(harness, {"staff"}, ())


def current_requests():
    """Every request still in force, by (Ministry, action)."""
    return {
        (row.ministry_duid, row.action): row
        for row in MinistryRequest.objects.exclude(
            state__in=["superseded", "cancelled"]
        )
    }


def decide_request(harness, staff, action, target, **fields):
    """One Ministry worklist decision through its service."""
    from .test_ministry_review_postgresql import decide as decide_ministry

    return decide_ministry(
        harness,
        staff,
        action,
        *([target] if target else []),
        fields={name: [value] for name, value in fields.items()},
    )


def choices(form):
    """Member 3's drawn choices."""
    entry = form["ministries"]["members"]["3"]
    return {"join": entry["join"], "leave": entry["leave"]}


def set_choices(join, leave):
    """A change that sets Member 3's Ministry choices."""

    def apply(answers):
        answers["ministries"]["members"]["3"] = {"join": join, "leave": leave}

    return apply


def test_requests_to_review_are_drawn_and_left_as_shown(response_service):
    """Rule 1: the same choices again make no request."""
    harness, _ = ministry_setup(response_service)
    before = current_requests()
    form, _ = again(harness)
    assert choices(form) == {"join": [9], "leave": [4]}
    assert MinistryRequest.objects.count() == len(before)
    assert {key: row.version for key, row in current_requests().items()} == {
        key: row.version for key, row in before.items()
    }


def test_an_accepted_join_is_drawn_and_unticking_withdraws_it(response_service):
    """An accepted join is shown chosen; unticking it withdraws it (rule 3)."""
    harness, staff = ministry_setup(response_service)
    join = current_requests()[9, "join"]
    decide_request(harness, staff, "accept", join)
    form, _ = again(harness)
    assert choices(form) == {"join": [9], "leave": [4]}
    join.refresh_from_db()
    assert (join.state, join.outcome) == ("resolved", "joined")
    again(harness, set_choices([], [4]))
    join.refresh_from_db()
    assert (join.state, join.outcome, join.resolved_at) == ("cancelled", None, None)


def test_an_edited_join_is_drawn_on_its_edit_and_reopens_on_its_own(
    response_service,
):
    """An Edit into Choir draws Choir chosen; left as shown it keeps its
    decision; choosing Food pantry again reopens the same request To review."""
    harness, staff = ministry_setup(response_service)
    join = current_requests()[9, "join"]
    decide_request(harness, staff, "edit", join, value="11")
    form, _ = again(harness)
    assert choices(form) == {"join": [11], "leave": [4]}
    join.refresh_from_db()
    assert (join.state, join.reviewed_ministry_duid) == ("resolved", 11)
    again(harness, set_choices([9], [4]))
    join.refresh_from_db()
    assert (join.state, join.reviewed_ministry_duid) == ("superseded", None)
    successor = MinistryRequest.objects.get(pk=join.superseded_by_id)
    assert (successor.ministry_duid, successor.state) == (9, "new")


def test_an_ignored_join_is_not_drawn_and_ticking_it_reopens(response_service):
    """Ignored: not chosen; left so, it stays Ignored; ticking it is rule 2."""
    harness, staff = ministry_setup(response_service)
    join = current_requests()[9, "join"]
    decide_request(harness, staff, "ignore", join)
    form, _ = again(harness)
    assert choices(form) == {"join": [], "leave": [4]}
    join.refresh_from_db()
    assert join.state == "resolved"
    again(harness, set_choices([9], [4]))
    join.refresh_from_db()
    assert join.state == "superseded"
    assert MinistryRequest.objects.get(pk=join.superseded_by_id).state == "new"


def test_an_added_join_unticked_keeps_its_decision(response_service):
    """A join the parish added is shown chosen; the Family unticking it keeps
    its decision, and its row says so."""
    from django.db import transaction

    from parishkit.stewardship.reports.ministry_worklist import (
        WorklistQuery,
        read_rows,
        worklist_context,
    )

    harness, staff = ministry_setup(response_service)
    decide_request(
        harness,
        staff,
        "add",
        None,
        value_member="3",
        value_ministry="11",
        value_action="join",
        value_in_place_of="",
    )
    added = MinistryRequest.objects.get(parish_added=True)
    form, _ = again(harness)
    assert choices(form) == {"join": [9, 11], "leave": [4]}
    again(harness, set_choices([9], [4]))
    added.refresh_from_db()
    assert (added.state, added.outcome) == ("resolved", "joined")
    query = WorklistQuery.parse({"stage": "all"})
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        result = read_rows(harness.campaign.pk, query, staff)
        context = worklist_context(
            harness.campaign.pk,
            query,
            staff,
            result,
            token="",
            mutable=True,
            action="/admin/reports/ministries/",
        )
    (row,) = [r for r in context["worklist"].rows if r["id"] == str(added.pk)]
    assert "Family unticked" in row["decision_label"]
    assert "Family asked again" not in row["decision_label"]


# --- Pledge ----------------------------------------------------------------


def test_an_edited_pledge_is_drawn_and_kept_when_sent_back(response_service):
    """Accepted (edited): the form shows the reviewer's amount and frequency
    with the Family's share methods; sent back, the decision stays in force."""
    from .test_pledge_review_postgresql import admin, decide, pledged, state

    harness, submission = pledged(response_service)
    decide(
        harness,
        admin(),
        "edit",
        [submission],
        value_amount="1,300",
        value_frequency="quarterly",
    )
    form, response = again(harness)
    answers = form["financial"]["answers"]
    assert (answers["annual_pledge"], answers["frequency"]) == ("1300.00", "quarterly")
    assert answers["shares"] == submission.answers["financial"]["shares"]
    assert response.annual_pledge == Decimal("1300.00")
    found = state(harness, response)
    assert (found.stage, found.in_force) == ("to_enter_by_hand", True)
    assert found.review.decision == "accepted_edited"


def test_an_edited_pledge_changed_again_reopens(response_service):
    """Rule 2: a pledge other than the one shown is To review again."""
    from .test_pledge_review_postgresql import admin, decide, pledged, state

    harness, submission = pledged(response_service)
    decide(
        harness,
        admin(),
        "edit",
        [submission],
        value_amount="1,300",
        value_frequency="quarterly",
    )

    def other(answers):
        answers["financial"]["annual_pledge"] = "1400.00"

    _, response = again(harness, other)
    found = state(harness, response)
    assert (found.stage, found.in_force) == ("to_review", False)


# --- Privacy ---------------------------------------------------------------

# The keys each census field of the payload carries, as before the reviewed
# form: never a stage, decision, note, reviewer or ParishSoft value.
FIELD_KEYS = {
    "name",
    "label",
    "required",
    "max_length",
    "kind",
    "choices",
    "value",
    "available",
    "changed",
    "conflict",
}
HOUSEHOLD_KEYS = {"name", "label", "kind", "value", "available", "changed", "conflict"}
PIPELINE_WORDS = (
    "Accepted",
    "Ignored",
    "To review",
    "Reviewer",
    "reviewer",
    "Added by the parish",
    "ParishSoft",
    "decision",
    "shown_items",
    "reviewed_view",
)


def test_the_payload_carries_no_pipeline_detail(response_service):
    """A reviewed form's payload names no stage, decision, note or reviewer."""
    import json

    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["first_name"]], "edit", value="Edited", note="Typo")
    decide(harness, staff, [rows["prefix"]], "ignore", note="Not needed")
    form, _ = again(harness)
    for entry in form["members"]:
        for field in entry["fields"]:
            assert set(field) == FIELD_KEYS, field["name"]
    for field in form["household"]["fields"]:
        assert set(field) == HOUSEHOLD_KEYS, field["name"]
    payload = {key: value for key, value in form.items() if key != "content"}
    text = json.dumps(payload)
    for word in (*PIPELINE_WORDS, "Typo", "Not needed", "staff@example.org"):
        assert word not in text, word
    with web_login():
        revisit(harness)
    page = harness.client.get("/family/")
    assert page.status_code == 200
    for word in ("Accepted (edited)", "Reviewer's value", "Added by the parish"):
        assert word not in page.content.decode()
