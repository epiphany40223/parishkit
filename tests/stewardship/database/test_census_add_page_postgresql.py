"""Census Add on the Census changes worklist (#1710).

The worklist's side of an addition: its row (To enter by hand, Added by the
parish, the reviewer's value and its creator), its own steps (Entered by
hand, and Back to review, which withdraws an open one and undoes an entered
one's mark, #1840), a bulk decision with the Family's own changes, all or
none, and Add over HTTP.
"""

import json
from uuid import uuid4

import pytest

from parishkit.stewardship import pipeline
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Action
from parishkit.stewardship.reports.census_changes import (
    EVERY_STAGE,
    CensusQuery,
    census_changes,
)
from parishkit.stewardship.reports.census_worklist import (
    CensusDecisions,
    worklist_page,
)
from parishkit.stewardship.responses.models import (
    CensusAddition,
    CensusAdditionResolution,
)
from parishkit.stewardship.web.review_worklist import StaleDecision, WorklistDecision
from parishkit.stewardship.web.tables import paginate

from .test_background_grants_postgresql import task_login
from .test_census_additions_postgresql import (
    add,
    address_form,
    campaign_of,
    member_form,
)
from .test_census_pipeline_review_postgresql import decide, prepared, state
from .test_census_resolution_postgresql import leader
from .test_census_review_postgresql import fresh

pytestmark = pytest.mark.django_db(transaction=True)


def worklist(harness, actor, campaign, action, items, *, note="", bulk=None):
    """One worklist decision through the census DecisionService, as web."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        principal = current_principal(harness.service.store, actor)
        return CensusDecisions(harness.service.store, campaign).decide(
            principal,
            WorklistDecision(
                Action(action),
                items,
                note,
                uuid4(),
                bulk=len(items) > 1 if bulk is None else bulk,
            ),
        )


def rows_of(harness, actor, campaign, stage=EVERY_STAGE):
    """The census rows the page reads at ``stage``, by id, for ``actor``."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        principal = current_principal(harness.service.store, actor)
        result = census_changes(campaign, CensusQuery(stage=stage), principal)
    return result, {row["id"]: row for row in result["rows"]}


def test_staff_add_a_member_change_that_starts_accepted(response_service):
    """An Add is To enter by hand, Added by the parish, with the reviewer's
    value and no Family's answer, under its creator's name and note; it is
    audited by its id only."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    addition, created = add(
        harness,
        staff,
        campaign,
        member_form(rows, "nickname", " Bobby "),
        note="Phoned in",
    )
    assert created and addition.value == "Bobby" and addition.entity_key == "3"
    event = AuditEvent.objects.get(subject_id=addition.pk)
    assert event.event_type == "census_change_updated"
    _, shown = rows_of(harness, staff, campaign)
    row = shown[f"add-{addition.pk}"]
    assert (row["stage"], row["placement"].decision.value) == (
        "to_enter_by_hand",
        "added",
    )
    assert (row["family_answer"], row["edited"], row["route_label"]) == (
        "",
        "Bobby",
        "By hand",
    )
    assert (row["resolution_by"], row["resolution_note"]) == (
        "staff@example.org",
        "Phoned in",
    )
    assert row["status"] == "to_do" and row["version"] == 1


def test_entered_by_hand_moves_the_version_and_withdraw_is_final(response_service):
    """Entered by hand moves an addition to its next version, so a step at
    the old one is stale. Withdrawing one moves it to History and frees the
    field for a new Add."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    first, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bo"))
    notice = worklist(
        harness, staff, campaign, "entered_by_hand", {f"add-{first.pk}": 1}
    )
    assert notice.endswith("marked entered by hand.")
    _, shown = rows_of(harness, staff, campaign)
    assert shown[f"add-{first.pk}"]["stage"] == "entered_by_hand"
    assert shown[f"add-{first.pk}"]["version"] == 2
    with pytest.raises(StaleDecision):
        worklist(harness, staff, campaign, "back_to_review", {f"add-{first.pk}": 1})
    second, _ = add(harness, staff, campaign, member_form(rows, "suffix", "Jr."))
    notice = worklist(
        harness, staff, campaign, "back_to_review", {f"add-{second.pk}": 1}
    )
    assert notice.endswith("withdrawn.")
    _, shown = rows_of(harness, staff, campaign, "history")
    assert shown[f"add-{second.pk}"]["stage"] == "history"
    assert shown[f"add-{second.pk}"]["status"] == "cancelled"
    third, _ = add(harness, staff, campaign, member_form(rows, "suffix", "Sr."))
    assert third.pk != second.pk
    assert CensusAdditionResolution.objects.count() == 2


def test_bulk_back_to_review_undoes_an_entered_addition_and_withdraws_an_open_one(
    response_service, monkeypatch
):
    """Over an entered and an open addition, Back to review reopens the
    first (its mark undone, To enter by hand) and withdraws the second, all
    or none: without a note, nothing is recorded (#1840)."""
    monkeypatch.setattr(
        pipeline, "TERMINAL_UNDO_STREAMS", frozenset({pipeline.Stream.CENSUS})
    )
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    first, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bo"))
    second, _ = add(harness, staff, campaign, member_form(rows, "suffix", "Jr."))
    worklist(harness, staff, campaign, "entered_by_hand", {f"add-{first.pk}": 1})
    items = {f"add-{first.pk}": 2, f"add-{second.pk}": 1}
    with pytest.raises(ValueError, match="needs a note"):
        worklist(harness, staff, campaign, "back_to_review", items)
    assert CensusAdditionResolution.objects.count() == 1
    notice = worklist(harness, staff, campaign, "back_to_review", items, note="Typo")
    assert notice == "2 changes sent back to review."
    _, shown = rows_of(harness, staff, campaign)
    reopened = shown[f"add-{first.pk}"]
    assert (reopened["stage"], reopened["version"]) == ("to_enter_by_hand", 3)
    assert (reopened["resolution_action"], reopened["resolution_note"]) == (
        "reopened",
        "Typo",
    )
    _, history = rows_of(harness, staff, campaign, "history")
    assert history[f"add-{second.pk}"]["stage"] == "history"
    # Each step has its own audit event, versions only.
    event = AuditEvent.objects.get(
        subject_id=CensusAdditionResolution.objects.get(action="reopened").pk
    )
    assert event.event_type == "census_change_updated"


def test_a_bulk_decision_covers_additions_and_changes_all_or_none(response_service):
    """A bulk Entered by hand marks an addition and the Family's accepted
    By hand change together; when one is stale, neither is marked."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    decide(harness, staff, [rows["prefix"]], "accept")
    prefix = fresh(rows["prefix"])
    first, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bo"))
    with pytest.raises(StaleDecision):
        worklist(
            harness,
            staff,
            campaign,
            "entered_by_hand",
            {str(prefix.pk): prefix.version, f"add-{first.pk}": 2},
        )
    assert state(prefix)[1] == "pending"
    assert CensusAdditionResolution.objects.count() == 0
    notice = worklist(
        harness,
        staff,
        campaign,
        "entered_by_hand",
        {str(prefix.pk): prefix.version, f"add-{first.pk}": 1},
    )
    assert notice == "2 changes marked entered by hand."
    assert state(prefix)[1] == "resolved_external"
    assert CensusAdditionResolution.objects.get().action == "entered"


def test_an_addition_takes_only_its_own_steps(response_service):
    """Accept, Ignore and Edit are not offered on an added change; nor is Add
    to a Ministry leader."""
    harness, admin, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    first, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bo"))
    for action in ("accept", "ignore"):
        with pytest.raises(ValueError):
            worklist(harness, staff, campaign, action, {f"add-{first.pk}": 1})
    other = leader(harness, admin)
    with pytest.raises(PermissionError):
        add(harness, other, campaign, member_form(rows, "suffix", "Jr."))


def test_the_worklist_words_an_addition_and_the_change_it_replaced(response_service):
    """The added row offers Entered by hand and a Back to review that says it
    withdraws; the ignored change names the one added in its place."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    decide(harness, staff, [rows["first_name"]], "ignore")
    first, _ = add(
        harness,
        staff,
        campaign,
        member_form(
            rows, "first_name", "Robert", in_place_of=str(rows["first_name"].pk)
        ),
    )
    result, _ = rows_of(harness, staff, campaign)
    table = paginate(result["rows"], {"size": "100"})
    query = CensusQuery(stage=EVERY_STAGE)
    page, _ = worklist_page(
        result, query, table, "/admin/reports/census/", mutable=True
    )
    assert page.add
    shown = {row["id"]: row for row in page.rows}
    added = shown[f"add-{first.pk}"]
    assert [icon["action"] for icon in added["icons"]] == [
        "accept",
        "back_to_review",
        "entered_by_hand",
    ]
    assert added["icons"][0]["selected"]
    back = added["icons"][1]
    assert back["title_one"] == "Withdraw {name}?" and "withdrawn" in back["body"]
    assert "Added in place of an ignored change." in added["decision_label"]
    ignored = shown[str(rows["first_name"].pk)]
    assert "Added in its place: First name: Robert" in ignored["decision_label"]


def test_bulk_stale_names_the_addition(response_service):
    """A stale addition in a bulk decision is named by its field and DUIDs."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    first, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bo"))
    with pytest.raises(StaleDecision) as stale:
        worklist(harness, staff, campaign, "entered_by_hand", {f"add-{first.pk}": 2})
    assert stale.value.name.startswith("Nickname for Member DUID 3 in Family DUID")


def test_add_over_http_answers_in_place_or_at_the_field(response_service, google):
    """Staff's page offers Add with the Add dialog; an Add posts to the page
    and answers with it and its notice; a refused one is a 400 naming the
    field, with its closed code."""
    from .test_census_worklist_page_postgresql import ROUTE, staff_browser
    from .test_information_followup_postgresql import search

    harness, _, _, rows = prepared(response_service)
    browser = staff_browser(google)
    response, body = search(browser, ROUTE, {})
    page = body.decode()
    assert response.status_code == 200
    assert 'name="value_field"' in page and 'value="add"' in page
    form = {
        "stage": "all",
        "decide": "add",
        "request_key": str(uuid4()),
        "note": "",
    } | {
        name: values[0] for name, values in member_form(rows, "nickname", "Bo").items()
    }
    response, body = search(browser, ROUTE, form)
    assert response.status_code == 200
    assert b"Nickname for Member DUID 3 in Family DUID" in body and b" added." in body
    assert CensusAddition.objects.count() == 1
    response, body = search(
        browser, ROUTE, form | {"request_key": str(uuid4()), "value_member": "987654"}
    )
    assert response.status_code == 400
    refusal = json.loads(body)
    assert [error["field_id"] for error in refusal["errors"]] == ["value_member"]
    assert refusal["refusal"]["message"].startswith("ParishSoft's data does not list")
    assert getattr(response, "stewardship_codes", None) == ("add_not_in_parishsoft",)


def test_an_ignored_change_an_addition_covers_is_not_reopened(response_service):
    """While an open addition of its field stands in for it, the ignored
    change cannot go back to review; once withdrawn, it can."""
    from parishkit.stewardship.responses.census_additions import AddRefusal

    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    decide(harness, staff, [rows["first_name"]], "ignore")
    ignored = fresh(rows["first_name"])
    first, _ = add(harness, staff, campaign, member_form(rows, "first_name", "Rob"))
    with pytest.raises(AddRefusal) as refused:
        worklist(
            harness,
            staff,
            campaign,
            "back_to_review",
            {str(ignored.pk): ignored.version},
            note="Family meant it",
        )
    assert refused.value.code == "reopen_added"
    worklist(harness, staff, campaign, "back_to_review", {f"add-{first.pk}": 1})
    worklist(
        harness,
        staff,
        campaign,
        "back_to_review",
        {str(ignored.pk): ignored.version},
        note="Family meant it",
    )
    assert state(ignored)[0] == "unreviewed"


def test_a_refused_reopen_says_what_to_do_over_http(response_service, google):
    """A posted Back to review of a covered ignored change is a 400 at the
    note, saying to withdraw the added change first."""
    from .test_census_worklist_page_postgresql import ROUTE, staff_browser
    from .test_information_followup_postgresql import search

    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["first_name"]], "ignore")
    ignored = fresh(rows["first_name"])
    add(harness, staff, campaign_of(rows), member_form(rows, "first_name", "Rob"))
    browser = staff_browser(google)
    form = {
        "stage": "all",
        "decide": "back_to_review",
        "item": str(ignored.pk),
        "version": str(ignored.version),
        "request_key": str(uuid4()),
        "note": "Family meant it",
    }
    response, body = search(browser, ROUTE, form)
    assert response.status_code == 400
    refusal = json.loads(body)
    assert [error["field_id"] for error in refusal["errors"]] == ["note"]
    assert refusal["refusal"]["fix"].startswith("Withdraw the added change first")
    assert state(ignored)[0] == "ignored"


def test_the_commands_reopen_of_a_covered_change_is_refused(response_service):
    """``pk-admin census resolve``'s reopen (an Administrator's) is refused
    while an open addition covers the ignored change, as the page's is."""
    from parishkit.stewardship.responses.census_resolution import ReopenCovered

    from .test_census_resolution_postgresql import prepared as with_home
    from .test_census_resolution_postgresql import resolve

    harness, admin, staff, home, _ = with_home(response_service)
    resolve(harness, staff, home, "ignored", note="Duplicate")
    fields = address_form({"home": home}, "home_address", line1="5 New St")
    add(harness, staff, home.submission.campaign_id, fields)
    with pytest.raises(ReopenCovered):
        resolve(harness, admin, fresh(home), "reopened", note="Not a duplicate")
    assert state(home)[0] == "ignored"
