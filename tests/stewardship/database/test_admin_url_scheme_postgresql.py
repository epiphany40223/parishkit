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
from django.urls import reverse

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.web.admin_routes import current_campaign

from ..policy_factory import address, assignment
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
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
    """With no current campaign, each report root names its own report.

    The retired Ministry reports root now redirects to Ministry requests,
    which shows the same page (NAV-11).
    """
    setup(auth_service.store)
    browser, _ = signed_in()
    for root, name in (
        (reverse("admin:reports"), b"Participation"),
        (reverse("admin:ministry_report"), b"Ministry requests"),
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


def test_old_campaign_setup_addresses_redirect_only_the_current_campaign(
    auth_service, google
):
    """Campaign setup's old addresses named a campaign (NAV-9)."""
    current = _current(auth_service.store)
    browser, _ = signed_in()
    old = f"/admin/campaign/{current}"
    for path, new in (
        ("settings", "/admin/campaign/settings/"),
        ("content", "/admin/campaign/content/"),
        ("content/email/initial", "/admin/campaign/content/email/initial/"),
        ("content/history", "/admin/campaign/content/history/"),
        ("images", "/admin/campaign/images/"),
        ("schedules", "/admin/campaign/schedules/"),
    ):
        moved = browser.get(f"{old}/{path}?start=default")
        assert moved.status_code == 301, path
        assert moved["Location"] == f"{new}?start=default"
        assert "no-store" in moved["Cache-Control"]
        assert browser.get(new).status_code == 200, new
    for path in ("settings", "content", "images/logo/remove", "share-options"):
        gone = browser.get(f"/admin/campaign/{uuid4()}/{path}")
        assert gone.status_code == 410, path
        assert REFUSAL.encode() in gone.content


def test_group_roots_open_the_first_entry_the_viewer_may_open(auth_service, google):
    """/admin/campaign/, /admin/mail/ and /admin/parish/ follow the menu."""
    _current(auth_service.store)
    browser, _ = signed_in()
    for root, first in (
        ("/admin/campaign/", "/admin/campaign/settings/"),
        # Pause and resume mail is greyed out in Testing mode.
        ("/admin/mail/", "/admin/mail/family-progress/"),
        ("/admin/parish/", "/admin/parish/settings/"),
    ):
        response = browser.get(root)
        assert response.status_code == 302, root
        assert response["Location"] == first
        assert "no-store" in response["Cache-Control"]
    for root in ("/admin/campaign/", "/admin/mail/", "/admin/parish/"):
        # Signed out: the sign-in refusal, never the group's first entry.
        signed_out = Client().get(root)
        assert signed_out.status_code == 403, root
        assert "Location" not in signed_out
        assert b"Sign in again" in signed_out.content
    # The no-slash form redirects to the root (no CommonMiddleware).
    moved = browser.get("/admin/campaign")
    assert moved.status_code == 301
    assert moved["Location"] == "/admin/campaign/"


def test_group_roots_without_an_open_entry_go_home(auth_service, google):
    """Staff open no Campaign setup page, so its root takes them Home."""
    store = auth_service.store
    _current(store)
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=("staff",)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    for root in ("/admin/campaign/", "/admin/mail/", "/admin/parish/"):
        response = browser.get(root)
        assert response.status_code == 302, root
        assert response["Location"] == "/admin/"


def test_old_campaign_setup_part_b_addresses_redirect_only_the_current_campaign(
    auth_service, google
):
    """The test email, go-live and Campaign Ministries old addresses (NAV-10)."""
    current = _current(auth_service.store)
    browser, _ = signed_in()
    revision = uuid4()
    for path, new in (
        (f"content/test/{revision}", reverse("admin:campaign_mail", args=[revision])),
        (
            f"content/test/{revision}/families",
            reverse("admin:campaign_mail_families", args=[revision]),
        ),
        ("go-live", reverse("admin:go_live")),
        ("go-live/families", reverse("admin:go_live_families")),
        ("production", reverse("admin:production_progress")),
        ("production/withdraw", reverse("admin:production_withdrawal")),
        ("ministries", reverse("admin:campaign_ministries")),
    ):
        moved = browser.get(f"/admin/campaign/{current}/{path}?size=25")
        assert moved.status_code == 301, path
        assert moved["Location"] == f"{new}?size=25"
        assert "no-store" in moved["Cache-Control"]
        gone = browser.get(f"/admin/campaign/{uuid4()}/{path}")
        assert gone.status_code == 410, path
        assert "no-store" in gone["Cache-Control"]


def test_old_go_live_form_never_acts_before_its_page(auth_service, google):
    """A readiness form left open on an old address starts nothing by itself.

    Another campaign's address answers 410 before any effect; the current
    one's answers a 308 to the page, which keeps its own checks.
    """
    current = _current(auth_service.store)
    browser, _ = signed_in()
    tasks = TaskRun.objects.count()
    values = {"action": "cleanup", "preview_token": "x", "acknowledge": "yes"}
    browser.get(reverse("admin:logs"))
    values["csrfmiddlewaretoken"] = browser.cookies["pk_admin_csrf"].value
    gone = browser.post(f"/admin/campaign/{uuid4()}/go-live", values)
    assert gone.status_code == 410 and "Location" not in gone
    moved = browser.post(f"/admin/campaign/{current}/go-live", values)
    assert moved.status_code == 308
    assert moved["Location"] == reverse("admin:go_live")
    assert not ProductionTransitionRequest.objects.exists()
    assert TaskRun.objects.count() == tasks


def test_new_campaign_pages_refuse_without_a_current_campaign(auth_service, google):
    """No current campaign: each moved page refuses plainly, never a server error."""
    setup(auth_service.store)
    browser, _ = signed_in()
    for name, args in (
        ("go_live", []),
        ("go_live_families", []),
        ("go_live_cleanup", [uuid4()]),
        ("go_live_links", [uuid4()]),
        ("production_confirmation", [uuid4(), uuid4()]),
        ("production_progress", []),
        ("production_withdrawal", []),
        ("campaign_mail", [uuid4()]),
        ("campaign_mail_families", [uuid4()]),
        ("campaign_ministries", []),
    ):
        response = browser.get(reverse(f"admin:{name}", args=args))
        assert 400 <= response.status_code < 500, (name, response.status_code)
    # Nor does any form posted to them: confirming or cancelling go-live,
    # retrying activation or sending a test (no campaign transaction runs).
    browser.get(reverse("admin:logs"))
    csrf = {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    for name, args, values in (
        ("go_live", [], {"action": "cleanup", "preview_token": "x"}),
        ("go_live_cleanup", [uuid4()], {"control": "x"}),
        ("go_live_links", [uuid4()], {"control": "x"}),
        (
            "production_confirmation",
            [uuid4(), uuid4()],
            {"action": "confirm", "preview": "x", "typed": "Production"},
        ),
        ("production_progress", [], {"control": "x"}),
        ("production_withdrawal", [], {"action": "confirm", "preview": "x"}),
        ("campaign_mail", [uuid4()], {"preview_token": "x"}),
        ("campaign_ministries", [], {"action": "preview"}),
    ):
        response = browser.post(reverse(f"admin:{name}", args=args), values | csrf)
        assert 400 <= response.status_code < 500, (name, response.status_code)
    assert not ProductionTransitionRequest.objects.exists()


# Old report addresses (NAV-11), each with its new page. The old address
# named the campaign right after /admin/reports/; a query string carries
# report filters.
OLD_REPORTS = (
    ("responses/", "response_dashboard", []),
    ("responses/submitted/", "response_list", ["submitted"]),
    ("participation/", "participation", []),
    ("financial/", "financial_report", []),
    ("talents/", "talents_report", []),
    ("information/", "information_queue", []),
    ("ministries/", "ministry_report", []),
    ("ministries/follow-up/", "ministry_followup", []),
    ("families/", "family_directory", []),
)


def test_old_report_addresses_redirect_only_the_current_campaign(auth_service, google):
    """Bookmarks and links to an old report page reach the current campaign's."""
    current = _current(auth_service.store)
    browser, _ = signed_in()
    item = uuid4()
    for path, name, args in (
        *OLD_REPORTS,
        (f"information/{item}/", "information_item", [item]),
        (f"ministries/follow-up/{item}/", "ministry_followup_item", [item]),
        (f"families/{item}/", "family_timeline", [item]),
    ):
        new = reverse(f"admin:{name}", args=args)
        moved = browser.get(f"/admin/reports/{current}/{path}?size=25")
        assert moved.status_code == 301, path
        assert moved["Location"] == f"{new}?size=25"
        assert "no-store" in moved["Cache-Control"]
        gone = browser.get(f"/admin/reports/{uuid4()}/{path}")
        assert gone.status_code == 410, path
        assert "no-store" in gone["Cache-Control"]
        assert REFUSAL.encode() in gone.content
    # The retired addresses that named no campaign open their reports.
    for old, name in (
        ("/admin/reports/campaigns/", "participation"),
        ("/admin/ministry-reports/", "ministry_report"),
        ("/admin/ministry-reports/campaigns/", "ministry_report"),
    ):
        moved = browser.get(old)
        assert moved.status_code == 301, old
        assert moved["Location"] == reverse(f"admin:{name}")


def test_old_report_forms_never_act_before_their_page(auth_service, google):
    """A report form left open on an old address starts nothing by itself.

    Another campaign's address answers 410 before any effect; the current
    one's answers a 308 that keeps the method and body, so the page's own
    checks (its CSRF token first) decide; a missing token is refused.
    """
    from parishkit.stewardship.reports.exact_models import ExactExportRequest
    from parishkit.stewardship.reports.export_models import ExportRequest

    current = _current(auth_service.store)
    browser, _ = signed_in()
    browser.get(reverse("admin:logs"))
    values = {
        "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
        "request_key": str(uuid4()),
        "format": "csv",
        "browser_timezone": "UTC",
    }
    item = uuid4()
    for path, name, args in (
        ("participation/export", "report_export_create", []),
        ("participation/exact-export", "report_exact_create", []),
        ("financial/export", "financial_export", []),
        ("information/export", "information_export", []),
        (f"information/{item}/update", "information_update", [item]),
        ("ministries/export/", "ministry_export", []),
        ("ministries/packet/", "ministry_packet", []),
        (f"ministries/follow-up/{item}/update", "ministry_followup_update", [item]),
        ("families/export", "family_directory_export", []),
        ("families/find", "find_family", []),
        ("responses/submitted/csv/", "response_list_export", ["submitted"]),
    ):
        gone = browser.post(f"/admin/reports/{uuid4()}/{path}", values)
        assert gone.status_code == 410 and "Location" not in gone, path
        moved = browser.post(f"/admin/reports/{current}/{path}", values)
        assert moved.status_code == 308, path
        assert moved["Location"] == reverse(f"admin:{name}", args=args)
        # Without its CSRF token the old address refuses the form outright.
        refused = browser.post(f"/admin/reports/{current}/{path}", {"x": "1"})
        assert refused.status_code == 403, path
    assert not ExportRequest.objects.exists()
    assert not ExactExportRequest.objects.exists()


def test_new_report_pages_refuse_or_explain_without_a_current_campaign(
    auth_service, google
):
    """No current campaign: no moved report page or form is a server error.

    The reports root, Ministry requests and Ministry follow-up show the "no
    campaign" page; the others refuse plainly (saying there is no current
    campaign), and no form starts an export.
    """
    from parishkit.stewardship.reports.exact_models import ExactExportRequest
    from parishkit.stewardship.reports.export_models import ExportRequest

    setup(auth_service.store)
    browser, _ = signed_in()
    item = uuid4()
    pages = (
        ("reports", []),
        ("response_dashboard", []),
        ("response_list", ["submitted"]),
        ("participation", []),
        ("financial_report", []),
        ("talents_report", []),
        ("information_queue", []),
        ("information_item", [item]),
        ("ministry_report", []),
        ("ministry_followup", []),
        ("ministry_followup_item", [item]),
        ("family_directory", []),
        ("family_timeline", [item]),
        ("weekly_digest_manual", []),
    )
    for name, args in pages:
        response = browser.get(reverse(f"admin:{name}", args=args))
        assert response.status_code < 500, (name, response.status_code)
        if name in {"reports", "ministry_report", "ministry_followup"}:
            assert response.status_code == 200, name
        else:
            assert 400 <= response.status_code < 500, (name, response.status_code)
            # Never "this campaign is no longer current": there is none.
            assert REFUSAL.encode() not in response.content, name
    browser.get(reverse("admin:logs"))
    values = {
        "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
        "request_key": str(uuid4()),
        "format": "csv",
        "browser_timezone": "UTC",
        "search": "examp",
    }
    for name, args in (
        ("report_export_create", []),
        ("report_exact_create", []),
        ("financial_report", []),
        ("financial_export", []),
        ("talents_report", []),
        ("talents_export", []),
        ("information_queue", []),
        ("information_export", []),
        ("information_update", [item]),
        ("ministry_report", []),
        ("ministry_joiners", []),
        ("ministry_leavers", []),
        ("ministry_export", []),
        ("ministry_packet", []),
        ("ministry_followup", []),
        ("ministry_followup_update", [item]),
        ("family_directory_export", []),
        ("find_family", []),
        ("response_list_export", ["submitted"]),
    ):
        response = browser.post(reverse(f"admin:{name}", args=args), values)
        assert response.status_code < 500, (name, response.status_code)
    assert not ExportRequest.objects.exists()
    assert not ExactExportRequest.objects.exists()


def test_reports_root_takes_a_ministry_leader_to_ministry_requests(
    auth_service, google
):
    """/admin/reports/ opens Ministry requests for a viewer without Participation."""
    store = auth_service.store
    _current(store)
    change(
        store,
        store.active(),
        uuid4(),
        [
            {"operation": "add", "section": "login_rules", **record}
            for record in (
                address("leader@example.org", roles=("ministry_leader",)),
                assignment("leader@example.org", ministry=9),
            )
        ],
    )
    google[0]["email"] = "leader@example.org"
    browser, _ = signed_in()
    response = browser.get(reverse("admin:reports"))
    assert response.status_code == 302
    assert response["Location"] == reverse("admin:ministry_report")
    assert "no-store" in response["Cache-Control"]
    # The form without the slash redirects to the root first.
    moved = browser.get(reverse("admin:reports").rstrip("/"))
    assert moved.status_code == 301
    assert moved["Location"] == reverse("admin:reports")


def test_export_page_keeps_no_old_address(auth_service, google):
    """The moved export and emailed report pages keep no old address (#864).

    The old export, latest-data export, manual weekly report and daily and
    weekly report addresses (those in report emails sent before NAV-12
    included) are gone (404), not redirected, through the real middleware;
    the shared export page has only its slashless form, and an unknown export
    is refused by the page.
    """
    current = _current(auth_service.store)
    browser, _ = signed_in()
    request = uuid4()
    page = reverse("admin:report_export", args=[request])
    moved = browser.get(f"/admin/reports/exports/{request}")
    assert moved.status_code == 301 and moved["Location"] == page
    for old in (
        f"/admin/reports/exact-exports/{request}/",
        f"/admin/reports/exports/{request}/cancel",
        f"/admin/reports/weekly-digests/request/{current}/",
        f"/admin/reports/daily-digests/{request}/",
        f"/admin/reports/daily-digests/{request}/chart.png",
        f"/admin/reports/weekly-digests/{request}/",
        f"/admin/reports/weekly-digests/{request}/items/{uuid4()}/",
    ):
        assert browser.get(old).status_code == 404, old
    assert 400 <= browser.get(page).status_code < 500
