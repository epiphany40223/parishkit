"""The Admin URL scheme (admin-portal spec, "URL scheme", #525).

Groups already moved to the scheme (System, with NAV-6) put every page under
``/admin/<group>/`` with a trailing slash, name no campaign and use nouns,
never GET verbs. Each page's form without the trailing slash redirects
permanently: 301 for GET and HEAD, 308 for other methods, keeping the query
string. Old addresses are not kept: each is 404 (#864). The JSON reads
scripts poll keep their addresses (decision 8).
"""

import re
from uuid import UUID

import pytest
from django.http import Http404
from django.test import RequestFactory
from django.urls import Resolver404, resolve, reverse

from parishkit.stewardship.accounts import admin_navigation as navigation
from parishkit.stewardship.admin_urls import (
    campaign,
    mail,
    parish,
    reports,
    slashless,
    system,
)
from parishkit.stewardship.reports.daily_digest import DailyDigestDocument
from parishkit.stewardship.reports.weekly_digest import WeeklyDigestDocument

# Menu groups whose URLs follow the scheme so far, by URL segment.
MOVED = {"system", "parish", "mail", "campaign", "reports"}
GROUPS = (
    system.patterns
    + parish.patterns
    + mail.patterns
    + campaign.patterns
    + reports.patterns
)
# Route segments that would be a GET target naming an action.
VERBS = {
    "acknowledge",
    "clear",
    "create",
    "delete",
    "dismiss",
    "edit",
    "find",
    "join",
    "leave",
    "new",
    "remove",
    "resolve",
    "retry",
    "select",
    "update",
    "withdraw",
}
# Segments the spec's placement table names on purpose: Send a weekly report
# now is the form for a new weekly report (``emailed/weekly/new/``).
SPEC_NAMED = {"weekly_digest_manual": {"new"}}
# JSON reads that scripts poll; they keep their old addresses (decision 8).
POLLED = {"background_counts", "background_tasks", "background_task"}
TASK = UUID(int=7)
SAMPLES = {"uuid": str(TASK), "str": "parishsoft", "slug": "logo"}


def _example(route):
    """A concrete path for a route pattern, with sample arguments."""
    return "/admin/" + re.sub(
        r"<(\w+):\w+>", lambda match: SAMPLES[match.group(1)], route
    )


@pytest.mark.parametrize("pattern", GROUPS, ids=lambda p: p.name)
def test_moved_routes_follow_the_scheme(pattern):
    """Under their group, trailing slash, no campaign, nouns only."""
    route = str(pattern.pattern)
    assert route.split("/")[0] in MOVED
    # Pages end in "/"; an image keeps its file extension.
    assert route.endswith("/") or (
        pattern.name not in navigation.PAGES and route.endswith(".png")
    )
    assert "campaign_id" not in route
    segments = {segment for segment in route.split("/") if segment}
    assert not segments & VERBS - SPEC_NAMED.get(pattern.name, set()), route


@pytest.mark.parametrize(
    "name",
    sorted(name for name, page in navigation.PAGES.items() if page.section in MOVED),
)
def test_every_moved_page_reverses_under_its_group(name):
    """Each page's URL is /admin/<group>/…/ for its menu group."""
    arguments = {
        parameter: "parishsoft" if parameter == "target" else TASK
        for parameter in navigation.route_parameters()[name]
    }
    url = reverse(f"admin:{name}", kwargs=arguments)
    assert url.startswith(f"/admin/{navigation.PAGES[name].section}/")
    assert url.endswith("/")


# Admin form actions that still name a campaign in their address (#865).
# None is left: #758 retired the JSON export API and the old postal routes,
# the last ones. A new route naming a campaign fails below instead of
# slipping in.
CAMPAIGN_ROUTES = set()


def test_no_admin_page_names_a_campaign():
    """Single-campaign interim: Admin addresses carry no campaign id (#865).

    Only the known form actions above may still take one; the old campaign
    addresses are gone (#864).
    """
    naming = {
        name
        for name, parameters in navigation.route_parameters().items()
        if "campaign_id" in parameters
    }
    assert naming == CAMPAIGN_ROUTES
    assert not CAMPAIGN_ROUTES & set(navigation.PAGES)


def test_weekly_report_request_names_no_campaign():
    """Send a weekly report now is a reports page; its old address is gone."""
    url = reverse("admin:weekly_digest_manual")
    assert url == "/admin/reports/emailed/weekly/new/"
    assert resolve(url).url_name == "weekly_digest_manual"
    # No redirect from the old campaign address (#864).
    with pytest.raises(Resolver404):
        resolve(f"/admin/reports/weekly-digests/request/{TASK}/")


def test_polled_reads_keep_their_addresses():
    """Scripts poll these JSON reads; they do not move (decision 8)."""
    assert reverse("admin:background_counts") == "/admin/background/counts"
    assert reverse("admin:background_tasks") == "/admin/background/tasks"
    assert (
        reverse("admin:background_task", args=[TASK])
        == f"/admin/background/tasks/{TASK}"
    )
    assert not POLLED & set(slashless.TARGETS.values())
    # The header's presence count; only the page moved (NAV-8).
    assert reverse("admin:presence_count") == "/admin/presence"
    assert reverse("admin:presence") == "/admin/mail/presence/"


@pytest.mark.parametrize(
    "query", ["format=count", "format=json", "format=json&format=count"]
)
def test_old_presence_address_still_answers_polled_reads(monkeypatch, query):
    """A header loaded before the move keeps polling the old address."""
    answered = []
    monkeypatch.setattr(
        mail.presence, "active_families", lambda request: answered.append(request)
    )
    request = RequestFactory().get("/admin/presence?" + query)
    assert mail.presence_reads(request) is None
    assert answered == [request]


@pytest.mark.parametrize("query", ["", "?size=25&page=2", "?format=html"])
def test_old_presence_page_address_is_gone(monkeypatch, query):
    """Without a polled format, the read would be the moved page: 404 (#864)."""
    monkeypatch.setattr(mail.presence, "active_families", pytest.fail)
    for method in ("get", "head", "post"):
        request = getattr(RequestFactory(), method)("/admin/presence" + query)
        with pytest.raises(Http404):
            mail.presence_reads(request)


T = "00000000-0000-0000-0000-000000000007"
# Every page's form without its trailing slash (with sample arguments) and
# the exact page it must reach, written out by hand so a typo in the
# slashless table cannot hide behind the same table: the spec's placement
# table and URL scheme are the source.
SLASHLESS_EXPECTED = {
    "/admin/system": "/admin/system/",
    "/admin/system/health": "/admin/system/health/",
    "/admin/system/source-form": "/admin/system/source-form/",
    "/admin/system/integrations": "/admin/system/integrations/",
    "/admin/system/integrations/parishsoft": "/admin/system/integrations/parishsoft/",
    f"/admin/system/key-changes/{T}": f"/admin/system/key-changes/{T}/",
    f"/admin/system/key-changes/{T}/selection": (
        f"/admin/system/key-changes/{T}/selection/"
    ),
    "/admin/system/background": "/admin/system/background/",
    f"/admin/system/background/{T}": f"/admin/system/background/{T}/",
    "/admin/system/logs": "/admin/system/logs/",
    "/admin/parish/settings": "/admin/parish/settings/",
    "/admin/parish/logos": "/admin/parish/logos/",
    f"/admin/parish/logos/{T}": f"/admin/parish/logos/{T}/",
    "/admin/parish/ministries": "/admin/parish/ministries/",
    "/admin/parish/files": "/admin/parish/files/",
    "/admin/parish/files/deletion": "/admin/parish/files/deletion/",
    f"/admin/parish/files/{T}/name": f"/admin/parish/files/{T}/name/",
    "/admin/parish/parishsoft-refresh": "/admin/parish/parishsoft-refresh/",
    f"/admin/changes/{T}": f"/admin/changes/{T}/",
    "/admin/campaign": "/admin/campaign/",
    "/admin/mail": "/admin/mail/",
    "/admin/parish": "/admin/parish/",
    "/admin/campaign/settings": "/admin/campaign/settings/",
    "/admin/campaign/copy": "/admin/campaign/copy/",
    "/admin/campaign/content": "/admin/campaign/content/",
    "/admin/campaign/content/history": "/admin/campaign/content/history/",
    f"/admin/campaign/content/history/{T}": f"/admin/campaign/content/history/{T}/",
    "/admin/campaign/content/parishsoft/parishsoft": (
        "/admin/campaign/content/parishsoft/parishsoft/"
    ),
    f"/admin/campaign/content/parishsoft/parishsoft/{T}": (
        f"/admin/campaign/content/parishsoft/parishsoft/{T}/"
    ),
    "/admin/campaign/images": "/admin/campaign/images/",
    "/admin/campaign/images/logo": "/admin/campaign/images/logo/",
    "/admin/campaign/images/logo/removal": "/admin/campaign/images/logo/removal/",
    f"/admin/campaign/images/logo/{T}": f"/admin/campaign/images/logo/{T}/",
    "/admin/campaign/schedules": "/admin/campaign/schedules/",
    "/admin/campaign/schedules/addition": "/admin/campaign/schedules/addition/",
    f"/admin/campaign/schedules/{T}": f"/admin/campaign/schedules/{T}/",
    "/admin/campaign/share-options": "/admin/campaign/share-options/",
    "/admin/campaign/talents": "/admin/campaign/talents/",
    "/admin/campaign/reminder-workgroup": "/admin/campaign/reminder-workgroup/",
    f"/admin/campaign/content/test/{T}": f"/admin/campaign/content/test/{T}/",
    f"/admin/campaign/content/test/{T}/families": (
        f"/admin/campaign/content/test/{T}/families/"
    ),
    "/admin/campaign/go-live": "/admin/campaign/go-live/",
    "/admin/campaign/go-live/families": "/admin/campaign/go-live/families/",
    f"/admin/campaign/go-live/cleanup/{T}": f"/admin/campaign/go-live/cleanup/{T}/",
    f"/admin/campaign/go-live/cleanup/{T}/links": (
        f"/admin/campaign/go-live/cleanup/{T}/links/"
    ),
    f"/admin/campaign/go-live/cleanup/{T}/links/{T}/confirmation": (
        f"/admin/campaign/go-live/cleanup/{T}/links/{T}/confirmation/"
    ),
    "/admin/campaign/production": "/admin/campaign/production/",
    "/admin/campaign/production/cancellation": (
        "/admin/campaign/production/cancellation/"
    ),
    "/admin/parish/ministries/campaign": "/admin/parish/ministries/campaign/",
    "/admin/mail/controls": "/admin/mail/controls/",
    "/admin/mail/family-progress": "/admin/mail/family-progress/",
    "/admin/mail/family-history": "/admin/mail/family-history/",
    "/admin/mail/outgoing": "/admin/mail/outgoing/",
    f"/admin/mail/outgoing/{T}": f"/admin/mail/outgoing/{T}/",
    "/admin/mail/refusals": "/admin/mail/refusals/",
    f"/admin/mail/refusals/{T}": f"/admin/mail/refusals/{T}/",
    "/admin/mail/family-portal": "/admin/mail/family-portal/",
    "/admin/mail/presence": "/admin/mail/presence/",
    "/admin/users/automation": "/admin/users/automation/",
    "/admin/users/automation/approval": "/admin/users/automation/approval/",
    # Responses and reports pages without their trailing slash (NAV-11).
    "/admin/reports": "/admin/reports/",
    "/admin/reports/responses": "/admin/reports/responses/",
    "/admin/reports/responses/logo": "/admin/reports/responses/logo/",
    "/admin/reports/participation": "/admin/reports/participation/",
    "/admin/reports/financial": "/admin/reports/financial/",
    "/admin/reports/talents": "/admin/reports/talents/",
    "/admin/reports/census": "/admin/reports/census/",
    "/admin/reports/information": "/admin/reports/information/",
    f"/admin/reports/information/{T}": f"/admin/reports/information/{T}/",
    "/admin/reports/ministries": "/admin/reports/ministries/",
    "/admin/reports/ministries/joining": "/admin/reports/ministries/joining/",
    "/admin/reports/ministries/leaving": "/admin/reports/ministries/leaving/",
    "/admin/reports/ministries/follow-up": "/admin/reports/ministries/follow-up/",
    f"/admin/reports/ministries/follow-up/{T}": (
        f"/admin/reports/ministries/follow-up/{T}/"
    ),
    "/admin/reports/families": "/admin/reports/families/",
    f"/admin/reports/families/{T}": f"/admin/reports/families/{T}/",
    # Exports and emailed reports (NAV-12).
    f"/admin/reports/exports/{T}": f"/admin/reports/exports/{T}/",
    "/admin/reports/emailed": "/admin/reports/emailed/",
    "/admin/reports/emailed/weekly/new": "/admin/reports/emailed/weekly/new/",
    f"/admin/reports/emailed/weekly/{T}": f"/admin/reports/emailed/weekly/{T}/",
    f"/admin/reports/emailed/weekly/{T}/items/{T}": (
        f"/admin/reports/emailed/weekly/{T}/items/{T}/"
    ),
    f"/admin/reports/emailed/daily/{T}": f"/admin/reports/emailed/daily/{T}/",
}

# Old Admin addresses, one or more per moved group: none is kept (#864).
OLD_ADDRESSES = (
    # System (NAV-6).
    "/admin/configuration/integrations",
    "/admin/configuration/integrations/parishsoft",
    f"/admin/configuration/credentials/{T}",
    "/admin/background",
    f"/admin/background/task/{T}",
    f"/admin/background/tasks/{T}/retry-daily-digest",
    "/admin/logs",
    "/admin/logs/export",
    # Parish data and change status (NAV-7).
    "/admin/configuration/parish",
    "/admin/configuration/branding",
    "/admin/files/",
    "/admin/files/upload",
    "/admin/source/refresh",
    f"/admin/configuration/requests/{T}",
    # Mail and Family portal (NAV-8).
    "/admin/deliveries",
    f"/admin/deliveries/{T}/resolve",
    "/admin/deliveries/family-sends",
    "/admin/family-portal",
    # Old addresses that named a campaign (NAV-9 to NAV-11).
    f"/admin/campaign/{T}/delivery",
    f"/admin/campaign/{T}/settings",
    f"/admin/campaign/{T}/content/test/{T}",
    f"/admin/campaign/{T}/images/logo/remove",
    f"/admin/campaign/{T}/go-live",
    f"/admin/campaign/{T}/production/withdraw",
    f"/admin/campaign/{T}/ministries",
    f"/admin/reports/{T}/responses/",
    f"/admin/reports/{T}/participation/",
    f"/admin/reports/{T}/participation/export",
    f"/admin/reports/{T}/families/",
    f"/admin/reports/{T}/families/{T}/",
    f"/admin/reports/{T}/ministries/follow-up/{T}/update",
    # Retired addresses that named no campaign (decisions 10 and 19).
    "/admin/reports/campaigns/",
    "/admin/ministry-reports/",
    "/admin/ministry-reports/campaigns/",
)


@pytest.mark.parametrize("old", OLD_ADDRESSES)
def test_old_addresses_are_gone(old):
    """The Administrator dropped old Admin addresses: each is 404 (#864)."""
    with pytest.raises(Resolver404):
        resolve(old)


def test_every_slashless_form_is_listed_once_and_expected():
    """One row per slashless form, each in the expected table, each a page."""
    olds = [old for old, _new in slashless.SLASHLESS]
    assert len(olds) == len(set(olds))
    assert {_example(old) for old in olds} == set(SLASHLESS_EXPECTED)
    routes = navigation.route_parameters()
    for _old, new in slashless.SLASHLESS:
        assert new in routes and new not in slashless.TARGETS


# Form actions posted only from their own page's script, at the reversed URL,
# have no slashless form (slashless.SLASHLESS's rule): Delete scheduled emails
# answers only the list's confirmation dialog (#878).
POSTED_ONLY = {"schedule_delete"}


def test_every_moved_page_has_its_slashless_form():
    """The one trailing-slash rule: each moved page's other form redirects."""
    pages = {
        str(pattern.pattern).rstrip("/")
        for pattern in GROUPS
        if pattern.name in navigation.PAGES and pattern.name not in POSTED_ONLY
    }
    assert pages <= {old for old, _new in slashless.SLASHLESS}


@pytest.mark.parametrize(("old", "new"), sorted(SLASHLESS_EXPECTED.items()))
def test_slashless_form_redirects_permanently_keeping_the_query(old, new):
    """GET/HEAD get 301 and POST gets 308 to the expected page, query kept."""
    match = resolve(old)
    assert match.url_name.endswith(slashless.SUFFIX)
    factory = RequestFactory()
    for method, status in (("get", 301), ("head", 301), ("post", 308)):
        request = getattr(factory, method)(old + "?state=all&page=2")
        response = match.func(request, **match.kwargs)
        assert response.status_code == status, method
        assert response["Location"] == new + "?state=all&page=2"
    # Without a query, the target is exactly the new page.
    assert match.func(factory.get(old), **match.kwargs)["Location"] == new


@pytest.mark.parametrize(
    ("section", "name"),
    [("campaign", "campaign_root"), ("mail", "mail_root"), ("parish", "parish_root")],
)
def test_group_roots_open_their_group(section, name):
    """/admin/<group>/ is the group's root view; System has its own (ADM-13)."""
    url = reverse(f"admin:{name}")
    assert url == f"/admin/{section}/"
    assert resolve(url).func.group_root == section
    assert name in navigation.NON_PAGES
    # The form without the slash redirects, as /admin/system does.
    slashless = resolve(url.rstrip("/"))
    assert slashless.url_name == f"{name}_slashless"
    assert slashless.func(RequestFactory().get(url.rstrip("/")))["Location"] == url


def test_test_email_pages_come_before_the_content_editor():
    """content/<kind>/<slot>/ would take content/test/<revision>/ as kind "test".

    The editor route is listed after the test email routes, in both address
    forms: the page and its no-slash form, so each reaches (or redirects to)
    the test page, never the editor (NAV-10).
    """
    page = f"/admin/campaign/content/test/{T}/"
    assert resolve(page).url_name == "campaign_mail"
    assert resolve(page + "families/").url_name == "campaign_mail_families"
    assert resolve(f"/admin/campaign/content/email/{T}/").url_name == "content_edit"
    names = [pattern.name for pattern in campaign.patterns]
    assert names.index("campaign_mail") < names.index("content_edit")
    assert names.index("campaign_mail_families") < names.index("content_edit")
    for old, name in (
        (page.rstrip("/"), "campaign_mail"),
        (page + "families", "campaign_mail_families"),
    ):
        match = resolve(old)
        assert match.url_name == f"{name}_slashless", old
        assert match.func.redirect_target == name


def test_the_go_live_chain_names_no_campaign():
    """Each go-live step sits under the one before it, with no campaign id."""
    cleanup = reverse("admin:go_live_cleanup", args=[TASK])
    links = reverse("admin:go_live_links", args=[TASK])
    confirmation = reverse("admin:production_confirmation", args=[TASK, TASK])
    assert reverse("admin:go_live") == "/admin/campaign/go-live/"
    assert cleanup.startswith("/admin/campaign/go-live/") and links.startswith(cleanup)
    assert confirmation.startswith(links) and confirmation.endswith("/confirmation/")
    withdrawal = reverse("admin:production_withdrawal")
    assert withdrawal.startswith(reverse("admin:production_progress"))
    # One home per concept: Campaign Ministries runs through Ministries.
    assert reverse("admin:campaign_ministries").startswith(reverse("admin:ministries"))
    assert navigation.PAGES["campaign_ministries"].parent == "ministries"


@pytest.mark.parametrize(
    "path",
    [
        f"/admin/campaign/{T}/exports/participation",
        "/admin/exports/download",
        f"/admin/campaign/{T}/exports/exact-participation",
        f"/admin/exact-exports/{T}",
        f"/admin/exact-exports/{T}/cancel",
        f"/admin/exact-exports/{T}/retry",
        f"/admin/exports/{T}",
        f"/admin/exports/{T}/cancel",
        f"/admin/exports/{T}/download-grant",
        # The old postal page and export: Admin-only, so no redirect (#864).
        f"/admin/reports/{T}/postal/",
        f"/admin/reports/{T}/postal/export",
    ],
)
def test_the_retired_json_export_api_and_postal_routes_are_gone(path):
    """No page, script or command line used the JSON export API (#758)."""
    with pytest.raises(Resolver404):
        resolve(path)


def test_reports_root_keeps_its_old_meaning():
    """/admin/reports/ opens Participation (or Ministry requests), not a group root.

    It stays a page of its own (admin-portal spec, "Menu groups"); its form
    without the slash redirects like every other page's (NAV-11).
    """
    url = reverse("admin:reports")
    assert url == "/admin/reports/"
    match = resolve(url)
    assert match.url_name == "reports"
    assert not hasattr(match.func, "group_root")
    slashless = resolve("/admin/reports")
    assert slashless.url_name == "reports_slashless"
    assert slashless.func(RequestFactory().get("/admin/reports"))["Location"] == url


def test_report_actions_and_records_resolve_to_their_own_routes():
    """No report route takes another's address (NAV-11/12 route-order traps).

    The directory's fixed actions are listed before its Family pages, Send a
    weekly report now before the weekly reports, no new route shadows an old
    emailed report address, and the old export addresses are gone (#864).
    """
    for path, name in (
        ("/admin/reports/families/exports/", "family_directory_export"),
        ("/admin/reports/families/search/", "find_family"),
        (f"/admin/reports/families/{T}/", "family_timeline"),
        ("/admin/reports/information/exports/", "information_export"),
        (f"/admin/reports/information/{T}/", "information_item"),
        (f"/admin/reports/information/{T}/record/", "information_update"),
        ("/admin/reports/ministries/follow-up/", "ministry_followup"),
        (f"/admin/reports/ministries/follow-up/{T}/", "ministry_followup_item"),
        ("/admin/reports/responses/submitted/", "response_list"),
        ("/admin/reports/responses/submitted/csv/", "response_list_export"),
        (f"/admin/reports/exports/{T}/", "report_export"),
        (f"/admin/reports/exports/{T}/download/", "report_export_download"),
        ("/admin/reports/emailed/weekly/new/", "weekly_digest_manual"),
        (f"/admin/reports/emailed/weekly/{T}/", "weekly_digest_snapshot"),
        (f"/admin/reports/emailed/daily/{T}/", "daily_digest_snapshot"),
        (f"/admin/reports/emailed/daily/{T}/chart.png", "daily_digest_chart"),
    ):
        assert resolve(path).url_name == name, path
    names = [pattern.name for pattern in reports.patterns]
    assert names.index("family_directory_export") < names.index("family_timeline")
    assert names.index("find_family") < names.index("family_timeline")
    assert names.index("weekly_digest_manual") < names.index("weekly_digest_snapshot")


def test_old_digest_addresses_are_gone():
    """The daily and weekly reports' old addresses are 404, not redirected (#864).

    The Administrator dropped old Admin aliases: a link in a report email sent
    before NAV-12 no longer opens; the report is listed on Emailed reports.
    """
    for old in (
        f"/admin/reports/daily-digests/{T}/",
        f"/admin/reports/daily-digests/{T}/chart.png",
        f"/admin/reports/daily-digests/{T}/download.png",
        f"/admin/reports/weekly-digests/{T}/",
        f"/admin/reports/weekly-digests/{T}/items/{T}/",
    ):
        with pytest.raises(Resolver404):
            resolve(old)


def test_new_digest_emails_link_their_emailed_report_pages():
    """New emails link the Emailed reports addresses, which open the reports."""
    from types import SimpleNamespace

    from parishkit.stewardship.reports.links import report_url

    sent = SimpleNamespace(snapshot_id=TASK)
    for document, name in (
        (DailyDigestDocument, "daily_digest_snapshot"),
        (WeeklyDigestDocument, "weekly_digest_snapshot"),
    ):
        path = document.report_path.fget(sent)
        assert resolve(path).url_name == name, path
        assert path == reverse(f"admin:{name}", args=[TASK])
        assert path.startswith("/admin/reports/emailed/")
        assert report_url("https://parish.example", path).endswith(path)


def test_every_export_shares_one_page():
    """A latest-data export's page is the shared export page (decision 8)."""
    page = reverse("admin:report_export", args=[TASK])
    for name in ("report_export_cancel", "report_export_retry"):
        assert reverse(f"admin:{name}", args=[TASK]).startswith(page)
    assert "report_exact" not in navigation.PAGES
    # No old address is kept for them (#864).
    for old in (
        f"/admin/reports/exact-exports/{T}/",
        f"/admin/reports/exports/{T}/cancel",
        f"/admin/reports/weekly-digests/request/{T}/",
    ):
        with pytest.raises(Resolver404):
            resolve(old)
