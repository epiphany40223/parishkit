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
    """Plausible arguments for a route and its ancestors: UUIDs and strings.

    A page's ancestors may need arguments its own route lacks (a test email
    names no slot); its view supplies those, so the whole chain's are given.
    """
    return {
        parameter: uuid4() if converter == "uuid" else "parishsoft"
        for page in navigation._chain(name)
        for converter, parameter in ROUTES[page]
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
        # A POST-only review page is named but never linked.
        assert crumb["url"] == (
            reverse(f"admin:{ancestor}", kwargs={key: arguments[key] for key in needed})
            if navigation.PAGES[ancestor].linkable
            else None
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
    # The listed ancestor is marked as the current location, not the page.
    assert [
        (item["label"], item["current"])
        for item in current[0]["items"]
        if item["current"]
    ] == [(navigation.PAGES["content_catalog"].label, "true")]
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


def test_exact_page_entry_is_marked_as_the_current_page():
    """On a sidebar entry's own page, its link is aria-current="page"."""
    match = SimpleNamespace(url_name="reports", namespace="admin", kwargs={})
    sections, _ = navigation.build(match, _items())
    marked = [
        item for section in sections for item in section["items"] if item["current"]
    ]
    assert [(item["label"], item["current"]) for item in marked] == [
        (navigation.PAGES["reports"].label, "page")
    ]


def test_retained_content_trail_avoids_the_current_campaign_editor():
    """Historical content links back through Campaign settings, not the editor."""
    assert navigation._chain("content_history_revision") == [
        "campaign_settings",
        "content_history",
        "content_history_revision",
    ]


@pytest.mark.parametrize(
    "state,ever_active,locked,mode,modules,offered",
    [
        ("draft", False, False, "testing", ["financial"], True),
        ("draft", False, False, "testing", ["census"], False),
        ("draft", False, True, "testing", ["financial"], False),
        ("draft", True, False, "testing", ["financial"], False),
        ("active", True, True, "production", ["financial"], False),
        ("draft", False, False, "production", ["financial"], False),
    ],
)
def test_share_options_is_offered_only_where_it_can_be_edited(
    monkeypatch, state, ever_active, locked, mode, modules, offered
):
    """The sidebar never links to share options the page would refuse."""
    from parishkit.stewardship.accounts import admin_context

    monkeypatch.setattr(admin_context, "allows", lambda *args, **kwargs: True)
    campaign = SimpleNamespace(
        pk=uuid4(),
        state=state,
        ever_active=ever_active,
        structural_locked=locked,
        active_configuration=SimpleNamespace(values={"modules": modules}),
    )
    items = admin_context._navigation_items(
        SimpleNamespace(ministries=()), True, campaign, SimpleNamespace(mode=mode)
    )
    assert any(name == "share_settings" for _, name, _, _ in items) is offered


def _match(name, **kwargs):
    """A resolver-match stand-in for an Admin page."""
    return SimpleNamespace(url_name=name, namespace=navigation.NAMESPACE, kwargs=kwargs)


def test_a_test_email_trail_names_its_email_editor():
    """Preview and test email sits under the revision it sends, by name."""
    campaign, revision = uuid4(), uuid4()
    placed = navigation.Placement(
        arguments={"kind": "email", "slot": "initial"},
        labels={"content_revision": "Initial invitation"},
    )
    match = _match("campaign_mail_families", campaign_id=campaign, revision_id=revision)
    _, trail = navigation.build(match, _items(), placed)
    assert [crumb["label"] for crumb in trail][2:] == [
        navigation.PAGES["content_catalog"].label,
        "Initial invitation",
        navigation.PAGES["campaign_mail"].label,
        navigation.PAGES["campaign_mail_families"].label,
    ]
    assert trail[3]["url"] == reverse(
        "admin:content_revision", args=[campaign, "email", "initial", revision]
    )
    assert trail[4]["url"] == reverse("admin:campaign_mail", args=[campaign, revision])
    assert navigation.back(match, placed)["url"] == trail[4]["url"]


def test_a_placed_configuration_change_joins_its_origin_section():
    """A change confirmed on an editor shows that editor's trail and section."""
    campaign, request_id = uuid4(), uuid4()
    placed = navigation.Placement(
        parent="content_edit",
        arguments={"campaign_id": campaign, "kind": "email", "slot": "initial"},
        flow="change",
        step="apply",
    )
    match = _match("configuration_request", request_id=request_id)
    sections, trail = navigation.build(match, _items(), placed)
    assert [crumb["label"] for crumb in trail] == [
        navigation.PAGES["index"].label,
        navigation.SECTION_LABELS["campaign"],
        navigation.PAGES["content_catalog"].label,
        navigation.PAGES["content_edit"].label,
        navigation.PAGES["configuration_request"].label,
    ]
    edit = reverse("admin:content_edit", args=[campaign, "email", "initial"])
    assert trail[3]["url"] == edit
    assert [section["key"] for section in sections if section["current"]] == [
        "campaign"
    ]
    assert navigation.back(match, placed) == {
        "label": navigation.PAGES["content_edit"].label,
        "url": edit,
    }


def test_an_unplaced_configuration_change_returns_home():
    """Without a remembered origin, the status page stands under Home."""
    match = _match("configuration_request", request_id=uuid4())
    _, trail = navigation.build(match, _items())
    assert [crumb["label"] for crumb in trail] == [
        navigation.PAGES["index"].label,
        navigation.PAGES["configuration_request"].label,
    ]
    assert navigation.back(match) == {
        "label": navigation.PAGES["index"].label,
        "url": reverse("admin:index"),
    }


def test_a_rule_change_returns_to_portal_users_not_the_post_only_review():
    """The rule review cannot be opened by a link, so Return goes to users."""
    placed = navigation.Placement(parent="user_rules", flow="change", step="apply")
    match = _match("configuration_request", request_id=uuid4())
    _, trail = navigation.build(match, _items(), placed)
    assert trail[-2] == {"label": navigation.PAGES["user_rules"].label, "url": None}
    assert navigation.back(match, placed)["url"] == reverse("admin:users")


def test_an_unknown_placed_parent_is_ignored():
    """A parent that is not a registered page leaves the static chain."""
    match = _match("configuration_request", request_id=uuid4())
    placed = navigation.Placement(parent="login")
    _, trail = navigation.build(match, _items(), placed)
    assert len(trail) == 2


@pytest.mark.parametrize(
    "step,states",
    [
        ("edit", ["current", "upcoming", "upcoming"]),
        ("review", ["done", "current", "upcoming"]),
        ("apply", ["done", "done", "current"]),
    ],
)
def test_flow_steps_mark_done_current_and_upcoming(step, states):
    """The indicator shows every step of the flow and where the Admin is."""
    shown = navigation.steps(navigation.Placement(flow="change", step=step))
    assert [item["state"] for item in shown] == states
    assert [item["label"] for item in shown] == [
        label for _key, label in navigation.FLOWS["change"]
    ]
    html = render_to_string(
        "stewardship/admin-flow-steps.html", {"admin_chrome": {"flow_steps": shown}}
    )
    assert html.count('aria-current="step"') == 1
    assert html.count("(done)") == states.count("done")


def test_flow_steps_are_absent_without_a_known_flow_and_step():
    """Pages outside a flow, or naming an unknown step, show no indicator."""
    for placed in (
        None,
        navigation.Placement(),
        navigation.Placement(flow="change", step="nonsense"),
        navigation.Placement(flow="nonsense", step="edit"),
    ):
        assert navigation.steps(placed) == []
    assert "<ol" not in render_to_string(
        "stewardship/admin-flow-steps.html", {"admin_chrome": {"flow_steps": []}}
    )


def test_change_origins_are_remembered_per_request_and_bounded():
    """The session maps recent change requests to the editor they came from."""
    campaign = uuid4()
    editor = reverse("admin:content_edit", args=[campaign, "email", "initial"])
    request = SimpleNamespace(session={}, path=editor)
    first = uuid4()
    navigation.remember_origin(request, first)
    assert navigation.change_origin(request, first) == (
        "content_edit",
        {"campaign_id": campaign, "kind": "email", "slot": "initial"},
    )
    assert navigation.change_origin(request, uuid4()) is None
    for _ in range(navigation.ORIGINS_KEPT):
        navigation.remember_origin(request, uuid4())
    assert len(request.session[navigation.ORIGINS_KEY]) == navigation.ORIGINS_KEPT
    assert navigation.change_origin(request, first) is None


@pytest.mark.parametrize(
    "path",
    [
        "/family/",
        "/admin/no-such-page",
        "/admin/logout",
        # A fixed id keeps the test ID the same wherever it is collected.
        "/admin/configuration/requests/00000000-0000-4000-8000-000000000001",
        None,
    ],
)
def test_change_origins_accept_only_registered_admin_pages(path):
    """A remembered path that is not an Admin page only loses the trail."""
    request_id = uuid4()
    request = SimpleNamespace(session={navigation.ORIGINS_KEY: {str(request_id): path}})
    assert navigation.change_origin(request, request_id) is None


def test_change_origins_need_a_session():
    """Requests without a session (scripts, tests) neither store nor read."""
    request = SimpleNamespace(path="/admin/users")
    navigation.remember_origin(request, uuid4())
    assert navigation.change_origin(request, uuid4()) is None


def test_a_key_page_for_an_unknown_integration_stays_under_integrations():
    """An unknown target places nothing, so the trail and Return stop there."""
    from parishkit.stewardship.accounts.integration_views import place_key_page

    request = SimpleNamespace()
    place_key_page(request, "nonsense")
    assert navigation.placement(request) is None
    match = _match("credential_status", request_id=uuid4())
    _, trail = navigation.build(match, _items())
    assert trail[-2] == {
        "label": navigation.PAGES["integration_settings"].label,
        "url": None,
    }
    assert navigation.back(match) == {
        "label": navigation.PAGES["integrations"].label,
        "url": reverse("admin:integrations"),
    }
    # A step still shows without a known integration.
    place_key_page(request, "nonsense", flow="change", step="review")
    assert navigation.placement(request).step == "review"


def test_go_live_steps_run_from_readiness_to_activation():
    """Going live shows its five steps; earlier ones are done, none are links."""
    shown = navigation.steps(navigation.Placement(flow="go_live", step="links"))
    assert [item["state"] for item in shown] == [
        "done",
        "done",
        "current",
        "upcoming",
        "upcoming",
    ]
    html = render_to_string(
        "stewardship/admin-flow-steps.html", {"admin_chrome": {"flow_steps": shown}}
    )
    assert "<a " not in html and html.count("<li") == 5
