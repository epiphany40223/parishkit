"""Outgoing mail's sending limit note under the real web login (#382 M3a).

The note reads the mail consumers' service status records and the Family
emails waiting on a longer retry backoff, through columns the web login
already reads; it never reads a refused address.
"""

from datetime import timedelta

import pytest
from django.db import connection, transaction

from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.delivery_reads import read_sending_holds
from parishkit.stewardship.service_status import ServiceStatusReporter

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_mail_daily_limit_postgresql import submit
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def holds(now=None):
    """Read the note's facts as the Outgoing mail page does (at ``now``)."""
    with web_login():
        return read_sending_holds(now or database_now())


def report(process, sender):
    """One mail consumer reports ``sender`` (state, seconds until) for itself."""
    reporter = ServiceStatusReporter(
        "mail-dispatch", process=process, sender=lambda: sender
    )
    with task_login(ServiceRole.MAIL_DISPATCH):
        assert reporter.report(connect=True)


@pytest.mark.parametrize(
    "result,throttled",
    [
        # Every address refused temporarily: the 15-minute throttle step.
        (FamilyDeliveryResult(Status.TRANSIENT, 1, transient=(0,)), True),
        # A Gmail rate limit answering this one message waits 15 minutes too.
        (FamilyDeliveryResult(Status.TRANSIENT, 1, limit="rate"), True),
        # An ordinary temporary refusal retries within the usual schedule.
        (FamilyDeliveryResult(Status.TRANSIENT, 1), False),
    ],
)
def test_only_a_longer_backoff_is_named(family_mail, result, throttled):  # noqa: F811
    """A throttled email is counted with its due time; a usual retry is not."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(family_mail)
        assert holds() is None
        assert submit(message, family_mail, result).state.value == "retry_wait"
    message.refresh_from_db()
    found = holds()
    if not throttled:
        assert found is None
        return
    assert found["throttled"] == 1 and found["throttled_due"] == message.not_before
    assert not found["daily_limit"] and not found["gmail_held"]
    # Once the retry time has passed, the email is still counted but no
    # past time is named.
    late = holds(message.not_before + timedelta(seconds=1))
    assert late["throttled"] == 1 and late["throttled_due"] is None


def test_only_the_current_campaign_and_mode_are_counted(family_mail):  # noqa: F811
    """A throttled email of another mode or campaign is not the page's concern."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(family_mail)
        result = FamilyDeliveryResult(Status.TRANSIENT, 1, transient=(0,))
        assert submit(message, family_mail, result).state.value == "retry_wait"
    assert holds()["throttled"] == 1
    configuration = SystemConfiguration.objects.get()
    assert configuration.mode == message.mode == "testing"
    for change in (dict(mode="production"), dict(current_campaign=None)):
        # The runtime row's own guard admits only real mode and campaign
        # transitions; this test only needs another value to read.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_system_configuration DISABLE TRIGGER USER"
            )
            SystemConfiguration.objects.update(**change)
            assert holds() is None
            transaction.set_rollback(True)
    assert holds()["throttled"] == 1


def test_running_senders_report_the_daily_and_gmail_limits():
    """The sender states come from the consumers' own status records."""
    report("main", ("running", None))
    assert holds() is None
    report("main", ("daily_limit", None))
    found = holds()
    assert found["daily_limit"] and not found["gmail_held"]
    assert found["gmail_until"] is None and found["throttled"] == 0
    report("mail", ("gmail_held", 900.0))
    found = holds()
    assert found["daily_limit"] and found["gmail_held"]
    now = database_now()
    assert (
        timedelta(minutes=14)
        < found["gmail_until"] - now
        <= timedelta(minutes=15, seconds=5)
    )
