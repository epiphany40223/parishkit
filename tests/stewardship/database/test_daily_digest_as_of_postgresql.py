"""A daily digest reports the end of the previous campaign day, whenever it sends.

#721: the chart ended on the report day while the text counted live Families
at the send. A Family that responds after midnight but before the send must
appear in neither the chart's last day, nor the text, nor the as-of line.
"""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from parishkit.stewardship.reports.digest_models import DailyDigestSnapshot

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import live_response_service  # noqa: F401
from .test_daily_digest_building_postgresql import build
from .test_fact_materialization_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

# The fixture campaign runs in America/New_York (EDT, UTC-4, in October).
REPORT_DAY = date(2054, 10, 7)
AFTER_MIDNIGHT = datetime(2054, 10, 8, 9, tzinfo=UTC)  # 5:00 AM local
MORNING_SEND = datetime(2054, 10, 8, 10, tzinfo=UTC)  # 6:00 AM local
LATE_SEND = datetime(2054, 10, 8, 15, tzinfo=UTC)  # 11:00 AM local


@pytest.mark.parametrize("send", [MORNING_SEND, LATE_SEND], ids=["morning", "late"])
def test_digest_reports_previous_day_end_not_send_time(
    live_response_service,  # noqa: F811
    send,
):
    harness = live_response_service
    # Production planning waits for activation catch-up; this one has no work.
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(AFTER_MIDNIGHT):
        respond(harness)
    with campaign_clock(send):
        _, document, content = build(harness)
    # The schedule slot, not the send time, fixes the report day.
    assert document.covered_dates[-1] == REPORT_DAY
    assert document.participation.last_date == REPORT_DAY
    assert document.report_day.local_date == REPORT_DAY
    # The chart's last point excludes the after-midnight response ...
    day = document.report_day
    assert (day.first_responses, day.cumulative_responses) == (0, 0)
    assert day.cohort_denominator >= 1
    # ... while the retained send-time observation already counts it, which
    # is exactly the live number the email used to show.
    snapshot = DailyDigestSnapshot.objects.get(pk=document.snapshot_id)
    assert snapshot.through_date == REPORT_DAY
    assert snapshot.submission_watermark == 1
    assert document.statistics.active.responses == 1
    # Every figure and the as-of line describe the end of the report day.
    as_of = "All figures are as of the end of October 7, 2054 (EDT)."
    total = f"{day.cohort_denominator:,}"
    assert f"Families that have responded: 0 out of {total}" in content.text
    assert "First submissions that day: 0" in content.text
    # One line per total (#720): the label, its bar, then the exact value.
    assert ">Families that have responded</td>" in content.html
    assert f"<strong>0 out of {total} (0%)</strong>" in content.html
    for body in (content.text, content.html):
        assert as_of in body
        assert f"1 out of {total}" not in body
        assert "Oct 8, 2054" not in body and "October 8, 2054" not in body
