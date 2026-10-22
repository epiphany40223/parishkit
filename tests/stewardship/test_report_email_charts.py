"""The editor's Participation charts "which charts" option, without a database.

The Charts checkboxes' offered charts, the stored choice the editor makes,
the refusal of pledges without financial stewardship and the review's words
(report-emails spec, "Participation charts block" and "Blocks field",
#1956).
"""

from datetime import UTC, datetime

from parishkit.stewardship.reports import report_email_editor as editor

from .test_report_schedule import NEW_YORK, report

NOW = datetime(2026, 10, 20, 16, 0, tzinfo=UTC)
FINANCIAL = NEW_YORK | {"modules": ["financial"]}
PLAIN = NEW_YORK | {"modules": []}
ALL = ["first_submissions", "cumulative_families", "pledges"]


def charted(charts=None):
    """Report values with the Participation charts block and ``charts`` chosen."""
    options = {} if charts is None else {"charts": charts}
    return report(
        blocks=[{"block": "participation_charts", "version": 1, "options": options}]
    )


def problems(values, campaign, saved=None):
    """The codes of the editor's problems with ``values``."""
    found = editor.value_problems(
        values, saved=saved, others=[], users=[], campaign=campaign, now=NOW, sent=False
    )
    return [(field, error.code) for field, error in found]


def test_pledges_are_offered_only_with_financial_stewardship():
    """The Charts boxes: all three with financial stewardship, else two."""
    assert editor.offered_charts(FINANCIAL) == ALL
    assert editor.offered_charts(PLAIN) == ALL[:2]


def test_parse_blocks_stores_a_choice_only_for_a_subset():
    """None stores no option (the default, all); a list is stored as given."""
    (entry,) = editor.parse_blocks(["participation_charts"], 5)
    assert entry["options"] == {}
    (entry,) = editor.parse_blocks(["participation_charts"], 5, ["pledges"])
    assert entry["options"] == {"charts": ["pledges"]}
    days, charts = editor.parse_blocks(
        ["day_by_day", "participation_charts"], 7, ["first_submissions"]
    )
    assert days["options"] == {"days": 7}
    assert charts["options"] == {"charts": ["first_submissions"]}


def test_pledges_without_financial_stewardship_are_refused_unless_saved():
    """A new or changed pledges choice needs financial stewardship."""
    assert problems(charted(["pledges"]), FINANCIAL) == []
    assert problems(charted(), PLAIN) == []
    assert problems(charted(["cumulative_families"]), PLAIN) == []
    assert problems(charted(["first_submissions", "pledges"]), PLAIN) == [
        ("charts", "charts_pledges")
    ]
    saved = charted(["pledges"])
    assert problems(charted(["pledges"]), PLAIN, saved=saved) == []
    assert problems(charted(ALL), PLAIN, saved=saved) == [("charts", "charts_pledges")]


def test_an_unknown_or_empty_choice_is_a_blocks_problem():
    """The block's own check refuses it, named at the Blocks field."""
    for charts in ([], ["unknown"], ["pledges", "first_submissions"]):
        assert problems(charted(charts), FINANCIAL) == [("block", "block_invalid")]


def test_the_ticked_charts_follow_what_the_block_draws():
    """The saved choice as offered, or every offered chart when none remains."""
    assert editor.ticked_charts(None, FINANCIAL) == ALL
    assert editor.ticked_charts(charted(), PLAIN) == ALL[:2]
    assert editor.ticked_charts(charted(["pledges"]), FINANCIAL) == ["pledges"]
    assert editor.ticked_charts(charted(["pledges"]), PLAIN) == ALL[:2]
    assert editor.ticked_charts(charted(["first_submissions", "pledges"]), PLAIN) == [
        "first_submissions"
    ]


def test_the_review_names_the_charts_drawn():
    """The review's block words list the charts in their order."""
    words = editor.describe(
        charted(["first_submissions", "pledges"]), FINANCIAL, NOW, []
    )
    assert words["blocks"] == [
        "Participation charts (First submissions of the day, Effective annual pledges)"
    ]
    words = editor.describe(charted(), PLAIN, NOW, [])
    assert words["blocks"] == [
        "Participation charts (First submissions of the day, Cumulative "
        "participating Families)"
    ]


def test_the_stored_choice_is_the_same_from_every_path():
    """Every offered chart is no choice, a strict subset its list, and an
    unchanged saved choice stays as saved (spec, "Participation charts
    block")."""
    assert editor.stored_charts(ALL, None, FINANCIAL) is None
    assert editor.stored_charts(ALL[:2], None, PLAIN) is None
    assert editor.stored_charts(ALL[:2], None, FINANCIAL) == ALL[:2]
    assert editor.stored_charts(["pledges"], None, FINANCIAL) == ["pledges"]
    saved = charted(ALL[:2])
    assert editor.stored_charts(ALL[:2], saved, PLAIN) == ALL[:2]
    kept = ["first_submissions", "pledges"]
    assert editor.stored_charts(kept, charted(kept), PLAIN) == kept
    assert editor.with_charts(charted(ALL), None) == charted()
    assert editor.with_charts(charted(), ["pledges"]) == charted(["pledges"])


def test_the_review_names_a_kept_chart_the_campaign_cannot_draw():
    """A saved pledges choice without financial stewardship is named as kept."""
    words = editor.describe(charted(["first_submissions", "pledges"]), PLAIN, NOW, [])
    assert words["blocks"] == [
        "Participation charts (First submissions of the day; kept, not drawn: "
        "Effective annual pledges)"
    ]
