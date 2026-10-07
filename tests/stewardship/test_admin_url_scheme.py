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
from parishkit.stewardship.admin_urls import campaign, legacy, mail, parish, system

# Menu groups whose URLs follow the scheme so far, by URL segment.
MOVED = {"system", "parish", "mail", "campaign"}
GROUPS = system.patterns + parish.patterns + mail.patterns + campaign.patterns
# Campaign setup pages that move with part B (NAV-10).
PART_B = {
    "campaign_mail",
    "campaign_mail_families",
    "campaign_ministries",
    "go_live",
    "go_live_cleanup",
    "go_live_families",
    "go_live_links",
    "production_confirmation",
    "production_progress",
    "production_withdrawal",
}
# Route segments that would be a GET target naming an action.
VERBS = {
    "acknowledge",
    "clear",
    "create",
    "delete",
    "dismiss",
    "edit",
    "new",
    "remove",
    "resolve",
    "retry",
    "select",
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
    sorted(
        name
        for name, page in navigation.PAGES.items()
        if page.section in MOVED and name not in PART_B
    ),
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


def test_old_test_email_addresses_still_reach_their_pages():
    """Until NAV-10 moves them, the test email pages beat the old editor route."""
    old = f"/admin/campaign/{T}/content/test/{T}"
    assert resolve(old).url_name == "campaign_mail"
    assert resolve(old + "/families").url_name == "campaign_mail_families"
