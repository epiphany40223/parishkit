"""The declarative Admin navigation covers every route and yields sound trails."""

import json
import logging
import re
from contextlib import nullcontext
from html.parser import HTMLParser
from pathlib import Path
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


def _items(arguments=None):
    """Every menu entry, available, as if the actor could open everything.

    Each entry's URL is the page's own, built from ``arguments`` where its
    route needs them, so trails may link it.
    """
    return [
        navigation.MenuItem(
            navigation.PAGES[entry.name].section,
            entry.name,
            navigation.PAGES[entry.name].label,
            navigation._link(entry.name, arguments or {}) or f"/nav/{entry.name}",
        )
        for entry in navigation.MENU
    ]


def test_every_admin_route_is_either_a_page_or_explicitly_not_one():
    """A new Admin route must be placed in the navigation or listed as a non-page."""
    pages = set(navigation.PAGES)
    assert not pages & navigation.NON_PAGES
    assert not (pages | navigation.NON_PAGES) & navigation.LEGACY
    known = pages | navigation.NON_PAGES | navigation.LEGACY
    assert set(ROUTES) == known, set(ROUTES) ^ known


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
    sections, trail = navigation.build(match, _items(arguments))
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
    sections, trail = navigation.build(match, _items({"campaign_id": campaign}))
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
    assert trail[2]["url"] == reverse("admin:content_catalog")


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
    match = SimpleNamespace(
        url_name="participation", namespace="admin", kwargs={"campaign_id": uuid4()}
    )
    sections, _ = navigation.build(match, _items())
    marked = [
        item for section in sections for item in section["items"] if item["current"]
    ]
    assert [(item["label"], item["current"]) for item in marked] == [
        (navigation.PAGES["participation"].label, "page")
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
    # The entry stays in the menu either way; it is linked only when offered.
    (share,) = [item for item in items if item.name == "share_settings"]
    assert (share.url is not None) is offered
    assert (share.reason is None) is offered


def _match(name, **kwargs):
    """A resolver-match stand-in for an Admin page."""
    return SimpleNamespace(url_name=name, namespace=navigation.NAMESPACE, kwargs=kwargs)


def test_a_test_email_trail_names_its_email_editor():
    """Preview and test email sits under the revision it sends, by name."""
    revision = uuid4()
    placed = navigation.Placement(
        arguments={"kind": "email", "slot": "initial"},
        labels={"content_revision": "Initial invitation"},
    )
    match = _match("campaign_mail_families", revision_id=revision)
    _, trail = navigation.build(match, _items(), placed)
    assert [crumb["label"] for crumb in trail][2:] == [
        navigation.PAGES["content_catalog"].label,
        "Initial invitation",
        navigation.PAGES["campaign_mail"].label,
        navigation.PAGES["campaign_mail_families"].label,
    ]
    assert trail[3]["url"] == reverse(
        "admin:content_revision", args=["email", "initial", revision]
    )
    assert trail[4]["url"] == reverse("admin:campaign_mail", args=[revision])
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
    edit = reverse("admin:content_edit", args=["email", "initial"])
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
    editor = reverse("admin:content_edit", args=["email", "initial"])
    request = SimpleNamespace(session={}, path=editor)
    first = uuid4()
    navigation.remember_origin(request, first)
    assert navigation.change_origin(request, first) == (
        "content_edit",
        {"kind": "email", "slot": "initial"},
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
        "/admin/changes/00000000-0000-4000-8000-000000000001/",
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


@pytest.mark.parametrize("offered", [True, False])
def test_a_sidebar_page_is_linked_only_while_the_sidebar_offers_it(offered):
    """A remembered origin the sidebar no longer offers is named, not linked.

    Share options, say, refuses once its campaign is locked, and the sidebar
    then hides it; the trail and Return link follow the sidebar (#196).
    """
    campaign = uuid4()
    share = reverse("admin:share_settings")
    items = [
        ("campaign", "campaign_settings", "Campaign settings", "/c"),
        *([("campaign", "share_settings", "Share options", share)] if offered else []),
    ]
    placed = navigation.Placement(
        parent="share_settings", arguments={"campaign_id": campaign}
    )
    match = _match("configuration_request", request_id=uuid4())
    _, trail = navigation.build(match, items, placed)
    assert trail[-2] == {
        "label": navigation.PAGES["share_settings"].label,
        "url": share if offered else None,
    }
    assert navigation.back(match, placed, items)["url"] == (
        share if offered else reverse("admin:index")
    )


def test_an_error_page_shows_no_step_or_placed_trail():
    """A view that recorded a step and then failed a recheck shows neither."""
    from parishkit.stewardship.web.error_pages import ERROR_PAGE_ATTRIBUTE

    request = SimpleNamespace()
    navigation.place(request, flow="go_live", step="confirm")
    assert navigation.placement(request).step == "confirm"
    setattr(request, ERROR_PAGE_ATTRIBUTE, True)
    assert navigation.placement(request) is None


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


# Stable menu shape (ADM-12.02 to .04; admin-portal spec, "Stable menu shape").


def _principal(role):
    """A real principal for a role; a Ministry leader holds one Ministry."""
    from parishkit.stewardship.accounts.policy import Principal

    return Principal(
        uuid4(),
        frozenset({role}),
        frozenset({9}) if role == "ministry_leader" else frozenset(),
    )


def _campaign(state="draft", modules=("financial", "ministry"), **facts):
    """A current-campaign stand-in carrying only what the menu reads."""
    return SimpleNamespace(
        pk=uuid4(),
        state=state,
        ever_active=facts.get("ever_active", state != "draft"),
        structural_locked=facts.get("locked", state != "draft"),
        active_configuration=SimpleNamespace(values={"modules": list(modules)}),
    )


# Every campaign situation the chrome can be in: no current campaign, each
# lifecycle state, each module set, and a locked or once-live draft.
CAMPAIGNS = {
    "none": None,
    "draft": _campaign(),
    "locked draft": _campaign(locked=True),
    "once-live draft": _campaign(ever_active=True),
    "census only": _campaign(modules=("census",)),
    "financial only": _campaign(modules=("financial",)),
    "ministry only": _campaign(modules=("ministry",)),
    **{
        state: _campaign(state)
        for state in ("scheduled", "active", "closed", "archived", "purging")
    },
}
ROLES = ("administrator", "staff", "ministry_leader")
# Which entries each role sees, whatever the mode or campaign state.
REPORTS = [
    "response_dashboard",
    "participation",
    "financial_report",
    "talents_report",
    "information_queue",
    "ministry_report",
    "ministry_followup",
    "family_directory",
]
# The Administrator's menu in the spec's order (admin-portal spec, "Menu
# groups"), written out so that reordering MENU fails here. Entries for pages
# that do not exist yet are absent; Send a weekly report now holds the
# place of Emailed reports until NAV-14.
SPEC_ORDER = [
    # Campaign setup
    "campaign_settings",
    "content_catalog",
    "artwork_settings",
    "schedule_settings",
    "share_settings",
    "talent_settings",
    "reminder_workgroup",
    "go_live",
    "production_progress",
    # Mail and Family portal
    "delivery_control",
    "family_email_progress",
    "family_email_sends",
    "deliveries",
    "family_portal",
    "presence",
    # Responses and reports
    "response_dashboard",
    "participation",
    "financial_report",
    "talents_report",
    "information_queue",
    "ministry_report",
    "ministry_followup",
    "family_directory",
    "weekly_digest_manual",
    # Parish data
    "parish_settings",
    "branding_settings",
    "ministries",
    "hosted_files",
    "source_refresh",
    # Users and access
    "users",
    "automation_access",
    # System
    "system_health",
    "integrations",
    "background",
    "logs",
]
SHAPES = {
    "administrator": SPEC_ORDER,
    "staff": REPORTS,
    "ministry_leader": ["ministry_report", "ministry_followup"],
}


def _menu(role, campaign, mode):
    """The menu items ``portal_chrome`` would build for this situation."""
    from parishkit.stewardship.accounts import admin_context
    from parishkit.stewardship.accounts.policy import Capability, allows

    actor = _principal(role)
    return admin_context._navigation_items(
        actor,
        allows(actor, Capability.CONFIGURE),
        campaign,
        SimpleNamespace(mode=mode),
    )


@pytest.mark.parametrize("role", ROLES)
def test_each_role_has_one_menu_shape_in_every_mode_and_campaign_state(role):
    """Only the role hides an entry: the mode and the campaign only grey one out."""
    for mode in ("testing", "production"):
        for name, campaign in CAMPAIGNS.items():
            items = _menu(role, campaign, mode)
            assert [item.name for item in items] == SHAPES[role], (mode, name)
            # Each entry sits in its page's group, in the menu's group order.
            order = [section.key for section in navigation.SECTIONS]
            assert [order.index(item.section) for item in items] == sorted(
                order.index(item.section) for item in items
            )
            for item in items:
                # Available with a URL, or greyed out with a reason: never both.
                assert (item.url is None) == bool(item.reason), (mode, name, item)


def test_every_campaign_entry_is_greyed_out_without_a_current_campaign():
    """No campaign: the campaign's pages say so instead of disappearing."""
    items = _menu("administrator", None, "testing")
    needs = {entry.name for entry in navigation.MENU if entry.campaign} | {
        # Campaign pages whose URLs name no campaign (#525) still need one.
        "campaign_settings",
        "content_catalog",
        "artwork_settings",
        "schedule_settings",
        "share_settings",
        "talent_settings",
        "reminder_workgroup",
        "go_live",
        "production_progress",
        "delivery_control",
        "family_email_progress",
        "family_email_sends",
        "response_dashboard",
        "participation",
        "financial_report",
        "talents_report",
        "information_queue",
        "ministry_report",
        "ministry_followup",
        "family_directory",
    }
    for item in items:
        if item.name in needs:
            assert (item.url, item.reason) == (None, navigation.NO_CAMPAIGN), item
        else:
            assert item.url and item.reason is None, item


@pytest.mark.parametrize(
    "campaign,mode,name,reason",
    [
        ("draft", "testing", "delivery_control", "Available in Production mode"),
        (
            "draft",
            "testing",
            "production_progress",
            "Production has not been confirmed for this campaign; "
            "start at Go-live readiness",
        ),
        # Production mode set without a confirmation: the draft never went live.
        (
            "draft",
            "production",
            "production_progress",
            "Production has not been confirmed for this campaign; "
            "start at Go-live readiness",
        ),
        (
            "active",
            "production",
            "go_live",
            "Only a draft campaign can go live; this one already has",
        ),
        (
            "active",
            "production",
            "share_settings",
            "Can be changed only in Testing mode, before the campaign goes live",
        ),
        ("archived", "production", "content_catalog", "The campaign is archived"),
        ("archived", "production", "artwork_settings", "The campaign is archived"),
        (
            "census only",
            "testing",
            "financial_report",
            "This campaign does not include Financial stewardship",
        ),
        (
            "census only",
            "testing",
            "ministry_report",
            "This campaign does not include Ministry stewardship",
        ),
    ],
)
def test_unavailable_entries_carry_their_plain_language_reason(
    campaign, mode, name, reason
):
    """Each greyed entry says why, in the words its tip shows."""
    (item,) = [
        item
        for item in _menu("administrator", CAMPAIGNS[campaign], mode)
        if item.name == name
    ]
    assert (item.url, str(item.reason)) == (None, reason)


def test_available_campaign_entries_link_the_current_campaign():
    """Production with a live campaign opens Pause and resume mail and activation."""
    campaign = CAMPAIGNS["active"]
    urls = {
        item.name: item.url for item in _menu("administrator", campaign, "production")
    }
    assert urls["delivery_control"] == reverse("admin:delivery_control")
    assert urls["production_progress"] == reverse("admin:production_progress")
    # The Response dashboard has its own entry (#522).
    assert urls["response_dashboard"] == reverse("admin:response_dashboard")
    assert urls["participation"] == reverse("admin:participation")
    assert urls["deliveries"] == reverse("admin:deliveries")


def test_menu_entries_and_groups_follow_the_spec():
    """Seven groups in campaign order; every entry is a page in its group."""
    assert [str(section.label) for section in navigation.SECTIONS] == [
        "Campaign setup",
        "Mail and Family portal",
        "Responses and reports",
        "Parish data",
        "Users and access",
        "System",
    ]
    assert len(navigation.MENU_NAMES) == len(navigation.MENU)
    for entry in navigation.MENU:
        assert navigation.PAGES[entry.name].parent is None, entry.name
        assert entry.name in ROUTES, entry.name


class _Menu(HTMLParser):
    """Collect the sidebar's anchors, tips, groups and summaries by attribute."""

    def __init__(self):
        """Start with nothing collected."""
        super().__init__()
        self.anchors, self.tips, self.groups, self.summaries = [], {}, [], {}
        self.lists, self._tip = [], None

    def handle_starttag(self, tag, attrs):
        """Record each element of interest with its attributes."""
        attrs = dict(attrs)
        if tag == "a":
            self.anchors.append(attrs)
        elif tag == "span" and attrs.get("role") == "tooltip":
            self._tip = attrs["id"]
            self.tips[self._tip] = {"attrs": attrs, "text": ""}
        elif tag == "details" and "data-menu-group" in attrs:
            self.groups.append(attrs)
        elif tag == "summary" and "id" in attrs:
            self.summaries[attrs["id"]] = attrs
        elif tag == "ul" and "aria-labelledby" in attrs:
            self.lists.append(attrs["aria-labelledby"])

    def handle_endtag(self, tag):
        """A tip's text ends with its span."""
        if tag == "span":
            self._tip = None

    def handle_data(self, data):
        """Gather a tip's reason text."""
        if self._tip:
            self.tips[self._tip]["text"] += data


def _render(items, match=None):
    """The sidebar HTML for these menu items on the given page."""
    sections, _trail = navigation.build(match, items)
    return render_to_string(
        "stewardship/admin-navigation.html",
        {"admin_chrome": {"home_url": "/admin/", "sections": sections}},
    )


def test_greyed_entries_are_focusable_links_without_href_described_by_their_reason():
    """No href; role, aria-disabled, tabindex and aria-describedby, plus the tip."""
    items = _menu("administrator", None, "testing")
    parsed = _Menu()
    parsed.feed(_render(items))
    greyed = [anchor for anchor in parsed.anchors if "aria-disabled" in anchor]
    assert len(greyed) == sum(1 for item in items if item.url is None) > 0
    for anchor in greyed:
        assert "href" not in anchor
        assert anchor["role"] == "link"
        assert anchor["aria-disabled"] == "true"
        assert anchor["tabindex"] == "0"
        tip = parsed.tips[anchor["aria-describedby"]]
        assert tip["text"] == str(navigation.NO_CAMPAIGN)
    available = [anchor for anchor in parsed.anchors if "href" in anchor]
    # Home, then every available entry; none of them is marked unavailable.
    assert len(available) == 1 + sum(1 for item in items if item.url)
    assert not any("role" in anchor or "tabindex" in anchor for anchor in available)
    # Tip ids are unique, so each entry announces its own reason.
    assert len(parsed.tips) == len(greyed)


def test_groups_are_labelled_disclosures_and_the_current_group_is_marked():
    """Each group is an open <details> whose summary names its entry list."""
    campaign = CAMPAIGNS["draft"]
    match = _match("content_edit", campaign_id=campaign.pk, kind="email", slot="x")
    parsed = _Menu()
    parsed.feed(_render(_menu("administrator", campaign, "testing"), match))
    assert [group["data-menu-group"] for group in parsed.groups] == [
        section.key for section in navigation.SECTIONS
    ]
    assert all("open" in group for group in parsed.groups)
    # Only the current page's group is forced open by the script.
    assert [
        group["data-menu-group"]
        for group in parsed.groups
        if "data-menu-current" in group
    ] == ["campaign"]
    assert parsed.lists == list(parsed.summaries)
    # The current entry stays marked on the link to its catalog.
    assert {"href", "aria-current"} <= set(
        next(anchor for anchor in parsed.anchors if anchor.get("aria-current"))
    )


@pytest.mark.parametrize("pending", [False, True])
def test_the_menu_ends_with_sign_out(pending):
    """Sign out is the last menu item, during setup as well."""
    html = render_to_string(
        "stewardship/admin-navigation.html",
        {
            "admin_chrome": {
                "setup_pending": pending,
                "setup_url": "/admin/setup",
                "home_url": "/admin/",
                "sections": navigation.build(None, _items())[0],
            }
        },
    )
    menu = html[: html.index("</nav>")]
    assert menu.rindex("Sign out") > max(menu.rindex("<a "), 0)
    assert '<form method="post" action="/admin/logout">' in menu


def test_admin_pages_load_the_menu_script_and_family_pages_do_not():
    """admin-menu-v1.js loads deferred on Admin pages only, apart from ui-v1.js."""
    script = '<script src="/static/stewardship/admin-menu-v1.js" defer></script>'
    assert script in render_to_string("stewardship/login.html", {})
    assert "admin-menu-v1.js" not in render_to_string(
        "stewardship/family-login.html", {}
    )
    static = Path(navigation.__file__).parent / "static" / "stewardship"
    assert "data-menu-tip" not in (static / "ui-v1.js").read_text(encoding="utf-8")
    assert "data-menu-tip" in (static / "admin-menu-v1.js").read_text(encoding="utf-8")


def test_the_menu_state_script_runs_right_after_the_sidebar():
    """admin-menu-state-v1.js loads without defer straight after the menu.

    It restores the remembered groups and the menu's scroll position (#620)
    before the page content is parsed, so the menu does not visibly jump; in
    the head the menu would not exist yet, and deferred it would run after
    the page shows.
    """
    html = render_to_string(
        "stewardship/admin-navigation.html",
        {"admin_chrome": {"setup_pending": True, "setup_url": "/admin/setup"}},
    )
    assert html.rstrip().endswith(
        '</nav>\n<script src="/static/stewardship/admin-menu-state-v1.js"></script>'
    )
    static = Path(navigation.__file__).parent / "static" / "stewardship"
    state = (static / "admin-menu-state-v1.js").read_text(encoding="utf-8")
    assert "pk-admin-menu-group:" in state and "sessionStorage" in state
    # The group state lives only in the early script.
    menu = (static / "admin-menu-v1.js").read_text(encoding="utf-8")
    assert "pk-admin-menu-group:" not in menu


def test_campaign_ministries_trail_runs_through_ministries():
    """Home › Parish data › Ministries › Campaign Ministries (one home, NAV-10)."""
    match = _match("campaign_ministries")
    sections, trail = navigation.build(match, _items())
    assert [crumb["label"] for crumb in trail] == [
        "Home",
        "Parish data",
        "Ministries",
        "Campaign Ministries",
    ]
    assert trail[2]["url"] == reverse("admin:ministries")
    assert trail[-1]["url"] is None
    assert [section["key"] for section in sections if section["current"]] == ["parish"]
    assert navigation.back(match, None, _items())["url"] == reverse("admin:ministries")


@pytest.mark.parametrize("role", ROLES)
def test_a_trail_links_only_pages_the_viewer_may_open(role):
    """No breadcrumb links a menu page the viewer's menu does not offer (#457 L3).

    The menu applies each page's own capability, so a crumb for a menu page,
    or for a report root that only redirects to one, is linked only when the
    menu offers it. Every page is checked for each role, with an active
    campaign so that each role's whole menu is available.
    """
    campaign = CAMPAIGNS["active"]
    items = _menu(role, campaign, "production")
    offered = {item.name: item.url for item in items if item.url}
    for name in sorted(navigation.PAGES):
        arguments = {**_arguments(name), "campaign_id": campaign.pk}
        _, trail = navigation.build(_match(name, **arguments), items)
        chain = navigation._chain(name)
        for ancestor, crumb in zip(chain[:-1], trail[-len(chain) : -1], strict=True):
            gate = navigation.PAGES[ancestor].entry or ancestor
            if crumb["url"] and gate in navigation.MENU_NAMES:
                assert gate in offered, (role, name, ancestor)


def test_a_ministry_leaders_export_names_participation_without_a_link():
    """A Ministry leader may open their export, but not the Participation report."""
    campaign = CAMPAIGNS["active"]
    for role, linked in (("ministry_leader", False), ("staff", True)):
        items = _menu(role, campaign, "production")
        for name in ("report_export", "report_exact"):
            _, trail = navigation.build(_match(name, request_id=uuid4()), items)
            assert trail[-2]["label"] == navigation.PAGES["participation"].label
            assert (trail[-2]["url"] == reverse("admin:reports")) is linked


def test_open_counts_read_once_per_request_and_only_for_offered_entries(
    monkeypatch,
):
    """One statement for both counts, none without a counted link (#585)."""
    from parishkit.stewardship.accounts import admin_context
    from parishkit.stewardship.reports.ministry_followup import FollowupQuery

    statements = []

    class Cursor:
        """Records each statement and answers both counts."""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, parameters):
            statements.append(parameters)

        def fetchone(self):
            return (
                3 if statements[-1]["information"] else None,
                2 if statements[-1]["ministry"] else None,
            )

    monkeypatch.setattr(
        admin_context,
        "connection",
        SimpleNamespace(cursor=lambda: Cursor(), in_atomic_block=False),
    )
    active = _campaign("active")
    cases = (
        # Both queues for Staff: every Ministry.
        ("staff", active, {"information_queue": 3, "ministry_followup": 2}),
        # A leader only has Ministry follow-up, scoped to their Ministry.
        ("ministry_leader", active, {"ministry_followup": 2}),
        # Greyed out (no Ministry module) or absent: no count, no query.
        ("ministry_leader", _campaign("active", modules=("census",)), {}),
        ("staff", None, {}),
    )
    for role, campaign, expected in cases:
        statements.clear()
        actor = _principal(role)
        items = _menu(role, campaign, "production")
        request = SimpleNamespace()
        counts = admin_context._open_counts(request, actor, items, campaign)
        assert counts == expected, role
        assert len(statements) == (1 if expected else 0), role
        # A second chrome render in the same request reuses the counts.
        assert admin_context._open_counts(request, actor, items, campaign) == counts
        assert len(statements) == (1 if expected else 0), role
        if expected:
            operational = role != "ministry_leader"
            assert statements[0]["operational"] is operational
            assert statements[0]["scope"] == ([] if operational else [9])
            assert statements[0]["campaign"] == campaign.pk
            # The count runs the follow-up page's own default filters.
            assert json.loads(statements[0]["filters"]) == (
                FollowupQuery().form_values() | {"assignee": "any"}
            )
            assert statements[0]["viewer"] == actor.identity


def test_open_counts_are_plain_numbers_with_screen_reader_words():
    """A count shows after the name, read as "(N open)"; zero shows none (#585)."""
    campaign = _campaign("active")
    items = _menu("staff", campaign, "production")
    sections, _trail = navigation.build(None, items)
    counts = {"information_queue": 1234, "ministry_followup": 0}
    for section in sections:
        for entry in section["items"]:
            entry["count"] = counts.get(entry["name"])
    html = render_to_string(
        "stewardship/admin-navigation.html",
        {"admin_chrome": {"home_url": "/admin/", "sections": sections}},
    )
    information = reverse("admin:information_queue")
    assert (
        f'<a href="{information}">Additional information '
        '<span class="admin-menu-count" aria-hidden="true">1,234</span>'
        '<span class="visually-hidden">(1,234 open)</span></a>'
    ) in html
    assert html.count("admin-menu-count") == 1


def test_a_failed_open_counts_query_leaves_the_menu_without_numbers(monkeypatch):
    """A database error in the counts is logged and the menu shows no count.

    The counts only decorate the menu (#585), so an error in their statement
    (for example inside the follow-up selection) must not break the page:
    the chrome gets no numbers, the failure is logged at WARNING to the
    process log and System logs, and the empty result is kept for the
    request, so the failing statement is not retried within it.
    """
    from django.db import DatabaseError

    from parishkit.stewardship.accounts import admin_context
    from parishkit.stewardship.observability import Event, FailureKind

    class Cursor:
        """Fails the counts statement as the selection would."""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, parameters):
            raise DatabaseError("invalid input syntax for type bigint")

    calls = []
    monkeypatch.setattr(
        admin_context,
        "connection",
        SimpleNamespace(cursor=lambda: Cursor(), in_atomic_block=True),
    )
    # Inside a view's transaction the count takes a savepoint; no database
    # here, so it is a recorded no-op stand-in.
    savepoints = []

    def atomic():
        """Record the savepoint the count takes."""
        savepoints.append(True)
        return nullcontext()

    monkeypatch.setattr(admin_context, "transaction", SimpleNamespace(atomic=atomic))
    monkeypatch.setattr(
        admin_context,
        "emit_failure",
        lambda error, **details: calls.append(("emit", details)),
    )
    monkeypatch.setattr(
        admin_context,
        "operational",
        lambda event, **details: calls.append(("operational", event, details)),
    )
    campaign = _campaign("active")
    items = _menu("staff", campaign, "production")
    request = SimpleNamespace()
    actor = _principal("staff")
    assert admin_context._open_counts(request, actor, items, campaign) == {}
    assert admin_context._open_counts(request, actor, items, campaign) == {}
    emit, durable = calls
    # The count's savepoint, then the durable entry's own.
    assert savepoints == [True, True]
    assert emit[1]["event"] is Event.REPORT_SHAPING_FAILED
    assert emit[1]["level"] == logging.WARNING
    assert durable[1] is Event.REPORT_SHAPING_FAILED
    assert durable[2]["level"] == "WARNING"
    assert durable[2]["context"]["failure_kind"] is FailureKind.DATABASE
