"""Census changes' review worklist over HTTP (#1683).

Every decision posts to the page itself with the page's view, through the
shared worklist POST (``review_worklist.read_decision``), and the answer is
the page redrawn with a notice. Staff review every change, By hand ones
too; a Ministry leader never reaches the page.
"""

import json
from uuid import uuid4

import pytest

from parishkit.stewardship.responses.models import ProposalResolution, ProposalReview

from ..census_factory import address
from .auth_builders import signed_in
from .test_census_pipeline_review_postgresql import prepared, state
from .test_census_review_postgresql import fresh
from .test_information_followup_postgresql import search

pytestmark = pytest.mark.django_db(transaction=True)

ROUTE = "/admin/reports/census/"


def staff_browser(google):
    """A Staff member's signed-in browser."""
    google[0]["email"] = "staff@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    return browser


def decide(proposal, action, **fields):
    """A row icon's decision form for ``proposal`` with the page's view."""
    return {
        "stage": "all",
        "decide": action,
        "item": str(proposal.pk),
        "version": str(proposal.version),
        "request_key": str(uuid4()),
        "note": "",
    } | fields


def test_staff_see_and_take_every_review_decision(response_service, google):
    """Staff's page lists every change To review, By hand ones too, with
    Accept on each and Entered by hand on the By hand ones; Accept answers
    with the page, its notice and the row Ready to update."""
    harness, _, _, rows = prepared(response_service)
    browser = staff_browser(google)
    response, body = search(browser, ROUTE, {})
    assert response.status_code == 200
    page = body.decode()
    assert "To review (4)" in page and "Summary" not in page
    assert page.count('data-row-action="accept"') == 4
    assert page.count('data-row-action="entered_by_hand"') == 3
    # Staff do not start an update run.
    assert "Update ParishSoft" not in page
    response, body = search(
        browser, ROUTE, decide(rows["first_name"], "accept", note=" Checked ")
    )
    assert response.status_code == 200
    assert b"accepted." in body and b"Checked" in body
    assert state(rows["first_name"])[:2] == ("approved", "pending")
    assert ProposalReview.objects.get().note == "Checked"
    # The same form again (another tab, drawn before) is now stale.
    response, body = search(browser, ROUTE, decide(rows["first_name"], "accept"))
    assert response.status_code == 200
    assert b"not recorded, changed by someone else" in body
    assert ProposalReview.objects.count() == 1


def test_entered_by_hand_and_bulk_accept_over_http(response_service, google):
    """Entered by hand on a To review By hand row accepts and marks it; a
    bulk Accept covers the rest, each at the version its row showed."""
    harness, _, _, rows = prepared(response_service)
    browser = staff_browser(google)
    response, body = search(
        browser, ROUTE, decide(rows["email_opt_out"], "entered_by_hand")
    )
    assert response.status_code == 200 and b"marked entered by hand." in body
    assert state(rows["email_opt_out"])[1] == "resolved_external"
    assert ProposalResolution.objects.count() == 1
    chosen = [rows["first_name"], rows["prefix"], rows["home_address"]]
    form = {
        "stage": "all",
        "bulk": "accept",
        "bulk_key": str(uuid4()),
        "bulk_note": "",
        "item": [f"{row.pk}:{row.version}" for row in chosen],
    }
    response, body = search(browser, ROUTE, form)
    assert response.status_code == 200 and b"3 changes accepted." in body
    assert {state(row)[0] for row in chosen} == {"approved"}


def test_an_edit_is_checked_and_refused_at_its_field(response_service, google):
    """An address Edit sends its parts; a part the Family form would refuse
    is a 400 naming that part, and a good one is Accepted (edited)."""
    harness, _, _, rows = prepared(response_service)
    browser = staff_browser(google)
    home = rows["home_address"]
    parts = {f"value_{key}": value for key, value in address(line1="10 Way").items()}
    response, body = search(
        browser, ROUTE, decide(home, "edit") | parts | {"value_postal_code": "402"}
    )
    assert response.status_code == 400
    refusal = json.loads(body)
    assert [error["field_id"] for error in refusal["errors"]] == ["value_postal_code"]
    assert refusal["refusal"]["message"] == "Enter a five-digit ZIP code or ZIP+4."
    assert ProposalReview.objects.count() == 0
    response, body = search(browser, ROUTE, decide(home, "edit") | parts)
    assert response.status_code == 200 and b"accepted (edited)." in body
    assert fresh(home).admin_value["line1"] == "10 Way"


@pytest.mark.parametrize(
    "broken",
    [
        # A decision the change's stage does not offer.
        {"decide": "back_to_review"},
        # Census Add is its own slice (#1710).
        {"decide": "add", "item": None, "version": None},
        # Edit with no value, and a value for another decision.
        {"decide": "edit"},
        {"decide": "accept", "value": "x"},
        # A malformed item or version.
        {"item": "not-a-uuid"},
        {"version": "x"},
    ],
)
def test_a_malformed_or_unoffered_decision_is_a_400(response_service, google, broken):
    """Nothing is recorded and the page is not redrawn."""
    harness, _, _, rows = prepared(response_service)
    browser = staff_browser(google)
    form = {
        key: value
        for key, value in (decide(rows["prefix"], "accept") | broken).items()
        if value is not None
    }
    assert search(browser, ROUTE, form)[0].status_code == 400
    assert not ProposalReview.objects.exists()


def test_reopening_an_ignored_change_needs_a_note_over_http(response_service, google):
    """Back to review on an ignored change is a 400 without a note."""
    harness, _, _, rows = prepared(response_service)
    browser = staff_browser(google)
    response, _ = search(browser, ROUTE, decide(rows["prefix"], "ignore"))
    assert response.status_code == 200
    prefix = fresh(rows["prefix"])
    response, _ = search(browser, ROUTE, decide(prefix, "back_to_review"))
    assert response.status_code == 400
    response, body = search(
        browser, ROUTE, decide(prefix, "back_to_review", note="Asked again")
    )
    assert response.status_code == 200 and b"sent back to review." in body


def test_only_what_the_stage_offers_is_recorded(response_service, google):
    """The services allow more than the worklist offers; the page refuses the
    rest: Entered by hand on a Conflict (accepted first) and Accept on an
    Ignored change (back to review first)."""
    from .test_census_conflict_postgresql import conflicted

    harness, _, _, rows = prepared(response_service)
    browser = staff_browser(google)
    prefix = conflicted(rows["prefix"], "Mr.")
    response, _ = search(browser, ROUTE, decide(prefix, "entered_by_hand"))
    assert response.status_code == 400
    assert not ProposalResolution.objects.exists()
    search(browser, ROUTE, decide(rows["home_address"], "ignore"))
    response, _ = search(browser, ROUTE, decide(fresh(rows["home_address"]), "accept"))
    assert response.status_code == 400
    assert state(rows["home_address"])[0] == "ignored"


def test_a_rebound_entered_by_hand_key_is_a_409(response_service, google):
    """The same drawn form sent again with another note asks for a reload."""
    harness, _, _, rows = prepared(response_service)
    browser = staff_browser(google)
    form = decide(rows["email_opt_out"], "entered_by_hand")
    assert search(browser, ROUTE, form)[0].status_code == 200
    response, _ = search(browser, ROUTE, form | {"note": "Another"})
    assert response.status_code == 409


def fetch(browser, path):
    """A GET of ``path`` through the response-owned transport, as ``search``
    posts: the response and its whole body."""
    import socket

    server, peer = socket.socketpair()
    try:
        response = browser.get(path, **{"gunicorn.socket": server})
        body = (
            b"".join(response.streaming_content)
            if response.streaming
            else response.content
        )
        response.close()
        return response, body
    finally:
        server.close()
        peer.close()


def test_the_view_is_in_its_address_for_a_reload(response_service, google):
    """An in-place Apply's answer names the view's own address with only
    the choices a link may carry, never the name search; a GET of that
    address draws the same view, and a GET carrying the search is refused
    (R38 finding 6)."""
    prepared(response_service)
    browser = staff_browser(google)
    response, body = search(
        browser, ROUTE, {"stage": "all", "kind": "contact", "search": "Smith"}
    )
    assert response.status_code == 200
    page = body.decode()
    assert f'<a href="{ROUTE}?stage=all&amp;kind=contact" data-page-address' in page
    response, body = fetch(browser, ROUTE + "?stage=all&kind=contact")
    assert response.status_code == 200
    page = body.decode()
    assert '<option value="all" selected>' in page
    assert f'<a href="{ROUTE}?stage=all&amp;kind=contact" data-page-address' in page
    # The default view is the plain address.
    response, body = fetch(browser, ROUTE)
    assert response.status_code == 200
    assert f'<a href="{ROUTE}" data-page-address' in body.decode()
    response, _ = fetch(browser, ROUTE + "?search=Smith")
    assert response.status_code == 400
