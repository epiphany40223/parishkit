"""Admin readiness predicts actual digest coalescing and recipient suppression."""

from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.campaigns.readiness_digests import digest_impact
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.readiness_weekly import weekly_message_count
from parishkit.stewardship.reports.weekly_models import WeeklyDigestRecipient

from .campaign_builders import campaign_clock
from .credential_builders import family_campaign
from .test_background_grants_postgresql import task_login
from .test_digest_schedule_planning_postgresql import add_digest
from .test_weekly_capture_postgresql import INSTANT
from .test_weekly_coverage_postgresql import accepted, publish
from .test_weekly_fanout_postgresql import captured
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)
ADMINS = ("admin@example.org", "second@example.org")


def test_web_preview_coalesces_all_daily_dates_and_omits_empty_weekly(tmp_path):
    """Physical recipient messages are not confused with logical dates or slots."""
    store, campaign, _, _ = family_campaign(tmp_path)
    add_digest(store, campaign)
    add_digest(store, campaign, weekly=True)
    campaign.refresh_from_db()
    with task_login(ServiceRole.WEB), work_transaction():
        with CaptureQueriesContext(connection) as queries:
            result = digest_impact(
                campaign,
                cutoff=datetime(2026, 10, 20, 12, tzinfo=UTC),
                recipients=ADMINS,
            )
        assert result.daily_messages == 2 and result.weekly_messages == 0
        assert result.coalesced_slots == 20  # 19 daily dates + one old weekly date.
        assert result.empty_weekly_reports == 1 and result.blocked_groups == 0
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
    cutoff = datetime(2026, 10, 20, 4, 15, tzinfo=UTC)
    with work_transaction():
        before = digest_impact(
            campaign, cutoff=cutoff - timedelta(microseconds=1), recipients=ADMINS
        )
        after = digest_impact(campaign, cutoff=cutoff, recipients=ADMINS)
    assert before.daily_messages == after.daily_messages == 2
    assert after.coalesced_slots == before.coalesced_slots + 1
    assert before.digest != after.digest


def test_daily_preview_crosses_date_pages_without_multiplying_messages(tmp_path):
    """A 123-day backlog is one recovery digest, not one message per page."""
    from ..campaign_factory import campaign as campaign_record
    from .campaign_builders import draft_campaign

    store, campaign, _ = draft_campaign(
        tmp_path, campaign_record(end_date="2027-04-30")
    )
    add_digest(store, campaign)
    campaign.refresh_from_db()
    with task_login(ServiceRole.WEB), work_transaction():
        result = digest_impact(
            campaign, cutoff=datetime(2027, 2, 1, 12, tzinfo=UTC), recipients=ADMINS
        )
    assert result.daily_messages == 2 and result.coalesced_slots == 123
    assert result.blocked_groups == 0
    assert not ScheduleOccurrence.objects.exists()


def test_weekly_preview_uses_actual_per_admin_acceptance_without_reading_prose(
    live_response_service,
):
    """Queued delivery covers nothing; accepted items suppress only that recipient."""
    harness = live_response_service
    respond(harness, "Private actionable information")
    with campaign_clock(INSTANT):
        claim, snapshot = captured(harness, additional_admins=(ADMINS[1],))
        publish(claim)
        fingerprints = []
        with task_login(ServiceRole.WEB), work_transaction():
            assert (
                weekly_message_count(
                    harness.campaign.pk, ADMINS, bind=fingerprints.append
                )
                == 2
            )
        accepted(
            WeeklyDigestRecipient.objects.get(snapshot=snapshot, address=ADMINS[0])
        )
        with task_login(ServiceRole.WEB), work_transaction():
            with CaptureQueriesContext(connection) as queries:
                assert (
                    weekly_message_count(
                        harness.campaign.pk, ADMINS, bind=fingerprints.append
                    )
                    == 1
                )
            assert not any(
                '"text"' in row["sql"]
                or '"html"' in row["sql"]
                or '"observation"' in row["sql"]
                for row in queries
            )
        accepted(
            WeeklyDigestRecipient.objects.get(snapshot=snapshot, address=ADMINS[1])
        )
        with task_login(ServiceRole.WEB), work_transaction():
            assert (
                weekly_message_count(
                    harness.campaign.pk, ADMINS, bind=fingerprints.append
                )
                == 0
            )
        assert "Private actionable information" not in repr(fingerprints)
