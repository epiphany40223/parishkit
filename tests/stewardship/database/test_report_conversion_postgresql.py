"""The conversion of the Daily and Weekly digests on an upgraded database (#1786).

A database seeded with both digests, their past sends' occurrences and
fulfillments and a skip date is converted by the offline conversion, under
its admin-recovery login: each definition keeps its UUID and history, its
next sends are the same instants and slot keys as before, and the report
pipeline then sends only what was still owed.
"""

from datetime import UTC, date, datetime

import pytest
from django.db import connection

from parishkit.stewardship.accounts.reports_conversion import converted_values
from parishkit.stewardship.accounts.reports_upgrade import digests_remain
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.schedule_evaluation import SchedulePlan
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
    ScheduleSelection,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailPreparation,
    ReportEmailSnapshot,
)

from .campaign_builders import campaign_clock, change
from .report_builders import (
    convert_reports,
    deliver_all,
    digest_history,
    digest_send,
    digests_admitted,
    fake_provider,
    prepare_due,
    run_finalizers,
    seed_digests,
    triggers_off,
)
from .test_background_grants_postgresql import task_login
from .test_family_mail_worker_postgresql import (  # noqa: F401
    dispatch_worker,
    family_mail,
)

pytestmark = pytest.mark.django_db(transaction=True)
NOW = datetime(2054, 10, 9, 15, tzinfo=UTC)


def plan_slots(definition, campaign, *, after=None):
    """The (slot, due) pairs the definition's current values give to the end.

    A Weekly digest's post-close send is included: today it is considered
    only while something is owed, and the converted Weekly makes it as an
    ordinary send (spec, "Conversion", "It sends every week").
    """
    plan = SchedulePlan.from_values(
        definition.current_revision.values, campaign.active_configuration.values
    )
    page = plan.page(
        through=datetime(2055, 1, 1, tzinfo=UTC),
        after=after,
        limit=100,
        include_final_weekly=True,
    )
    return [(slot.key, slot.due_at) for slot in page.slots]


def test_conversion_keeps_every_definition_its_history_and_its_sends(
    response_service, monkeypatch
):
    """The upgrade parity the spec's second gating test asks for."""
    store, campaign = response_service.service.store, response_service.campaign
    daily_id, weekly_id = seed_digests(store, campaign, monkeypatch)
    with digests_admitted(monkeypatch):
        result = change(
            store,
            store.active(),
            __import__("uuid").uuid4(),
            [
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": str(daily_id),
                    "values": {"skip_dates": ["2054-10-04"]},
                }
            ],
        )
    assert result.state == "applied"
    daily = ScheduleDefinition.objects.get(pk=daily_id)
    weekly = ScheduleDefinition.objects.get(pk=weekly_id)
    campaign.refresh_from_db()
    before = {
        daily_id: plan_slots(daily, campaign),
        weekly_id: plan_slots(weekly, campaign),
    }
    old_values = {
        daily_id: daily.current_revision.values,
        weekly_id: weekly.current_revision.values,
    }
    for day in (1, 2, 3):
        slot = date(2054, 10, day).isoformat()
        due = dict(before[daily_id])[slot]
        digest_history(daily_id, slot, due, disposition="delivered")
    due = dict(before[weekly_id])["2054-10-05"]
    digest_history(weekly_id, "2054-10-05", due, disposition="empty")
    assert digests_remain()
    with campaign_clock(NOW), task_login(ServiceRole.ADMIN_RECOVERY, exact=True):
        receipt = convert_reports()(store)
    assert receipt.state == "applied"
    assert not digests_remain()
    assert store.active().document() is not None
    for identifier in (daily_id, weekly_id):
        definition = ScheduleDefinition.objects.get(pk=identifier)
        assert definition.kind == "report"
        assert definition.current_revision.kind == "report"
        # The values are the conversion table's.
        assert definition.current_revision.values["kind"] == "report"
        assert definition.current_revision.values == converted_values(
            old_values[identifier],
            next(
                row["values"]
                for row in store.active().document()["sections"]["content"]
                if row["id"] == old_values[identifier]["template_version"]
            ),
        )
        # The same sends at the same instants and slot keys, skips kept.
        assert plan_slots(definition, campaign) == before[identifier]
        assert ScheduleSelection.objects.filter(
            definition=definition, selected_revision=definition.current_revision
        ).exists()
    assert "2054-10-04" not in dict(before[daily_id])
    # History is untouched and still names the same definitions and slots.
    assert sorted(
        ScheduleFulfillment.objects.filter(definition_id=daily_id).values_list(
            "slot", "disposition"
        )
    ) == [(f"2054-10-0{day}", "delivered") for day in (1, 2, 3)]
    assert list(
        ScheduleFulfillment.objects.filter(definition_id=weekly_id).values_list(
            "slot", "disposition"
        )
    ) == [("2054-10-05", "empty")]
    assert ScheduleOccurrence.objects.count() == 4
    assert (
        AuditEvent.objects.filter(event_type="operator_reports_converted").count() == 1
    )
    assert store.active().document()["sections"]["schedules"]
    # A second run has nothing left to convert.
    with task_login(ServiceRole.ADMIN_RECOVERY, exact=True):
        assert convert_reports()(store) is None


def test_conversion_waits_for_a_digest_send_in_progress(response_service, monkeypatch):
    """A pending digest send refuses the activation; nothing is converted."""
    store, campaign = response_service.service.store, response_service.campaign
    daily_id, _ = seed_digests(store, campaign, monkeypatch)
    daily = ScheduleDefinition.objects.get(pk=daily_id)
    slot, due = plan_slots(daily, campaign)[0]
    occurrence = digest_history(daily_id, slot, due, state="pending", disposition=None)
    with task_login(ServiceRole.ADMIN_RECOVERY, exact=True):
        receipt = convert_reports()(store)
    assert receipt.state == "failed"
    assert digests_remain()
    assert ScheduleDefinition.objects.get(pk=daily_id).kind == "daily_digest"
    # Once the send settles, a new run converts.
    from .report_builders import triggers_off

    with triggers_off():
        ScheduleOccurrence.objects.filter(pk=occurrence.pk).update(state="succeeded")
    with task_login(ServiceRole.ADMIN_RECOVERY, exact=True):
        assert convert_reports()(store).state == "applied"
    assert not digests_remain()


# The morning of October 9, 2054, after the converted Daily's 07:00 send.
DAILY_MORNING = datetime(2054, 10, 9, 12, tzinfo=UTC)
EDITED_INTRO = "<p>Team: the {{ campaign_name }} figures.</p>"


def legacy_envelope(store, campaign, sent, *, testing, intro=EDITED_INTRO):
    """The subject and intro the Daily digest's envelope gave the same body.

    ``render_digest_envelope`` (the digest pipeline's) named the report
    "Daily campaign digest — <day>" and put the template's intro before the
    compiled body, leaving out an intro that still holds a built-in default
    (``default_daily_intro``); the converted report's own subject and intro
    must give the same subject, HTML and text around the same body.
    """
    from parishkit.stewardship.jobs.campaign_mail_values import (
        campaign_values,
        document_parish,
    )
    from parishkit.stewardship.jobs.digest_content import identified_subject
    from parishkit.stewardship.web.content import prepare_content, render_template

    names = campaign_values(
        parish=document_parish(store.active().document()),
        campaign=campaign.active_configuration.values,
    )
    from parishkit.stewardship.accounts.content_defaults import EMAILS

    subject = identified_subject(
        render_template(EMAILS["daily_digest"].subject, names, subject=True),
        "Daily campaign digest — October 8, 2054",
        campaign=" ".join(names.get("campaign_name", "").split()),
        parish=" ".join(names.get("parish_name", "").split()),
    )
    from parishkit.stewardship.jobs.digest_content import default_daily_intro

    prepared = prepare_content(intro)
    intro_html = render_template(prepared.html, names, html=True)
    intro_text = render_template(prepared.text, names)
    if default_daily_intro(prepared.html, prepared.text):
        intro_html = intro_text = ""
    return ("[TEST] " if testing else "") + subject, intro_html, intro_text


@pytest.mark.parametrize("edited", [True, False], ids=["edited", "default"])
def test_the_converted_daily_sends_the_digests_email(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    edited,
):
    """The converted Daily's first send is the Daily digest's email.

    Gating test (spec, "Gating tests"): the converted report keeps the
    digest's subject and its edited intro (a default intro is left out, as
    the digest left it out) around the body the compiler draws byte for
    byte as the digest did (``test_report_document``), at the same slot,
    and settles that slot as delivered.
    """
    from parishkit.stewardship.accounts.content_defaults import EMAILS

    intro = EDITED_INTRO if edited else EMAILS["daily_digest"].html
    from parishkit.stewardship.digest_delivery import DigestDeliveryMail

    harness, path = dispatch_worker
    store, campaign = harness.service.store, harness.campaign
    daily_id, _ = seed_digests(store, campaign, monkeypatch, daily_html=intro)
    daily = ScheduleDefinition.objects.get(pk=daily_id)
    slots = dict(plan_slots(daily, campaign))
    # Every earlier morning was sent before the upgrade.
    for day in range(1, 8):
        slot = date(2054, 10, day).isoformat()
        digest_history(daily_id, slot, slots[slot], disposition="delivered")
    with (
        campaign_clock(DAILY_MORNING),
        task_login(ServiceRole.ADMIN_RECOVERY, exact=True),
    ):
        assert convert_reports()(store).state == "applied"
    with campaign_clock(DAILY_MORNING), fake_provider(monkeypatch) as sent:
        prepare_due()
        deliver_all(harness, path)
        run_finalizers()
    daily_sends = [mail for mail in sent if isinstance(mail, DigestDeliveryMail)]
    assert len(daily_sends) == 1
    (mail,) = daily_sends
    preparation = ReportEmailPreparation.objects.get(definition_id=daily_id)
    snapshot = ReportEmailSnapshot.objects.get(preparation=preparation)
    subject, intro_html, intro_text = legacy_envelope(
        store, campaign, mail, testing=True, intro=intro
    )
    assert mail.subject == subject
    # The digest's Testing line, then its intro, then the compiled body.
    body_html = snapshot.html.removeprefix(intro_html)
    body_text = snapshot.text.removeprefix(intro_text + "\n\n" if intro_text else "")
    assert (body_html != snapshot.html) is edited
    assert (body_text != snapshot.text) is edited
    line = mail.html.split("</p>", 1)[0].removeprefix("<h2>TEST</h2><p>")
    assert line.startswith("TEST — sent to ") and " instead of Administrator " in line
    assert mail.html.endswith(intro_html + body_html)
    assert mail.text.endswith((intro_text + "\n\n" if intro_text else "") + body_text)
    assert (
        "Here is yesterday’s report; all data is as of the end of October 8, 2054"
        in (body_text)
    )
    fulfillments = dict(
        ScheduleFulfillment.objects.filter(definition_id=daily_id).values_list(
            "slot", "disposition"
        )
    )
    assert fulfillments["2054-10-08"] == "delivered"
    assert "2054-10-09" not in fulfillments


# A Monday morning after the converted Weekly's 08:00 send.
WEEKLY_MORNING = datetime(2054, 10, 12, 14, tzinfo=UTC)


def test_the_converted_weekly_sends_with_its_documented_changes(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """The converted Weekly's first send shows the spec's changes.

    Gating test (spec, "Conversion" and "Gating tests"): its default intro
    is reworded (decision 17), and with no open request (this fixture is in
    Testing, whose rehearsal answers are never listed) it still sends,
    saying so, with no corrections section (decisions 14 to 16). Its links
    open the send's saved copy. The listing of open requests is the
    report pipeline's suites' subject.
    """
    from parishkit.stewardship.reports.report_document import send_path
    from parishkit.stewardship.weekly_delivery import WeeklyDeliveryMail

    harness, path = dispatch_worker
    store, campaign = harness.service.store, harness.campaign
    _, weekly_id = seed_digests(store, campaign, monkeypatch)
    weekly = ScheduleDefinition.objects.get(pk=weekly_id)
    slots = dict(plan_slots(weekly, campaign))
    digest_history(weekly_id, "2054-10-05", slots["2054-10-05"], disposition="empty")
    with (
        campaign_clock(WEEKLY_MORNING),
        task_login(ServiceRole.ADMIN_RECOVERY, exact=True),
    ):
        assert convert_reports()(store).state == "applied"
    with campaign_clock(WEEKLY_MORNING), fake_provider(monkeypatch) as sent:
        prepare_due()
        deliver_all(harness, path)
        run_finalizers()
    weekly_sends = [mail for mail in sent if isinstance(mail, WeeklyDeliveryMail)]
    assert len(weekly_sends) == 1
    (mail,) = weekly_sends
    preparation = ReportEmailPreparation.objects.get(definition_id=weekly_id)
    assert "Weekly report" in mail.subject or "weekly report" in mail.subject
    assert "Here are the additional information requests that are still open." in (
        mail.text
    )
    assert "No open requests." in mail.text
    assert "correction" not in mail.text.lower()
    assert send_path(preparation.pk) in mail.html
    fulfillments = dict(
        ScheduleFulfillment.objects.filter(definition_id=weekly_id).values_list(
            "slot", "disposition"
        )
    )
    assert fulfillments == {"2054-10-05": "empty", "2054-10-12": "delivered"}


# The Daily's first morning and the Weekly's first Monday of the fixture.
DAILY_SLOT, WEEKLY_SLOT = "2054-10-01", "2054-10-05"


def finished_sends(store, campaign, monkeypatch, **send):
    """The two digests, each with one past send as the release before left it.

    Returns the Daily's and the Weekly's definition IDs and their sends.
    """
    daily_id, weekly_id = seed_digests(store, campaign, monkeypatch)
    sends = []
    for identifier, slot in ((daily_id, DAILY_SLOT), (weekly_id, WEEKLY_SLOT)):
        definition = ScheduleDefinition.objects.get(pk=identifier)
        due = dict(plan_slots(definition, campaign))[slot]
        sends.append(digest_send(identifier, slot, due, **send))
    return daily_id, weekly_id, sends


def converted(store):
    """Run the conversion as ``upgrade-reports`` does; its receipt."""
    with campaign_clock(NOW), task_login(ServiceRole.ADMIN_RECOVERY, exact=True):
        return convert_reports()(store)


def blocking(occurrence):
    """Whether the schedule work row reads the occurrence as blocking."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT blocking FROM stewardship_schedule_work_row WHERE id=%s",
            [occurrence.pk],
        )
        return cursor.fetchone()[0]


def assert_converted(receipt, *identifiers):
    """The conversion applied and left no digest definition behind."""
    assert receipt.state == "applied"
    assert not digests_remain()
    for identifier in identifiers:
        assert ScheduleDefinition.objects.get(pk=identifier).kind == "report"


@pytest.mark.parametrize("settled_by", ["prepare", "finalize", "delivery"])
def test_conversion_admits_finished_daily_and_weekly_sends(
    response_service, monkeypatch, settled_by
):
    """A finished digest send never blocks its definition's conversion (#1839).

    The conversion retypes each definition to a report before replacing its
    revision, so the schedule work row must still prove a Daily or Weekly
    send's own task, whichever task settled it: the preparation's, the
    finalizer's or a recipient message's delivery.
    """
    store, campaign = response_service.service.store, response_service.campaign
    daily_id, weekly_id, sends = finished_sends(
        store, campaign, monkeypatch, settled_by=settled_by
    )
    assert not any(blocking(send) for send in sends)
    assert_converted(converted(store), daily_id, weekly_id)
    assert not any(blocking(send) for send in sends)
    assert (
        ScheduleFulfillment.objects.filter(
            definition_id__in=(daily_id, weekly_id), disposition="delivered"
        ).count()
        == 2
    )


# Settled outcomes other than delivery that Production may hold.
OUTCOMES = {
    "failed": {
        "state": "failed",
        "task_state": "failed",
        "message_state": "permanent_failure",
        "disposition": None,
    },
    "skipped": {
        "state": "skipped",
        "task_state": "cancelled",
        "message_state": "cancelled",
        "disposition": None,
    },
}


@pytest.mark.parametrize("outcome", sorted(OUTCOMES))
def test_conversion_admits_failed_and_skipped_sends(
    response_service, monkeypatch, outcome
):
    """A failed or skipped digest send is finished; the conversion proceeds."""
    store, campaign = response_service.service.store, response_service.campaign
    daily_id, weekly_id, sends = finished_sends(
        store, campaign, monkeypatch, **OUTCOMES[outcome]
    )
    assert_converted(converted(store), daily_id, weekly_id)
    assert not any(blocking(send) for send in sends)


def test_conversion_admits_a_daily_with_no_sends(response_service, monkeypatch):
    """A Daily that never sent converts beside a Weekly that did."""
    store, campaign = response_service.service.store, response_service.campaign
    daily_id, weekly_id = seed_digests(store, campaign, monkeypatch)
    weekly = ScheduleDefinition.objects.get(pk=weekly_id)
    digest_send(weekly_id, WEEKLY_SLOT, dict(plan_slots(weekly, campaign))[WEEKLY_SLOT])
    assert_converted(converted(store), daily_id, weekly_id)
    assert not ScheduleOccurrence.objects.filter(definition_id=daily_id).exists()


def test_conversion_admits_finished_sends_while_delivery_is_paused(
    response_service, monkeypatch
):
    """A campaign whose delivery is paused converts once its sends finished."""
    store, campaign = response_service.service.store, response_service.campaign
    daily_id, weekly_id, _ = finished_sends(store, campaign, monkeypatch)
    with triggers_off(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_campaign SET delivery_paused=true,"
            " paused_at=clock_timestamp(), pause_actor_id=gen_random_uuid(),"
            " pause_version=pause_version+1 WHERE id=%s",
            [campaign.pk],
        )
    assert_converted(converted(store), daily_id, weekly_id)


# Each way a digest send is still in flight after the conversion, written
# over a finished send with the triggers off: the statements change its
# occurrence %(o)s, its preparation task %(t)s or its message %(m)s.
IN_FLIGHT = {
    # A pending send whose preparation task's worker was lost.
    "abandoned_task": (
        "UPDATE stewardship_schedule_occurrence SET state='pending' WHERE id=%(o)s",
        "UPDATE stewardship_task_run SET state='abandoned' WHERE id=%(t)s",
    ),
    # A message handed to the provider with no answer yet.
    "submitting_message": (
        "UPDATE stewardship_outbox_message SET state='submitting',"
        " finished_at=NULL WHERE id=%(m)s",
    ),
    # A send that names another send's task is foreign work, finished or not.
    "foreign_task": (
        "UPDATE stewardship_schedule_occurrence SET task_id=%(f)s WHERE id=%(o)s",
    ),
}


@pytest.mark.parametrize("case", sorted(IN_FLIGHT))
def test_a_digest_send_in_flight_still_blocks_the_converted_definition(
    response_service, monkeypatch, case
):
    """The converted definitions' digest sends block while still in flight.

    Only finished work is admitted (#1839): a send whose task is running or
    lost, whose message is with the provider, or whose task is not its own
    still blocks a replacement of the report's revision.
    """
    store, campaign = response_service.service.store, response_service.campaign
    daily_id, weekly_id, sends = finished_sends(
        store, campaign, monkeypatch, settled_by="prepare"
    )
    assert_converted(converted(store), daily_id, weekly_id)
    daily, weekly = sends
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.task_id,r.outbox_id FROM stewardship_daily_digest_preparation p"
            " JOIN stewardship_daily_digest_snapshot s ON s.preparation_id=p.id"
            " JOIN stewardship_daily_digest_ready y ON y.snapshot_id=s.id"
            " JOIN stewardship_daily_digest_recipient r ON r.ready_id=y.id"
            " WHERE p.occurrence_id=%s",
            [daily.pk],
        )
        task, message = cursor.fetchone()
    names = {"o": daily.pk, "t": task, "m": message, "f": weekly.task_id}
    with triggers_off(), connection.cursor() as cursor:
        for statement in IN_FLIGHT[case]:
            cursor.execute(statement, names)
    assert blocking(daily)
    assert not blocking(weekly)


def test_the_converted_daily_sends_after_real_digest_history(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """Upgrade path: delivered Daily and Weekly sends, then the next report.

    Each earlier morning and the first Monday were sent, and settled by
    their last message's delivery, as the release before left them; the
    conversion applies and the converted Daily sends the next morning.
    """
    harness, path = dispatch_worker
    store, campaign = harness.service.store, harness.campaign
    daily_id, weekly_id = seed_digests(store, campaign, monkeypatch)
    daily = ScheduleDefinition.objects.get(pk=daily_id)
    weekly = ScheduleDefinition.objects.get(pk=weekly_id)
    slots = dict(plan_slots(daily, campaign))
    for day in range(1, 8):
        slot = date(2054, 10, day).isoformat()
        digest_send(daily_id, slot, slots[slot])
    digest_send(weekly_id, WEEKLY_SLOT, dict(plan_slots(weekly, campaign))[WEEKLY_SLOT])
    with (
        campaign_clock(DAILY_MORNING),
        task_login(ServiceRole.ADMIN_RECOVERY, exact=True),
    ):
        assert_converted(convert_reports()(store), daily_id, weekly_id)
    with campaign_clock(DAILY_MORNING), fake_provider(monkeypatch):
        prepare_due()
        deliver_all(harness, path)
        run_finalizers()
    fulfillments = dict(
        ScheduleFulfillment.objects.filter(definition_id=daily_id).values_list(
            "slot", "disposition"
        )
    )
    assert fulfillments["2054-10-08"] == "delivered"
    assert "2054-10-09" not in fulfillments
