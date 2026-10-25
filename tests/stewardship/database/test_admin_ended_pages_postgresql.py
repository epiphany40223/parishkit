"""Every Admin page load whose session has ended goes to sign-in (#2062).

#2058 sent the pages that answer with the shared signed-out refusal to the
sign-in page. These tests cover the rest: the pages with their own error
pages (System logs, System health, Sent mail, Background work), the
Ministry pages, the report pages, and the access gate before setup or
during a restore review. Scripts keep the typed 403 the session-ended
dialog reads, and each page's other refusals keep its own error page.
"""

import json
import re
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from django.db.models import F
from django.urls import get_resolver, reverse
from django.utils import timezone

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

from .auth_builders import signed_in, unguarded
from .campaign_builders import add_draft

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = {"HTTP_ACCEPT": "text/html", "HTTP_SEC_FETCH_MODE": "navigate"}
SCRIPT = {"HTTP_ACCEPT": "application/json", "HTTP_SEC_FETCH_MODE": "cors"}

# Admin routes whose GET is not a page load an ended session sends to
# sign-in, each for its own reason.
NOT_SENT_TO_SIGN_IN = {
    # The sign-in page itself.
    "login",
    # The session-ended dialog's own script read, a 401 by contract.
    "session_status",
    # Only its JSON reads answer here; any other read is 404 (#864).
    "presence_count",
    # The parish logo, which Family pages show too; no session needed.
    "branding_asset",
    # Copy campaign is refused before the session is read (rule 10).
    "campaign_clone",
    # Opens System health, which then sends the page load to sign-in.
    "system",
    # Setup's cancellation, admitted only for the original setup sign-in.
    "setup_finishing",
}
# Without a current campaign, a campaign test email's address names no
# campaign, so it is refused as malformed (400) before the session is read.
NO_CAMPAIGN = {"campaign_mail", "campaign_mail_families"}


def _routes():
    """``(name, converters)`` for every named Admin route but slashless ones.

    A slashless address only redirects to its slashed page, which is
    covered itself.
    """
    admin = next(
        pattern
        for pattern in get_resolver().url_patterns
        if getattr(pattern, "namespace", None) == "admin"
    )
    for pattern in admin.url_patterns:
        name = getattr(pattern, "name", None)
        if name and not name.endswith("_slashless"):
            yield name, re.findall(r"<(\w+):(\w+)>", str(pattern.pattern))


def _url(name, converters):
    """The route's address with plausible arguments (an int is a page
    position, as the review wizard's run pages take)."""
    plausible = {"uuid": uuid4, "int": lambda: 1}
    arguments = {
        parameter: plausible.get(converter, lambda: "parishsoft")()
        for converter, parameter in converters
    }
    return reverse(f"admin:{name}", kwargs=arguments)


def _sign_in(page):
    """The sign-in address an ended session's load of ``page`` goes to."""
    query = {"ended": "1"} | ({} if page == "/admin/" else {"next": page})
    return f"/admin/login?{urlencode(query)}"


def _ended(**fields):
    """A browser whose Admin session has ended (revoked, as by a sign-out)."""
    browser, login = signed_in(**fields)
    assert login.status_code == 302
    PortalSession.objects.filter(revoked_at__isnull=True).update(
        revoked_at=timezone.now(), version=F("version") + 1
    )
    return browser


@pytest.mark.parametrize("campaign", [False, True])
def test_every_admin_page_load_goes_to_sign_in(auth_service, google, campaign):
    """Each Admin GET route redirects an ended session to sign-in with next.

    With and without a current campaign, so a view that reads the campaign
    before the session is caught too. A route that answers no GET (405) is
    a form or script target, not a page load. Any new page with its own
    handling of a missing session fails here until it sends the page load
    to sign-in, or is listed in ``NOT_SENT_TO_SIGN_IN`` with its reason.
    """
    if campaign:
        store = auth_service.store
        add_draft(store, store.active(), uuid4())
    browser = _ended()
    wrong = {}
    for name, converters in sorted(_routes()):
        if name in NOT_SENT_TO_SIGN_IN or (not campaign and name in NO_CAMPAIGN):
            continue
        url = _url(name, converters)
        response = browser.get(url, **PAGE)
        if response.status_code == 405:
            continue
        answer = (response.status_code, response.get("Location"))
        if answer != (302, _sign_in(url)):
            wrong[name] = answer
    assert wrong == {}
    # The System group's root opens System health, which signs in above.
    response = browser.get(reverse("admin:system"), **PAGE)
    assert (response.status_code, response["Location"]) == (
        302,
        reverse("admin:system_health"),
    )


# One page per family of views with its own error page or admission.
FAMILIES = (
    "admin:logs",
    "admin:system_health",
    "admin:deliveries",
    "admin:mail_queue",
    "admin:family_email_sends",
    "admin:background",
    "admin:reports",
    "admin:ministry_report",
    "admin:census_changes",
)


@pytest.mark.parametrize("name", FAMILIES)
def test_scripts_keep_the_typed_signed_out_refusal(auth_service, google, name):
    """Fetch/JSON callers get the 403 the session-ended dialog reads."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    browser = _ended()
    response = browser.get(reverse(name), **SCRIPT)
    assert response.status_code == 403
    data = json.loads(response.content)
    assert data["errors"][0]["code"] == "denied"
    assert data["refusal"]["link"] == {"url": "/admin/login", "label": "Sign in"}


@pytest.mark.parametrize(
    "name,heading",
    [
        ("admin:logs", "System logs unavailable"),
        ("admin:deliveries", "Delivery action unavailable"),
        ("admin:mail_queue", "Delivery action unavailable"),
    ],
)
def test_other_refusals_keep_the_page_s_own_error(auth_service, google, name, heading):
    """A current session's refused request still gets the page's own page.

    Here an address with a query the page does not take, a 400.
    """
    browser, login = signed_in()
    assert login.status_code == 302
    response = browser.get(reverse(name), {"unexpected": "1"}, **PAGE)
    assert response.status_code == 400
    assert f"<h1>{heading}</h1>" in response.content.decode()


def test_a_restore_review_sends_an_ended_session_to_sign_in(auth_service, google):
    """The access gate answers with the shared signed-out refusal.

    A page load goes to sign-in with the page and the ended flag, and a
    script gets the typed 403 the session-ended dialog reads.
    """
    browser = _ended()
    with unguarded():
        SystemConfiguration.objects.update(restore_review_required=True)
    for path in ("/admin/users/", "/admin/maintenance"):
        response = browser.get(path, **PAGE)
        assert (response.status_code, response["Location"]) == (302, _sign_in(path))
        script = browser.get(path, **SCRIPT)
        assert script.status_code == 403
        assert json.loads(script.content)["refusal"]["link"]["url"] == "/admin/login"
