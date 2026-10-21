"""The shared tab component (#1712; ui-conventions spec, "Tabs").

A page with several views of one subject draws them as tabs
(components/tabs.html) from the ``TabSet`` that ``web/tabs.py`` builds from
the address's bare ``tab`` parameter. These tests cover the helper (the
chosen tab, its fallback and each tab's link), the drawn component (the
WAI-ARIA roles and states, the roving tabindex, the lazily drawn panels and
an unavailable tab) and the in-place guard: every tab names its own panel,
a region in the component itself, as its fragment. The behaviour is in the
browser tests (browser/test_tabs.py).
"""

import re
from pathlib import Path

import pytest
from django.template import engines
from django.template.loader import render_to_string

from parishkit.stewardship.web.tabs import Tab, tab_set

from .test_in_place_controls import IN_PLACE_TAG, names_region

COMPONENT = "stewardship/components/tabs.html"
SOURCE = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship/components/tabs.html"
)
PANEL = engines["django"].from_string("<p>Panel for {{ tab.key }}</p>")


def three(parameters, **extra):
    """Timeline, an unavailable Census and Financial (with a count)."""
    return tab_set(
        "/admin/family/",
        parameters,
        [
            Tab("timeline", "Timeline", template=PANEL),
            Tab("census", "Census", unavailable="Census is not in this campaign."),
            Tab("financial", "Financial", template=PANEL, count=3),
        ],
        **extra,
    )


def render(parameters):
    """The component drawn for an address's parameters."""
    return render_to_string(
        COMPONENT,
        {"tabs_id": "hub", "label": "Family information", "tabs": three(parameters)},
    )


def tag(html, element_id):
    """The opening tag of the element with ``element_id``."""
    return re.search(rf'<[^>]*\bid="{element_id}"[^>]*>', html).group(0)


def test_the_address_chooses_the_tab_and_falls_back_to_the_first():
    """``?tab=`` names the chosen tab; no parameter, an unknown key or an
    unavailable tab shows the first available one, with no error."""
    assert three({}).selected == "timeline"
    assert three({"tab": "financial"}).selected == "financial"
    assert three({"tab": "nonsense"}).selected == "timeline"
    assert three({"tab": "census"}).selected == "timeline"
    chosen = three({"tab": "financial"})
    assert chosen.chosen.key == "financial"
    assert [tab.selected for tab in chosen.tabs] == [False, False, True]
    assert chosen.default == "timeline"


def test_the_default_skips_an_unavailable_first_tab():
    """The first available tab is the default, not the first drawn."""
    tabs = tab_set(
        "/p",
        {},
        [Tab("a", "A", unavailable="Not yet."), Tab("b", "B", template=PANEL)],
    )
    assert tabs.default == tabs.selected == "b"


def test_each_link_is_the_page_with_its_carried_parameters_and_tab():
    """A tab's link keeps only what the page carries, never a stale tab."""
    tabs = three({}, carry=(("family", "12 34"), ("tab", "old")))
    assert [tab.href for tab in tabs.tabs] == [
        "/admin/family/?family=12+34&tab=timeline",
        "/admin/family/?family=12+34&tab=census",
        "/admin/family/?family=12+34&tab=financial",
    ]


@pytest.mark.parametrize(
    "tabs, message",
    [
        ([Tab("a", "A", template=PANEL), Tab("a", "B", template=PANEL)], "unique"),
        ([Tab("a", "A", unavailable="Not yet.")], "available"),
    ],
)
def test_a_set_needs_unique_keys_and_an_available_tab(tabs, message):
    """Duplicate keys or nothing to show is a programming error."""
    with pytest.raises(ValueError, match=message):
        tab_set("/p", {}, tabs)


@pytest.mark.parametrize(
    "key, template, message",
    [("Not a slug", PANEL, "slug"), ("a", "", "panel template")],
)
def test_a_tab_needs_a_slug_key_and_a_panel(key, template, message):
    """A key becomes an id and an address value; an available tab needs a
    panel to draw."""
    with pytest.raises(ValueError, match=message):
        Tab(key, "A", template=template)


def test_the_row_is_a_labelled_tablist_with_a_roving_tabindex():
    """Each available tab is an in-place link to its address that names its
    panel; only the chosen tab is selected and in the Tab order."""
    html = render({"tab": "financial"})
    row = re.search(r'<div class="tabs-list"[^>]*>', html).group(0)
    assert 'role="tablist"' in row and 'aria-label="Family information"' in row
    assert 'data-tabs-default="timeline"' in row
    timeline = tag(html, "hub-tab-timeline")
    financial = tag(html, "hub-tab-financial")
    assert 'href="/admin/family/?tab=timeline#hub-panel-timeline"' in timeline
    assert 'aria-controls="hub-panel-timeline"' in timeline
    assert 'aria-selected="false"' in timeline and 'tabindex="-1"' in timeline
    assert 'aria-selected="true"' in financial and 'tabindex="0"' in financial
    for each in (timeline, financial):
        assert 'role="tab"' in each
        assert 'data-in-place="hub-tab-' in each and "data-in-place-only" in each
    assert '>Financial <span class="tabs-count">3</span></a>' in html


def test_only_the_chosen_panel_is_drawn():
    """Panels load lazily: the chosen one holds its template and is marked
    loaded; every other is an empty, hidden region of its own."""
    html = render({"tab": "financial"})
    chosen = tag(html, "hub-panel-financial")
    other = tag(html, "hub-panel-timeline")
    for each in (chosen, other):
        assert 'role="tabpanel"' in each and 'tabindex="0"' in each
        assert "data-in-place-region" in each and "data-in-place-own" in each
    assert 'aria-labelledby="hub-tab-financial"' in chosen
    assert "data-tab-loaded" in chosen and "hidden" not in chosen
    assert "hidden" in other and "data-tab-loaded" not in other
    assert "<p>Panel for financial</p>" in html
    assert "Panel for timeline" not in html
    assert f"{other}</div>" in html


def test_an_unavailable_tab_is_greyed_with_its_reason_and_no_panel():
    """No link and no panel; it is named by a tip that sits after the row,
    so the tablist holds only tabs."""
    html = render({})
    census = tag(html, "hub-tab-census")
    assert "href" not in census and 'aria-disabled="true"' in census
    assert 'aria-describedby="hub-tip-census"' in census and "data-menu-tip" in census
    assert 'id="hub-panel-census"' not in html
    row = re.search(r'role="tablist".*?</div>', html, re.S).group(0)
    assert "hub-tip-census" in row and 'id="hub-tip-census"' not in row
    tip = re.search(r'<span class="admin-tip" id="hub-tip-census"[^>]*>([^<]*)', html)
    assert tip.group(1) == "Census is not in this campaign."
    assert 'role="tooltip"' in tip.group(0)


def test_the_tab_region_is_a_named_fragment():
    """The in-place guard (test_in_place_controls) sees every tab link, and
    each one's fragment names a region the component itself draws, both in
    the template's source and in what it renders."""
    source = SOURCE.read_text()
    links = [match.group(0) for match in IN_PLACE_TAG.finditer(source)]
    assert len(links) == 1 and 'role="tab"' in links[0]
    fragment = re.search(r'href="[^"#]*#([^"]*)"', links[0]).group(1)
    assert fragment == "{{ tabs_id }}-panel-{{ tab.key }}"
    assert names_region(source, fragment)
    html = render({})
    for link in IN_PLACE_TAG.finditer(html):
        drawn = re.search(r'href="[^"#]*#([^"]*)"', link.group(0)).group(1)
        assert names_region(html, drawn), drawn
