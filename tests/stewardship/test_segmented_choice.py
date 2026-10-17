"""The shared binary choice (#1535; ui-conventions spec, "Binary choices").

A single choice between two views of an Admin page is a segmented pair
(components/segmented-choice.html): a labelled group of two in-place links,
the chosen one aria-current. These tests render the component, check the
Participation page's Chart population links, and guard against a new
two-option menu in an Admin template.
"""

import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.reports.workspace import ReportQuery
from parishkit.stewardship.reports.workspace_views import scope_links

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
COMPONENT = "stewardship/components/segmented-choice.html"
# Two-option menus an Admin template may still draw, each with its reason.
# Keep empty unless a reason is recorded here.
TWO_OPTION_SELECTS = {}
# A Family portal page extends the portal base directly; Admin pages extend
# admin-base. The rule covers Admin pages only.
FAMILY_PAGE = re.compile(r"\{%\s*extends\s+['\"]stewardship/base\.html['\"]")
# A <select> and its body, and an <option> tag inside it.
SELECT = re.compile(r"<select\b([^>]*)>(.*?)</select>", re.S)
OPTION = re.compile(r"<option\b")


def render(chosen):
    """The component for a Production | Testing rehearsal pair."""
    return render_to_string(
        COMPONENT,
        {
            "choice_id": "mode",
            "region_id": "table",
            "label": "Records shown",
            "first_key": "mode-production",
            "first_label": "Production",
            "first_href": "/timeline?mode=production",
            "second_key": "mode-testing",
            "second_label": "Testing rehearsal",
            "second_href": "/timeline?mode=testing",
            "chosen": chosen,
        },
    )


def test_the_pair_is_a_labelled_group_of_two_in_place_links():
    """A group named by its visible label; each segment an in-place link to
    its view, ending in the region's fragment; only the chosen one is
    aria-current."""
    html = render("mode-testing")
    group = re.search(r"<div [^>]*>", html).group(0)
    assert 'role="group"' in group and 'aria-labelledby="mode-label"' in group
    assert 'id="mode-label">Records shown</span>' in html
    links = re.findall(r"<a [^>]*>[^<]*</a>", html)
    assert links == [
        '<a href="/timeline?mode=production#table" data-in-place="mode-production">'
        "Production</a>",
        '<a href="/timeline?mode=testing#table" data-in-place="mode-testing"'
        ' aria-current="page">Testing rehearsal</a>',
    ]
    assert html.count('aria-current="page"') == 1
    assert render("mode-production").count('aria-current="page"') == 1


def test_scope_links_keep_the_view_and_return_to_the_first_page():
    """Each Chart population segment keeps the daily table's sort and size
    and an explicit zone, and goes back to the first page."""
    query = ReportQuery(
        scope="historical",
        timezone="America/Chicago",
        page=3,
        sort="date_asc",
        size="100",
    )
    links = scope_links(query)
    assert set(links) == {"historical", "current"}
    for scope, link in links.items():
        address = urlsplit(link)
        assert address.path == reverse("admin:response_dashboard")
        assert dict(parse_qsl(address.query)) == {
            "scope": scope,
            "page": "1",
            "sort": "date_asc",
            "size": "100",
            "timezone": "America/Chicago",
        }
    # A page drawn without a zone leaves the zone to report-v1.js.
    implicit = scope_links(ReportQuery(timezone_explicit=False))
    assert "timezone" not in implicit["current"]


def test_every_pair_is_drawn_by_the_shared_component():
    """The old pill links are gone: each view choice uses the component."""
    for path in sorted(TEMPLATES.rglob("*.html")):
        assert "choice-links" not in path.read_text(), path.name
    assert (TEMPLATES / "response-dashboard.html").read_text().count(COMPONENT) == 2
    assert COMPONENT in (TEMPLATES / "family-timeline.html").read_text()


def test_no_admin_template_draws_a_two_option_menu():
    """A choice between two views is a binary choice, never a two-option
    <select> (#1535). Only menus whose options are written out can be
    counted; a menu built from a loop or a tag is checked in review. Family
    portal pages (which extend the portal base) are outside the rule."""
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text()
        if FAMILY_PAGE.search(text):
            continue
        for match in SELECT.finditer(text):
            body = match.group(2)
            if re.search(r"\{%\s*(?:for|include|export_format_options)\b", body):
                continue
            if len(OPTION.findall(body)) != 2:
                continue
            select_id = re.search(r'\bid="([^"]+)"', match.group(1))
            key = f"{path.name}#{select_id.group(1) if select_id else '?'}"
            if key not in TWO_OPTION_SELECTS:
                found.append(key)
    assert found == []


def test_the_guard_sees_a_two_option_menu():
    """The guard's pattern finds a written-out two-option menu."""
    sample = '<select id="x"><option>A</option><option>B</option></select>'
    match = SELECT.search(sample)
    assert match and len(OPTION.findall(match.group(2))) == 2
