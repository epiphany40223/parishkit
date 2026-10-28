"""The commitment period's start and the roster read as of it (#2293)."""

from datetime import date

from parishkit.stewardship.campaigns.configuration import commitment_start
from parishkit.stewardship.responses.refresh_checks import (
    refresh_date,
    roster_date,
    roster_held,
)

from .campaign_factory import campaign, financial


def test_the_commitment_start_is_the_stored_financial_start():
    """The period keeps its stored key; without one there is no start."""
    values = campaign(modules=["financial"], financial=financial())["values"]
    assert commitment_start(values) == date(2055, 1, 1)
    assert commitment_start(campaign()["values"]) is None


def row(member, ministry, start, end, *, current):
    """A normalized roster row as the corpus stores it (ISO dates)."""
    return {
        "member_key": member,
        "ministry_key": ministry,
        "startDate": start,
        "endDate": end,
        "current": current,
    }


# Member 3 joins Food pantry (9) on the commitment start and leaves Choir
# (4) the day before; Member 5's Choir membership has no dates at all.
CORPUS = {
    "roster": {
        "choir": row("3", "4", "2026-01-01", "2054-12-31", current=True),
        "pantry": row("3", "9", "2055-01-01", None, current=False),
        "undated": row("5", "4", None, None, current=False),
    }
}


def test_the_roster_is_read_as_of_the_commitment_start():
    """A join dated the start is held and a leave ending the day before is
    not; a row with no dates is never held; the end date is exclusive."""
    assert roster_held(CORPUS, date(2055, 1, 1)) == {("3", "9")}
    assert roster_held(CORPUS, date(2054, 12, 31)) == set()
    assert roster_held(CORPUS, date(2054, 12, 30)) == {("3", "4")}


def test_without_a_commitment_period_the_stored_flag_decides():
    """No start: each row's ``current``, the roster as of the refresh date,
    which is also what the Family form shows."""
    assert roster_held(CORPUS, None) == {("3", "4")}


def test_the_roster_date_is_the_later_of_the_refresh_and_the_start():
    """Before the period begins its start decides; once it is under way the
    refresh date does; on the start itself both agree. No commitment period
    gives no date (the stored flags), and an unknown refresh date leaves
    the start."""
    start = date(2055, 1, 1)
    assert roster_date(start, date(2054, 10, 27)) == start
    assert roster_date(start, start) == start
    assert roster_date(start, date(2055, 3, 1)) == date(2055, 3, 1)
    assert roster_date(start, None) == start
    assert roster_date(None, date(2055, 3, 1)) is None


def test_the_refresh_date_is_the_load_as_of_date():
    """Only a YYYY-MM-DD string naming a real day counts, as the SQL read
    has it; anything else is no refresh date."""
    assert refresh_date({"load": {"as_of_date": "2055-03-01"}}) == date(2055, 3, 1)
    for cursor in (
        None,
        {},
        {"load": []},
        {"load": {}},
        {"load": {"as_of_date": 20550301}},
        {"load": {"as_of_date": "20550301"}},
        {"load": {"as_of_date": "2055-02-30"}},
        {"load": {"as_of_date": "2055-03-01T00:00"}},
    ):
        assert refresh_date(cursor) is None
