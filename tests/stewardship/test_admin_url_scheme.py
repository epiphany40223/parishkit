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
from parishkit.stewardship.admin_urls import legacy, system

# Menu groups whose URLs follow the scheme so far, by URL segment.
MOVED = {"system"}
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
SAMPLES = {"uuid": str(TASK), "str": "parishsoft"}


def _example(route):
    """A concrete path for a route pattern, with sample arguments."""
    return "/admin/" + re.sub(
        r"<(\w+):\w+>", lambda match: SAMPLES[match.group(1)], route
    )


@pytest.mark.parametrize("pattern", system.patterns, ids=lambda p: p.name)
def test_moved_routes_follow_the_scheme(pattern):
    """Under their group, trailing slash, no campaign, nouns only."""
    route = str(pattern.pattern)
    assert route.split("/")[0] in MOVED
    assert route.endswith("/")
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
    "/admin/users/automation": "/admin/users/automation/",
    "/admin/users/automation/approval": "/admin/users/automation/approval/",
}


def test_every_old_address_is_listed_once_and_expected():
    """One legacy row per old address, and each one is in the expected table."""
    olds = [old for old, _new, _campaign, _suffix in legacy.ROWS]
    assert len(olds) == len(set(olds))
    assert {_example(old) for old in olds} == set(EXPECTED)
    routes = navigation.route_parameters()
    for _old, new, _campaign, _suffix in legacy.ROWS:
        assert new in routes and not new.startswith(legacy.PREFIX)


def test_every_moved_page_has_its_slashless_form():
    """The one trailing-slash rule: each moved page's other form redirects."""
    pages = {
        str(pattern.pattern).rstrip("/")
        for pattern in system.patterns
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
