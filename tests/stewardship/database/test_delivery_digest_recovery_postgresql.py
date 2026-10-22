"""Resume preserves per-recipient delivery proof across report replacement.

The reports are report emails (#1786), which replaced the Daily and Weekly
digests: a report shaped as the converted Daily, two Administrators in its
cohort, sent by the real report producer, worker and mail dispatch role.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest

from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.campaigns.delivery_control_models import (
    DeliveryControlCommand,
)
from parishkit.stewardship.campaigns.recovery_coverage import covered_dates
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.reports.report_email_models import ReportEmailRecipient
from parishkit.stewardship.storage import StaleRecordError

from .campaign_builders import campaign_clock
from .report_builders import add_digest, begin, execute, prepare_due, produce, set_user
from .test_background_grants_postgresql import task_login
from .test_delivery_control_postgresql import accepted_sender_check
from .test_family_mail_dispatch_postgresql import claim
from .test_setup_mail_views_postgresql import web_login
from .test_withdrawal_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    ready_links,
    scheduled,
    setup_service,
)

pytestmark = pytest.mark.django_db(transaction=True)
SECOND = "second@example.org"


def latest_recipients():
    """The recipients of the newest report send, by address."""
    newest = ReportEmailRecipient.objects.order_by("-snapshot__created_at").first()
    return {
        row.address: row
        for row in ReportEmailRecipient.objects.filter(
            snapshot_id=newest.snapshot_id
        ).select_related("outbox", "snapshot__preparation")
    }


def accept(message):
    """Deliver one report message through the exact mail dispatch role."""
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        assert begin(message, execution) is not None
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )


def test_prepared_report_resume_keeps_accepted_recipient_delivery(
    scheduled,  # noqa: F811
    monkeypatch,
    tmp_path,
):
    """One accepted message is never resent when its held sibling is replaced.

    Resume coalesces the held send into a recovery send covering the same
    report days; the accepted message keeps its delivery, the held one is
    cancelled, and an uncertain outcome of the recovery send blocks resume.
    """
    item = scheduled
    store = item.arguments[1].store
    add_digest(store, item.campaign)
    set_user(store, "second@example.org", "administrator", name="Second, Admin")
    due = item.campaign.active_configuration.starts_at + timedelta(days=2, hours=12)
    with campaign_clock(due):
        statuses = produce()
        assert statuses
        with web_login():
            _, token = commands.preview_pause(
                *item.arguments, reason="Inspect report preparation"
            )
            commands.confirm(*item.arguments, token=token)
        accepted_sender_check(item, monkeypatch, tmp_path)
        with web_login(), pytest.raises(StaleRecordError, match="report preparation"):
            commands.preview_resume(*item.arguments, reason="Too early")
        for status in statuses:
            execute(status)
        with web_login():
            _, token = commands.preview_resume(
                *item.arguments, reason="Finish prepared report"
            )
            commands.confirm(*item.arguments, token=token)
        # The same dates now belong to a new recovery send. The actual worker
        # prepares its recipients; no test fabricates snapshot evidence.
        prepare_due()
        latest = latest_recipients()
        first = next(row for address, row in latest.items() if address != SECOND)
        first, second = first.outbox, latest[SECOND].outbox
        accept(first)
        first.refresh_from_db()
        accepted_version = first.version
        with web_login():
            _, token = commands.preview_pause(
                *item.arguments, reason="Combine held sibling"
            )
            commands.confirm(*item.arguments, token=token)
            impact = commands.digest_recovery_impact(item.campaign.pk)
            assert impact["prepared"] == impact["selected"] == 1
            assert impact["blocked"] == 0
        old_occurrence = ScheduleOccurrence.objects.get(
            pk=latest[SECOND].snapshot.preparation.occurrence_id
        )
        expected_dates = covered_dates(old_occurrence.pk)
        accepted_sender_check(item, monkeypatch, tmp_path)
        with web_login():
            _, token = commands.preview_resume(
                *item.arguments, reason="Release one owed recipient"
            )
            original_create = DeliveryControlCommand.objects.create

            def interrupted(**values):
                """Crash after the real SQL effects but before the outer commit."""
                original_create(**values)
                raise RuntimeError("Synthetic lost request before commit")

            with (
                patch.object(
                    DeliveryControlCommand.objects, "create", side_effect=interrupted
                ),
                pytest.raises(RuntimeError, match="before commit"),
            ):
                commands.confirm(*item.arguments, token=token)
        item.campaign.refresh_from_db()
        second.refresh_from_db()
        old_occurrence.refresh_from_db()
        assert item.campaign.delivery_paused and second.pause_hold_id
        assert second.state == old_occurrence.state == "pending"
        with web_login():
            commands.confirm(*item.arguments, token=token)
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.state == "delivered" and first.version == accepted_version
        assert second.state == "cancelled" and second.pause_hold_id is None
        assert TaskRun.objects.get(pk=second.task_id).state == "cancelled"
        old_occurrence.refresh_from_db()
        assert old_occurrence.state == "coalesced"
        selected = ScheduleOccurrence.objects.get(pk=old_occurrence.replacement_id)
        assert covered_dates(selected.pk) == expected_dates
        prepare_due()
        recipients = latest_recipients()
        # A report's recovery send is a new report to its whole cohort: unlike
        # the digests, whose recipients reused accepted coverage, the
        # Administrator who accepted the earlier send gets one too (#1786; the
        # documented difference in the report-emails spec, "Pause and resume").
        # The accepted message itself is never resent or changed.
        assert set(recipients) == set(latest) and all(
            row.disposition == "queued" and row.outbox.state == "pending"
            for row in recipients.values()
        )
        assert first.pk not in {row.outbox_id for row in recipients.values()}
        assert recipients[SECOND].snapshot.preparation.occurrence_id == selected.pk
        replacement = recipients[SECOND].outbox
        assert replacement.pk != second.pk and replacement.state == "pending"
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(replacement)
            assert begin(replacement, execution) is not None
            finish_submission(
                replacement.pk,
                execution.claim,
                FamilyDeliveryResult(Status.UNKNOWN, 1),
            )
        with web_login():
            _, token = commands.preview_pause(
                *item.arguments, reason="Reconcile unknown provider outcome"
            )
            commands.confirm(*item.arguments, token=token)
        accepted_sender_check(item, monkeypatch, tmp_path)
        with web_login():
            assert commands.digest_recovery_impact(item.campaign.pk)["blocked"] == 1
            with pytest.raises(StaleRecordError, match="resolved delivery"):
                commands.preview_resume(
                    *item.arguments, reason="Must not resend uncertain mail"
                )
        replacement = OutboxMessage.objects.get(pk=replacement.pk)
        assert replacement.state == "delivery_unknown" and replacement.attempt == 1
