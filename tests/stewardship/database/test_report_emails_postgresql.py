"""Report emails end to end (#1786, delivery plan item 4).

The conversion's rewritten shared guards are the first to admit a report's
occurrence, outbox, pin and completion writes, so these tests send report
emails through the real scheduler producer, worker, mail dispatch and
settlement, and check the outcome each send records.
"""

from datetime import UTC, datetime

import pytest

from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailPreparation,
    ReportEmailSnapshot,
)
from parishkit.stewardship.weekly_delivery import WeeklyDeliveryMail

from .campaign_builders import campaign_clock
from .report_builders import (
    WEEKLY_BLOCKS,
    add_report,
    deliver_all,
    fake_provider,
    messages,
    prepare_due,
    run_finalizers,
)
from .test_family_mail_worker_postgresql import (  # noqa: F401
    dispatch_worker,
    family_mail,
)

pytestmark = pytest.mark.django_db(transaction=True)
# A Monday of the fixture campaign (October 1 to 31, 2054), after 08:00.
MONDAY = datetime(2054, 10, 12, 14, tzinfo=UTC)


def weekly(store, campaign):
    """A report shaped as the converted Weekly, sending Mondays at 8:00 AM."""
    return add_report(
        store,
        campaign,
        name="Weekly report",
        blocks=WEEKLY_BLOCKS,
        content_required=True,
        schedule={
            "type": "repeat",
            "frequency": "weekly",
            "weekdays": [0],
            "from": "campaign_start",
            "until": "first_send_after_campaign_end",
            "time": "08:00:00",
        },
    )


def test_a_weekly_report_with_nothing_open_sends_with_its_funnel(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """Decision 16: the funnel always has content, so the send is not skipped."""
    harness, path = dispatch_worker
    identifier = weekly(harness.service.store, harness.campaign)
    with campaign_clock(MONDAY), fake_provider(monkeypatch) as sent:
        prepared = prepare_due()
        assert len(prepared) == 1
        preparation = ReportEmailPreparation.objects.get()
        assert preparation.phase == "complete"
        snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
        assert snapshot.has_content
        assert "No open requests." in snapshot.text
        deliver_all(harness, path)
        run_finalizers()
    assert len(sent) == 1 and isinstance(sent[0], WeeklyDeliveryMail)
    assert sent[0].subject.startswith("[TEST] ")
    assert [message.state for message in messages()] == ["delivered"]
    # The missed Monday before is coalesced into this one send, never a burst.
    occurrences = {
        row.slot: (row.state, row.reason)
        for row in ScheduleOccurrence.objects.filter(definition_id=identifier)
    }
    assert occurrences == {
        "2054-10-05": ("coalesced", "missed_report_recovery"),
        "2054-10-12": ("succeeded", "report_email_complete"),
    }
    fulfillments = {
        row.slot: row.disposition
        for row in ScheduleFulfillment.objects.filter(definition_id=identifier)
    }
    assert fulfillments == {"2054-10-05": "coalesced", "2054-10-12": "delivered"}
