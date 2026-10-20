"""Save and next and the remembered queue view on Ministry requests (#534).

Additional information had them too until it became a review worklist
(#1685), which decides requests on the list itself.

Each queue page remembers its applied filters, sort and page in the signed-in
session under an opaque token; item links, the item form and "Return to …"
carry only that token. Save and next saves like Save, then opens the next
open item after this one in the remembered view, or returns to the queue
after the last one. A stale save never moves on.
"""

import re
import socket
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.deployment import ServiceRole

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post
from .test_information_followup_postgresql import search
from .test_ministry_exports_postgresql import leader
from .test_ministry_followup_postgresql import edit, requests
from .test_ministry_reports_postgresql import setup
from .test_report_workspace_postgresql import read as get

pytestmark = pytest.mark.django_db(transaction=True)

UNKNOWN = "0" * 32


def linked(body, path):
    """(item ids in page order, the one queue token they all carry)."""
    found = re.findall(
        rb'href="'
        + re.escape(path.encode())
        + rb'([0-9a-f-]{36})/\?queue=([0-9a-f]{32})"',
        body,
    )
    tokens = {token for _, token in found}
    assert len(tokens) == 1
    return [item.decode() for item, _ in found], tokens.pop().decode()


def viewed(body):
    """The queue view's own token, from the address it puts in the address bar."""
    return re.search(rb'data-queue-address="[^"]*\?queue=([0-9a-f]{32})"', body)[
        1
    ].decode()


def fetched(browser, path):
    """GET ``path`` as ui-v1.js fetches an in-place history page."""
    server, peer = socket.socketpair()
    try:
        response = browser.get(
            path, HTTP_X_REQUESTED_WITH="fetch", **{"gunicorn.socket": server}
        )
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


def following(body):
    """The next item id the item page sent with its form ("" for none)."""
    return re.search(rb'name="next" value="([^"]*)"', body)[1].decode()


def test_ministry_save_and_next_follows_the_remembered_view(response_service, google):
    """Staff move through Ministry follow-up without losing the view."""
    harness = setup(response_service)
    browser, actor, _, _ = leader(harness, google, roles=("staff",))
    join, leave = requests()
    route = reverse("admin:ministry_report")
    # Request pages keep their addresses under follow-up/ (#1133).
    items = route + "follow-up/"
    view = {"stage": "all", "sort": "ministry", "size": "25"}
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, route, view)
        assert response.status_code == 200
        order, token = linked(body, items)
        assert set(order) == {str(join.pk), str(leave.pk)}
        # The filters stay private: only the opaque token is in any link.
        # (By Ministry's counts carry their own closed filters, #1133.)
        table = re.sub(
            rb'<section id="by-ministry".*?</section>', b"", body, flags=re.S
        )
        assert b"?search=" not in body and b"stage=all" not in table
        first, second = order
        response, body = get(browser, f"{items}{first}/?queue={token}")
        assert response.status_code == 200 and following(body) == second
        assert b"Save and next" in body and b"data-in-place-moves" in body
        assert f'name="queue" value="{token}"'.encode() in body
        assert f'ministries/?queue={token}">Return to'.encode() in body
        # An in-place history page swaps only the history, so it skips the
        # scan; an ordinary load of the same address still scans.
        paged = f"{items}{first}/?page=1&queue={token}"
        response, body = fetched(browser, paged)
        assert response.status_code == 200 and following(body) == ""
        assert following(get(browser, paged)[1]) == second
        form = {
            "expected_version": "1",
            "state": "in_progress",
            "outcome": "",
            "notes": "Left a message",
            "contact_channel": "",
            "queue": token,
            "then": "next",
            "next": second,
        }
        update = f"{items}{first}/record/"
        # A correctable refusal stays on the request, in place, keeping the
        # view's token and the next request (#553).
        response, body = search(
            browser,
            update,
            form | {"request_key": str(uuid4()), "state": "resolved"},
        )
        assert response.status_code == 400 and b"data-error-summary" in body
        assert f'name="queue" value="{token}"'.encode() in body
        assert following(body) == second
        # A stale save never moves on.
        stale = form | {"request_key": str(uuid4()), "expected_version": "9"}
        assert post(browser, update, stale).status_code == 409
        response = post(browser, update, form | {"request_key": str(uuid4())})
        assert response.status_code == 302
        assert response["Location"] == f"{items}{second}/?queue={token}"
        # Plain Save stays on the request, keeping the view.
        response = post(
            browser,
            f"{items}{second}/record/",
            form | {"request_key": str(uuid4()), "then": "", "next": ""},
        )
        assert response["Location"] == f"{items}{second}/?queue={token}"
        # The last request: nothing follows, so Save and next returns to the
        # remembered view, with its filters, sort and page size.
        response, body = get(browser, f"{items}{second}/?queue={token}")
        assert following(body) == "" and b"returns to the list" in body
        # That button submits natively: the queue loads once (ui-v1.js).
        assert b"data-in-place-native" in body
        response = post(
            browser,
            f"{items}{second}/record/",
            form | {"request_key": str(uuid4()), "expected_version": "2", "next": ""},
        )
        assert response["Location"] == f"{route}?queue={token}"
        response, body = get(browser, f"{route}?queue={token}")
        assert response.status_code == 200
        assert b'<option value="all" selected>Every stage' in body
        assert b'aria-sort="ascending"' in body
        assert linked(body, items) == (order, token)
        # An unknown token is the default view; anything else is refused.
        response, body = get(browser, f"{route}?queue={UNKNOWN}")
        assert response.status_code == 200
        assert b'<option value="to_review" selected>' in body
        for suffix in ("?queue=Private", f"?queue={token}&search=x", "?state=any"):
            assert get(browser, route + suffix)[0].status_code == 400
        for invalid in (
            {"then": "elsewhere"},
            {"next": "not-a-request"},
            {"queue": "Private"},
        ):
            values = form | {"request_key": str(uuid4()), "expected_version": "3"}
            assert post(browser, update, values | invalid).status_code == 400
    # A request that is not open is skipped: closing the second leaves
    # nothing after the first in the same view.
    _, leave_now = requests()
    other = leave_now if str(leave_now.pk) == second else join
    other.refresh_from_db()
    edit(
        harness,
        actor,
        other,
        state="closed_no_response",
        outcome="no_response",
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, f"{items}{first}/?queue={token}")
        assert response.status_code == 200 and following(body) == ""
    # A token belongs to its own sign-in: another session of the same person
    # finds nothing under it and sees the default view.
    other_browser, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(other_browser, f"{route}?queue={token}")
        assert response.status_code == 200
        assert b'<option value="to_review" selected>' in body
        assert token.encode() not in body


def test_ministry_leader_next_stays_in_scope(response_service, google):
    """A leader's next request comes only from the leader's own Ministries.

    The view holds only leave requests, which are all outside the leader's
    scope, so it is empty for the leader and the join request (outside the
    view) has nothing next. A scan that ignored scope would always find the
    other Ministry's open leave request: the order of the two requests (one
    submission instant) never decides the outcome.
    """
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google)
    join, leave = requests()
    route = reverse("admin:ministry_report")
    # Request pages keep their addresses under follow-up/ (#1133).
    items = route + "follow-up/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, route, {"stage": "all", "kind": "leave"})
        assert response.status_code == 200 and str(leave.pk).encode() not in body
        token = viewed(body)
        response, body = get(browser, f"{items}{join.pk}/?queue={token}")
        assert response.status_code == 200 and following(body) == ""
        assert str(leave.pk).encode() not in body
