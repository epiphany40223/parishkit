"""A current session whose role lacks the page is told so, not to sign in (#1924)."""

import json
from uuid import uuid4

import pytest
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from parishkit.stewardship.accounts.models import PortalSession

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = {"HTTP_ACCEPT": "text/html", "HTTP_SEC_FETCH_MODE": "navigate"}
SCRIPT = {"HTTP_ACCEPT": "application/json", "HTTP_SEC_FETCH_MODE": "cors"}
ROLE = "This page is for Administrators. Your access doesn&#x27;t include it."
# The error page's own sign-in link; the Admin base also carries a hidden
# signed-out dialog with that text.
SIGN_IN = '<p><a href="/admin/login">Sign in again</a></p>'


def signed_in_with(auth_service, google, email, roles):
    """Sign ``email`` in through an exact-address rule granting ``roles``."""
    store = auth_service.store
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **address(email, roles)}],
    )
    google[0].update(email=email, sub=f"synthetic-{uuid4().hex}")
    browser, login = signed_in()
    assert login.status_code == 302
    return browser


# The users page (the shared ``admin_editing.principal`` admission) and
# System logs (its own error page).
PAGES = ("admin:users", "admin:logs")


@pytest.mark.parametrize("roles", [("staff",), ("ministry_leader",)])
@pytest.mark.parametrize("name", PAGES)
def test_staff_and_leaders_are_told_the_page_is_for_administrators(
    auth_service, google, roles, name
):
    """The page names the role, links home and offers no sign-in again."""
    browser = signed_in_with(auth_service, google, "reader@example.org", roles)
    page = browser.get(reverse(name), **PAGE)
    body = page.content.decode()
    assert page.status_code == 403
    assert "<h1>Access unavailable</h1>" in body
    assert ROLE in body
    assert "Your session may have ended" not in body
    assert SIGN_IN not in body
    assert 'href="/admin/">Administration home</a>' in body
    # The in-place (script) request gets the same refusal and closed code.
    script = browser.get(reverse(name), **SCRIPT)
    assert script.status_code == 403
    data = json.loads(script.content)
    assert data["errors"][0]["code"] == "denied"
    assert data["refusal"]["message"] == (
        "This page is for Administrators. Your access doesn't include it."
    )
    assert data["refusal"]["link"] == {"url": "/admin/", "label": "Administration home"}


@pytest.mark.parametrize("name", PAGES)
def test_an_ended_session_still_says_to_sign_in_again(auth_service, google, name):
    """Without a current session the refusal stays the sign-in one."""
    browser = signed_in_with(auth_service, google, "reader@example.org", ("staff",))
    PortalSession.objects.filter(revoked_at__isnull=True).update(
        revoked_at=timezone.now(), version=F("version") + 1
    )
    page = browser.get(reverse(name), **PAGE)
    if name == "admin:users":
        # The shared admission's signed-out refusal: a page load goes to
        # sign-in with the page as next and the ended flag (#2058).
        assert (page.status_code, page["Location"]) == (
            302,
            "/admin/login?ended=1&next=%2Fadmin%2Fusers%2F",
        )
    else:
        # System logs answers with its own error page.
        body = page.content.decode()
        assert page.status_code == 403
        assert "Sign in again" in body
        assert ROLE not in body
    script = browser.get(reverse(name), **SCRIPT)
    assert script.status_code == 403
    assert "This page is for" not in script.content.decode()


def test_an_administrator_still_opens_the_page(auth_service, google):
    """The default synthetic Administrator is admitted as before."""
    browser, _ = signed_in()
    assert browser.get(reverse("admin:logs"), **PAGE).status_code == 200


STAFF = (
    "This page is for Administrators and Staff. Your access doesn&#x27;t include it."
)


def assert_role_page(page, text):
    """A 403 error page with the role wording and no Sign in again."""
    body = page.content.decode()
    assert page.status_code == 403
    assert text in body
    assert "Your session may have ended" not in body
    assert SIGN_IN not in body


@pytest.mark.parametrize("name", ["admin:response_dashboard", "admin:timeline"])
def test_a_leader_on_a_staff_report_is_told_it_is_for_staff(auth_service, google, name):
    """Report pages refuse through ``denial()`` with the Staff wording.

    The Response dashboard is gated by ``export_views._principal`` and the
    Timeline by its own ``require``; both run before any lookup, so the
    Family in the Timeline's address need not exist.
    """
    browser = signed_in_with(
        auth_service, google, "leader@example.org", ("ministry_leader",)
    )
    kwargs = {"family_id": uuid4()} if name == "admin:timeline" else {}
    assert_role_page(browser.get(reverse(name, kwargs=kwargs), **PAGE), STAFF)


def test_staff_bulk_action_on_sent_mail_is_refused_by_role(auth_service, google):
    """A Staff bulk POST to Sent mail is a 403 with the role wording, not a 500."""
    browser = signed_in_with(auth_service, google, "staff@example.org", ("staff",))
    browser.get("/admin/", **PAGE)
    values = {
        "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
        "action": "preview",
        "kind": "confirm_unsent",
        "purpose": "all",
        "note": "None of these went out.",
        "checked": "yes",
    }
    assert_role_page(browser.post(reverse("admin:deliveries"), values, **PAGE), ROLE)
    script = browser.post(reverse("admin:deliveries"), values, **SCRIPT)
    assert script.status_code == 403
    assert json.loads(script.content)["refusal"]["message"] == (
        "This page is for Administrators. Your access doesn't include it."
    )


def test_background_task_reads_refuse_staff_by_role(auth_service, google):
    """The task list's JSON read (``jobs.views._read_once``) names the role.

    An ended session still gets the plain denial without a role message.
    """
    browser = signed_in_with(auth_service, google, "staff@example.org", ("staff",))
    script = browser.get(reverse("admin:background_tasks"), **SCRIPT)
    assert script.status_code == 403
    data = json.loads(script.content)
    assert data["errors"][0]["code"] == "denied"
    assert data["refusal"]["message"] == (
        "This page is for Administrators. Your access doesn't include it."
    )
    PortalSession.objects.filter(revoked_at__isnull=True).update(
        revoked_at=timezone.now(), version=F("version") + 1
    )
    ended = browser.get(reverse("admin:background_tasks"), **SCRIPT)
    assert ended.status_code == 403
    assert "This page is for" not in ended.content.decode()
