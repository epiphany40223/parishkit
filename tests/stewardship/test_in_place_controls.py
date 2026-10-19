"""Admin controls that act in place (#519): markup the shared mechanism in
ui-v1.js relies on, and the no-script landing every such control keeps."""

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from django.template.loader import render_to_string

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


def test_background_updates_itself_in_place():
    """Background work has no Refresh link (#1586): a hidden periodic
    re-read link names the table region as its fragment (ui-v1.js re-reads
    the address shown, so the filter, sort, size and page are kept), the
    work counts follow it, and an opened Technical details stays open."""
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
    assert "Refresh current work" not in page
    link = re.search(r"<a [^>]*data-reread-every[^>]*>", page).group(0)
    assert link == (
        '<a hidden data-in-place data-in-place-quiet data-reread-every="30000" '
        'href="/admin/system/background/#table">'
    )
    assert '<p id="background-counts" data-table-sync>' in page
    assert (
        f'<details class="technical-details" id="task-type-{task["id"]}" '
        "data-keep-open>"
    ) in page


def test_presence_updates_itself_in_place():
    """Families on the form now has no Refresh list link (#1586): a hidden
    periodic re-read link names the table, and the count and time outside
    the table follow it."""
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
    assert "Refresh list" not in page
    link = re.search(r"<a [^>]*data-reread-every[^>]*>", page).group(0)
    assert link == (
        '<a hidden data-in-place data-in-place-quiet data-reread-every="30000" '
        'href="/admin/mail/presence/#table">'
    )
    assert '<p id="presence-count" data-table-sync>' in page
    assert '<p id="presence-as-of" data-table-sync>' in page


# Fragment templates whose in-place control acts on a region the page
# around them draws: the template's name, and the page templates'. The
# integration status line is also the passive status view live-status-v1.js
# reads, whose answer never holds the page's regions.
HOSTED = {
    "integration-summary.html": ("integration-settings.html",),
    # Off-site backups' settled access check (#519): its follow-up re-reads
    # the settings page's status line.
    "backup-status-lines.html": ("integration-settings.html",),
    # Refresh from ParishSoft shows the run it requested (#521).
    "background-task-status.html": ("background-task.html", "source-refresh.html"),
    # Sent mail's bulk review region (#382 M4): Confirm and Continue
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
    # A binary choice (#1535): every page that draws the shared segmented
    # pair names the region its links refresh (region_id, required).
    "segmented-choice.html": tuple(
        path.name
        for path in sorted(TEMPLATES.glob("*.html"))
        if "components/segmented-choice.html" in path.read_text()
    ),
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
    #521; the held-email groups, #757), else the default. Without a default
    (``{{ region_id }}``, a binary choice's segments, #1535) every include
    must pass one.
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
            named = re.fullmatch(
                r"\{\{ region_id(?:\|default:'([\w-]+)')? \}\}", fragment
            )
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
                    assert region, (path.name, include)
                    # A shared table's region is drawn with its anchor,
                    # "table" for a table with no prefix (the directory's
                    # and the roster's binary choices, #1535).
                    assert names_region(host, region) or (
                        region == "table" and names_region(host, "{{ table.anchor }}")
                    ), (path.name, region)
    assert found >= 8


def included_in_region(path, fragment):
    """Whether every template that includes ``path`` names ``fragment``.

    A shared partial (System health's Take a backup now button, ADM-13) can
    hold an in-place form whose region is defined by the page including it;
    at least one includer must name the region, and every includer must
    either name it or be a polled fragment that carries the partial only as
    a template[data-live-mirror] copy for that page: with every such
    <template> element removed, the includer no longer names the partial.
    An includer that is itself a partial counts as naming the region when
    it is, in turn, included in it (Accept this change once's button, inside
    its offer, inside #accept-change).
    """
    name = f"stewardship/{path.name}"
    includers = [
        (other, other.read_text())
        for other in TEMPLATES.rglob("*.html")
        if other != path and name in other.read_text()
    ]
    mirrors = re.compile(r"<template\b[^>]*\bdata-live-mirror=.*?</template>", re.S)

    def names(other, text):
        """The includer names the region, or sits inside one that does."""
        return names_region(text, fragment) or included_in_region(other, fragment)

    return any(names(other, text) for other, text in includers) and all(
        names(other, text) or name not in mirrors.sub("", text)
        for other, text in includers
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
    Refresh status replaces only its own region, so Families typed but not
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
    # The recent tests update by themselves; there is no Refresh link (#1586).
    assert "Refresh status" not in text


def test_campaign_test_send_is_in_place_and_live():
    """Send this test email posts in place (#519): its form sits in the
    notices' region and lands on Recent test results, whose heading takes
    focus. The list is a live status region, pending while a test is on its
    way, whose settled copy carries a quiet follow-up that re-reads the
    form's region (and Campaign setup's stepper and action row). The manual
    Refresh test results link is gone."""
    text = (TEMPLATES / "campaign-mail.html").read_text()
    form = re.search(r'<form [^>]*data-in-place="campaign-test-send"[^>]*>', text)
    assert 'action="#campaign-tests"' in form.group(0)
    assert "data-in-place-message=\"{% translate 'Test email requested.' %}\"" in (
        form.group(0)
    )
    assert form.group(0) in region_body(text, "campaign-test-send")
    assert 'id="campaign-test-pending"' in region_body(text, "campaign-test-send")
    results = region_body(text, "campaign-tests")
    assert (
        'tabindex="-1" data-in-place-focus>{% translate "Recent test results" %}'
        in (results)
    )
    status = re.search(r'<div [^>]*data-live-status="campaign-tests"[^>]*>', results)
    assert "{% if pending %} data-live-pending{% endif %}" in status.group(0)
    assert '{% if page_url %} data-live-url="{{ page_url }}"{% endif %}' in (
        status.group(0)
    )
    follow = re.search(r"<a hidden data-live-follow[^>]*>", results).group(0)
    assert "data-in-place data-in-place-quiet data-in-place-only" in follow
    # The settled re-read also redraws Campaign setup's stepper and action
    # row, so the first accepted test shows Continue (#1583).
    assert 'data-in-place-only="campaign-stepper campaign-step-actions"' in follow
    assert 'href="{{ page_url }}#campaign-test-send"' in follow
    assert "Refresh test results" not in text
    assert "live-status-v1.js" in text


def test_backup_test_access_is_in_place():
    """Off-site backups' Test access posts in place (#519): only the backup
    page's settings form is data-in-place, naming the status line region,
    and Test access is no Review (data-review-skip), while Save opens the
    review dialog (#750). The settled check's follow-up re-reads the status
    line in place instead of reloading the page."""
    text = (TEMPLATES / "integration-settings.html").read_text()
    form = re.search(r'<form method="post"[^>]*id="integration-form"[^>]*>', text)
    assert (
        "{% if target == 'backup' %} action=\"{{ page_url }}#integration-status\" "
        'data-in-place="backup-test"'
    ) in form.group(0)
    assert 'data-review-open="integration-review"' in form.group(0)
    assert names_region(text, "integration-status")
    test = re.search(
        r'\{% button type="submit" name="action" value="test"[^%]*%\}', text
    ).group(0)
    assert "data-review-skip" in test
    save = re.search(
        r'\{% button type="submit" name="action" value="preview"[^%]*%\}', text
    )
    assert "data-in-place-native" not in save.group(0)
    lines = (TEMPLATES / "backup-status-lines.html").read_text()
    follow = re.search(r"<a hidden data-live-follow[^>]*>", lines).group(0)
    assert "data-in-place data-in-place-quiet" in follow
    assert 'href="{{ follow_url }}#integration-status"' in follow


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


def test_response_dashboard_choices_apply_in_place():
    """The Response dashboard (#1537) is one region holding every panel, the
    report options (its zone) and both binary choices (#519 PR 5, #1535):
    Responses shown and the Chart population each land on it, so every
    answer redraws the page's links with the view now shown. The daily
    table keeps its own region inside the chart panel, and no form opts out
    of the in-place filter path any more. No Apply button is left."""
    text = (TEMPLATES / "response-dashboard.html").read_text()
    region = re.search(r'<div id="response-dashboard"[^>]*>', text).group(0)
    assert names_region(text, "response-dashboard")
    assert "data-report-options" in region and "data-report-zone" in region
    choices = re.findall(
        r"\{% include \"stewardship/components/segmented-choice\.html\""
        r"[^%]*%\}",
        text,
    )
    assert len(choices) == 2
    assert all('region_id="response-dashboard"' in choice for choice in choices)
    keys = " ".join(choices)
    for key in ("mode-production", "mode-testing", "scope-historical", "scope-current"):
        assert f'_key="{key}"' in keys, key
    assert "Apply report options" not in text and '<select id="scope"' not in text
    chart = text.split('id="participation-chart"', 1)[1]
    assert "data-table-region" in chart.split('id="participation-export"', 1)[0]
    for path in TEMPLATES.rglob("*.html"):
        assert "data-filter-reload" not in path.read_text(), path.name


def test_live_status_pages_update_themselves_and_dismiss_in_place():
    """The live-status pages have no Refresh link (#1586): each region still
    wraps the page's live-status element, so an in-place answer (Send, a
    Retry) brings a fresh one live-status-v1.js watches. An integration's
    Dismiss acts in place (#519 PR 7a)."""
    for name, region in (
        ("credential-status.html", "credential-progress"),
        ("family-email-progress.html", "send-progress"),
        ("campaign-mail-families.html", "family-tests"),
    ):
        text = (TEMPLATES / name).read_text()
        assert 'data-in-place="refresh"' not in text, name
        body = text.split(f'<div id="{region}" data-in-place-region>', 1)[1]
        assert "data-live-status" in body or "progress-status.html" in body, name
    settings = (TEMPLATES / "integration-settings.html").read_text()
    assert '<div id="integration-status" data-in-place-region>' in settings
    summary = (TEMPLATES / "integration-summary.html").read_text()
    form = re.search(r"<form [^>]*dismiss_credential_result[^>]*>", summary).group(0)
    assert '#integration-status" ' in form and 'data-in-place="dismiss"' in form


def test_remaining_live_status_pages_update_themselves():
    """Background task, Change status and both report export pages have no
    Refresh link (#1586); each region wraps the page's live-status element,
    so live-status-v1.js watches the one an in-place answer brings. The
    export pages' Retry and Cancel no longer hand focus to a Refresh link.
    Their campaign-work notice is a region of its own, drawn even when
    empty, so it comes and goes with the buttons it explains."""
    for name, region in (
        ("background-task.html", "task-status"),
        ("configuration-request.html", "change-status"),
        ("report-export.html", "export-status"),
        ("report-exact.html", "export-status"),
    ):
        text = (TEMPLATES / name).read_text()
        assert 'data-in-place="refresh"' not in text, name
        assert 'data-in-place-fallback="refresh"' not in text, name
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


def test_task_page_regions_survive_its_quiet_refresh():
    """The task page has no Refresh task link (#1586); when its task ends it
    re-reads its own address quietly (#869). The latest retry and the
    technical details are regions, the details' list inside the disclosure,
    so that refresh never closes it."""
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
    assert "Refresh task" not in page and 'id="task-refresh"' not in page
    assert '<div id="task-latest" data-in-place-region>' in page
    details = page.split('<details class="technical-details">', 1)[1]
    assert details.split("</details>", 1)[0].count(
        '<dl id="task-details" data-in-place-region>'
    )


def test_change_status_never_follows_back_to_a_settings_page():
    """The in-place settings panel of #532 is retired (#1429): every editor
    reviews in the review dialog (#750), which follows Change status itself,
    so the status draws no follow-up back to the page."""
    text = (TEMPLATES / "configuration-request-status.html").read_text()
    assert "data-live-follow" not in text and "follow_url" not in text
    assert not (TEMPLATES / "settings-review.html").exists()


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


# A form's opening tag in a template, whose attributes may hold template
# tags and variables (a "%}" is not the tag's end).
FORM_TAG = r'<form id="settings-form"(?:[^>{]|\{[^}]*\})*>'


def test_share_options_review_in_the_dialog():
    """Share options say "option", not "setting", in the hint after its
    Save button (#921); its form opens the shared review dialog
    (#750) from a region redrawn from the page's address once a change is
    applied, and the page draws no in-place review region."""
    text = (TEMPLATES / "share-settings.html").read_text()
    form = re.search(FORM_TAG, text).group(0)
    assert 'data-require-change="base_digest"' in form
    assert 'data-review-open="share-review"' in form
    # Inside the campaign wizard the form saves directly instead (#1404 PR 4).
    assert "{% if wizard.direct %}data-direct-save" in form
    assert "data-in-place" not in form
    assert 'data-refresh-url="{{ refresh_url }}"' in text
    assert "settings-review" not in text
    assert 'dialog_id="share-review"' in text and "review=True" in text
    # A compact review (#1356): the button says Save, the dialog asks the
    # question and its lines describe it.
    assert 'translate "Change an option to save it." as option_hint' in text
    assert "review-changes-button.html' with hint=option_hint label=save_label" in text
    assert 'translate "Save" as save_label' in text
    assert 'translate "Save the share options?" as review_title' in text
    assert 'describedby="share-review-summary" %}' in text


def test_the_workgroup_confirms_in_the_dialog():
    """No-reminders WorkGroup's form carries the action's own label, Save,
    and its hint (#1356); it opens the shared review dialog, described by
    its confirmation's sentence, from a region redrawn from the page's
    address once a change is applied, and the page draws no in-place review
    region."""
    text = (TEMPLATES / "reminder-workgroup.html").read_text()
    form = re.search(r'<form id="settings-form"[^>]*>', text).group(0)
    assert 'data-require-change="base_digest"' in form
    assert 'data-review-open="workgroup-review"' in form
    assert "data-in-place" not in form
    assert 'data-refresh-url="{{ refresh_url }}"' in text
    assert "settings-review" not in text
    assert 'dialog_id="workgroup-review"' in text and "review=True" in text
    assert 'describedby="workgroup-review-summary"' in text
    assert "with hint=name_hint label=save_label" in text
    button = render_to_string(
        "stewardship/components/review-changes-button.html", {"label": "Save"}
    )
    assert ">Save</button>" in button and "Review changes" not in button
    fragment = render_to_string(
        "stewardship/reminder-workgroup-review.html",
        {"review": {"clears": False, "preview": "signed"}},
    )
    assert 'data-review-done="No-reminders WorkGroup changed."' in fragment
    assert '<p id="workgroup-review-summary">From the next ParishSoft refresh' in (
        fragment
    )
    assert 'name="preview" value="signed" data-review-preview' in fragment
    assert "Current" not in fragment and "New WorkGroup" not in fragment
    cleared = render_to_string(
        "stewardship/reminder-workgroup-review.html",
        {"review": {"clears": True, "preview": "signed"}},
    )
    assert "gets the Reminders again" in cleared


def test_campaign_settings_reviews_in_the_dialog_once():
    """Campaign settings is one editor with one Review changes (#1119): its
    form opens the shared review dialog (#750), waits for a real change
    (#921), and draws no in-place review region or second form."""
    text = (TEMPLATES / "campaign-settings.html").read_text()
    forms = re.findall(FORM_TAG.replace(' id="settings-form"', r"\b"), text)
    assert len(forms) == 1
    assert 'data-review-open="campaign-review"' in forms[0]
    # Inside the campaign wizard the form saves directly instead (#1404 PR 4).
    assert "{% if wizard.direct %}data-direct-save" in forms[0]
    assert 'data-require-change="base_digest"' in forms[0]
    assert "data-in-place" not in forms[0]
    assert text.count("stewardship/components/review-changes-button.html") == 1
    assert 'dialog_id="campaign-review"' in text and "review=True" in text
    assert "(read-only)" not in text
    # Save is the action's own label (#1356), for a compact review and a
    # full one alike.
    assert 'translate "Save" as save_label' in text
    assert "hint=unchanged_hint label=save_label" in text
    assert 'translate "Change a setting to save it." as unchanged_hint' in text


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
        # Each names its slot's own status line, drawn in the slot's pane,
        # so the status shows beside the control and the page never jumps.
        assert 'data-review-outcome="artwork-outcome-{{ entry.slot }}"' in form
    slot_end = text.index("</section>")
    assert text.rindex('outcome_id="artwork-outcome-"|add:entry.slot', 0, slot_end) > 0
    assert "outcome_id=" not in text[text.index("{% endfor %}") :]
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
    assert "state.form.dataset.reviewOutcome || dialog.dataset.reviewOutcome" in script


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


def test_parish_settings_review_in_the_dialog():
    """Parish settings' form opens the shared review dialog (#750) and
    waits for a real change (#921); it sits in a region redrawn from the
    page's address once a change is applied, the page draws no in-place
    review region, and the review fragment carries the signed preview."""
    text = (TEMPLATES / "parish-settings.html").read_text()
    forms = re.findall(r"<form\b[^>]*>", text)
    assert len(forms) == 1
    assert 'data-review-open="parish-review"' in forms[0]
    assert 'data-require-change="base_digest"' in forms[0]
    assert "data-in-place" not in forms[0] and "data-table-sync" not in forms[0]
    assert 'data-refresh-url="{{ refresh_url }}"' in text
    assert 'id="settings-review"' not in text and "live-status-v1.js" not in text
    assert 'dialog_id="parish-review"' in text and "review=True" in text
    review = (TEMPLATES / "parish-settings-review.html").read_text()
    assert "data-review-answer" in review and "data-review-preview" in review


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


# Page-refresh controls the go-live redesign removes (#1586 slice 2), kept
# here until then (#1585) so a new one elsewhere still fails.
GO_LIVE_REFRESH = {
    "go-live-readiness.html": {"Refresh readiness preview"},
    "go-live-cleanup.html": {"Refresh cleanup progress"},
    "go-live-links.html": {"Refresh preparation progress"},
    "production-progress.html": {"Refresh Production progress"},
}
# Wording of a control that re-reads the page's own state.
REREAD_WORDING = re.compile(
    r"^(?:Refresh|Reload|Re-read|Check for updates|Check again|Check status|"
    r"Update now|Update status|Get latest)\b",
    re.IGNORECASE,
)
# Controls with that wording that are not page refreshes, with why.
NOT_PAGE_REFRESH = {
    # Actions that start a ParishSoft download.
    "Refresh from ParishSoft",
    "Refresh now",
    # A refusal page's way back to the item it refused (another page).
    "Reload this request",
    "Reload this item",
    # Asks the email provider again which held emails went out (an action).
    "Check again for emails that may have gone out",
    # The Family portal's closed-campaign page (not an Admin page).
    "Check again",
}
# A control's opening and closing: a link, a button, the {% button %} tag,
# or an <input> (its label is its value).
CONTROL = re.compile(
    r"<(?P<tag>a|button)\b(?P<attrs>[^>]*)>(?P<inner>.*?)</(?P=tag)>"
    r"|\{%\s*button\b(?P<battrs>.*?)%\}(?P<binner>.*?)\{%\s*endbutton\s*%\}"
    r"|<input\b(?P<iattrs>(?:[^>{]|\{[{%].*?[%}]\})*)>",
    re.IGNORECASE | re.DOTALL,
)
# A translated literal, either quote, optionally saved "as" a variable.
TRANSLATE = re.compile(
    r"\{%\s*(?:translate|trans)\s+(?P<q>[\"'])(?P<text>.*?)(?P=q)"
    r"(?:\s+as\s+(?P<var>\w+))?[^%]*%\}",
    re.DOTALL,
)
# A blocktranslate's text, optionally saved "asvar".
BLOCK = re.compile(
    r"\{%\s*blocktrans(?:late)?\b(?P<args>[^%]*)%\}(?P<text>.*?)"
    r"\{%\s*endblocktrans(?:late)?\s*%\}",
    re.DOTALL,
)
# The source tree, for labels built in Python (button and link labels).
PACKAGE = TEMPLATES.parents[2]
PYTHON_LABEL = re.compile(
    r"(?:gettext(?:_lazy)?|pgettext(?:_lazy)?|_)\(\s*(?:[\"'][^\"']*[\"']\s*,\s*)?"
    r"(?P<q>[\"'])(?P<text>[^\"']+)(?P=q)"
    r"|[\"'](?:label|button|link_text|action_label)[\"']\s*:\s*(?P<q2>[\"'])"
    r"(?P<text2>[^\"']+)(?P=q2)"
)


def control_labels(text):
    """Every visible control's label in a template source ``text``: the
    translated literals inside it (either quote, translate or
    blocktranslate), a variable a translate ... as saved, the plain text,
    and an input's value. A hidden link (the periodic re-read's own, a live
    status's follow link) is not a visible control."""
    saved = {m["var"]: m["text"] for m in TRANSLATE.finditer(text) if m["var"]}
    for m in BLOCK.finditer(text):
        var = re.search(r"\basvar\s+(\w+)", m["args"])
        if var:
            saved[var.group(1)] = m["text"]
    labels = []
    for m in CONTROL.finditer(text):
        attrs = m["attrs"] or m["battrs"] or m["iattrs"] or ""
        if re.search(r"(?<![\w-])hidden(?![\w-])", attrs):
            continue
        if m["iattrs"] is not None:
            value = re.search(r"\bvalue=(?P<q>[\"'])(?P<v>.*?)(?P=q)", attrs, re.DOTALL)
            inner = value["v"] if value else ""
        else:
            inner = m["inner"] if m["inner"] is not None else m["binner"]
        labels += [t["text"] for t in TRANSLATE.finditer(inner) if not t["var"]]
        labels += [b["text"] for b in BLOCK.finditer(inner)]
        labels += [
            saved[v] for v in re.findall(r"\{\{\s*(\w+)\s*\}\}", inner) if v in saved
        ]
        plain = re.sub(r"\{[%{#].*?[%}#]\}|<[^>]*>", " ", inner, flags=re.DOTALL)
        labels.append(plain)
    return {" ".join(label.split()) for label in labels} - {""}


def test_control_labels_reads_every_way_a_label_is_written():
    """The guard below sees a label however a template writes it."""
    source = (
        "<a href='x'>{% translate 'Refresh A' %}</a>"
        '<button>{% trans "Refresh B" %}</button>'
        "{% button type='submit' %}{% blocktranslate %}Refresh C{% endblocktranslate %}"
        "{% endbutton %}"
        '{% translate "Refresh D" as label %}<a href="y">{{ label }}</a>'
        '<input type="submit" value="{% translate \'Refresh E\' %}">'
        "<a href='z'>Check for updates</a>"
        '<a hidden href="w">{% translate "Refresh hidden" %}</a>'
        '<a href="v">{% translate "Refresh F" %}</a> <a href="u">'
        '{% translate "Refresh G" %}</a>'
    )
    labels = {label for label in control_labels(source) if REREAD_WORDING.match(label)}
    assert labels == {
        "Refresh A",
        "Refresh B",
        "Refresh C",
        "Refresh D",
        "Refresh E",
        "Check for updates",
        "Refresh F",
        "Refresh G",
    }


def test_no_manual_page_refresh_controls():
    """Admin pages update themselves (UX conventions, "Pages that update
    themselves", #1586): no template draws a visible link, button or input,
    and no Python code builds a label, with wording that re-reads the page's
    own state ("Refresh…", "Check for updates", …), other than the go-live
    pages slice 2 converts (#1585). The controls in NOT_PAGE_REFRESH are not
    page refreshes. Every occurrence in a file counts."""
    found = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        for label in control_labels(path.read_text()):
            if REREAD_WORDING.match(label) and label not in NOT_PAGE_REFRESH:
                found.setdefault(path.name, set()).add(label)
    assert found == GO_LIVE_REFRESH
    built = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "migrations" in path.parts or "tests" in path.parts:
            continue
        for m in PYTHON_LABEL.finditer(path.read_text()):
            label = m["text"] or m["text2"]
            # A message (a sentence ending in a stop) is not a control label.
            sentence = label.rstrip().endswith((".", "?", "!"))
            exempt = sentence or label in NOT_PAGE_REFRESH
            if REREAD_WORDING.match(label) and not exempt:
                built.setdefault(path.name, set()).add(label)
    assert built == {}
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text()
        assert "data-live-restart" not in text, path.name
        assert "data-live-refresh-label" not in text, path.name
        # The periodic re-read's own link, and a figures status's follow-up
        # link, are never visible controls.
        for tag in re.findall(r"<a\b[^>]*\bdata-reread(?:-every)?\b[^>]*>", text):
            assert re.search(r"(?<![\w-])hidden(?![\w-])", tag), path.name


# Text telling the reader to reload the page by hand to see background work
# progress (#1586 slice 3): a page that waits on work follows it instead.
REFRESH_THIS_PAGE = re.compile(r"refresh\s+this\s+page", re.IGNORECASE)
# Python modules of the go-live pages the go-live redesign converts (#1585).
GO_LIVE_MODULES = {"activation_progress.py"}


def test_no_refresh_this_page_instructions():
    """No page tells the reader to "refresh this page" to check on work in
    progress (UX conventions, "Pages that update themselves", #1586): no
    template, Python message or script says it, other than the go-live
    pages slice 2 converts (#1585)."""
    scripts = sorted((TEMPLATES.parents[1] / "static/stewardship").glob("*.js"))
    modules = [
        path
        for path in sorted(PACKAGE.rglob("*.py"))
        if not {"migrations", "tests"} & set(path.parts)
        and path.name not in GO_LIVE_MODULES
    ]
    pages = [
        path
        for path in sorted(TEMPLATES.rglob("*.html"))
        if path.name not in GO_LIVE_REFRESH
    ]
    found = {
        path.name
        for path in (*pages, *modules, *scripts)
        if REFRESH_THIS_PAGE.search(path.read_text())
    }
    assert found == set()


@pytest.mark.parametrize(
    ("constant", "sentence"),
    [
        (
            "UPDATES_REFUSED",
            "Couldn't check for updates. Reload the page or sign in again.",
        ),
        ("UPDATES_RETRYING", "Couldn't check for updates — retrying."),
    ],
)
def test_failed_updates_say_the_same_in_both_scripts(constant, sentence):
    """A refused automatic read (the sign-in ended) and a failed one that
    will be retried each say one sentence on every page, whether a status
    region or a periodic re-read failed (UX conventions, "Pages that update
    themselves", #1586, #1647)."""
    scripts = TEMPLATES.parents[1] / "static/stewardship"
    said = {
        re.search(rf'const {constant} = "([^"]+)";', (scripts / name).read_text())[1]
        for name in ("ui-v1.js", "live-status-v1.js")
    }
    assert said == {sentence}
