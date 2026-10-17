"""Publish to ParishSoft's read side and page, without a database (#528).

The plan table's wording (``census_publication.plan_row``), its summary
sentences, the order Check ParishSoft's gates apply in, and the page and
Census changes link as the templates draw them.
"""

from datetime import UTC, datetime
from html import unescape
from types import SimpleNamespace

import pytest

from parishkit.stewardship.reports import census_publication as publication
from parishkit.stewardship.reports.census_changes import shape
from parishkit.stewardship.web.tables import paginate

from .test_census_changes import row

CHECKED = datetime(2054, 10, 6, 15, 0, tzinfo=UTC)


def change(**values):
    """One approved automatic change as Census changes words it."""
    return shape(row(decision="approved", **values))


def field(outcome):
    """A plan's field row with ``outcome``."""
    return SimpleNamespace(outcome=outcome)


@pytest.mark.parametrize(
    ("outcome", "blocked", "accepted", "edited", "expected"),
    [
        # Will be written: compared with the Family's baseline.
        ("write", False, False, None, ("write", "555-0100", "555-0199")),
        # An edited value is what is written.
        ("write", False, False, "555-0142", ("write", "555-0100", "555-0142")),
        # Use the Family's value anyway compared with ParishSoft's value then.
        ("write", False, True, None, ("write", "555-0111", "555-0199")),
        # A write on a blocked Member is held back.
        ("write", True, False, None, ("held", "555-0100", "555-0199")),
        # Already in ParishSoft: both show the value to write.
        (
            "already_in_parishsoft",
            False,
            False,
            None,
            ("already_in_parishsoft", "555-0199", "555-0199"),
        ),
        # The plan kept no value for these, so the page says why.
        (
            "conflict",
            True,
            False,
            None,
            ("conflict", "Changed since the Family answered", ""),
        ),
        ("cannot_check", True, False, None, ("cannot_check", "Could not be read", "")),
    ],
)
def test_plan_row_words_each_outcome(outcome, blocked, accepted, edited, expected):
    """Each outcome shows what ParishSoft held at the check and what is
    written, as the reports spec words them."""
    values = {"current_value": "555-0111"}
    if edited is not None:
        values |= {"admin_value_set": True, "admin_value": edited}
    shown = publication.plan_row(
        field(outcome), change(**values), blocked=blocked, accepted=accepted
    )
    assert (shown["outcome"], shown["at_check"], shown["will_write"]) == expected
    assert shown["outcome_label"] == publication.OUTCOMES[expected[0]]
    assert (shown["member_duid"], shown["family_duid"]) == (41, 7001)


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        (
            {
                "write_fields": 12,
                "write_entities": 5,
                "blocked_entities": 2,
                "already": 3,
            },
            "12 changes for 5 Members will be written. 2 Members are held back. "
            "3 changes were already in ParishSoft.",
        ),
        (
            {
                "write_fields": 1,
                "write_entities": 1,
                "blocked_entities": 0,
                "already": 0,
            },
            "1 change for 1 Member will be written.",
        ),
        (
            {
                "write_fields": 0,
                "write_entities": 0,
                "blocked_entities": 1,
                "already": 1,
            },
            "Nothing will be written. 1 Member is held back. "
            "1 change was already in ParishSoft.",
        ),
    ],
)
def test_summary_counts_the_plan(counts, expected):
    """The summary states the counts the confirmation binds."""
    assert publication.summary(counts) == expected


def test_check_gates_apply_in_order(monkeypatch):
    """The switch first, then nothing approved, a run unfinished, and other
    campaign work; with none of them Check ParishSoft is available."""
    running, held = [False], [False]
    monkeypatch.setattr(publication, "publication_running", lambda _: running[0])

    def admit(campaign_id, *, mutating):
        """Refuse while other campaign work holds the campaign."""
        if held[0]:
            raise PermissionError("held")

    monkeypatch.setattr(publication, "admit_campaign", admit)

    def gate(**values):
        """The gate for a campaign with ``values`` overriding the defaults."""
        options = {"enabled": True, "waiting_changes": 1} | values
        return publication.check_gate("campaign", **options)

    assert gate() is None
    held[0] = True
    assert gate() is publication.HELD_CAMPAIGN
    running[0] = True
    assert gate() is publication.RUNNING
    assert gate(waiting_changes=0) is publication.NOTHING_APPROVED
    assert gate(enabled=False, waiting_changes=0) is publication.OFF
    for found in (publication.OFF, publication.RUNNING):
        refusal = found.refusal()
        assert refusal.reason == found.reason and str(refusal) == found.message


def test_enabled_follows_only_a_true_setting(settings):
    """Only True turns the page on; anything else leaves it off."""
    for value, expected in ((True, True), (False, False), ("1", False)):
        settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = value
        assert publication.enabled() is expected
    del settings.STEWARDSHIP_PARISHSOFT_PUBLICATION
    assert publication.enabled() is False


def render(**context):
    """The page's HTML for ``context``, publication on by default."""
    from django.template.loader import render_to_string

    values = {
        "enabled": True,
        "waiting": {"changes": 2, "members": 1},
        "gate": None,
        "view": None,
        "table": None,
        "request_key": "k",
    }
    return unescape(
        render_to_string("stewardship/census-publication.html", values | context)
    )


def test_page_switched_off_offers_no_button():
    """Off: the page says so and draws neither the check nor a plan."""
    html = render(enabled=False, waiting=None, gate=publication.OFF)
    assert "Publishing to ParishSoft is switched off." in html
    assert "Check ParishSoft</button>" not in html
    assert "Newest check" not in html


def test_page_draws_check_unavailable_with_its_reason():
    """A gate draws the button disabled, described by its reserved reason."""
    html = render(gate=publication.RUNNING)
    button = html.split("Check ParishSoft</button>")[0].rsplit("<button", 1)[1]
    assert "disabled" in button
    assert 'aria-describedby="publication-check-reason"' in button
    assert (
        '<p class="help reserve-line" id="publication-check-reason">'
        "Publishing is running. Wait for it to finish.</p>" in html
    )
    # Available: the same reserved line, empty, so nothing moves.
    html = render()
    button = html.split("Check ParishSoft</button>")[0].rsplit("<button", 1)[1]
    assert "disabled" not in button
    assert '<p class="help reserve-line" id="publication-check-reason"></p>' in html
    assert "2 approved Automatic changes are waiting to be published" in html


def test_page_shows_the_plan_in_the_census_changes_column_order():
    """The plan table follows Census changes' columns, then the plan's own;
    a Testing plan says nothing is written or marked."""
    rows = [
        publication.plan_row(field("write"), change(), blocked=False, accepted=False),
    ]
    view = {
        "plan": SimpleNamespace(created_at=CHECKED, testing=True),
        "checked_by": "admin@example.org",
        "rows": rows,
        "others": 2,
        "summary": "1 change for 1 Member will be written.",
    }
    table = paginate(rows, {}, sorting=publication.SORTING)
    html = render(view=view, table=table)
    headings = [
        "Family",
        "Family DUID",
        "Who",
        "Member DUID",
        "What changed",
        "ParishSoft at the check",
        "Will write",
        "Outcome",
    ]
    positions = [html.index(f">{heading}<") for heading in headings]
    assert positions == sorted(positions)
    assert "Checked by admin@example.org at" in html
    assert "Testing mode: ParishSoft was checked, and nothing is written" in html
    assert "1 change for 1 Member will be written." in html
    assert "Will be written" in html and "555-0199" in html
    assert "2 other changes for these Members are not approved" in html


@pytest.mark.parametrize(
    ("administrator", "on", "expected"),
    [
        (True, True, "link"),
        (True, False, "unavailable"),
        (False, True, None),
    ],
)
def test_census_changes_links_to_the_page(administrator, on, expected):
    """Administrators get the link, unavailable with its reason while
    publication is off; Staff see nothing of it."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.reports.census_change_views import SORTING
    from parishkit.stewardship.reports.census_changes import CensusQuery

    html = render_to_string(
        "stewardship/census-changes.html",
        {
            "table": paginate([], {}, sorting=SORTING),
            "query": CensusQuery(),
            "query_fields": {},
            "summary": [],
            "status_choices": [],
            "route_choices": [],
            "kind_choices": [],
            "export_timezones": ["UTC"],
            "administrator": administrator,
            "publication_on": on,
        },
    )
    link = 'href="/admin/reports/census/publication/"'
    unavailable = 'aria-describedby="publication-off-reason"'
    assert (link in html) is (expected == "link")
    assert (unavailable in html) is (expected == "unavailable")
    if expected == "unavailable":
        assert "Publishing to ParishSoft is switched off." in html
    if expected is None:
        assert "Publish to ParishSoft" not in html
