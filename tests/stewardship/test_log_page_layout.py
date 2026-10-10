"""System logs layout: level icons, compact filters, cross-links, hidden IDs."""

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.policy import Capability, Principal, allows
from parishkit.stewardship.audit.log_rows import (
    AUDIT_LABEL,
    LEVEL_LABELS,
    LEVELS,
    LogQuery,
    audit_row,
    log_table,
    operational_row,
    page_context,
    task_subject,
)

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _operational(level, index):
    """One operational row as the view hands it to the template."""
    row = operational_row(
        {
            "id": UUID(int=100 + index),
            "created_at": NOW,
            "level": level,
            "event": "task_failed",
            "actor_id": UUID(int=900),
            "correlation_id": UUID(int=200 + index),
            "context": {"outcome": "failed"},
        }
    )
    row.update(actor=None, actor_worker=True, task_subject=False)
    return row


def _audit():
    """A task audit entry: its subject is the task, so it links to the task page."""
    row = audit_row(
        {
            "id": UUID(int=300),
            "created_at": NOW,
            "event_type": "task_retry_requested",
            "actor_id": UUID(int=301),
            "correlation_id": UUID(int=302),
            "campaign_reference": UUID(int=303),
            "subject_id": UUID(int=304),
            "auditcontext__context": None,
        }
    )
    row.update(actor="admin@example.org", actor_worker=False, task_subject=True)
    return row


def _render(query=None, rows=None, *, linked=False):
    """Render the page from the production context builder."""
    rows = (
        rows
        if rows is not None
        else [
            *(_operational(level, index) for index, level in enumerate(LEVELS)),
            _audit(),
        ]
    )
    return render_to_string(
        "stewardship/logs.html",
        page_context(
            query or LogQuery(),
            log_table(
                query or LogQuery(), rows, through=NOW, action="/admin/system/logs/"
            ),
            linked=linked,
        ),
    )


@pytest.mark.parametrize("level", [*(level.lower() for level in LEVELS), "audit"])
def test_each_level_has_its_own_decorative_icon(level):
    """One matched icon per level, hidden from assistive technology."""
    html = _render()
    icon = re.search(rf'<svg class="level-icon level-icon-{level}"[^>]*>', html)
    assert icon and 'aria-hidden="true"' in icon.group(0)
    assert 'focusable="false"' in icon.group(0)
    assert "style=" not in html


def _level_cells(html):
    """Each table row's second cell (the Level column), top to bottom.

    The row header (Time) comes first, so this also proves the column order.
    """
    return re.findall(
        r'<tr[^>]*>\s*<th scope="row">.*?</th>\s*<td class="log-level-cell">(.*?)</td>',
        html,
        re.S,
    )


def test_level_is_the_second_column_after_time():
    """Time stays first; Level, the icon column, is second (#569)."""
    head = re.search(r"<thead>(.*?)</thead>", _render(), re.S).group(1)
    headings = re.findall(r"<th\b[^>]*>(.*?)</th>", head, re.S)
    assert "Time" in headings[0] and headings[1] == "Level"


@pytest.mark.parametrize("level", LEVELS)
def test_level_cell_shows_the_level_choices_icon_with_its_name(level):
    """An operational row's Level cell is the shared icon the level choices
    use, byte for byte, named for screen readers and as a tooltip (#569)."""
    html = _render()
    cells = _level_cells(html)
    assert len(cells) == len(LEVELS) + 1
    cell = cells[LEVELS.index(level)]
    icon = render_to_string(
        "stewardship/components/level-icon.html", {"level": level.lower()}
    )
    label = str(LEVEL_LABELS[level][1])
    # The same markup appears once beside its checkbox and once in the row.
    assert icon in cell and html.count(icon) == 2
    assert f'<span class="visually-hidden">{label}</span>' in cell
    assert f'title="{label}"' in cell
    # The icon stands alone: no visible word or source line beside it.
    assert re.sub(r"<[^>]+>", "", cell.replace(label, "")).strip() == ""


def test_audit_rows_show_the_shared_audit_icon():
    """Audit records have no level; their Level cell is the shared audit icon
    the Show choices use, named for screen readers and as a tooltip, never
    the words alone, which widened the column (#601)."""
    html = _render()
    cell = _level_cells(html)[-1]
    icon = render_to_string(
        "stewardship/components/level-icon.html", {"level": "audit"}
    )
    assert icon in cell and html.count(icon) == 2
    assert f'<span class="visually-hidden">{AUDIT_LABEL}</span>' in cell
    assert f'title="{AUDIT_LABEL}"' in cell
    assert re.sub(r"<[^>]+>", "", cell.replace(str(AUDIT_LABEL), "")).strip() == ""
    assert "log-kind" not in html


def test_audit_icon_differs_in_shape_from_every_level_icon():
    """Shape, not only color, tells the audit record from each level (#601)."""

    def shapes(level):
        """The icon's shape elements as (tag, geometry), ignoring every color.

        Only geometry attributes are kept, so two icons that differ only in
        fill or stroke color compare equal and fail the test.
        """
        icon = render_to_string(
            "stewardship/components/level-icon.html", {"level": level}
        )
        body = re.search(r"<svg[^>]*>(.*)</svg>", icon, re.S).group(1)
        geometry = ("d", "x", "y", "width", "height", "rx", "cx", "cy", "r")
        return [
            (tag, tuple(re.findall(rf'\b({"|".join(geometry)})="([^"]*)"', attrs)))
            for tag, attrs in re.findall(r"<(\w+)\b([^>]*)/?>", body)
        ]

    audit = shapes("audit")
    # The outline, drawn first, is a portrait board (a clipboard), with lines.
    tag, geometry = audit[0]
    outline = dict(geometry)
    assert tag == "rect" and float(outline["height"]) > float(outline["width"])
    for level in LEVELS:
        drawn = shapes(level.lower())
        assert drawn and drawn != audit
        # No level's outline is a board: circles, a triangle, an octagon.
        assert drawn[0][0] != "rect" and drawn[0] != audit[0]


def _choices(html):
    """The Show fieldset's checkbox labels, in order."""
    fieldset = re.search(
        r'<fieldset class="log-level-choices"[^>]*>(.*?)</fieldset>', html, re.S
    ).group(1)
    return re.findall(r"<label[^>]*>(.*?)</label>", fieldset, re.S)


def test_six_kinds_of_entry_are_one_row_of_checkboxes():
    """The five levels and audit records are six checkboxes under Show, each
    with its shared icon; the Source field is gone (#601)."""
    html = _render()
    labels = _choices(html)
    names = [re.search(r'name="(\w+)"', label).group(1) for label in labels]
    assert names == [*(level.lower() for level in LEVELS), "audit"]
    for name, label in zip(names, labels, strict=True):
        assert 'type="checkbox"' in label and 'value="yes"' in label
        icon = render_to_string(
            "stewardship/components/level-icon.html", {"level": name}
        )
        assert icon in label
    assert "Audit record</label>" in html.replace(" </label>", "</label>")
    assert "<legend>Show</legend>" in html
    assert 'name="source"' not in html and "log-source" not in html
    assert ">Source<" not in html


@pytest.mark.parametrize(
    ("values", "ticked"),
    [
        ({}, ["info", "warning", "error", "critical", "audit"]),
        ({"applied": "yes", "audit": "yes"}, ["audit"]),
        ({"applied": "yes", "debug": "yes"}, ["debug"]),
        ({"source": "operational"}, ["info", "warning", "error", "critical"]),
    ],
)
def test_checkboxes_show_the_applied_choice(values, ticked):
    """Defaults are unchanged; a legacy Source arrives as its ticks (#601)."""
    labels = _choices(_render(LogQuery.parse(values)))
    checked = [
        re.search(r'name="(\w+)"', label).group(1)
        for label in labels
        if " checked>" in label
    ]
    assert checked == ticked


def test_the_choices_gate_apply_until_one_is_ticked():
    """The complete gate keeps Apply unavailable with no box ticked (#601)."""
    html = _render()
    form = re.search(r'<form id="table-filters"[^>]*>', html).group(0)
    assert "data-require-complete" in form
    fieldset = re.search(r'<fieldset class="log-level-choices"[^>]*>', html).group(0)
    assert "data-require-one" in fieldset
    assert 'data-missing-hint="Tick at least one kind of entry to show."' in fieldset
    # The hint names the group too, as it does the gated Apply button.
    assert 'aria-describedby="log-filter-hint"' in fieldset


@pytest.mark.parametrize(
    ("values", "campaign_note"),
    [
        ({"applied": "yes", "info": "yes", "campaign": str(UUID(int=5))}, True),
        ({"applied": "yes", "audit": "yes", "campaign": str(UUID(int=5))}, False),
        ({"applied": "yes", "info": "yes"}, False),
        ({"applied": "yes", "info": "yes", "subject": str(UUID(int=5))}, True),
    ],
)
def test_an_empty_campaign_filter_without_audit_says_why(values, campaign_note):
    """A campaign matches only audit records, so with Audit record unticked
    the empty table says to tick it instead of the general advice."""
    html = _render(LogQuery.parse(values), rows=[])
    note = "Campaign and subject filters list audit records only; tick Audit record."
    assert (note in html) is campaign_note
    assert ("Try a wider date range" in html) is not campaign_note


def test_critical_rows_stand_out():
    """A CRITICAL entry's row is highlighted; the others are not."""
    html = _render()
    assert html.count('class="log-row-critical"') == 1


def test_identifiers_sit_under_closed_technical_details():
    """Every identifier shown as text is inside a closed Technical details block.

    Identifiers still travel in hidden inputs and links, which are not text.
    """
    html = _render()
    assert html.count('<details class="technical-details">') == len(LEVELS) + 1
    visible = re.sub(
        r"<details class=\"technical-details\">.*?</details>", "", html, flags=re.S
    )
    visible = re.sub(r"<[^>]+>", " ", visible)
    assert not UUID_TEXT.search(visible)


def test_entries_cross_link_by_correlation_actor_campaign_and_task():
    """IDs become actions: related entries, same actor, same campaign, open task."""
    html = _render()
    rows = len(LEVELS) + 1
    assert html.count(">Show related entries</button>") == rows
    assert html.count(">Same actor</button>") == rows
    assert html.count(">Same campaign</button>") == 1
    assert html.count(">Open task</a>") == 1
    assert f'href="/admin/system/background/{UUID(int=304)}/">Open task</a>' in html
    # A campaign filter only ever matches audit records, so it asks for them
    # alone: Audit record ticked, every level unticked (#601).
    form = re.search(r"<form[^>]*>(?:(?!</form>).)*>Same campaign<", html, re.S)
    hidden = dict(
        re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', form.group(0))
    )
    assert hidden == {"applied": "yes", "audit": "yes", "campaign": str(UUID(int=303))}


def test_identifier_filters_fold_away_unless_used():
    """The three identifier filters are closed until one of them is set."""
    closed = _render()
    assert '<details id="log-identifier-filters" class="log-more-filters">' in closed
    used = _render(
        LogQuery.parse({"applied": "yes", "audit": "yes", "actor": str(UUID(int=5))})
    )
    assert '<details id="log-identifier-filters" class="log-more-filters" open>' in used


@pytest.mark.parametrize(
    ("event", "subject", "linked"),
    [
        ("task_retry_requested", UUID(int=1), True),
        ("background_viewed", UUID(int=1), True),
        ("background_viewed", None, False),
        ("admin_login", UUID(int=1), False),
    ],
)
def test_only_task_subjects_open_the_task_page(event, subject, linked):
    """Task entries and views of one task's page link to it; other subjects don't."""
    assert task_subject({"event": event, "subject_id": subject}) is linked


@pytest.mark.parametrize(
    "roles",
    [
        frozenset({"administrator"}),
        frozenset({"staff"}),
        frozenset({"ministry_leader"}),
        frozenset({"staff", "ministry_leader"}),
    ],
)
def test_every_log_reader_can_open_the_linked_task_page(roles):
    """The "Open task" link is shown to every log reader, so each must be able
    to open the task page; a role split that breaks this must change the link.
    """
    principal = Principal(uuid4(), roles, frozenset({1}))
    for ministry_id in (None, 1):
        if allows(principal, Capability.SYSTEM_LOGS, ministry_id=ministry_id):
            assert allows(
                principal, Capability.BACKGROUND_WORK, ministry_id=ministry_id
            )


def test_the_link_to_these_filters_leaves_identifiers_out():
    """The page draws its own address for bookmarks (#536): the link filters
    only, which ui-v1.js puts in the address bar; search text and identifiers
    stay out, and a note says so while one is applied."""
    query = LogQuery.parse(
        {"text": "lag", "ministry": "42", "correlation": str(UUID(int=5))}
    )
    html = _render(query)
    link = re.search(r'<a href="([^"]*)" data-page-address>', html).group(1)
    assert link == "/admin/system/logs/?ministry=42"
    assert str(UUID(int=5)) not in link and "lag" not in link
    note = "search and identifier filters are left out of links"
    assert note in html
    assert note in _render(LogQuery.parse({"text": "lag"}))
    assert note not in _render(LogQuery.parse({"ministry": "42"}))
    region = re.search(r'<div id="log-link" data-table-sync>', html)
    assert region is not None


def test_a_followed_links_zone_is_noted_for_the_script_to_show():
    """Only a followed link with days names its zone; the note starts hidden
    and ui-v1.js shows it when the browser's zone differs."""
    query = LogQuery.parse({"start": "2026-09-01", "zone": "Asia/Tokyo"})
    note = re.search(
        r'<p class="notice" data-link-zone="([^"]+)" hidden>',
        _render(query, linked=True),
    )
    assert note and note.group(1) == "Asia/Tokyo"
    assert "data-link-zone" not in _render(query)
    assert "data-link-zone" not in _render(LogQuery.parse({"text": "x"}), linked=True)


def test_search_and_ministry_fields_gate_apply_with_their_own_hints():
    """Search refuses an address and Ministry takes digits, each with a hint
    the complete gate shows instead of the browser's bubble."""
    html = _render(LogQuery.parse({"text": "lag", "ministry": "42"}))
    search = re.search(r'<input id="log-text"[^>]*>', html).group(0)
    assert 'pattern="[^@]*"' in search and 'value="lag"' in search
    assert 'name="text"' in search and 'maxlength="64"' in search
    ministry = re.search(r'<input id="log-ministry"[^>]*>', html).group(0)
    assert 'pattern="[1-9][0-9]{0,9}"' in ministry and 'value="42"' in ministry
    assert "Search text cannot hold an email address" in html
    assert "Type a Ministry DUID as digits only, or clear it." in html
    # The subject filter sits with the other identifiers, opened when used.
    subject = _render(LogQuery.parse({"subject": str(UUID(int=5))}))
    assert re.search(r'<details id="log-identifier-filters"[^>]* open>', subject)


def test_the_activity_choice_offers_sign_in_activity_in_plain_words():
    """The filter bar's Activity choice (#953) shows its applied group, and
    its explanation never asks the reader to type a type name."""
    plain = _render()
    select = re.search(
        r'<select id="log-activity" name="activity">(.*?)</select>', plain
    )
    assert select is not None
    assert re.findall(r'<option value="([^"]*)"', select.group(1)) == ["", "sign_in"]
    assert '<option value="" selected>All activity</option>' in plain
    chosen = _render(LogQuery.parse({"activity": "sign_in"}))
    assert '<option value="sign_in" selected>Sign-in activity</option>' in chosen
    assert '<label for="log-activity" id="log-activity-label">Activity</label>' in plain
    # The control sits in the filter bar before the type, so nothing appears
    # or moves when it is chosen.
    assert plain.index('id="log-activity"') < plain.index('id="log-event"')


def test_same_actor_keeps_the_sign_in_activity_choice():
    """Same actor on a sign-in view lists that person's sign-in activity
    only (#953); without a group it sends the actor alone, as before."""
    row = _audit()
    grouped = _render(LogQuery.parse({"activity": "sign_in"}), rows=[row])
    form = re.search(r'name="actor" value="[^"]+">(<input[^>]*>)?', grouped)
    assert form.group(1) == '<input type="hidden" name="activity" value="sign_in">'
    plain = _render(rows=[row])
    form = re.search(r'name="actor" value="[^"]+">(<input[^>]*>)?', plain)
    assert form.group(1) is None


@pytest.mark.parametrize(
    ("values", "activity_note"),
    [
        ({"applied": "yes", "info": "yes", "activity": "sign_in"}, True),
        ({"applied": "yes", "audit": "yes", "activity": "sign_in"}, False),
    ],
)
def test_sign_in_activity_without_audit_says_why(values, activity_note):
    """Sign-in activity is audit records, so with Audit record unticked the
    empty table says to tick it."""
    html = _render(LogQuery.parse(values), rows=[])
    note = "Sign-in activity is made of audit records; tick Audit record."
    empty = re.search(r'<td colspan="6">(.*?)</td>', html).group(1)
    assert (note in empty) is activity_note
    assert ("clearing the activity, type" in html) is not activity_note


def test_the_link_and_download_carry_the_activity_choice():
    """A sign-in view's own link and its download form both carry the group,
    so a bookmark and the file match the page."""
    html = _render(
        LogQuery.parse({"applied": "yes", "audit": "yes", "activity": "sign_in"})
    )
    link = re.search(r'<a href="([^"]*)" data-page-address>', html).group(1)
    assert link == "/admin/system/logs/?activity=sign_in&amp;applied=yes&amp;audit=yes"
    export = re.search(r'<form id="table-export".*?</form>', html, re.S).group(0)
    assert '<input type="hidden" name="activity" value="sign_in">' in export


def test_sign_in_activity_greys_the_levels_and_waits_for_audit():
    """While Sign-in activity is chosen (#953) the five level boxes stay in
    place but are disabled (data-enabled-when), and Apply waits for Audit
    record (data-required-when), its label naming why."""
    labels = _choices(_render())
    marks = {re.search(r'name="(\w+)"', label).group(1): label for label in labels}
    for level in ("debug", "info", "warning", "error", "critical"):
        assert 'data-enabled-when="activity="' in marks[level], level
        assert "data-required-when" not in marks[level]
    audit = marks["audit"]
    assert 'data-required-when="activity=sign_in"' in audit
    assert "data-enabled-when" not in audit
    html = _render()
    label = re.search(
        r'<label data-missing-hint="([^"]*)"><input[^>]*name="audit"', html
    )
    assert label.group(1) == (
        "Sign-in activity is made of audit records; tick Audit record."
    )
