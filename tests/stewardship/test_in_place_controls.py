"""Admin controls that act in place (#519): markup the shared mechanism in
ui-v1.js relies on, and the no-script landing every such control keeps."""

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.presence import PRESENCE_SORTING
from parishkit.stewardship.jobs.views import TASK_SORTING
from parishkit.stewardship.web.contracts import PageWindow
from parishkit.stewardship.web.tables import paginate, window_table

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
# An opening <a> or <form> tag that opts into the in-place mechanism.
IN_PLACE_TAG = re.compile(r"<(a|form)\b[^>]*\bdata-in-place\b(?!-)[^>]*>")
# The marker that makes an element a region the mechanism can swap.
REGION_MARK = re.compile(r"\bdata-(?:in-place|table)-region\b")


def names_region(text, fragment):
    """Whether ``text`` has an opening tag with ``id="fragment"`` that is
    marked as a region, whichever order the two attributes are written in."""
    tags = re.findall(rf'<[^>]*(?<![\w-])id="{re.escape(fragment)}"[^>]*>', text)
    return any(REGION_MARK.search(tag) for tag in tags)


def test_names_region_accepts_either_attribute_order():
    """The id and the region marker can come in either order, but must share
    one tag, and a plain element with that id is not a region."""
    assert names_region('<div id="t" data-table-region>', "t")
    assert names_region('<section data-in-place-region id="t">', "t")
    assert not names_region('<div id="t"></div><p data-in-place-region>', "t")
    assert not names_region('<div id="other" data-in-place-region>', "t")
    assert not names_region('<div data-id="t" data-in-place-region>', "t")


def test_current_query_keeps_filters_sort_size_and_page():
    """A Refresh link reloads the very page the reader is on."""
    table = paginate(list(range(130)), {"page": "2", "size": "25"}, carry=(("q", "x"),))
    assert table.current_query == "q=x&size=25&page=2"
    assert table.current_query == table.query(table.number)


def test_background_refresh_keeps_the_view_and_refreshes_in_place():
    """Refresh current work carries the state filter, sort, size and page,
    names the table region as its fragment, and is a sync node, so a later
    sort or page change updates it; the work counts follow it too."""
    task = {
        "id": str(UUID(int=1)),
        "type": "source_refresh",
        "name": "Refresh",
        "state": "running",
        "heartbeat_at": NOW.isoformat(),
        "created_at": NOW.isoformat(),
        "progress": {"phase": "", "current": 0, "total": 0},
    }
    table = window_table(
        PageWindow(2, 25),
        [task],
        True,
        carry=[("state", "all")],
        total=(60, False),
        sorting=TASK_SORTING,
        sort="-created",
    )
    page = render_to_string(
        "stewardship/background.html",
        {
            "work": {
                "counts": {"active": 1, "queued": 0, "retry_wait": 0, "abandoned": 0}
            },
            "states": ("nonterminal", "all"),
            "selected_state": "all",
            "table": table,
        },
    )
    link = re.search(r'<a id="background-refresh"[^>]*>', page).group(0)
    assert "data-table-sync" in link and "data-in-place" in link
    assert 'data-in-place-message="List refreshed."' in link
    assert 'href="/admin/background?state=all&amp;sort=-created' in link
    assert "size=25&amp;page=2#table" in link
    assert '<p id="background-counts" data-table-sync>' in page


def test_presence_refresh_refreshes_the_list_in_place():
    """Refresh list keeps size and sort (page 1, as before), lands on the
    table, and the count and time outside the table follow it."""
    table = window_table(
        PageWindow(1, 50),
        [],
        False,
        total=(0, False),
        sorting=PRESENCE_SORTING,
        sort="-heartbeat",
    )
    page = render_to_string(
        "stewardship/presence.html",
        {"presence": {"count": 0, "as_of": NOW}, "table": table},
    )
    link = re.search(r'<a id="table-refresh"[^>]*>', page).group(0)
    assert "data-table-sync" in link and "data-in-place" in link
    assert link.endswith('?size=50&amp;sort=-heartbeat#table">')
    assert '<p id="presence-count" data-table-sync>' in page
    assert '<p id="presence-as-of" data-table-sync>' in page


def test_every_in_place_control_names_its_region_as_a_fragment():
    """The fragment is required: without script (or when the fetch fails) the
    ordinary load must land on the region, not at the top, and the script
    finds the region by it. So every data-in-place link or form ends its href
    or action in a #fragment that names a region (data-in-place-region or
    data-table-region, literally or as {{ table.anchor }}) in the same
    template. The earlier data-region-link name is gone."""
    found = 0
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text()
        assert "data-region-link" not in text, path.name
        for tag in IN_PLACE_TAG.finditer(text):
            found += 1
            target = re.search(r'\b(?:href|action)="([^"]*)"', tag.group(0))
            assert target and "#" in target.group(1), (path.name, tag.group(0))
            fragment = target.group(1).split("#", 1)[1]
            assert names_region(text, fragment), (path.name, fragment)
    assert found >= 8


def region_body(text, region):
    """The template text inside the div region ``region``, up to the </div>
    that closes it, counting the divs nested inside it."""
    start = re.search(rf'<div id="{region}" data-in-place-region>', text).end()
    depth = 1
    for tag in re.finditer(r"<(/?)div\b", text[start:]):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return text[start : start + tag.start()]
    raise AssertionError(f"{region} is never closed")


def test_follow_up_saves_are_in_place():
    """Save follow-up on a Ministry follow-up request and on an information
    item saves in place (#519 PR 2): each form is a data-in-place POST whose
    action lands on the request's panel, which holds the form and its
    history, with a key to refocus the fresh Save button and the message the
    live region reads. Both wait for their prerequisites before Save."""
    for name, region, key in (
        ("ministry-followup.html", "followup-item", "followup-save"),
        ("information.html", "information-item", "information-save"),
    ):
        text = (TEMPLATES / name).read_text()
        form = re.search(r'<form method="post"[^>]*_update[^>]*>', text).group(0)
        assert f'#{region}" ' in form, name
        assert f'data-in-place="{key}"' in form, name
        assert "data-in-place-message=\"{% translate 'Follow-up saved.' %}\"" in form
        assert "data-require-complete" in form, name
        assert names_region(text, region), name
        # The panel holds the form and the history below it.
        panel = region_body(text, region)
        assert form in panel and "history pages" in panel, name


def test_information_reopen_confirmation_shows_only_when_unticking():
    """The confirmation to reopen completed follow-up is shown, and required
    before Save, only once "Follow-up completed" is unticked."""
    text = (TEMPLATES / "information.html").read_text()
    group = re.search(r'<div data-show-when="followed_up!=yes"[^>]*>', text).group(0)
    assert 'data-required-when-shown="confirm_clear"' in group
    assert "data-missing-hint=" in group
    assert 'aria-describedby="information-save-hint"' in text
    hint = '<p class="help" id="information-save-hint" data-complete-hint hidden>'
    assert hint in text


# The history and report pagers (#519 PR 6): template, region, whether the
# pager pages only its own region (data-in-place-only), and its two keys.
PAGERS = (
    ("delivery.html", "delivery-history", False, ("page-previous", "page-next")),
    (
        "information.html",
        "information-history",
        True,
        ("history-newer", "history-older"),
    ),
    (
        "ministry-followup.html",
        "followup-history",
        True,
        ("history-newer", "history-older"),
    ),
    ("weekly-digest.html", "weekly-report-page", False, ("page-previous", "page-next")),
)


def test_history_pagers_act_in_place():
    """Each pager's two links land on their region, hand focus to each other
    when one is gone from the fresh page, and say what they did. A follow-up
    history sits inside its item's panel and pages only itself, so the Save
    form, which stays outside the history, keeps what the reader typed."""
    for name, region, only, keys in PAGERS:
        text = (TEMPLATES / name).read_text()
        assert names_region(text, region), name
        for key, other in (keys, keys[::-1]):
            link = re.search(rf'<a [^>]*data-in-place="{key}"[^>]*>', text).group(0)
            assert f'#{region}" ' in link, (name, key)
            assert f'data-in-place-fallback="{other}"' in link, (name, key)
            assert "data-in-place-message=" in link, (name, key)
            assert ("data-in-place-only" in link) == only, (name, key)
        if only:
            panel = (
                "information-item" if name == "information.html" else "followup-item"
            )
            body = region_body(text, panel)
            assert names_region(body, region), name
            assert "<form" not in region_body(text, region), name


def test_delivery_resolution_forms_stay_outside_the_history():
    """Paging a delivery's history never replaces a resolution form, so
    evidence the reader is typing survives it."""
    text = (TEMPLATES / "delivery.html").read_text()
    history = region_body(text, "delivery-history")
    assert "<form" not in history and "Attempt history" in history


def test_log_cross_links_filter_in_place():
    """System logs' Show related entries, Same actor and Same campaign (#519
    PR 3) filter in place: each is a data-in-place POST that only reads
    (never a save), sets the filter form's visible fields from the answer,
    says what it showed, lands on the table, and has a button id unique to
    its entry so focus can return to it."""
    text = (TEMPLATES / "logs.html").read_text()
    forms = re.findall(
        r'<form method="post" action="\{% url \'admin:logs\' %\}#[^>]*>', text
    )
    assert len(forms) == 3
    for form in forms:
        assert '#{{ table.anchor }}"' in form
        for mark in ("data-in-place ", "data-in-place-read", "data-in-place-filters"):
            assert mark in form, (mark, form)
        assert "data-in-place-message=" in form
    for kind in ("related", "actor", "campaign"):
        assert f'id="log-{{{{ row.icon }}}}-{{{{ row.id }}}}-{kind}"' in text
    assert '<details id="log-identifier-filters"' in text


def test_acknowledgements_act_in_place():
    """The critical-problems banner and Home's security events acknowledge in
    place (#519 PR 4). Each region is drawn even when empty, so the answer
    after the last acknowledgement still carries it. The banner's server
    answers with Home from any page, so its form takes the banner from any
    page's answer (data-in-place-anywhere)."""
    banners = (TEMPLATES / "admin-banners.html").read_text()
    assert re.search(
        r'\{% if admin_chrome\.admin %\}<div id="critical-events" '
        r"data-in-place-region>\{% if admin_chrome\.critical_count %\}",
        banners,
    )
    form = re.search(r"<form [^>]*critical_events_acknowledge[^>]*>", banners).group(0)
    assert '#critical-events" ' in form
    assert 'data-in-place="critical-acknowledge"' in form
    assert "data-in-place-anywhere" in form and "data-in-place-message=" in form
    home = (TEMPLATES / "home.html").read_text()
    assert re.search(
        r'<div id="security-events" data-in-place-region>'
        r"\{% if dashboard\.security_events %\}",
        home,
    )
    form = re.search(r"<form [^>]*security_event_acknowledge[^>]*>", home).group(0)
    assert '#security-events" ' in form
    assert 'data-in-place="security-acknowledge-{{ event.id }}"' in form
    assert "data-in-place-anywhere" not in form


def test_participation_options_apply_in_place():
    """Apply report options (#519 PR 5) is a data-in-place GET landing on
    the statistics, and the statistics, chart and export panels are regions,
    so every panel the options reshape is refreshed together. The chart keeps
    its daily table's own region inside it, and no form opts out of the
    in-place filter path any more."""
    text = (TEMPLATES / "participation.html").read_text()
    form = re.search(r'<form id="table-filters"[^>]*>', text).group(0)
    assert '#participation-statistics" ' in form
    assert 'data-in-place="report-options"' in form
    assert "data-in-place-message=" in form and "data-filter-reload" not in form
    for region in (
        "participation-statistics",
        "participation-chart",
        "participation-export",
    ):
        assert names_region(text, region), region
    chart = text.split('id="participation-chart"', 1)[1]
    assert "data-table-region" in chart.split('id="participation-export"', 1)[0]
    for path in TEMPLATES.rglob("*.html"):
        assert "data-filter-reload" not in path.read_text(), path.name
