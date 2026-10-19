"""The Family hub's Ministry, Financial and last tabs, from plain values (#1678).

``hub_streams`` shapes slices 3 and 4: a Member tab's Ministry joins and
leaves, the Financial tab's pledge and the Additional tab (the cannot
participate answers and Additional information); the page draws the chosen
one. The database
reads are covered by ``database/test_hub_streams_postgresql.py``.
"""

from datetime import date, timedelta
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.reports.hub_census import HubCensus, shape
from parishkit.stewardship.reports.hub_streams import (
    HubFinancial,
    HubOther,
    financial_tab,
    ministry_rows,
    other_tab,
)
from parishkit.stewardship.reports.money import MoneyAmount
from parishkit.stewardship.reports.timeline_views import page_context
from parishkit.stewardship.web.dates import using

from .test_hub_census import ANSWERS, NAMES, ROWS
from .test_response_metrics import START
from .test_timelines import CAMPAIGN, FAMILY, IDENTITY, TIMELINE

ADDED = "6f1c1b6e-0000-4000-8000-000000000001"
ITEM = UUID("00000000-0000-4000-8000-0000000000aa")


def request(ministry, action="join", state="new", outcome=None, **flags):
    """One ``MINISTRY_REQUESTS`` row, open and unticked unless told."""
    return {
        "ministry": ministry,
        "action": action,
        "state": state,
        "outcome": outcome,
        "resolved": flags.get("resolved", False),
        "entered": flags.get("entered", False),
    }


NAMES_BY_DUID = {4: "Ushers", 9: "Adult Choir", 11: "Altar Servers", 12: "Lectors"}
REQUESTS = [
    request(9),
    request(4, "leave", "resolved", "declined"),
    request(11, state="resolved", outcome="joined"),
    request(12, state="resolved", outcome="joined", entered=True),
    # Superseded or cancelled: history, never shown.
    request(13, state="cancelled"),
]


def test_ministry_rows_follow_the_ministry_stage_mapping():
    """Each request in force with its roster baseline, answer and stage."""
    rows = ministry_rows(REQUESTS, NAMES_BY_DUID)
    assert [
        (row.label, row.baseline, row.answer, row.stage_label, row.note) for row in rows
    ] == [
        ("Adult Choir", "Not in this Ministry", "Join", "To review", ""),
        (
            "Altar Servers",
            "Not in this Ministry",
            "Join",
            "To enter by hand",
            ("Joined the Ministry"),
        ),
        (
            "Lectors",
            "Not in this Ministry",
            "Join",
            "Entered by hand",
            ("Joined the Ministry"),
        ),
        ("Ushers", "In this Ministry", "Leave", "Ignored", "Stayed in the Ministry"),
    ]
    # Reconciliation saw the roster already changed: Confirmed.
    (row,) = ministry_rows(
        [request(9, state="resolved", outcome="joined", resolved=True)],
        NAMES_BY_DUID,
    )
    assert row.placement.stage is Stage.CONFIRMED
    assert ministry_rows([], NAMES_BY_DUID) == ()


def test_a_member_no_longer_current_keeps_its_stage_with_a_note():
    """Ministry requests hides it by default (#1217); the hub says where."""
    (row,) = ministry_rows(
        [request(11, state="resolved", outcome="joined") | {"member_status": "moved"}],
        NAMES_BY_DUID,
    )
    assert row.stage_label == "To enter by hand"
    assert "Members no longer in ParishSoft" in row.note
    assert row.note.startswith("Not in ParishSoft: ")
    # A current Member, or one the Family added, has no such note.
    for status in ("current", None):
        (row,) = ministry_rows([request(9) | {"member_status": status}], NAMES_BY_DUID)
        assert row.note == ""


def test_a_ministry_state_with_no_stage_is_shown_without_one(caplog):
    """A state the SQL guards never store is logged and shows no chip."""
    with caplog.at_level("WARNING"):
        (row,) = ministry_rows([request(9, state="odd")], NAMES_BY_DUID)
    assert row.placement is None and row.stage_label == ""
    assert any("hub_ministry_stage" in str(vars(item)) for item in caplog.records)


def shaped_pledge(**values):
    """One Financial stewardship row as ``financial.shape_result`` words it."""
    row = {
        "annual": MoneyAmount(120000),
        "installment": MoneyAmount(10000),
        "frequency_label": "Monthly",
        "cannot_give": False,
        "shares": [
            {"label": "Online giving", "text": ""},
            {"label": "Other", "text": "Stock gift"},
        ],
        "source_pledge": MoneyAmount(100000),
        "source_contributions": MoneyAmount(90000),
    }
    return row | values


METADATA = {
    "comparison_start": "2026-01-01",
    "comparison_end": "2026-12-31",
    "giving_through": date(2026, 9, 30),
}


def test_the_financial_tab_is_one_pledge_item():
    """The pledge's one stage, its amount against ParishSoft's, and the rest."""
    tab = financial_tab(shaped_pledge(), METADATA)
    assert tab.pledge.addition.stage_label == "To review"
    assert [
        (row.label, row.baseline, row.baseline_read, row.answer)
        for row in tab.pledge.rows
    ] == [
        ("Annual pledge (ParishSoft 2026)", "$1,000.00", True, "$1,200.00"),
        ("Frequency", "", False, "Monthly ($100.00 each)"),
        ("Share methods", "", False, "Online giving; Other: Stock gift"),
    ]
    assert tab.pledge.unchanged == () and tab.years == "2026"
    assert tab.contributions.display == "$900.00"
    # Cannot contribute, no share method, and money not proven complete.
    tab = financial_tab(
        shaped_pledge(
            cannot_give=True,
            installment=MoneyAmount(None),
            frequency_label="Cannot contribute",
            shares=[],
            source_pledge=MoneyAmount(None),
        ),
        METADATA | {"comparison_start": "", "comparison_end": ""},
    )
    assert [(row.label, row.baseline, row.answer) for row in tab.pledge.rows] == [
        ("Annual pledge", "Unavailable", "Cannot contribute financially"),
        ("Frequency", "", "Cannot contribute"),
        ("Share methods", "", "None chosen"),
    ]


SERVICE = {
    "members": {"3": {"cannot_serve": True}, "12": {"cannot_serve": False}},
    "proposed_members": {ADDED: {"cannot_serve": True}},
}


def other(**values):
    """One ``OTHER`` row: cannot attend, the service answers, no item."""
    row = {
        "cannot_attend": False,
        "service": SERVICE,
        "item": None,
        "text": None,
        "disposition": None,
        "follow_up_needed": None,
        "followed_up": None,
    }
    return row | values


def test_the_last_tab_stages_each_yes_and_the_current_item():
    """Yes answers and the item are items; No answers are unchanged."""
    members = shape(ANSWERS, NAMES, ROWS).members
    item = {
        "item": ITEM,
        "text": "Please call\nabout the choir",
        "disposition": "current_actionable",
        "follow_up_needed": True,
        "followed_up": False,
    }
    tab = other_tab(other(**item), members, ministry=True, information=True)
    assert tab.item == ITEM and tab.ministry
    assert [
        (row.label, row.answer, row.stage_label) for row in tab.subject.changed
    ] == [
        ("Cannot participate in ministries: Adams, Requested", "Yes", "To review"),
        ("Cannot participate in ministries: Adams, Zed", "Yes", "To review"),
        ("Additional information", "Please call\nabout the choir", "To handle"),
    ]
    assert [(row.label, row.answer) for row in tab.subject.unchanged] == [
        ("Cannot attend Mass or prayer services", "No"),
        ("Cannot participate in ministries: Adams, Bo", "No"),
    ]
    assert all(not row.baseline_read for row in tab.subject.changed)
    # Followed up reads Handled; no item reads "None given", unchanged.
    tab = other_tab(
        other(**item | {"followed_up": True}), members, ministry=True, information=True
    )
    assert tab.subject.changed[-1].stage_label == "Handled"
    tab = other_tab(
        other(cannot_attend=True), members, ministry=False, information=True
    )
    assert [(row.label, row.answer) for row in tab.subject.rows] == [
        ("Cannot attend Mass or prayer services", "Yes"),
        ("Additional information", "None given"),
    ]
    assert tab.subject.changed[0].stage_label == "To review" and not tab.ministry
    # Without Additional information, no such row at all.
    tab = other_tab(other(), members, ministry=False, information=False)
    assert [row.label for row in tab.subject.rows] == [
        "Cannot attend Mass or prayer services"
    ]


def render(tab, **options):
    """The hub page with ``tab`` chosen, as the view renders it."""
    with using("us_long"):
        context = page_context(
            CAMPAIGN,
            FAMILY,
            IDENTITY,
            TIMELINE,
            START + timedelta(days=1),
            full=True,
            show_codes=True,
            parameters={"tab": tab},
            **options,
        )
        return render_to_string("stewardship/timeline.html", context)


def panel(page, key):
    """The chosen panel's own markup."""
    return page.split(f'id="family-tabs-panel-{key}"')[1].split('role="tabpanel"')[0]


def test_the_financial_panel_draws_the_pledge():
    """One stage line, the table, the contributions line and the link."""
    page = render(
        "financial",
        sections=("financial",),
        hub_financial=financial_tab(shaped_pledge(), METADATA),
    )
    assert page.count("data-tab-loaded") == 1
    drawn = panel(page, "financial")
    assert 'This pledge: <span class="stage-chip">' in drawn
    assert 'id="hub-financial-caption"' in drawn
    assert '<span class="cell-blank">(not read)</span>' in drawn
    assert "ParishSoft contributions for 2026: $900.00." in drawn
    assert "Giving data read through September 30, 2026." in drawn
    assert 'href="/admin/reports/financial/"' in drawn
    assert "Show unchanged" not in drawn and "The Financial tab shows" in page
    page = render("financial", sections=("financial",), hub_financial=HubFinancial())
    assert "has not answered the financial questions yet" in panel(page, "financial")
    page = render(
        "financial",
        sections=("financial",),
        hub_financial=HubFinancial(available=False),
    )
    assert "cannot be read right now" in panel(page, "financial")


def test_the_last_panel_lists_items_then_unchanged():
    """Items with chips, a closed Show unchanged and the handling links."""
    members = shape(ANSWERS, NAMES, ROWS).members
    hub = other_tab(
        other(
            item=ITEM,
            text="Line one\nLine two",
            disposition="current_actionable",
            follow_up_needed=False,
            followed_up=False,
        ),
        members,
        ministry=True,
        information=True,
    )
    page = render("other", sections=("members", "other"), hub_other=hub)
    drawn = panel(page, "other")
    assert 'id="hub-other-changed-caption"' in drawn
    assert "Line one<br>Line two" in drawn
    assert "Show unchanged (2 fields)" in drawn and "<details" in drawn
    assert 'href="/admin/reports/limitations/"' in drawn
    assert f'href="/admin/reports/information/{ITEM}/"' in drawn
    # No response yet; no item links to the queue; no Ministry, no Limitations.
    page = render(
        "other",
        sections=("other",),
        hub_other=HubOther(information=True),
        information=True,
    )
    assert "has not submitted a response yet" in panel(page, "other")
    assert 'href="/admin/reports/information/"' in panel(page, "other")
    assert "/admin/reports/limitations/" not in panel(page, "other")
    assert "and the Family's current Additional information" in page


def test_without_additional_information_the_last_tab_never_mentions_it():
    """A Ministry campaign without Additional information: no link, no words."""
    members = shape(ANSWERS, NAMES, ROWS).members
    hub = other_tab(other(), members, ministry=True, information=False)
    page = render("other", sections=("members", "other"), hub_other=hub)
    drawn = panel(page, "other")
    assert "Additional information</th>" not in drawn
    assert "/admin/reports/information/" not in drawn
    assert "gave no Additional information" not in drawn
    assert "and the Family's current Additional information" not in page
    hub = other_tab(other(service={}), members, ministry=True, information=False)
    page = render("other", sections=("members", "other"), hub_other=hub)
    assert "the Family answered No to each of these.</p>" in panel(page, "other")


def test_member_panels_add_ministry_joins_and_leaves():
    """With census both parts; without it the Ministry part alone."""
    census = shape(ANSWERS, NAMES, ROWS)
    rows = ministry_rows(REQUESTS, NAMES_BY_DUID)
    page = render(
        "member-1",
        sections=("family", "members"),
        census=census,
        ministry=True,
        hub_ministry=rows,
    )
    drawn = panel(page, "member-1")
    assert 'id="hub-member-1-changed-caption"' in drawn
    assert 'id="hub-member-1-ministry-caption"' in drawn
    assert drawn.index("Open Census changes") < drawn.index("Adult Choir")
    assert '<span class="help">Stayed in the Ministry</span>' in drawn
    assert 'href="/admin/reports/ministries/"' in drawn
    assert "Each Member tab also lists the Ministries" in page
    # A Ministry-only campaign: named Member tabs, no census part.
    ministry_only = shape(
        {"family": {}, "members": {"3": {}, "12": {}}, "proposed_members": {}},
        NAMES,
        [],
        census=False,
    )
    page = render(
        "member-2",
        sections=("members", "other"),
        census=ministry_only,
        ministry=True,
        hub_ministry=(),
    )
    drawn = panel(page, "member-2")
    assert "<h2" in drawn and "Adams, Bo" in drawn
    assert "Census changes" not in drawn and "Show unchanged" not in drawn
    assert "No Ministry joins or leaves requested." in drawn
    assert 'aria-disabled="true"' not in page
    # The Family tab never draws a Ministry part.
    page = render("family", sections=("family", "members"), census=census)
    assert "Ministry joins and leaves" not in panel(page, "family")


def test_the_census_tabs_keep_each_members_entity():
    """Each Member subject carries the identity its requests use."""
    members = shape(ANSWERS, NAMES, ROWS).members
    assert [item.entity for item in members] == [
        ("member", "3"),
        ("member", "4"),
        ("member", "5"),
        ("member", "12"),
        ("member", "7"),
        ("proposed_member", "6f1c1b6e-0000-4000-8000-000000000002"),
        ("proposed_member", ADDED),
    ]
    assert HubCensus().subject("member-1") is None
