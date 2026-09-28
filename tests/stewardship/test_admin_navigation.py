"""The declarative Admin navigation covers every route and yields sound trails."""

import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.template.loader import render_to_string
from django.urls import get_resolver, reverse

from parishkit.stewardship.accounts import admin_navigation as navigation


def _admin_patterns():
    """Every named route in the Admin namespace, with its converter types."""
    admin = next(
        pattern
        for pattern in get_resolver().url_patterns
        if getattr(pattern, "namespace", None) == navigation.NAMESPACE
    )
    return {
        pattern.name: re.findall(r"<(\w+):(\w+)>", str(pattern.pattern))
        for pattern in admin.url_patterns
        if getattr(pattern, "name", None)
    }


ROUTES = _admin_patterns()


def _arguments(name):
    """Plausible resolved arguments for a route: UUIDs and short strings."""
    return {
        parameter: uuid4() if converter == "uuid" else "parishsoft"
        for converter, parameter in ROUTES[name]
    }


def _items():
    """One sidebar entry per root page, as if the actor could see everything."""
    return [
        (page.section, name, page.label, f"/nav/{name}")
        for name, page in navigation.PAGES.items()
        if page.section and page.parent is None
    ]


def test_every_admin_route_is_either_a_page_or_explicitly_not_one():
    """A new Admin route must be placed in the navigation or listed as a non-page."""
    pages = set(navigation.PAGES)
    assert not pages & navigation.NON_PAGES
    assert set(ROUTES) == pages | navigation.NON_PAGES, set(ROUTES) ^ (
        pages | navigation.NON_PAGES
    )


def test_pages_form_acyclic_trees_within_known_sections():
    """Parents exist, chains end at a root, and children share their section."""
    sections = {section.key for section in navigation.SECTIONS}
    for name, page in navigation.PAGES.items():
        assert page.section is None or page.section in sections, name
        if page.parent is not None:
            parent = navigation.PAGES[page.parent]
            assert parent.section == page.section, name
        assert navigation._chain(name)[-1] == name


def test_route_parameters_match_the_resolver():
    """Ancestor links are reversed with exactly the arguments each route needs."""
    parameters = navigation.route_parameters()
    for name, converters in ROUTES.items():
        assert parameters[name] == tuple(parameter for _, parameter in converters)


@pytest.mark.parametrize("name", sorted(navigation.PAGES))
def test_every_admin_page_renders_a_breadcrumb_trail(name):
    """Home first, then the section and ancestors, ending at the current page."""
    arguments = _arguments(name)
    match = SimpleNamespace(
        url_name=name, namespace=navigation.NAMESPACE, kwargs=arguments
    )
    sections, trail = navigation.build(match, _items())
    page = navigation.PAGES[name]
    assert trail[0]["label"] == navigation.PAGES["index"].label
    assert trail[-1] == {"label": page.label, "url": None}
    if name == "index":
        assert len(trail) == 1
        return
    labels = [crumb["label"] for crumb in trail]
    if page.section:
        assert labels[1] == navigation.SECTION_LABELS[page.section]
        assert any(section["current"] for section in sections)
    # Each ancestor links to a real Admin URL built from this page's arguments.
    chain = navigation._chain(name)
    # The trail ends with the chain: its ancestors, then the current page.
    for ancestor, crumb in zip(chain[:-1], trail[-len(chain) : -1], strict=True):
        assert crumb["label"] == navigation.PAGES[ancestor].label
        needed = navigation.route_parameters()[ancestor]
        assert crumb["url"] == reverse(
            f"admin:{ancestor}", kwargs={key: arguments[key] for key in needed}
        )
    html = render_to_string(
        "stewardship/admin-breadcrumbs.html",
        {"admin_chrome": {"breadcrumbs": trail}},
    )
    assert 'aria-label="Breadcrumb"' in html
    assert html.count('aria-current="page"') == 1
    assert str(page.label) in html


def test_current_item_and_section_follow_the_page_chain():
    """Editing an email highlights Campaign › Pages and emails in the sidebar."""
    campaign = uuid4()
    match = SimpleNamespace(
        url_name="content_edit",
        namespace=navigation.NAMESPACE,
        kwargs={"campaign_id": campaign, "kind": "email", "slot": "initial"},
    )
    sections, trail = navigation.build(match, _items())
    current = [section for section in sections if section["current"]]
    assert [section["key"] for section in current] == ["campaign"]
    assert [item["label"] for item in current[0]["items"] if item["current"]] == [
        navigation.PAGES["content_catalog"].label
    ]
    assert [crumb["label"] for crumb in trail] == [
        navigation.PAGES["index"].label,
        navigation.SECTION_LABELS["campaign"],
        navigation.PAGES["content_catalog"].label,
        navigation.PAGES["content_edit"].label,
    ]
    assert trail[2]["url"] == reverse("admin:content_catalog", args=[campaign])


def test_sections_without_visible_entries_are_omitted():
    """Capability filtering happens upstream: an unlisted section never appears."""
    match = SimpleNamespace(url_name="reports", namespace="admin", kwargs={})
    items = [("reports", "reports", "Campaign reports", "/r")]
    sections, trail = navigation.build(match, items)
    assert [section["key"] for section in sections] == ["reports"]
    assert trail[1] == {"label": navigation.SECTION_LABELS["reports"], "url": "/r"}


def test_breadcrumb_label_overrides_only_the_current_page():
    """A view can name the specific email while ancestors keep their labels."""
    trail = [
        {"label": "Home", "url": "/admin/"},
        {"label": "Campaign", "url": "/c"},
        {"label": "Edit page or email", "url": None},
    ]
    html = render_to_string(
        "stewardship/admin-breadcrumbs.html",
        {"admin_chrome": {"breadcrumbs": trail}, "breadcrumb_label": "Reminder"},
    )
    assert '<span aria-current="page">Reminder</span>' in html
    assert "Edit page or email" not in html


def test_non_admin_and_unknown_routes_yield_no_trail():
    """Family pages and unclassified matches never get Admin breadcrumbs."""
    for match in (
        None,
        SimpleNamespace(url_name="index", namespace="family", kwargs={}),
        SimpleNamespace(url_name="logout", namespace="admin", kwargs={}),
    ):
        _, trail = navigation.build(match, _items())
        assert trail == []
