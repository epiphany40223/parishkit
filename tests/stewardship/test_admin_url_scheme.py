"""The Admin URL scheme (admin-portal spec, "URL scheme", #525).

Groups already moved to the scheme (System, with NAV-6) put every page under
``/admin/<group>/`` with a trailing slash, name no campaign and use nouns,
never GET verbs. Every old address is a legacy route that redirects
permanently: 301 for GET and HEAD, 308 for other methods, keeping the query
string. The JSON reads scripts poll keep their addresses (decision 8).
"""

import re
from uuid import UUID

import pytest
from django.test import RequestFactory
from django.urls import resolve, reverse

from parishkit.stewardship.accounts import admin_navigation as navigation
from parishkit.stewardship.admin_urls import (
    campaign,
    legacy,
    mail,
    parish,
    reports,
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
    assert not segments & VERBS, route


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


def test_polled_reads_keep_their_addresses():
    """Scripts poll these JSON reads; they do not move (decision 8)."""
    assert reverse("admin:background_counts") == "/admin/background/counts"
    assert reverse("admin:background_tasks") == "/admin/background/tasks"
    assert (
        reverse("admin:background_task", args=[TASK])
        == f"/admin/background/tasks/{TASK}"
    )
    assert not POLLED & set(legacy.TARGETS.values())
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


@pytest.mark.parametrize(
    ("method", "status"), [("get", 301), ("head", 301), ("post", 308)]
)
def test_old_presence_address_redirects_its_page_reads(monkeypatch, method, status):
    """Without a polled format, the old address is the page: it moved."""
    monkeypatch.setattr(mail.presence, "active_families", pytest.fail)
    request = getattr(RequestFactory(), method)("/admin/presence?size=25&page=2")
    response = mail.presence_reads(request)
    assert response.status_code == status
    assert response["Location"] == "/admin/mail/presence/?size=25&page=2"
    plain = mail.presence_reads(RequestFactory().get("/admin/presence"))
    assert plain["Location"] == "/admin/mail/presence/"


T = "00000000-0000-0000-0000-000000000007"
# Every old address (with sample arguments) and the exact page it must reach,
# written out by hand so a typo in the legacy table cannot hide behind the
# same table: the spec's placement table and URL scheme are the source.
EXPECTED = {
    # Old System addresses (NAV-6).
    "/admin/configuration/integrations": "/admin/system/integrations/",
    "/admin/configuration/integrations/parishsoft": (
        "/admin/system/integrations/parishsoft/"
    ),
    "/admin/configuration/integrations/parishsoft/status": (
        "/admin/system/integrations/parishsoft/status/"
    ),
    "/admin/configuration/integrations/parishsoft/dismiss": (
        "/admin/system/integrations/parishsoft/dismissal/"
    ),
    f"/admin/configuration/credentials/{T}": f"/admin/system/key-changes/{T}/",
    f"/admin/configuration/credentials/{T}/select": (
        f"/admin/system/key-changes/{T}/selection/"
    ),
    "/admin/background": "/admin/system/background/",
    f"/admin/background/task/{T}": f"/admin/system/background/{T}/",
    f"/admin/background/task/{T}/status": f"/admin/system/background/{T}/status/",
    f"/admin/background/tasks/{T}/retry-family-preparation": (
        f"/admin/system/background/{T}/family-preparation-retry/"
    ),
    f"/admin/background/tasks/{T}/retry-daily-digest": (
        f"/admin/system/background/{T}/daily-digest-retry/"
    ),
    f"/admin/background/tasks/{T}/retry-weekly-digest": (
        f"/admin/system/background/{T}/weekly-digest-retry/"
    ),
    f"/admin/background/tasks/{T}/retry-export-cleanup": (
        f"/admin/system/background/{T}/export-cleanup-retry/"
    ),
    "/admin/logs": "/admin/system/logs/",
    "/admin/logs/export": "/admin/system/logs/export/",
    # Old Parish data and change-status addresses (NAV-7).
    "/admin/configuration/parish": "/admin/parish/settings/",
    "/admin/configuration/branding": "/admin/parish/logos/",
    f"/admin/configuration/branding/{T}": f"/admin/parish/logos/{T}/",
    f"/admin/configuration/branding/assets/{T}.png": (
        f"/admin/parish/logos/assets/{T}.png"
    ),
    "/admin/configuration/ministries": "/admin/parish/ministries/",
    "/admin/files/": "/admin/parish/files/",
    "/admin/files/upload": "/admin/parish/files/uploads/",
    "/admin/files/delete": "/admin/parish/files/deletion/",
    f"/admin/files/{T}/name": f"/admin/parish/files/{T}/name/",
    "/admin/source/refresh": "/admin/parish/parishsoft-refresh/",
    f"/admin/configuration/requests/{T}": f"/admin/changes/{T}/",
    # Old Mail and Family portal addresses (NAV-8).
    "/admin/deliveries": "/admin/mail/outgoing/",
    f"/admin/deliveries/{T}": f"/admin/mail/outgoing/{T}/",
    f"/admin/deliveries/{T}/resolve": f"/admin/mail/outgoing/{T}/resolution/",
    "/admin/deliveries/refusals": "/admin/mail/refusals/",
    f"/admin/deliveries/refusals/{T}": f"/admin/mail/refusals/{T}/",
    f"/admin/deliveries/refusals/{T}/clear": (f"/admin/mail/refusals/{T}/clearance/"),
    "/admin/deliveries/family-progress": "/admin/mail/family-progress/",
    "/admin/deliveries/family-progress/status": ("/admin/mail/family-progress/status/"),
    "/admin/deliveries/family-sends": "/admin/mail/family-history/",
    "/admin/family-portal": "/admin/mail/family-portal/",
    # Pages already in the scheme, without their trailing slash.
    "/admin/system": "/admin/system/",
    "/admin/system/health": "/admin/system/health/",
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
    "/admin/campaign/share-options": "/admin/campaign/share-options/",
    "/admin/campaign/talents": "/admin/campaign/talents/",
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
    "/admin/reports/family-codes": "/admin/reports/family-codes/",
    # Retired addresses that named no campaign (decisions 10 and 19): the
    # two campaign choosers and the old Ministry reports root.
    "/admin/reports/campaigns/": "/admin/reports/participation/",
    "/admin/ministry-reports/": "/admin/reports/ministries/",
    "/admin/ministry-reports/campaigns/": "/admin/reports/ministries/",
}


# Old addresses that name a campaign: they redirect only for the current
# campaign, which the PostgreSQL suite checks through the real session.
C = f"/admin/campaign/{T}"
CAMPAIGN_EXPECTED = {
    f"{C}/delivery": "/admin/mail/controls/",
    # Campaign setup, part A (NAV-9).
    f"{C}/settings": "/admin/campaign/settings/",
    f"{C}/clone": "/admin/campaign/copy/",
    f"{C}/content": "/admin/campaign/content/",
    f"{C}/content/history": "/admin/campaign/content/history/",
    f"{C}/content/history/{T}": f"/admin/campaign/content/history/{T}/",
    f"{C}/content/parishsoft/parishsoft": (
        "/admin/campaign/content/parishsoft/parishsoft/"
    ),
    f"{C}/content/parishsoft/parishsoft/{T}": (
        f"/admin/campaign/content/parishsoft/parishsoft/{T}/"
    ),
    f"{C}/images": "/admin/campaign/images/",
    f"{C}/images/logo": "/admin/campaign/images/logo/",
    f"{C}/images/logo/remove": "/admin/campaign/images/logo/removal/",
    f"{C}/images/logo/{T}": f"/admin/campaign/images/logo/{T}/",
    f"{C}/schedules": "/admin/campaign/schedules/",
    f"{C}/share-options": "/admin/campaign/share-options/",
    f"{C}/talents": "/admin/campaign/talents/",
    # Campaign setup, part B (NAV-10).
    f"{C}/content/test/{T}": f"/admin/campaign/content/test/{T}/",
    f"{C}/content/test/{T}/families": f"/admin/campaign/content/test/{T}/families/",
    f"{C}/go-live": "/admin/campaign/go-live/",
    f"{C}/go-live/families": "/admin/campaign/go-live/families/",
    f"{C}/go-live/cleanup/{T}": f"/admin/campaign/go-live/cleanup/{T}/",
    f"{C}/go-live/cleanup/{T}/links": f"/admin/campaign/go-live/cleanup/{T}/links/",
    f"{C}/go-live/cleanup/{T}/links/{T}/confirm": (
        f"/admin/campaign/go-live/cleanup/{T}/links/{T}/confirmation/"
    ),
    f"{C}/production": "/admin/campaign/production/",
    f"{C}/production/withdraw": "/admin/campaign/production/cancellation/",
    f"{C}/ministries": "/admin/parish/ministries/campaign/",
    f"{C}/family-codes": "/admin/reports/family-codes/",
}
# Old report addresses named the campaign under /admin/reports/ (NAV-11).
R = f"/admin/reports/{T}"
CAMPAIGN_EXPECTED |= {
    f"{R}/responses/": "/admin/reports/responses/",
    f"{R}/responses/logo/": "/admin/reports/responses/logo/",
    f"{R}/responses/logo/csv/": "/admin/reports/responses/logo/csv/",
    f"{R}/participation/": "/admin/reports/participation/",
    f"{R}/participation/{T}.png": f"/admin/reports/participation/{T}.png",
    f"{R}/participation/export": "/admin/reports/participation/exports/",
    f"{R}/participation/exact-export": "/admin/reports/participation/exact-exports/",
    f"{R}/financial/": "/admin/reports/financial/",
    f"{R}/financial/export": "/admin/reports/financial/exports/",
    f"{R}/talents/": "/admin/reports/talents/",
    f"{R}/talents/export": "/admin/reports/talents/exports/",
    f"{R}/information/": "/admin/reports/information/",
    f"{R}/information/export": "/admin/reports/information/exports/",
    f"{R}/information/{T}/": f"/admin/reports/information/{T}/",
    f"{R}/information/{T}/update": f"/admin/reports/information/{T}/record/",
    f"{R}/ministries/": "/admin/reports/ministries/",
    f"{R}/ministries/join/": "/admin/reports/ministries/joining/",
    f"{R}/ministries/leave/": "/admin/reports/ministries/leaving/",
    f"{R}/ministries/export/": "/admin/reports/ministries/exports/",
    f"{R}/ministries/packet/": "/admin/reports/ministries/packets/",
    f"{R}/ministries/follow-up/": "/admin/reports/ministries/follow-up/",
    f"{R}/ministries/follow-up/{T}/": f"/admin/reports/ministries/follow-up/{T}/",
    f"{R}/ministries/follow-up/{T}/update": (
        f"/admin/reports/ministries/follow-up/{T}/record/"
    ),
    f"{R}/families/": "/admin/reports/families/",
    f"{R}/families/export": "/admin/reports/families/exports/",
    f"{R}/families/find": "/admin/reports/families/search/",
    f"{R}/families/{T}/": f"/admin/reports/families/{T}/",
}


def test_every_old_address_is_listed_once_and_expected():
    """One legacy row per old address, and each one is in the expected table."""
    olds = [old for old, _new, _campaign, _suffix in legacy.ROWS]
    assert len(olds) == len(set(olds))
    assert {_example(old) for old in olds} == set(EXPECTED) | set(CAMPAIGN_EXPECTED)
    named = {_example(old) for old, _new, campaign, _ in legacy.ROWS if campaign}
    assert named == set(CAMPAIGN_EXPECTED)
    for old, new in CAMPAIGN_EXPECTED.items():
        match = resolve(old)
        assert match.func.legacy_campaign
        arguments = {k: v for k, v in match.kwargs.items() if k != "campaign_id"}
        assert reverse(f"admin:{match.func.legacy_target}", kwargs=arguments) == new
    routes = navigation.route_parameters()
    for _old, new, _campaign, _suffix in legacy.ROWS:
        assert new in routes and not new.startswith(legacy.PREFIX)


def test_every_moved_page_has_its_slashless_form():
    """The one trailing-slash rule: each moved page's other form redirects."""
    pages = {
        str(pattern.pattern).rstrip("/")
        for pattern in GROUPS
        if pattern.name in navigation.PAGES
    }
    slashless = {old for old, _new in legacy.SLASHLESS}
    assert pages <= slashless


@pytest.mark.parametrize(("old", "new"), sorted(EXPECTED.items()))
def test_old_address_redirects_permanently_keeping_the_query(old, new):
    """GET/HEAD get 301 and POST gets 308 to the expected page, query kept."""
    match = resolve(old)
    assert match.url_name.startswith(legacy.PREFIX)
    factory = RequestFactory()
    for method, status in (("get", 301), ("head", 301), ("post", 308)):
        request = getattr(factory, method)(old + "?state=all&page=2")
        response = match.func(request, **match.kwargs)
        assert response.status_code == status, method
        assert response["Location"] == new + "?state=all&page=2"
    # Without a query, the target is exactly the new page.
    assert match.func(factory.get(old), **match.kwargs)["Location"] == new


def test_old_addresses_name_their_pages_for_remembered_origins():
    """A change confirmed on an old integration address still leads back to it."""
    match = resolve("/admin/configuration/integrations/parishsoft")
    assert navigation.LEGACY_TARGETS[match.url_name] == "integration_settings"
    assert match.url_name in navigation.LEGACY


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
    assert slashless.url_name == f"{legacy.PREFIX}{name}_slashless"
    assert slashless.func(RequestFactory().get(url.rstrip("/")))["Location"] == url


def test_test_email_pages_come_before_the_content_editor():
    """content/<kind>/<slot>/ would take content/test/<revision>/ as kind "test".

    The editor route is listed after the test email routes, at every address
    form: the page, its no-slash form and the old campaign address, so each
    reaches (or redirects to) the test page, never the editor (NAV-10).
    """
    page = f"/admin/campaign/content/test/{T}/"
    assert resolve(page).url_name == "campaign_mail"
    assert resolve(page + "families/").url_name == "campaign_mail_families"
    assert resolve(f"/admin/campaign/content/email/{T}/").url_name == "content_edit"
    names = [pattern.name for pattern in campaign.patterns]
    assert names.index("campaign_mail") < names.index("content_edit")
    assert names.index("campaign_mail_families") < names.index("content_edit")
    for old, name in (
        (page.rstrip("/"), "campaign_mail_slashless"),
        (page + "families", "campaign_mail_families_slashless"),
        (f"/admin/campaign/{T}/content/test/{T}", "campaign_mail"),
        (f"/admin/campaign/{T}/content/test/{T}/families", "campaign_mail_families"),
    ):
        match = resolve(old)
        assert match.url_name == f"{legacy.PREFIX}{name}", old
        assert match.func.legacy_target == name.removesuffix("_slashless")


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
    assert slashless.url_name == f"{legacy.PREFIX}reports_slashless"
    assert slashless.func(RequestFactory().get("/admin/reports"))["Location"] == url


def test_report_actions_and_records_resolve_to_their_own_routes():
    """No report route takes another's address (NAV-11 route-order traps).

    The directory's fixed actions are listed before its Family pages, and the
    export and emailed report pages that stay in ``urls.py`` until NAV-12
    are not shadowed by the moved report routes or the old report addresses.
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
        (f"/admin/reports/exact-exports/{T}/", "report_exact"),
        (f"/admin/reports/daily-digests/{T}/", "daily_digest_snapshot"),
        (f"/admin/reports/weekly-digests/{T}/", "weekly_digest_snapshot"),
        ("/admin/reports/campaigns/", f"{legacy.PREFIX}participation_chooser"),
        (f"/admin/reports/{T}/families/", f"{legacy.PREFIX}family_directory"),
        (f"/admin/reports/{T}/families/{T}/", f"{legacy.PREFIX}family_timeline"),
    ):
        assert resolve(path).url_name == name, path
    names = [pattern.name for pattern in reports.patterns]
    assert names.index("family_directory_export") < names.index("family_timeline")
    assert names.index("find_family") < names.index("family_timeline")


def test_sent_digest_email_links_still_open_their_reports():
    """Daily and weekly report emails already sent link these exact addresses.

    The emailed report pages move with NAV-12, which redirects these; until
    then each still opens its page directly, never an old-report redirect or
    a 410 (NAV-11 moves the report pages around them).
    """
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
        assert report_url("https://parish.example", path).endswith(path)
    # A weekly report's item links, as its page renders them.
    item = f"/admin/reports/weekly-digests/{T}/items/{T}/"
    assert resolve(item).url_name == "weekly_digest_item"
