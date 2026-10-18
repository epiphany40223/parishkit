"""The Family hub's census tabs, shaped and drawn from plain values (#1678).

``hub_census.shape`` turns one response's census answers and its change
rows in force into the Family tab and the Member tabs; the page draws the
chosen one with ``hub-census-panel.html``. The database read is covered by
``database/test_hub_census_postgresql.py``.
"""

from datetime import timedelta

from django.template.loader import render_to_string

from parishkit.stewardship.pipeline import Placement, Stage
from parishkit.stewardship.reports.hub_census import FieldRow, HubCensus, shape
from parishkit.stewardship.reports.timeline_views import page_context
from parishkit.stewardship.responses.member_census import MEMBER_FIELDS
from parishkit.stewardship.web.dates import using

from .test_response_metrics import START
from .test_timelines import CAMPAIGN, FAMILY, IDENTITY, TIMELINE

HOME = {"line1": "1 Old Road", "city": "Town", "region": "IN", "country": "US"}
NEW_HOME = HOME | {"line1": "9 Census Way"}
UNKNOWN = object()


def member(first, last, **values):
    """A current Member's complete ordinary census answer."""
    answer = {field.name: "" for field in MEMBER_FIELDS}
    return answer | {"first_name": first, "last_name": last} | values


def change(kind, key, field, submitted, baseline=UNKNOWN, **state):
    """One change row in force, unreviewed and pending unless ``state`` says."""
    row = {
        "entity_kind": kind,
        "entity_key": key,
        "field": field,
        "baseline_available": baseline is not UNKNOWN,
        "baseline_value": None if baseline is UNKNOWN else baseline,
        "submitted_value": submitted,
        "admin_value_set": False,
        "admin_value": None,
        "handling": "manual",
        "decision": "unreviewed",
        "execution": "pending",
    }
    return row | state


ANSWERS = {
    "family": {
        "home_address": NEW_HOME,
        "mailing_address": None,
        "email_opt_out": False,
    },
    "members": {
        "3": member("Requested", "Adams", email="ann@example.org"),
        "12": member("Bo", "Adams"),
        "4": {"moved_household": True, "confirmed": True},
        "5": {"deceased_status": True, "death_date": "2026-09-01", "confirmed": True},
    },
    "proposed_members": {
        "6f1c1b6e-0000-4000-8000-000000000001": member("Zed", "Adams"),
        "6f1c1b6e-0000-4000-8000-000000000002": member("Cy", "Adams"),
    },
}
NAMES = {
    "3": ["Ann", "Adams"],
    "4": ["Dee", "Adams"],
    "5": ["Eve", "Adams"],
    "7": ["Gus", "Adams"],
    "12": ["Bo", "Adams"],
}
ROWS = [
    change("family", "1234", "home_address", NEW_HOME, HOME),
    # Accepted with the reviewer's own value; the API can write it.
    change(
        "member",
        "3",
        "first_name",
        "Requested",
        "Ann",
        handling="api",
        decision="approved",
        admin_value_set=True,
        admin_value="Anne",
    ),
    # An older answer this response replaced: history, not a change.
    change("member", "12", "nickname", "B", "", execution="superseded"),
    change(
        "member", "4", "moved_household", True, False, execution="resolved_external"
    ),
    change("member", "5", "deceased_status", True, False),
    change("member", "5", "death_date", "2026-09-01"),
    # A request an earlier response kept for a Member ParishSoft no longer
    # lists in this Family.
    change(
        "member", "7", "moved_household", True, False, execution="resolved_upstream"
    ),
    change(
        "proposed_member",
        "6f1c1b6e-0000-4000-8000-000000000001",
        "new_member",
        ANSWERS["proposed_members"]["6f1c1b6e-0000-4000-8000-000000000001"],
    ),
]


def test_the_family_tab_compares_each_household_field():
    """A change shows its baseline, answer and stage; the rest is unchanged."""
    family = shape(ANSWERS, NAMES, ROWS).family
    assert (family.key, str(family.label), family.mark) == ("family", "Family", "")
    (home,) = family.changed
    assert home.label == "Home address" and home.changed
    assert home.baseline == "1 Old Road, Town, IN, US"
    assert home.answer == "9 Census Way, Town, IN, US"
    # A Family address is always By hand: unreviewed reads To review.
    assert home.stage_label == "To review" and home.stage_look == ""
    assert [(row.label, row.answer) for row in family.unchanged] == [
        ("Mailing address", ""),
        ("Opt out of all parish emails", "No"),
    ]
    assert not family.reviewer_column


def test_member_tabs_follow_the_spec_order_with_their_marks():
    """Existing by DUID, then kept-only ones, then added by name; marks."""
    members = shape(ANSWERS, NAMES, ROWS).members
    assert [(item.key, item.label, item.mark) for item in members] == [
        ("member-1", "Adams, Requested", ""),
        ("member-2", "Adams, Dee", "moved"),
        ("member-3", "Adams, Eve", "deceased"),
        ("member-4", "Adams, Bo", ""),
        ("member-5", "Adams, Gus", "missing"),
        ("member-6", "Adams, Cy", "new"),
        ("member-7", "Adams, Zed", "new"),
    ]
    assert str(members[1].mark_label) == "No longer in this household"
    assert str(members[4].mark_label) == "Not in ParishSoft"
    assert "not in ParishSoft yet" in str(members[5].mark_note)


def test_a_member_tab_shows_its_change_stage_and_reviewers_value():
    """Accepted (edited) and API-writable reads Ready to update, with the
    Reviewer's value; a superseded row is no change."""
    members = shape(ANSWERS, NAMES, ROWS).members
    ann = members[0]
    (first,) = ann.changed
    assert (first.label, first.baseline, first.answer) == (
        "First name",
        "Ann",
        "Requested",
    )
    assert first.reviewer == "Anne" and ann.reviewer_column
    assert first.stage_label == "Ready to update"
    assert first.placement.decision.label == "Accepted (edited)"
    assert len(ann.unchanged) == len(MEMBER_FIELDS) - 1
    assert ("Email address", "ann@example.org") in [
        (row.label, row.answer) for row in ann.unchanged
    ]
    # Bo's only row is history, so every field is unchanged.
    assert members[3].changed == () and len(members[3].unchanged) == len(MEMBER_FIELDS)


def test_moved_deceased_and_kept_members_show_only_their_requests():
    """The form asked nothing else about them; each request has its stage."""
    members = shape(ANSWERS, NAMES, ROWS).members
    moved, deceased, kept = members[1], members[2], members[4]
    assert [(row.label, row.answer, row.stage_label) for row in moved.rows] == [
        ("No longer in this household", "Yes", "Entered by hand")
    ]
    assert moved.rows[0].baseline == "No"
    assert [(row.label, row.answer, row.stage_label) for row in deceased.rows] == [
        ("Deceased", "Yes", "To review"),
        ("Death date", "2026-09-01", "To review"),
    ]
    # A death date ParishSoft never held reads (not read), not (blank).
    assert deceased.rows[1].baseline_read is False
    assert [(row.label, row.stage_label) for row in kept.rows] == [
        ("No longer in this household", "Confirmed")
    ]


def test_an_added_member_shows_the_addition_once_and_its_answers():
    """The addition's stage is the tab's; its rows are the Family's answers."""
    members = shape(ANSWERS, NAMES, ROWS).members
    zed, cy = members[6], members[5]
    assert zed.addition.stage_label == "To review"
    assert [row.label for row in zed.rows] == [field.label for field in MEMBER_FIELDS]
    assert ("First name", "Zed") in [(row.label, row.answer) for row in zed.rows]
    # An added Member with no change row in force (none expected) has no stage.
    assert cy.addition is None


def test_names_fall_back_to_parishsoft_then_the_shared_words():
    """A Member with no name anywhere is "Name not recorded in ParishSoft"."""
    answers = {"family": {"home_address": None}, "members": {"8": {}, "9": {}}}
    members = shape(answers, {"8": [" Hal ", "Ito"]}, []).members
    assert [item.label for item in members] == [
        "Ito, Hal",
        "Name not recorded in ParishSoft",
    ]


def test_without_census_member_tabs_are_named_only():
    """A Ministry-only campaign: no Family tab subject and no rows."""
    answers = {"family": {}, "members": {"3": {}, "12": {}}, "proposed_members": {}}
    census = shape(answers, NAMES, [], census=False)
    assert census.family is None and not census.census
    assert [(item.label, item.rows) for item in census.members] == [
        ("Adams, Ann", ()),
        ("Adams, Bo", ()),
    ]
    # A census campaign whose responses carry no census answers yet.
    assert shape(answers, NAMES, []) == HubCensus()


def test_entered_incorrectly_takes_the_error_look():
    """The needs-correction stage's chip is the Error look, with its icon."""
    row = FieldRow(
        "Email address", "a@b", True, placement=Placement(Stage.ENTERED_INCORRECTLY)
    )
    assert row.stage_look == "error"
    chip = render_to_string("stewardship/hub-stage.html", {"row": row})
    assert 'class="stage-chip status-error"' in chip and "level-icon-error" in chip
    assert "Entered incorrectly" in chip
    failed = FieldRow(
        "First name", "X", True, placement=Placement(Stage.READY_TO_UPDATE, failed=True)
    )
    chip = render_to_string("stewardship/hub-stage.html", {"row": failed})
    assert 'class="stage-chip"' in chip and "Last update failed" in chip


def render(tab, census):
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
            sections=("family", "members"),
            census=census,
        )
        return render_to_string("stewardship/timeline.html", context)


def test_the_family_panel_is_the_only_one_drawn():
    """The chosen census tab's panel alone, with its tables and link."""
    page = render("family", shape(ANSWERS, NAMES, ROWS))
    assert page.count("data-tab-loaded") == 1
    panel = page.split('id="family-tabs-panel-family"')[1]
    assert 'id="hub-family-changed-caption"' in panel
    assert "Reviewer's value</th>" not in panel
    assert '<span class="status-word">To review</span>' in panel
    assert "Show unchanged (2 fields)" in panel
    assert 'href="/admin/reports/census/"' in panel
    # The tabs are named, each Member by name; no Member DUID is an address.
    assert 'aria-controls="family-tabs-panel-member-5"' in page
    assert ">Adams, Requested</a>" in page and "tab=3" not in page
    assert "The Family tab and one tab per Member" in page


def test_a_member_panel_draws_its_mark_and_reviewers_value():
    """A Member tab's panel: Reviewer's value column only when one is set."""
    census = shape(ANSWERS, NAMES, ROWS)
    page = render("member-1", census)
    panel = page.split('id="family-tabs-panel-member-1"')[1]
    assert "Reviewer's value</th>" in panel and "<td>Anne</td>" in panel
    assert "Ready to update" in panel and 'id="hub-member-1-unchanged-caption"' in panel
    page = render("member-2", census)
    panel = page.split('id="family-tabs-panel-member-2"')[1]
    assert '<span class="status-badge">No longer in this household</span>' in panel
    assert "Show unchanged" not in panel.split("</section>")[0]
    page = render("member-7", census)
    panel = page.split('id="family-tabs-panel-member-7"')[1]
    assert "Adding this Member:" in panel and "The Family's answers" in panel


def test_before_any_response_the_family_tab_says_so():
    """No census answers yet: the Family tab explains, no Member tab."""
    page = render("family", HubCensus())
    assert "has not submitted census answers yet" in page
    assert 'data-tab="member-1"' not in page


def test_a_change_with_no_stage_is_shown_without_one(caplog):
    """A state the SQL guards never store neither breaks the page nor
    guesses a stage: the change shows no chip and the failure is logged."""
    rows = [change("family", "1", "home_address", NEW_HOME, HOME, decision="odd")]
    with caplog.at_level("WARNING"):
        (home,) = shape(ANSWERS, NAMES, rows).family.changed
    assert home.placement is None and home.stage_label == ""
    assert render_to_string("stewardship/hub-stage.html", {"row": home}).strip() == ""
    (record,) = [
        item for item in caplog.records if "report_shaping_failed" in item.getMessage()
    ]
    assert "hub_census_stage" in str(vars(record))
