"""Old Admin addresses keep working against the real database (ADM-12, #525).

``legacy(…, campaign=True)`` redirects an old address that names the current
campaign and refuses any other campaign with 410, both never cached;
``current_campaign`` hands a view the current campaign's id. Through the
real middleware, an old System address answers a signed-in Administrator
with a permanent redirect to its page, and the page opens. The report roots'
"no campaign" page is named after the report opened (NAV-5b review).
"""

from uuid import UUID, uuid4

import pytest
from django.test import Client, RequestFactory, override_settings

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.web.admin_routes import current_campaign

from .auth_builders import signed_in
from .campaign_builders import add_draft
from .test_clone_views_postgresql import setup

pytestmark = pytest.mark.django_db(transaction=True)

REFUSAL = "This campaign is no longer the current campaign."


def _current(store):
    """Make a draft the current campaign and return its id."""
    result, row, _mail = add_draft(store, store.active(), uuid4())
    assert result.state == "applied"
    current = SystemConfiguration.objects.get().current_campaign_id
    assert current == UUID(row["id"])
    return current


# A campaign-scoped old address, routed through the real middleware.
CAMPAIGN_URLS = "tests.stewardship.database.legacy_campaign_urls"


def post(browser, url):
    """POST with the real Admin CSRF cookie, set by opening a page first."""
    if "pk_admin_csrf" not in browser.cookies:
        browser.get("/admin/system/logs/")
    token = browser.cookies["pk_admin_csrf"].value
    return browser.post(url, {"csrfmiddlewaretoken": token})


def _old(campaign):
    """The test-only old address naming ``campaign``."""
    return f"/admin/campaign/{campaign}/test-old-logs"


@override_settings(ROOT_URLCONF=CAMPAIGN_URLS)
def test_campaign_legacy_redirects_only_the_current_campaign(auth_service, google):
    """The current campaign redirects; another campaign is gone; neither cached."""
    current = _current(auth_service.store)
    browser, _ = signed_in()
    moved = browser.get(_old(current) + "?actor=a")
    assert moved.status_code == 301
    assert moved["Location"] == "/admin/system/logs/?actor=a"
    assert "no-store" in moved["Cache-Control"]
    posted = post(browser, _old(current))
    assert posted.status_code == 308 and "no-store" in posted["Cache-Control"]
    for answer in (browser.get(_old(uuid4())), post(browser, _old(uuid4()))):
        assert answer.status_code == 410
        assert "no-store" in answer["Cache-Control"]
        assert REFUSAL.encode() in answer.content
        assert "Location" not in answer


@override_settings(ROOT_URLCONF=CAMPAIGN_URLS)
def test_campaign_legacy_says_nothing_before_sign_in(auth_service, google):
    """Signed out, the current and another campaign get the same answer."""
    current = _current(auth_service.store)
    answers = [Client().get(_old(campaign)) for campaign in (current, uuid4())]
    for answer in answers:
        assert answer.status_code in {302, 401, 403}
        assert "/admin/system/logs/" not in answer.get("Location", "")
        assert REFUSAL.encode() not in answer.content
    assert answers[0].status_code == answers[1].status_code


@override_settings(ROOT_URLCONF=CAMPAIGN_URLS)
def test_campaign_legacy_refuses_when_there_is_no_current_campaign(
    auth_service, google
):
    """With no current campaign, every campaign address is gone."""
    setup(auth_service.store)
    assert SystemConfiguration.objects.get().current_campaign_id is None
    browser, _ = signed_in()
    assert browser.get(_old(uuid4())).status_code == 410


def test_current_campaign_supplies_the_current_id(auth_service):
    """A campaign-free route reuses a view that takes ``campaign_id``."""
    current = _current(auth_service.store)
    seen = {}

    def view(request, campaign_id, **kwargs):
        """Record what the wrapper passed."""
        seen.update(kwargs, campaign_id=campaign_id)
        return None

    current_campaign(view)(RequestFactory().get("/new/"), slot="logo")
    assert seen == {"campaign_id": current, "slot": "logo"}


def test_old_system_addresses_redirect_through_the_middleware(auth_service, google):
    """A signed-in Administrator's bookmark lands on the moved page."""
    browser, _ = signed_in()
    for old, new in (
        ("/admin/logs?source=audit", "/admin/system/logs/?source=audit"),
        ("/admin/background", "/admin/system/background/"),
        ("/admin/system/logs", "/admin/system/logs/"),
        ("/admin/users/automation", "/admin/users/automation/"),
        ("/admin/configuration/integrations", "/admin/system/integrations/"),
        (
            "/admin/configuration/integrations/parishsoft",
            "/admin/system/integrations/parishsoft/",
        ),
    ):
        response = browser.get(old)
        assert response.status_code == 301, old
        assert response["Location"] == new
        # The filter only demonstrates that the query string is kept.
        page = new.split("?")[0]
        assert browser.get(page).status_code == 200, page


def test_empty_report_roots_are_named_after_their_report(auth_service, google):
    """With no current campaign, each report root names its own report."""
    setup(auth_service.store)
    browser, _ = signed_in()
    for root, name in (
        ("/admin/reports/", b"Participation"),
        ("/admin/ministry-reports/", b"Ministry requests"),
    ):
        response = browser.get(root)
        assert response.status_code == 200, root
        assert b"<h1>" + name + b"</h1>" in response.content, root
        assert b"Return to Home" in response.content


def test_old_pause_and_resume_address_redirects_only_the_current_campaign(
    auth_service, google
):
    """Pause and resume mail's old address named a campaign (NAV-8)."""
    current = _current(auth_service.store)
    browser, _ = signed_in()
    moved = browser.get(f"/admin/campaign/{current}/delivery")
    assert moved.status_code == 301
    assert moved["Location"] == "/admin/mail/controls/"
    assert "no-store" in moved["Cache-Control"]
    posted = post(browser, f"/admin/campaign/{current}/delivery")
    assert posted.status_code == 308
    assert posted["Location"] == "/admin/mail/controls/"
    for answer in (
        browser.get(f"/admin/campaign/{uuid4()}/delivery"),
        post(browser, f"/admin/campaign/{uuid4()}/delivery"),
    ):
        assert answer.status_code == 410
        assert "no-store" in answer["Cache-Control"]
        assert REFUSAL.encode() in answer.content


def test_old_mail_addresses_redirect_through_the_middleware(auth_service, google):
    """Mail bookmarks land on the moved pages; the header count still polls."""
    _current(auth_service.store)
    browser, _ = signed_in()
    for old, new in (
        ("/admin/deliveries?state=all", "/admin/mail/outgoing/?state=all"),
        ("/admin/deliveries/refusals", "/admin/mail/refusals/"),
        ("/admin/deliveries/family-sends", "/admin/mail/family-history/"),
        ("/admin/family-portal", "/admin/mail/family-portal/"),
        ("/admin/presence?size=25", "/admin/mail/presence/?size=25"),
        ("/admin/mail/outgoing", "/admin/mail/outgoing/"),
    ):
        response = browser.get(old)
        assert response.status_code == 301, old
        assert response["Location"] == new
        assert browser.get(new).status_code == 200, new
    polled = browser.get("/admin/presence?format=count")
    assert polled.status_code == 200
    assert set(polled.json()) == {"count", "as_of"}


def test_old_mail_form_with_a_bad_csrf_token_is_refused(auth_service, google):
    """A form left open on an old address still needs its CSRF token (#525)."""
    from parishkit.stewardship.accounts import family_maintenance

    _current(auth_service.store)
    browser, _ = signed_in()
    browser.get("/admin/mail/family-portal/")
    response = browser.post(
        "/admin/family-portal",
        {"csrfmiddlewaretoken": "x" * 64, "action": "close", "message": ""},
        follow=True,
    )
    assert response.status_code == 403
    # Refused at the old address or after the 308: either way nothing changed.
    assert not family_maintenance.current_state(cached=False).closed
