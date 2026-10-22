"""Admin readiness predicts the report emails' coalescing and skipped sends.

Report emails replaced the Daily and Weekly digests (#1786): each report's
due sends are coalesced into one, one message per cohort address, unless
the send would be skipped as empty.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.campaigns.readiness_digests import digest_impact
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole

from .credential_builders import family_campaign
from .report_builders import WEEKLY_INSTANT as INSTANT
from .report_builders import add_digest, add_report
from .test_background_grants_postgresql import task_login
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)
ADMINS = ("admin@example.org", "second@example.org")


def test_web_preview_coalesces_all_daily_dates_and_weekly_dates(tmp_path):
    """Physical recipient messages are not confused with logical dates or slots."""
    store, campaign, _, _ = family_campaign(tmp_path)
    add_digest(store, campaign)
    add_digest(store, campaign, weekly=True)
    campaign.refresh_from_db()
    with task_login(ServiceRole.WEB), work_transaction():
        with CaptureQueriesContext(connection) as queries:
            result = digest_impact(
                campaign,
                cutoff=datetime(2054, 10, 20, 12, tzinfo=UTC),
                recipients=ADMINS,
            )
        # One message each to the fixture's one Administrator: the Weekly's
        # funnel always has content, so it is never empty (decision 16).
        assert result.daily_messages == 1 and result.weekly_messages == 1
        # 19 daily dates and two weekly ones, each coalesced into one send.
        assert result.coalesced_slots == 18 + 1
        assert result.empty_weekly_reports == 0 and result.blocked_groups == 0
        assert not any(
            '"text"' in row["sql"] or '"html"' in row["sql"] for row in queries
        )
    assert not ScheduleOccurrence.objects.exists()


def test_digest_preview_exact_boundary_changes_binding_even_with_same_mail_count(
    tmp_path,
):
    store, campaign, _, _ = family_campaign(tmp_path)
    add_digest(store, campaign)
    campaign.refresh_from_db()
    cutoff = datetime(2054, 10, 20, 4, 15, tzinfo=UTC)
    with work_transaction():
        before = digest_impact(
            campaign, cutoff=cutoff - timedelta(microseconds=1), recipients=ADMINS
        )
        after = digest_impact(campaign, cutoff=cutoff, recipients=ADMINS)
    assert before.daily_messages == after.daily_messages == 1
    assert after.coalesced_slots == before.coalesced_slots + 1
    assert before.digest != after.digest


def test_daily_preview_crosses_date_pages_without_multiplying_messages(tmp_path):
    """A 123-day backlog is one recovery send, not one message per page."""
    from ..campaign_factory import campaign as campaign_record
    from .campaign_builders import draft_campaign

    store, campaign, _ = draft_campaign(
        tmp_path, campaign_record(end_date="2055-04-30")
    )
    add_digest(store, campaign)
    campaign.refresh_from_db()
    with task_login(ServiceRole.WEB), work_transaction():
        result = digest_impact(
            campaign, cutoff=datetime(2055, 2, 1, 12, tzinfo=UTC), recipients=ADMINS
        )
    assert result.daily_messages == 1 and result.coalesced_slots == 122
    assert result.blocked_groups == 0
    assert not ScheduleOccurrence.objects.exists()


def test_a_report_with_nothing_to_list_counts_as_empty(live_response_service):
    """Only a report of list blocks that sends only with content is skipped.

    A report with just the open requests block and Send only when there is
    something to report makes no message while nothing is open, and its
    cohort's messages once a request is open (report-emails spec, "Sending
    only when there is content"); the count never reads a request's text.
    """
    harness = live_response_service
    store, campaign = harness.service.store, harness.campaign
    add_report(
        store,
        campaign,
        name="Open requests",
        blocks=[{"block": "additional_information", "version": 1, "options": {}}],
        content_required=True,
        schedule={
            "type": "repeat",
            "frequency": "weekly",
            "weekdays": [2],
            "from": "campaign_start",
            "until": "campaign_end",
            "time": "00:15:00",
        },
    )
    campaign.refresh_from_db()

    def impact():
        """The readiness impact at the fixture's later morning."""
        with task_login(ServiceRole.WEB), work_transaction():
            return digest_impact(campaign, cutoff=INSTANT, recipients=ADMINS)

    nothing = impact()
    assert (nothing.weekly_messages, nothing.empty_weekly_reports) == (0, 1)
    respond(harness, "Private actionable information")
    with CaptureQueriesContext(connection) as queries:
        found = impact()
    assert found.weekly_messages >= 1 and found.empty_weekly_reports == 0
    assert found.digest != nothing.digest
    reads = [row["sql"] for row in queries if row["sql"].startswith("SELECT")]
    assert not [sql for sql in reads if '"text"' in sql or '"html"' in sql]
