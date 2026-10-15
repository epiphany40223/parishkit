"""Admin controls that act in place (#519): markup the shared mechanism in
ui-v1.js relies on, and the no-script landing every such control keeps."""

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.admin_editing import in_place_follow, review_region
from parishkit.stewardship.accounts.presence import PRESENCE_SORTING
from parishkit.stewardship.jobs.views import EVENT_SORTING, TASK_SORTING
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
    assert 'href="/admin/system/background/?state=all&amp;sort=-created' in link
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


# Fragment templates whose in-place control acts on a region the page
# around them draws: the template's name, and the page templates'. The
# integration status line is also the passive status view live-status-v1.js
# reads, whose answer never holds the page's regions.
# The in-place settings pages (#532) draw their review region around
# settings-review.html, whose Apply names it, and around the change status a
# confirmed change shows, whose follow-up link names it too.
SETTINGS_PAGES = (
    "parish-settings.html",
    "reminder-workgroup.html",
)
# Campaign images and Parish logos review in the review dialog (#750).
# Ministries changes nothing any more (#1267).
REVIEW_PAGES = SETTINGS_PAGES
HOSTED = {
    "integration-summary.html": ("integration-settings.html",),
    "settings-review.html": REVIEW_PAGES,
    "configuration-request-status.html": REVIEW_PAGES,
    # Refresh from ParishSoft shows the run it requested (#521).
    "background-task-status.html": ("background-task.html", "source-refresh.html"),
    # Outgoing mail's bulk review region (#382 M4): Confirm and Continue
    # sit inside it, and the bulk panel draws it.
    "delivery-bulk-review.html": ("delivery-bulk.html",),
    # The roster tick (#528): on the request page, and on each row of
    # Roster changes to enter.
    "roster-tick.html": ("ministry-followup.html",),
    "roster-tick-row.html": ("ministry-roster.html",),
    # Past campaigns' review region (#1018): Apply sits inside it.
    "past-campaigns-review.html": ("past-campaigns.html",),
    # The held-email table by send (#757): the Restore review page draws it
    # in its default region, Held emails in its own (region_id).
    "held-email-groups.html": ("restore-review.html", "held-emails.html"),
    # A setup test page's live status region (#1354), whose follow-up re-reads
    # the panel each test page draws around it.
    "setup-test-status.html": ("setup-mail.html", "setup-notification.html"),
}


def test_every_in_place_control_names_its_region_as_a_fragment():
    """The fragment is required: without script (or when the fetch fails) the
    ordinary load must land on the region, not at the top, and the script
    finds the region by it. So every data-in-place link or form ends its href
    or action in a #fragment that names a region (data-in-place-region or
    data-table-region, literally or as {{ table.anchor }}) in the same
    template, or, for a fragment template listed in HOSTED, in the page
    template that draws it around the control. A hosted fragment template
    may name its region ``{{ region_id|default:'…' }}``: each include's
    region is then the region_id it passes (Refresh from ParishSoft's run,
    #521; the held-email groups, #757), else the default.
    The earlier data-region-link name is gone."""
    found = 0
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text()
        assert "data-region-link" not in text, path.name
        for tag in IN_PLACE_TAG.finditer(text):
            found += 1
            target = re.search(r'\b(?:href|action)="([^"]*)"', tag.group(0))
            assert target and "#" in target.group(1), (path.name, tag.group(0))
            fragment = target.group(1).split("#", 1)[1]
            hosts = (
                [(TEMPLATES / name).read_text() for name in HOSTED[path.name]]
                if (path.name in HOSTED)
                else [text]
            )
            named = re.fullmatch(r"\{\{ region_id\|default:'([\w-]+)' \}\}", fragment)
            for host in hosts:
                if named is None:
                    # A shared partial (Take a backup now's button) may hold a
                    # form whose region the page including it names.
                    assert names_region(host, fragment) or included_in_region(
                        path, fragment
                    ), (path.name, fragment)
                    continue
                assert path.name in HOSTED, path.name
                # Each include of the fragment names the region_id it
                # passes, or else the default. Either quote may delimit the
                # included name, and a shared component sits under
                # components/ (the held-email table, #757).
                includes = [
                    tag.group(0)
                    for tag in re.finditer(
                        r"\{% include (['\"])stewardship/(?:components/)?"
                        rf"{re.escape(path.name)}\1[^%]*%\}}",
                        host,
                    )
                ]
                assert includes, (path.name, "never included")
                for include in includes:
                    passed = re.search(r"\bregion_id=([\"'])([\w-]+)\1", include)
                    region = passed.group(2) if passed else named.group(1)
                    assert names_region(host, region), (path.name, region)
    assert found >= 8


def included_in_region(path, fragment):
    """Whether every template that includes ``path`` names ``fragment``.

    A shared partial (System health's Take a backup now button, ADM-13) can
    hold an in-place form whose region is defined by the page including it;
    at least one includer must name the region, and every includer must
    either name it or be a polled fragment that carries the partial only as
    a template[data-live-mirror] copy for that page: with every such
    <template> element removed, the includer no longer names the partial.
    """
    name = f"stewardship/{path.name}"
    includers = [
        other.read_text()
        for other in TEMPLATES.rglob("*.html")
        if other != path and name in other.read_text()
    ]
    mirrors = re.compile(r"<template\b[^>]*\bdata-live-mirror=.*?</template>", re.S)
    return any(names_region(text, fragment) for text in includers) and all(
        names_region(text, fragment) or name not in mirrors.sub("", text)
        for text in includers
    )


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


def test_family_test_check_and_send_are_in_place():
    """Check these Families and Send these Family tests act in place (#519
    PR 7c): each is a data-in-place POST landing on the review region, which
    holds the notices, the allowance, Check and the review. Send's redirect
    leaves no review, so the requested notice takes focus and is announced.
    The step-up's Confirm with Google stays an ordinary form, and Refresh
    status replaces only its own region, so Families typed but not
    yet checked survive it."""
    text = (TEMPLATES / "campaign-mail-families.html").read_text()
    assert names_region(text, "family-test-review")
    region = region_body(text, "family-test-review")
    check = re.search(r'<form [^>]*data-in-place="family-test-check"[^>]*>', text)
    send = re.search(r'<form [^>]*data-in-place="family-test-send"[^>]*>', text)
    for form in (check.group(0), send.group(0)):
        assert 'action="{{ refresh_url }}#family-test-review"' in form
        assert form in region
    assert "data-in-place-message=\"{% translate 'Families checked.' %}\"" in (
        check.group(0)
    )
    assert 'data-in-place-message-from="family-test-sent"' in send.group(0)
    assert 'id="family-test-sent" tabindex="-1" data-in-place-focus' in region
    assert "family-test-available" in region
    assert "components/step-up-notice.html" in region
    refresh = re.search(r'<a [^>]*>{% translate "Refresh status" %}</a>', text)
    assert "data-in-place-only" in refresh.group(0)


def test_information_reopen_confirmation_shows_only_when_unticking():
    """The confirmation to reopen completed follow-up is shown, and required
    before Save, only once "Follow-up completed" is unticked."""
    text = (TEMPLATES / "information.html").read_text()
    group = re.search(r'<div data-show-when="followed_up!=yes"[^>]*>', text).group(0)
    assert 'data-required-when-shown="confirm_clear"' in group
    assert "data-missing-hint=" in group
    # Save names its hint, and also the locked notice while other campaign
    # work holds edits (#563).
    assert (
        'aria-describedby=mutable|yesno:"information-save-hint,'
        'information-save-hint information-locked"' in text
    )
    hint = '<p class="help" id="information-save-hint" data-complete-hint hidden>'
    assert hint in text


# The history and report pagers (#519 PR 6): template, region, whether the
# pager pages only its own region (data-in-place-only), and its two keys.
PAGERS = (
    ("delivery.html", "delivery-history", False, ("page-previous", "page-next")),
    (
        "delivery.html",
        "delivery-evidence",
        False,
        ("evidence-previous", "evidence-next"),
    ),
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
    # The Admin evidence, below the forms (#1015), is a region of its own
    # that the same pagers refresh.
    evidence = region_body(text, "delivery-evidence")
    assert "<form" not in evidence and "Admin evidence and decisions" in evidence


def test_log_cross_links_filter_in_place():
    """System logs' related-entries, same-actor, same-subject (#536) and
    same-campaign (#519 PR 3) icons filter in place: each is a data-in-place POST
    that only reads (never a save), sets the filter form's visible fields
    from the answer, says what it showed, lands on the table, and has a
    button id unique to its entry so focus can return to it."""
    text = (TEMPLATES / "logs.html").read_text()
    forms = re.findall(
        r'<form method="post" action="\{% url \'admin:logs\' %\}#[^>]*>', text
    )
    assert len(forms) == 4
    for form in forms:
        assert '#{{ table.anchor }}"' in form
        for mark in ("data-in-place ", "data-in-place-read", "data-in-place-filters"):
            assert mark in form, (mark, form)
        assert "data-in-place-message=" in form
    # Each is a shared {% button %} icon (#1065), which takes its id as one
    # value, so the template builds the log-<icon>-<id> stem first.
    assert '{% with base="log-"|add:icon|add:"-"|add:row_id %}' in text
    for kind in ("related", "actor", "subject", "campaign"):
        assert f'{{% button type="submit" id=base|add:"-{kind}"' in text
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


def test_live_status_pages_refresh_and_dismiss_in_place():
    """Refresh on the live-status pages and an integration's Dismiss act in
    place (#519 PR 7a): each is a data-in-place control whose region wraps
    the page's live-status element, so live-status-v1.js can watch the
    fresh one, and whose message says what was done."""
    for name, region, words in (
        ("credential-status.html", "credential-progress", "Refresh status"),
        ("family-email-progress.html", "send-progress", "Refresh progress"),
        ("campaign-mail-families.html", "family-tests", "Refresh status"),
    ):
        text = (TEMPLATES / name).read_text()
        link = re.search(rf'<a [^>]*>{{% translate "{words}" %}}</a>', text).group(0)
        assert f'#{region}" ' in link and 'data-in-place="refresh"' in link, name
        assert "data-in-place-message=" in link, name
        body = text.split(f'<div id="{region}" data-in-place-region>', 1)[1]
        assert "data-live-status" in body or "progress-status.html" in body, name
    settings = (TEMPLATES / "integration-settings.html").read_text()
    assert '<div id="integration-status" data-in-place-region>' in settings
    summary = (TEMPLATES / "integration-summary.html").read_text()
    form = re.search(r"<form [^>]*dismiss_credential_result[^>]*>", summary).group(0)
    assert '#integration-status" ' in form and 'data-in-place="dismiss"' in form


def test_remaining_live_status_refreshes_are_in_place():
    """Refresh on Background task, Change status and both report export pages
    re-reads the page in place (#519 PR 7b): each link names a region that
    wraps the page's live-status element, so live-status-v1.js watches the
    fresh one, and says what it did. The export pages' campaign-work notice
    is a region of its own, drawn even when empty, so it comes and goes with
    the buttons it explains."""
    for name, region, words, message in (
        ("background-task.html", "task-status", "Refresh task", "Task refreshed."),
        (
            "configuration-request.html",
            "change-status",
            "Refresh status",
            "Status refreshed.",
        ),
        ("report-export.html", "export-status", "Refresh status", "Status refreshed."),
        ("report-exact.html", "export-status", "Refresh status", "Status refreshed."),
    ):
        text = (TEMPLATES / name).read_text()
        link = re.search(rf'<a [^>]*>{{% translate "{words}" %}}</a>', text).group(0)
        assert f'#{region}"' in link and 'data-in-place="refresh"' in link, name
        assert f"data-in-place-message=\"{{% translate '{message}' %}}\"" in link
        head = text.split(f'<div id="{region}" data-in-place-region>', 1)[1]
        head = head.split("</div>", 1)[0]
        # The region holds the live-status element itself, or only the
        # include that draws it.
        assert "data-live-status" in head or re.fullmatch(
            r"\{% include 'stewardship/[\w-]+-status\.html' %\}", head
        ), name
        if name.startswith("report-"):
            assert re.search(
                r'<div id="export-lock" data-in-place-region>\{% if not mutable %\}'
                r'<p class="notice" id="export-locked">',
                text,
            ), name


def test_refresh_task_keeps_the_history_view():
    """Refresh task keeps the Task history's sort, size and page, and follows
    later table choices (data-table-sync). The latest retry and the technical
    details are regions too, the details' list inside the disclosure, so a
    refresh never closes it."""
    events = [
        {
            "version": 1,
            "at": NOW.isoformat(),
            "action": "created",
            "state": "queued",
            "progress": {"phase": "", "current": 0, "total": 0},
            "phase_text": "",
        }
    ]
    history = window_table(
        PageWindow(2, 20),
        events,
        False,
        total=(30, False),
        sorting=EVENT_SORTING,
        sort="-version",
    )
    task = {
        "id": UUID(int=5),
        "name": "Refresh",
        "type": "source_refresh",
        "state": "running",
        "active": True,
        "attempt": 1,
        "retry_sequence": 0,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "progress": {"phase": "", "current": 0, "total": 0},
    }
    page = render_to_string(
        "stewardship/background-task.html",
        {"task": task, "work": {"events": events}, "history": history},
    )
    link = re.search(r'<a id="task-refresh"[^>]*>', page).group(0)
    assert "data-table-sync" in link
    assert f'href="/admin/system/background/{task["id"]}/?' in link
    assert "sort=-version" in link and "page=2#task-status" in link
    assert '<div id="task-latest" data-in-place-region>' in page
    details = page.split('<details class="technical-details">', 1)[1]
    assert details.split("</details>", 1)[0].count(
        '<dl id="task-details" data-in-place-region>'
    )


@pytest.mark.parametrize("state", ["applied", "failed", "cancelled", "staged"])
def test_only_an_applied_change_refreshes_its_settings_page(state):
    """An in-place settings page refreshes itself only once its change is
    applied (#532 review). A change that was not applied leaves the page
    alone, so its form keeps the version it was reviewed at and a change
    refused as stale must be reloaded before the next Review."""
    receipt = SimpleNamespace(state=state, request_id=uuid4(), failure_code="")
    html = render_to_string(
        "stewardship/configuration-request-status.html",
        {"receipt": receipt, "follow_url": "/admin/parish/settings/?request=x"},
    )
    assert ("data-live-follow" in html) == (state == "applied")


@pytest.mark.parametrize("page", SETTINGS_PAGES)
def test_settings_review_waits_for_a_change(page):
    """Each in-place settings form keeps Review changes unavailable until it
    differs from what is saved (#921): the form names its version field for
    the change gate, and draws the shared Review button with its hint."""
    text = (TEMPLATES / page).read_text()
    form = re.search(r'<form id="settings-form"[^>]*>', text).group(0)
    assert 'data-require-change="base_digest"' in form
    assert "stewardship/components/review-changes-button.html" in text
    assert 'translate "Review changes"' not in text


def test_review_button_hint_follows_it_and_keeps_its_space():
    """The hint sits right after Review changes, idle (hidden by visibility,
    so it keeps its space) until the page script shows it; a page can word
    it for its own form."""
    html = render_to_string("stewardship/components/review-changes-button.html", {})
    button, hint = re.fullmatch(
        r"\s*(<button\b[^>]*>.*?</button>)\s*(<span\b[^>]*>.*?</span>)\s*", html, re.S
    ).groups()
    assert 'type="submit"' in button and "Review changes" in button
    # Firefox would otherwise bring the script's disabled back on a reload.
    assert 'autocomplete="off"' in button
    # ui-v1.js names the hint after its form, so two forms never share an id.
    assert " id=" not in hint
    assert "data-unchanged-hint" in hint and "data-idle" in hint
    assert "Change a setting to review it." in hint
    worded = render_to_string(
        "stewardship/components/review-changes-button.html",
        {"hint": "Change an option to review it."},
    )
    assert "Change an option to review it." in worded
    assert "Change a setting" not in worded
    css = (TEMPLATES.parents[1] / "static/stewardship/ui-v1.css").read_text()
    assert ".unchanged-hint[data-idle] { visibility: hidden; }" in css


def test_share_options_review_in_the_dialog():
    """Share options say "option", not "setting", in the hint after its
    Review changes button (#921); its form opens the shared review dialog
    (#750) from a region redrawn from the page's address once a change is
    applied, and the page draws no in-place review region."""
    text = (TEMPLATES / "share-settings.html").read_text()
    form = re.search(r'<form id="settings-form"[^>]*>', text).group(0)
    assert 'data-require-change="base_digest"' in form
    assert 'data-review-open="share-review"' in form
    assert "data-in-place" not in form
    assert 'data-refresh-url="{{ refresh_url }}"' in text
    assert "settings-review" not in text
    assert 'dialog_id="share-review"' in text and "review=True" in text
    assert 'translate "Change an option to review it." as option_hint' in text
    assert "review-changes-button.html' with hint=option_hint" in text


def test_campaign_settings_reviews_in_the_dialog_once():
    """Campaign settings is one editor with one Review changes (#1119): its
    form opens the shared review dialog (#750), waits for a real change
    (#921), and draws no in-place review region or second form."""
    text = (TEMPLATES / "campaign-settings.html").read_text()
    forms = re.findall(r"<form\b[^>]*>", text)
    assert len(forms) == 1
    assert 'data-review-open="campaign-review"' in forms[0]
    assert 'data-require-change="base_digest"' in forms[0]
    assert "data-in-place" not in forms[0]
    assert text.count("stewardship/components/review-changes-button.html") == 1
    assert "'stewardship/settings-review.html'" not in text
    assert 'dialog_id="campaign-review"' in text and "review=True" in text
    assert "(read-only)" not in text


def test_campaign_images_review_in_the_dialog():
    """Each Campaign images upload and removal opens the shared review dialog
    (#750) from the slots' region, which is redrawn from the page's address
    once a change is applied; the page draws no in-place review region, and
    the dialog's Review sends an upload multipart, as the form would."""
    text = (TEMPLATES / "artwork-settings.html").read_text()
    forms = re.findall(r"<form\b[^>]*>", text)
    assert len(forms) == 2
    for form in forms:
        assert 'data-review-open="artwork-review"' in form
        assert "data-in-place" not in form
    upload = next(form for form in forms if "artwork-upload-" in form)
    assert 'enctype="multipart/form-data"' in upload
    assert "data-require-complete" in upload
    # Remove this image is drawn only when the slot has an image.
    assert text.index("{% if entry.asset %}<form") < text.index("artwork-remove-")
    assert 'data-refresh-url="{{ refresh_url }}"' in text
    assert "settings-review" not in text
    assert 'dialog_id="artwork-review"' in text and "review=True" in text
    script = (TEMPLATES.parents[1] / "static/stewardship/ui-v1.js").read_text()
    assert 'form.enctype === "multipart/form-data" ? data' in script


def test_parish_logos_review_in_the_dialog():
    """Parish logos' upload opens the shared review dialog (#750) from the
    form's region, which is redrawn from the page's address once a change
    is applied, as are the current logos; the page draws no in-place review
    region, and the upload is sent multipart and waits for a chosen file."""
    text = (TEMPLATES / "branding-settings.html").read_text()
    forms = re.findall(r"<form\b[^>]*>", text)
    assert len(forms) == 1
    assert 'data-review-open="branding-review"' in forms[0]
    assert "data-in-place" not in forms[0]
    assert 'enctype="multipart/form-data"' in forms[0]
    assert "data-require-complete" in forms[0]
    assert 'data-refresh-url="{{ refresh_url }}"' in text
    assert '<section id="branding-current" class="panel" data-in-place-region' in text
    assert "settings-review" not in text
    assert 'dialog_id="branding-review"' in text and "review=True" in text
    review = (TEMPLATES / "branding-review.html").read_text()
    assert "data-review-answer" in review and "data-review-preview" in review
    assert 'name="bundle"' in review


def test_the_follow_up_carries_only_the_change():
    """Change status's read and its quiet follow-up name only the in-place
    page and the change. Ministries is no in-place page any more (#1267):
    it changes nothing, so a follow-up never leads there."""
    receipt = SimpleNamespace(request_id="53200000-0000-4000-8000-000000000532")
    status = review_region("parish_settings", receipt=receipt)["status_url"]
    read = {key: values[0] for key, values in parse_qs(urlsplit(status).query).items()}
    assert read == {"in_place": "parish_settings"}
    follow = in_place_follow(read | {"q": "choir", "next": "/x"}, receipt.request_id)
    assert urlsplit(follow).path == "/admin/parish/settings/"
    assert parse_qs(urlsplit(follow).query) == {"request": [receipt.request_id]}
    assert in_place_follow({"in_place": "ministries"}, receipt.request_id) is None


def test_go_live_and_production_refreshes_are_in_place():
    """The go-live and Production pages' Refresh links, once plain reloads
    (``href=""``), re-read the page in place (#519 PR 7b-2): each names its
    page's real address and a region there as its fragment, and says what it
    did. Readiness and Production activation gain regions of their own;
    Testing cleanup's and Prepare Family links' regions wrap their
    live-status elements, so live-status-v1.js watches the fresh ones."""
    for name, region, words, message in (
        (
            "go-live-readiness.html",
            "readiness-preview",
            "Refresh readiness preview",
            "Readiness preview refreshed.",
        ),
        (
            "go-live-cleanup.html",
            "cleanup-progress",
            "Refresh cleanup progress",
            "Progress refreshed.",
        ),
        (
            "go-live-links.html",
            "{{ table.anchor }}",
            "Refresh preparation progress",
            "Progress refreshed.",
        ),
        (
            "production-progress.html",
            "production-status",
            "Refresh Production progress",
            "Progress refreshed.",
        ),
    ):
        text = (TEMPLATES / name).read_text()
        assert 'href=""' not in text, name
        link = re.search(rf'<a [^>]*>{{% translate "{words}" %}}</a>', text).group(0)
        assert 'href="{% url ' in link, name
        assert f'#{region}"' in link and 'data-in-place="refresh"' in link, name
        assert f"data-in-place-message=\"{{% translate '{message}' %}}\"" in link
        assert names_region(text, region), name
        if name in ("go-live-cleanup.html", "go-live-links.html"):
            head = text.split(f'<div id="{region}" data-in-place-region>', 1)[1]
            assert "data-live-status" in head.split("</section>", 1)[0], name


def test_go_live_refresh_regions_that_come_and_go():
    """Regions a Refresh can fill or empty are drawn even when empty: Prepare
    Family links' Prepare panel and Production activation's Cancel go-live.
    Testing cleanup's technical details are a region inside the disclosure,
    so a refresh never closes it, and the links Refresh keeps the shown
    history page."""
    links = (TEMPLATES / "go-live-links.html").read_text()
    assert '<div id="links-prepare" data-in-place-region>{% if prepare %}' in links
    assert "?{{ table.current_query }}#{{ table.anchor }}" in links
    production = (TEMPLATES / "production-progress.html").read_text()
    assert (
        '<div id="production-withdrawal" data-in-place-region>'
        "{% if withdrawal_available %}"
    ) in production
    cleanup = (TEMPLATES / "go-live-cleanup.html").read_text()
    details = cleanup.split('<details class="technical-details">', 1)[1]
    assert (
        '<dl id="cleanup-details" data-in-place-region>'
        in details.split("</details>", 1)[0]
    )


def test_hosted_file_upload_is_in_place():
    """Upload posts its multipart body in place (#519 PR 7c): the form lands
    on the Upload panel, a region only its own answer replaces
    (data-in-place-own), so a sort, page or deletion redraw keeps a chosen
    file and a typed name. The fresh "Uploaded …" notice is read out."""
    text = (TEMPLATES / "hosted-files.html").read_text()
    assert names_region(text, "hosted-file-upload")
    region = re.search(r'<div id="hosted-file-upload" [^>]*>', text).group(0)
    assert "data-in-place-own" in region
    form = re.search(r'<form [^>]*data-in-place="hosted-file-upload"[^>]*>', text)
    tag = form.group(0)
    assert "action=\"{% url 'admin:hosted_file_upload' %}#hosted-file-upload\"" in tag
    assert 'enctype="multipart/form-data"' in tag
    assert 'data-in-place-message-from="hosted-file-uploaded"' in tag
    # The form sits inside the panel region, which closes right after it.
    assert text.index(region) < form.start()
    assert text[form.start() :].split("</form>", 1)[1].startswith("</div>")
    assert 'id="hosted-file-uploaded" data-table-sync' in text
    # The emptied form holds Upload, so the file field takes focus instead.
    from parishkit.stewardship.accounts.hosted_file_views import UploadForm

    assert "data-in-place-focus" in UploadForm().fields["file"].widget.attrs
