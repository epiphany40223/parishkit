"""Resume preserves per-recipient delivery proof across digest replacement."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.campaigns.delivery_control_models import (
    DeliveryControlCommand,
)
from parishkit.stewardship.campaigns.recovery_coverage import covered_dates
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.reports.digest_fanout import fanout_daily
from parishkit.stewardship.reports.digest_models import DailyDigestRecipient
from parishkit.stewardship.reports.digest_tasks import daily_handler
from parishkit.stewardship.storage import StaleRecordError

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_daily_digest_dispatch_postgresql import begin
from .test_daily_digest_fanout_postgresql import ready_report
from .test_daily_digest_planning_postgresql import allocate
from .test_daily_digest_tasks_postgresql import execute
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


def test_prepared_digest_resume_reuses_accepted_recipient_coverage(
    scheduled,  # noqa: F811
    monkeypatch,
    tmp_path,
):
    """One accepted child is never resent when its held sibling is replaced."""
    item = scheduled
    harness = SimpleNamespace(campaign=item.campaign, service=item.arguments[1])
    due = item.campaign.active_configuration.starts_at + timedelta(days=2, hours=12)
    with campaign_clock(due):
        owner, ready = ready_report(harness, additional_admins=("second@example.org",))
        with web_login():
            _, token = commands.preview_pause(
                *item.arguments, reason="Inspect report preparation"
            )
            commands.confirm(*item.arguments, token=token)
        accepted_sender_check(item, monkeypatch, tmp_path)
        with web_login(), pytest.raises(StaleRecordError, match="report preparation"):
            commands.preview_resume(*item.arguments, reason="Too early")
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            assert fanout_daily(owner).phase == "complete"
        with web_login():
            _, token = commands.preview_resume(
                *item.arguments, reason="Finish prepared report"
            )
            commands.confirm(*item.arguments, token=token)
        # The same dates now belong to a new unowned aggregate. The actual
        # worker prepares its recipients; no test fabricates snapshot evidence.
        status = allocate(claim_task=False)
        with task_login(ServiceRole.WORKER, exact=True):
            execute(status, daily_handler(public_origin="https://parish.example"))
        latest = DailyDigestRecipient.objects.filter(
            ready__snapshot__preparation__task_id=status.run_id
        ).select_related("outbox")
        first = latest.get(address=ready.recipients[0]).outbox
        second = latest.get(address="second@example.org").outbox
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(first)
            assert begin(first, execution) is not None
            finish_submission(
                first.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
            )
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
            pk=latest.first().ready.snapshot.preparation.occurrence_id
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
        status = allocate(claim_task=False)
        with task_login(ServiceRole.WORKER, exact=True):
            execute(status, daily_handler(public_origin="https://parish.example"))
        recipients = DailyDigestRecipient.objects.filter(
            ready__snapshot__preparation__task_id=status.run_id
        )
        covered = recipients.get(address=ready.recipients[0])
        assert covered.outbox_id is None and covered.covered_messages == [str(first.pk)]
        replacement = recipients.get(address="second@example.org")
        assert replacement.outbox_id not in {None, second.pk}
        assert OutboxMessage.objects.get(pk=replacement.outbox_id).state == "pending"
        unresolved = OutboxMessage.objects.get(pk=replacement.outbox_id)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(unresolved)
            assert begin(unresolved, execution) is not None
            finish_submission(
                unresolved.pk, execution.claim, FamilyDeliveryResult(Status.UNKNOWN, 1)
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
        unresolved.refresh_from_db()
        assert unresolved.state == "delivery_unknown" and unresolved.attempt == 1
