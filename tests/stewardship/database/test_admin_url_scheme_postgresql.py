"""The Admin URL scheme against the real database (ADM-12, #525).

``current_campaign`` hands a view the current campaign's id. Through the
real middleware, a page's slashless form answers a signed-in Administrator
with a permanent redirect to the page, and an old Admin address is 404
(#864). The report roots' "no campaign" page is named after the report
opened (NAV-5b review).
"""

from uuid import UUID, uuid4

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
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


def post(browser, url):
    """POST with the real Admin CSRF cookie, set by opening a page first."""
    if "pk_admin_csrf" not in browser.cookies:
        browser.get("/admin/system/logs/")
    token = browser.cookies["pk_admin_csrf"].value
    return browser.post(url, {"csrfmiddlewaretoken": token})


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


def test_slashless_forms_redirect_and_old_addresses_are_gone(auth_service, google):
    """A slashless page URL lands on the page; an old address is 404 (#864)."""
    current = _current(auth_service.store)
    browser, _ = signed_in()
    for old, new in (
        ("/admin/system/logs?source=audit", "/admin/system/logs/?source=audit"),
        ("/admin/users/automation", "/admin/users/automation/"),
        ("/admin/mail/outgoing", "/admin/mail/outgoing/"),
    ):
        response = browser.get(old)
        assert response.status_code == 301, old
        assert response["Location"] == new
        # The filter only demonstrates that the query string is kept.
        page = new.split("?")[0]
        assert browser.get(page).status_code == 200, page
    for old in (
        "/admin/logs",
        "/admin/background",
        "/admin/configuration/integrations/parishsoft",
        "/admin/deliveries",
        "/admin/family-portal",
        "/admin/presence?size=25",
        f"/admin/campaign/{current}/delivery",
        f"/admin/campaign/{current}/settings",
        f"/admin/campaign/{uuid4()}/go-live",
        f"/admin/reports/{current}/participation/",
        f"/admin/reports/{uuid4()}/families/",
        "/admin/reports/campaigns/",
        "/admin/ministry-reports/",
    ):
        assert browser.get(old).status_code == 404, old
    # A form left open on an old address is not re-posted anywhere.
    assert post(browser, f"/admin/campaign/{current}/go-live").status_code == 404
    # The header's presence count still polls its address.
    polled = browser.get("/admin/presence?format=count")
    assert polled.status_code == 200
    assert set(polled.json()) == {"count", "as_of"}


def test_empty_report_roots_are_named_after_their_report(auth_service, google):
    """With no current campaign, each report root names its own report."""
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
        ("response_list", ["submitted"]),
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
