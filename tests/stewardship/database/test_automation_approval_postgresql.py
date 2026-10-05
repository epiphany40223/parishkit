"""The automation approval page, in a browser signed in with Google (ADM-11).

The page an Administrator opens from the command line's link: it needs a
fresh sign-in, offering "Confirm with Google" rather than refusing when the
sign-in is older than five minutes; it gives a wrong code and another
Administrator's pairing the same reply; it shows the request (label escaped,
scope as plain text, the host prefix) with a confused-deputy warning; and it
lets the scope and lifetime be lowered, never raised.
"""

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


def test_a_stale_sign_in_gets_the_confirm_step_not_a_refusal(auth_service, google):
    """Older than five minutes: Confirm with Google, and no code is accepted."""
    browser, _ = signed_in()
    _, code = start(auth_service)
    stale_sign_in()
    page = browser.get(PAGE)
    assert page.status_code == 200
    assert b"Confirm with Google" in page.content
    assert b'name="code" autocomplete="off" spellcheck="false" maxlength="16" ' in (
        page.content
    )
    assert b"required disabled" in page.content
    review = lookup(browser, code)
    assert review.status_code == 200 and b"Confirm with Google" in review.content
    assert b"launch &lt;assistant&gt;" not in review.content
    assert approve(browser, code).status_code == 200
    assert not AutomationSession.objects.exists()


def test_staff_never_reach_the_page(two_admins, google):  # noqa: F811
    """Only Administrators approve sessions."""
    claims, _ = google
    claims.update(sub="staff-subject", email="staff@example.org")
    staff, _ = signed_in(Client(enforce_csrf_checks=True))
    assert staff.get(PAGE).status_code == 403
