"""The fixture campaign calendar stays aligned and far ahead of the real date."""

from datetime import date

from . import campaign_factory, financial_factory
from .fixture_calendar import EARLIEST, EPOCH, NOTICE


def test_shared_factories_start_the_campaign_at_the_epoch():
    """The factories and the guarded epoch describe the same campaign."""
    owner = campaign_factory.campaign()
    assert owner["values"]["start_date"] == EPOCH.isoformat()
    assert campaign_factory.schedule(owner["id"])["values"]["date"] == EPOCH.isoformat()
    assert financial_factory.configuration()["start_date"] == EPOCH.isoformat()


def test_real_date_is_well_before_the_fixture_calendar():
    """Announce the next expiry a year ahead instead of failing database tests."""
    assert date.today() < EARLIEST - NOTICE, (
        f"The real date is within {NOTICE.days} days of the fixture calendar "
        f"({EARLIEST}). Shift the fixture campaign timeline forward by another 28 "
        "years; see tests/stewardship/fixture_calendar.py and issue #421."
    )
