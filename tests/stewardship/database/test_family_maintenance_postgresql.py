"""An Administrator closes and reopens the Family portal for maintenance (#274)."""

from uuid import uuid4

import pytest
from django.test import Client

from parishkit.stewardship.accounts import family_maintenance
from parishkit.stewardship.audit.models import AuditEvent

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change as change_rules
from .test_security_events_postgresql import home
from .test_user_rule_views_postgresql import web

pytestmark = pytest.mark.django_db(transaction=True)
ROUTE = "/admin/mail/family-portal/"
BANNER = "The Family portal is closed for maintenance."


@pytest.fixture(autouse=True)
def fresh_state():
    """No test sees another test's cached switch state."""
    family_maintenance._cache.update(at=None)
    yield
    family_maintenance._cache.update(at=None)


def change(browser, action, message=""):
    """Post the page's own form with the genuine CSRF cookie."""
    with web():
        return browser.post(
            ROUTE,
            {
                "action": action,
                "message": message,
                "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
            },
        )


def family(method, path):
    """A Family browser request under the real restricted web role."""
    client = Client()
    with web():
        if method == "post":
            return client.post(
                path,
                "{}",
                content_type="application/json",
                HTTP_ACCEPT="application/json",
            )
        return client.get(path)


def maintenance(response):
    """True when the response is the maintenance page or its JSON answer."""
    return b"Sorry, the site is temporarily unavailable" in response.content or (
        response.get("Content-Type", "").startswith("application/json")
        and b'"maintenance": true' in response.content
    )


def events():
    """The switch's audit trail, oldest first."""
    return list(
        AuditEvent.objects.filter(event_type__in=family_maintenance.EVENTS)
        .order_by("created_at")
        .values_list("event_type", flat=True)
    )


def test_close_shows_maintenance_to_families_and_reopen_restores(auth_service, google):
    """Every Family route closes at once; keepalive stays open; reopening restores."""
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        page = browser.get(ROUTE)
    assert page.status_code == 200 and b"The Family portal is open" in page.content
    # The message is kept in the audit log too, so ask for no personal data.
    assert b"leave out personal details" in page.content
    assert not maintenance(family("get", "/"))

    response = change(browser, "close", "  We expect to be back by 3 PM.  ")
    assert response.status_code == 302 and response["Location"] == ROUTE
    assert events() == ["family_maintenance_started"]
    event = AuditEvent.objects.get(event_type="family_maintenance_started")
    assert event.auditcontext.context == {
        "review_reason": "We expect to be back by 3 PM."
    }
    assert BANNER in home(browser)

    for path in ("/", "/family/", "/access/not-a-real-token"):
        closed = family("get", path)
        assert closed.status_code == 503 and maintenance(closed), path
        assert closed["Retry-After"] == str(family_maintenance.RETRY_AFTER_SECONDS)
        assert b"temporarily unavailable" in closed.content
        assert b"We expect to be back by 3 PM." in closed.content
    for path in ("/family/form", "/family/submit", "/family/presence"):
        closed = family("post", path)
        assert closed.status_code == 503, path
        assert closed.json() == {
            "maintenance": True,
            "message": "We expect to be back by 3 PM.",
        }
    # Keepalive and sign-out belong to the Family's own session.
    assert not maintenance(family("post", "/family/keepalive"))
    logout = family("post", "/family/logout")
    assert logout.status_code == 302 and not maintenance(logout)
    # Try again repeats a personal link rather than sending it to code sign-in.
    link = family("get", "/access/not-a-real-token")
    assert b'href="/access/not-a-real-token"' in link.content

    assert change(browser, "open").status_code == 302
    assert events() == ["family_maintenance_started", "family_maintenance_ended"]
    assert not family_maintenance.current_state(cached=False).closed
    assert not maintenance(family("get", "/"))
    assert BANNER not in home(browser)


def test_repeating_the_current_state_changes_nothing(auth_service, google):
    """Reopening an open portal is refused, and nothing is audited."""
    browser, _ = signed_in()
    assert change(browser, "open").status_code == 400
    assert events() == []


def test_a_message_with_an_email_address_is_refused(auth_service, google):
    """The note is the Administrator's text, never a container for addresses."""
    browser, _ = signed_in()
    assert change(browser, "close", "Write to office@example.org").status_code == 400
    assert events() == []
    assert not maintenance(family("get", "/"))


def test_changing_the_switch_needs_a_fresh_sign_in(auth_service, google, monkeypatch):
    """A session outside the fresh window must confirm with Google again."""
    from parishkit.stewardship.accounts import sessions

    browser, _ = signed_in()
    monkeypatch.setattr(sessions, "FRESH_SECONDS", -1)
    response = change(browser, "close")
    assert response.status_code == 403
    assert events() == []


def reader(auth_service, google, role):
    """Sign in a non-Administrator Admin role."""
    store = auth_service.store
    change_rules(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=(role,)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    return browser


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_other_roles_see_the_banner_but_cannot_switch(auth_service, google, role):
    """Chairs field Family calls, so they see the banner; only Administrators act."""
    admin, _ = signed_in()
    assert change(admin, "close").status_code == 302
    browser = reader(auth_service, google, role)
    assert BANNER in home(browser)
    with web():
        assert browser.get(ROUTE).status_code == 403
    assert change(browser, "open").status_code == 403
    assert events() == ["family_maintenance_started"]


def test_a_change_without_the_csrf_token_is_refused(auth_service, google):
    """The switch is an ordinary CSRF-protected form post."""
    browser, _ = signed_in()
    with web():
        response = browser.post(ROUTE, {"action": "close", "message": ""})
    assert response.status_code == 403
    assert events() == []


def test_the_note_is_rendered_as_text(auth_service, google):
    """Markup in the note is escaped for Families and Administrators alike."""
    browser, _ = signed_in()
    assert change(browser, "close", "<script>alert(1)</script>").status_code == 302
    closed = family("get", "/")
    assert b"<script>alert(1)</script>" not in closed.content
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in closed.content
    with web():
        page = browser.get(ROUTE).content
    assert b"<script>alert(1)</script>" not in page
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_the_newest_change_decides(auth_service, google):
    """Closed, reopened and closed again: the latest note and state win."""
    browser, _ = signed_in()
    assert change(browser, "close", "First").status_code == 302
    assert change(browser, "open").status_code == 302
    assert not family_maintenance.current_state(cached=False).closed
    assert change(browser, "close", "Second").status_code == 302
    state = family_maintenance.current_state(cached=False)
    assert state.closed and state.message == "Second"
    assert events() == [
        "family_maintenance_started",
        "family_maintenance_ended",
        "family_maintenance_started",
    ]
