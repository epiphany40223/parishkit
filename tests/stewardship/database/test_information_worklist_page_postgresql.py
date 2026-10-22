"""The Additional information review worklist page (#1685).

The page lists the requests by stage and records each decision posted to
it, answering with the same page and a notice; a request's own page shows
its stage and its history worded by decision. Read and decided under the
real web login.
"""

import re
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.campaigns.models import FamilyCampaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    AdditionalInformationRevision,
)

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_information_followup_postgresql import search
from .test_report_workspace_postgresql import read
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

ROUTE = "/admin/reports/information/"


def decision(item, action, *, note="", key=None, **view):
    """One row icon's post: the decision, the row's item and version, the view."""
    item.refresh_from_db()
    return {
        "decide": action,
        "item": str(item.pk),
        "version": str(item.version),
        "request_key": str(key or uuid4()),
        "note": note,
        **view,
    }


def stage_options(body):
    """The Stage filter's choices as the page draws them."""
    menu = body.decode().split('id="information-stage"')[1].split("</select>")[0]
    return re.findall(r">([^<]+)</option>", menu)


def test_the_worklist_lists_and_decides_requests_in_place(
    live_response_service, google
):
    """To review first; each decision answers with the page and its notice."""
    harness = live_response_service
    item = AdditionalInformationItem.objects.get(
        submission=respond(harness, "Please call about the choir")
    )
    duid = FamilyCampaign.objects.get(pk=item.submission.family_id).family_duid
    browser, login = signed_in()
    assert login.status_code == 302
    assert reverse("admin:information_queue") == ROUTE
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, ROUTE)
        assert response.status_code == 200 and response["Cache-Control"] == "no-store"
        page = body.decode()
        assert stage_options(body) == [
            "To review (1)",
            "To handle (0)",
            "Ignored (0)",
            "Handled (0)",
            "History (0)",
            "Every stage (1)",
        ]
        # The shared worklist: no chart, no Filters disclosure, no Baseline
        # or Reviewer's value column, and the export pane below the table.
        head = page.split("<thead>")[1].split("</thead>")[0]
        assert "Reviewer's value" not in head and "Baseline" not in head
        assert "Family's answer" in head and "Family DUID" in head
        assert "information-followup-spec" not in page
        assert page.index('id="table-filters"') < page.index("<table")
        assert page.index("<table") < page.index('id="table-export"')
        # The Family's answer links to the request's own page; the name opens
        # its Timeline.
        assert f'href="{ROUTE}{item.pk}/">Please call about the choir</a>' in page
        assert '<th scope="row"><a href="/admin/reports/families/' in page
        # A list read by GET takes no query string: its view is POST only.
        assert read(browser, ROUTE + "?search=choir")[0].status_code == 400
        # Accept: the row leaves To review, and the notice names it by DUID.
        accept = decision(item, "accept", note="Will call")
        response, body = search(browser, ROUTE, accept)
        assert response.status_code == 200
        assert f"The request from Family DUID {duid} accepted.".encode() in body
        assert "To handle (1)" in stage_options(body)
        # A Stage alone may come by GET, a My review work card's link (#1818):
        # the page opens at that Stage. Anything more, a repeated Stage or one
        # the filter does not offer is the page's 400.
        response, body = read(browser, ROUTE + "?stage=to_handle")
        assert response.status_code == 200
        assert b'<option value="to_handle" selected>' in body
        # Its address carries the Stage, so a reload draws the same view;
        # To review's is the plain address.
        assert f'<a href="{ROUTE}?stage=to_handle" data-page-address'.encode() in body
        assert f'href="{ROUTE}{item.pk}/">'.encode() in body
        _, body = read(browser, ROUTE + "?stage=to_review")
        assert f'href="{ROUTE}{item.pk}/">'.encode() not in body
        assert f'<a href="{ROUTE}" data-page-address'.encode() in body
        for query in (
            "?stage=nope",
            "?stage=to_handle&search=choir",
            "?stage=a&stage=b",
        ):
            assert read(browser, ROUTE + query)[0].status_code == 400, query
        # The retired submitted-date filter (#1888), from an old link or a
        # page drawn before then, is dropped unread, never refused.
        stale = "?stage=to_handle&start=2026-11-01&end=bad&zone=Nowhere/Zone"
        response, body = read(browser, ROUTE + stale)
        assert response.status_code == 200
        assert b'<option value="to_handle" selected>' in body
        assert b'name="start"' not in body
        stale = {"stage": "to_handle", "search": "", "start": "bad", "zone": ""}
        assert search(browser, ROUTE, stale)[0].status_code == 200
        # The same post again replays it; the key with another choice is a
        # 409 asking for a reload.
        accept["version"] = str(int(accept["version"]))
        assert search(browser, ROUTE, accept)[0].status_code == 200
        rebound = accept | {"decide": "ignore"}
        assert search(browser, ROUTE, rebound)[0].status_code == 409
        assert AdditionalInformationRevision.objects.count() == 1
        # On To handle the Accept icon is drawn selected, Handled offered.
        response, body = search(browser, ROUTE, {"stage": "to_handle"})
        page = body.decode()
        accepted = page.split('aria-label="Accept the request from')[1].split(">")[0]
        assert 'aria-pressed="true"' in accepted and 'aria-disabled="true"' in accepted
        assert 'data-row-action="handled"' in page
        assert "Will call" in page
        # The export pane takes the view: To handle is follow-up needed.
        export = page.split('id="table-export"')[1].split("</form>")[0]
        assert 'name="needed" value="yes"' in export
        assert 'name="completed" value="no"' in export
        # A stale post (the version moved) records nothing and says so.
        stale = decision(item, "handled") | {"version": "1"}
        response, body = search(browser, ROUTE, stale)
        assert response.status_code == 200
        assert b": not recorded, changed by someone else." in body
        # Handled, in bulk from the selection bar.
        item.refresh_from_db()
        bulk = {
            "bulk": "handled",
            "bulk_key": str(uuid4()),
            "bulk_note": "Called them",
            "item": f"{item.pk}:{item.version}",
            "stage": "to_handle",
        }
        response, body = search(browser, ROUTE, bulk)
        assert response.status_code == 200
        assert f"The request from Family DUID {duid} marked handled.".encode() in body
        assert "Handled (1)" in stage_options(body)
        # The request's page: its stage and its history, by decision.
        response, body = read(browser, f"{ROUTE}{item.pk}/")
        page = body.decode()
        assert response.status_code == 200 and "Stage:" in page
        assert '<span class="status-word">Handled</span>' in page
        assert "Version 2: Accepted" in page and "Version 3: Handled" in page
        assert "Save follow-up" not in page and "<textarea" not in page


def test_ignore_reopening_takes_an_optional_note(live_response_service, google):
    """Back to review on an Ignored request records with no note (#1889)."""
    harness = live_response_service
    item = AdditionalInformationItem.objects.get(
        submission=respond(harness, "Just so you know")
    )
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, ROUTE, decision(item, "ignore"))
        assert response.status_code == 200 and b"ignored." in body
        assert "Ignored (1)" in stage_options(body)
        reopened = decision(item, "back_to_review")
        response, body = search(browser, ROUTE, reopened)
        assert response.status_code == 200 and b"sent back to review." in body
        assert "To review (1)" in stage_options(body)
        # A request of another campaign, or none at all, is refused.
        unknown = decision(item, "accept") | {"item": str(uuid4())}
        assert search(browser, ROUTE, unknown)[0].status_code == 403
        for malformed in (
            decision(item, "edit"),
            decision(item, "accept") | {"value": "x"},
            {"stage": "nowhere"},
        ):
            assert search(browser, ROUTE, malformed)[0].status_code == 400


def test_the_menu_counts_the_requests_to_review(live_response_service, google):
    """The menu's Additional information count is the worklist's To review
    count (#1685): a decision moves the request out of it, and Back to
    review brings it back."""
    from types import SimpleNamespace

    from parishkit.stewardship.accounts import admin_context
    from parishkit.stewardship.accounts.models import PortalUser
    from parishkit.stewardship.accounts.policy import Principal

    harness = live_response_service
    item = AdditionalInformationItem.objects.get(
        submission=respond(harness, "Please count me")
    )
    browser, _ = signed_in()
    staff = Principal(
        PortalUser.objects.get(email="admin@example.org").pk,
        frozenset({"staff"}),
        frozenset(),
    )
    items = [SimpleNamespace(name="information_queue", url=ROUTE)]

    def count():
        """The menu's count, read as the web role reads it."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            return admin_context._open_counts(
                SimpleNamespace(), staff, items, harness.campaign
            ).get("information_queue")

    assert count() == 1
    for action, note, expected in (
        ("accept", "", 0),
        ("back_to_review", "", 1),
        ("ignore", "", 0),
        ("back_to_review", "Misread it", 1),
    ):
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response, _ = search(browser, ROUTE, decision(item, action, note=note))
        assert response.status_code == 200, action
        assert count() == expected, action


def test_a_request_saved_before_0126_as_not_needed_is_ignored_everywhere(
    live_response_service, google
):
    """A revision from before migration 0126 left follow-up not needed: the
    worklist lists the request as Ignored and the menu does not count it."""
    from types import SimpleNamespace

    from django.db import connection, transaction

    from parishkit.stewardship.accounts import admin_context
    from parishkit.stewardship.accounts.models import PortalUser
    from parishkit.stewardship.accounts.policy import Principal

    harness = live_response_service
    item = AdditionalInformationItem.objects.get(
        submission=respond(harness, "Saved long ago")
    )
    browser, _ = signed_in()
    actor = PortalUser.objects.get(email="admin@example.org").pk
    # Written with the triggers off, as only a database from before 0126
    # holds such a revision.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        AdditionalInformationRevision.objects.create(
            actor_id=actor,
            item_id=item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=False,
            followed_up=False,
            notes="Nothing to do",
        )
        AdditionalInformationItem.objects.filter(pk=item.pk).update(
            version=item.version + 1
        )
    staff = Principal(actor, frozenset({"staff"}), frozenset())
    items = [SimpleNamespace(name="information_queue", url=ROUTE)]
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert (
            admin_context._open_counts(
                SimpleNamespace(), staff, items, harness.campaign
            ).get("information_queue")
            == 0
        )
        response, body = read(browser, ROUTE)
    assert response.status_code == 200
    assert stage_options(body)[:3] == ["To review (0)", "To handle (0)", "Ignored (1)"]
