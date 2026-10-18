"""The automation approval page, in a browser signed in with Google (ADM-11).

The page an Administrator opens from the command line's link: any live
sign-in approves, however old (#1547); it gives a wrong code and another
Administrator's pairing the same reply; it shows the request (label escaped,
scope as plain text, the host prefix) with a confused-deputy warning; and it
lets the scope and lifetime be lowered, never raised.
"""

import re

import pytest
from django.test import Client

from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.sessions import database_now

from .auth_builders import signed_in, stale_sign_in
from .automation_builders import HOST, post, start, two_admins  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/users/automation/approval/"


def lookup(browser, code):
    """Enter a user code."""
    return post(browser, PAGE, action="lookup", code=code)


def approve(browser, code, *, scope="full", days=30):
    """Approve a code through the page."""
    return post(browser, PAGE, action="approve", code=code, scope=scope, days=str(days))


def test_the_page_reviews_and_approves_a_request_in_place(auth_service, google):
    """The review shows what was asked, plainly; approval shows its result here."""
    browser, _ = signed_in()
    _, code = start(auth_service)
    page = browser.get(PAGE)
    assert page.status_code == 200 and b"Confirm with Google" not in page.content
    review = lookup(browser, code.lower()[:4] + "-" + code.lower()[4:])
    assert b"launch &lt;assistant&gt;" in review.content
    assert b"launch <assistant>" not in review.content
    assert b"<dd>Full</dd>" in review.content
    assert HOST[:12].encode() in review.content and HOST[:13].encode() not in (
        review.content
    )
    assert b"Approve only a code you started in your own terminal" in review.content
    approved = approve(browser, code)
    assert approved.status_code == 200 and b"Session approved" in approved.content
    assert AutomationSession.objects.get().scope == "full"


def test_a_wrong_code_and_another_administrators_pairing_look_the_same(
    two_admins,  # noqa: F811
    google,
):
    """No detail of any pairing is shown to the wrong Administrator."""
    browser, _ = signed_in()
    _, code = start(two_admins, email="other@example.org")
    for response in (
        lookup(browser, "ZZZZZZZZ"),
        lookup(browser, code),
        approve(browser, code),
    ):
        assert response.status_code == 200
        assert b"does not match a pending request" in response.content
        assert b"launch &lt;assistant&gt;" not in response.content
    assert not AutomationSession.objects.exists()


def test_the_page_lowers_but_never_raises_scope_or_lifetime(auth_service, google):
    """A read-only request offers no full scope; neither can be raised by hand."""
    browser, _ = signed_in()
    _, code = start(auth_service, scope="read-only", days=7)
    review = lookup(browser, code)
    assert b'value="full"' not in review.content and b"<dd>Read-only</dd>" in (
        review.content
    )
    assert approve(browser, code, scope="full", days=7).status_code == 400
    assert approve(browser, code, scope="read_only", days=8).status_code == 400
    assert not AutomationSession.objects.exists()
    assert approve(browser, code, scope="read_only", days=3).status_code == 200
    row = AutomationSession.objects.get()
    assert row.scope == "read_only"
    assert (row.expires_at - database_now()).days < 3


def test_an_old_sign_in_approves_without_a_step_up(auth_service, google):
    """A sign-in older than five minutes approves at once (#1547), through the
    SQL guard, which records the browser session's own sign-in instant."""
    browser, _ = signed_in()
    _, code = start(auth_service)
    stale_sign_in()
    page = browser.get(PAGE)
    assert page.status_code == 200 and b"Confirm with Google" not in page.content
    (field,) = re.findall(rb'<input id="automation-code"[^>]*>', page.content)
    assert b" disabled" not in field
    review = lookup(browser, code)
    assert review.status_code == 200 and b"launch &lt;assistant&gt;" in review.content
    approved = approve(browser, code)
    assert approved.status_code == 200 and b"Session approved" in approved.content
    assert AutomationSession.objects.get().scope == "full"


def test_staff_never_reach_the_page(two_admins, google):  # noqa: F811
    """Only Administrators approve sessions."""
    claims, _ = google
    claims.update(sub="staff-subject", email="staff@example.org")
    staff, _ = signed_in(Client(enforce_csrf_checks=True))
    assert staff.get(PAGE).status_code == 403
