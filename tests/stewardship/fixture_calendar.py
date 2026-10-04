"""The synthetic fixture campaign's calendar, kept far ahead of the real date.

Database fixtures populate Families and promote source data on the real
``statement_timestamp()`` and then pin the campaign clock to the fixture
campaign's instants. That is only monotonic while the real date precedes the
fixture campaign, so the campaign timeline (campaign and schedule dates, the
proposed financial period, ``year_label`` and campaign-clock pins) lies in
2054. Source history stays in the real past (comparison periods, giving and
pledge dates, source ``as_of`` dates, birth dates), because source refreshes
clamp giving windows to the real snapshot date. See issue #421.

The timeline was moved from 2026 by exactly 28 years, which keeps weekdays,
leap years and America/New_York DST dates unchanged. When the guard test in
``test_fixture_calendar.py`` starts failing, shift the timeline by another 28
years the same way.
"""

from datetime import date, timedelta

# The shared fixture campaign's first day (``campaign_factory.campaign()``).
# The factories spell it literally; the guard test asserts they agree.
EPOCH = date(2054, 10, 1)
# The earliest shifted date a database test runs on (a year-long campaign in
# test_catchup_preparation_postgresql.py). The guard must stay ahead of this,
# not only of EPOCH; lower it if an earlier shifted date is added.
EARLIEST = date(2054, 1, 1)
# The guard fails once the real date is this close to EARLIEST.
NOTICE = timedelta(days=366)
