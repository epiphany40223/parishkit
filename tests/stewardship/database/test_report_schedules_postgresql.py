"""Report email schedules end to end (#1786, delivery plan item 4).

Each test sends report emails through the real scheduler producer, worker,
mail dispatch and settlement over several mornings of the fixture campaign
(October 1 to 31, 2054, in New York, so a 7:00 AM send is 11:00 UTC), and
checks which sends were made: a pause makes no catch-up burst, Send now
neither moves nor fulfills a scheduled send, the converted Daily and Weekly
make their sends after the campaign closes, and a skipped date skips exactly
that send.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.report_email_manual import request_send_now
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailManualRequest,
)

from .campaign_builders import campaign_clock, change, close_campaign
from .report_builders import (
    add_digest,
    deliver_all,
    edit_report,
    execute,
    fake_provider,
    prepare_due,
    run_finalizers,
)
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_worker_postgresql import (  # noqa: F401
    dispatch_worker,
    family_mail,
)
from .test_policy_postgresql import user

pytestmark = pytest.mark.django_db(transaction=True)


def at(day, hour=16, month=10):
    """An instant of 2054 in UTC: by default the afternoon, after 7:00 AM sends."""
    return datetime(2054, month, day, hour, tzinfo=UTC)


def send_round(harness, path, monkeypatch, instant):
    """Make and deliver every report send due at ``instant``; the mail sent."""
    with campaign_clock(instant), fake_provider(monkeypatch) as sent:
        prepare_due()
        deliver_all(harness, path)
        run_finalizers()
    return sent


def outcomes(identifier):
    """Each slot of the report: (occurrence state, fulfillment disposition)."""
    settled = dict(
        ScheduleFulfillment.objects.filter(definition_id=identifier).values_list(
            "slot", "disposition"
        )
    )
    return {
        row.slot: (row.state, settled.get(row.slot))
        for row in ScheduleOccurrence.objects.filter(definition_id=identifier)
    }


def pause_range(start, end=None):
    """One pause interval in the stored form (``report_values.instant``)."""
    return {"start": start.isoformat(), "end": end and end.isoformat()}


def test_a_paused_then_resumed_report_does_not_catch_up(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """Decision 8: sends due while paused are never sent nor coalesced.

    The converted Daily sends on October 2, is paused that afternoon and
    resumed on October 5's afternoon; its October 3, 4 and 5 sends fell in
    the pause. After Resume nothing is owed, and October 6's send is an
    ordinary one, not a recovery send covering the paused days.
    """
    harness, path = dispatch_worker
    store, campaign = harness.service.store, harness.campaign
    with campaign_clock(at(2)):
        identifier = add_digest(store, campaign, time="07:00:00")
    assert len(send_round(harness, path, monkeypatch, at(2))) == 1
    with campaign_clock(at(2, 18)):
        edit_report(store, identifier, paused_ranges=[pause_range(at(2, 18))])
    assert send_round(harness, path, monkeypatch, at(4)) == []
    with campaign_clock(at(5, 18)):
        edit_report(
            store, identifier, paused_ranges=[pause_range(at(2, 18), at(5, 18))]
        )
    assert send_round(harness, path, monkeypatch, at(5, 19)) == []
    (mail,) = send_round(harness, path, monkeypatch, at(6))
    assert "recovery" not in mail.subject
    # The report day of each send is its slot: October 6's reports on the 5th.
    assert outcomes(identifier) == {
        "2054-10-01": ("succeeded", "delivered"),
        "2054-10-05": ("succeeded", "delivered"),
    }
    # The closed interval is kept in the report's current values.
    definition = ScheduleDefinition.objects.get(pk=identifier)
    assert definition.current_revision.values["paused_ranges"] == [
        pause_range(at(2, 18), at(5, 18))
    ]


def send_now(harness, principal, identifier, command):
    """Send the report now as ``principal``, through the Web identity."""
    definition = ScheduleDefinition.objects.get(pk=identifier)
    with task_login(ServiceRole.WEB, exact=True):
        return request_send_now(
            harness.service.store,
            principal.pk,
            harness.campaign.pk,
            definition.pk,
            definition.current_revision_id,
            command_id=command,
            configuration_id=SystemConfiguration.objects.get().active_configuration_id,
        )


def test_send_now_neither_moves_nor_fulfills_a_scheduled_send(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """Spec, "Send now": one manual occurrence, no scheduled slot touched.

    A Weekly report (Wednesdays) is sent now on Monday, October 5; its
    Wednesday, October 7 send is still made at its time, as an ordinary
    send, and only that slot is fulfilled.
    """
    harness, path = dispatch_worker
    store, campaign = harness.service.store, harness.campaign
    with campaign_clock(at(5)):
        identifier = add_digest(store, campaign, weekly=True, time="07:00:00")
    assert send_round(harness, path, monkeypatch, at(5)) == []
    principal, command = user("admin@example.org"), uuid4()
    with campaign_clock(at(5)), fake_provider(monkeypatch) as sent:
        task = send_now(harness, principal, identifier, command)
        execute(task)
        # Replaying the same command returns its original send.
        replay = send_now(harness, principal, identifier, command)
        assert replay.run_id == task.run_id
        deliver_all(harness, path)
        run_finalizers()
    assert len(sent) == 1 and "manual" in sent[0].subject
    assert ReportEmailManualRequest.objects.count() == 1
    (manual,) = ScheduleOccurrence.objects.filter(definition_id=identifier)
    assert manual.slot.startswith("manual:") and manual.state == "succeeded"
    assert not ScheduleFulfillment.objects.filter(definition_id=identifier).exists()
    # Nothing scheduled is due before Wednesday's time, then it is sent.
    assert send_round(harness, path, monkeypatch, at(7, 10)) == []
    (mail,) = send_round(harness, path, monkeypatch, at(7))
    assert "manual" not in mail.subject and "recovery" not in mail.subject
    scheduled = {
        slot: outcome
        for slot, outcome in outcomes(identifier).items()
        if not slot.startswith("manual:")
    }
    assert scheduled == {"2054-10-07": ("succeeded", "delivered")}


def test_the_converted_reports_send_after_the_campaign_closes(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """Spec, "Sends after the campaign ends": a closed campaign keeps sending.

    The converted Daily's last send is the day after the campaign's last
    day (Sunday, November 1, reporting on October 31); the converted
    Weekly's is its first send after that day (Wednesday, November 4). The
    campaign is live in Production and closed at its end; the sends missed
    while nothing ran are coalesced into the first send after close.
    """
    harness, path = dispatch_worker
    harness = activate_response_service(harness)
    store, campaign = harness.service.store, harness.campaign
    daily = add_digest(store, campaign, time="07:00:00")
    weekly = add_digest(store, campaign, weekly=True, time="07:00:00")
    close_campaign(campaign, uuid4())
    assert campaign.state == "closed"
    first = send_round(harness, path, monkeypatch, at(1, month=11))
    assert len(first) == 2
    assert outcomes(daily)["2054-10-31"] == ("succeeded", "delivered")
    assert ScheduleOccurrence.objects.get(
        definition_id=daily, slot="2054-10-31"
    ).due_at == at(1, 12, month=11)
    assert "2054-11-04" not in outcomes(weekly)
    # Wednesday, November 4: the Weekly's post-close send, and nothing else.
    (mail,) = send_round(harness, path, monkeypatch, at(4, month=11))
    assert "recovery" not in mail.subject
    assert outcomes(weekly)["2054-11-04"] == ("succeeded", "delivered")
    assert ScheduleOccurrence.objects.get(
        definition_id=weekly, slot="2054-11-04"
    ).due_at == at(4, 12, month=11)
    # Neither report sends again.
    assert send_round(harness, path, monkeypatch, at(12, month=11)) == []


def test_a_converted_reports_skip_date_skips_exactly_that_send(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """A converted Daily's skip date skips exactly that send, nothing more.

    Skipping October 4 (the send on October 5) leaves the sends before and
    after it, and October 6's is not a recovery send.
    """
    harness, path = dispatch_worker
    store, campaign = harness.service.store, harness.campaign
    with campaign_clock(at(2)):
        identifier = add_digest(store, campaign, time="07:00:00")
        patch = [
            {
                "operation": "update",
                "section": "schedules",
                "id": str(identifier),
                "values": {"skip_dates": ["2054-10-04"]},
            }
        ]
        assert change(store, store.active(), uuid4(), patch).state == "applied"
    values = ScheduleDefinition.objects.get(pk=identifier).current_revision.values
    assert values["kind"] == "report" and values["skip_dates"] == ["2054-10-04"]
    for day in (2, 3, 4):
        assert len(send_round(harness, path, monkeypatch, at(day))) == 1
    assert send_round(harness, path, monkeypatch, at(5)) == []
    (mail,) = send_round(harness, path, monkeypatch, at(6))
    assert "recovery" not in mail.subject
    assert outcomes(identifier) == {
        slot: ("succeeded", "delivered")
        for slot in ("2054-10-01", "2054-10-02", "2054-10-03", "2054-10-05")
    }
