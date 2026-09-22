"""Admin weekly delivery decisions retain frozen subsets and provider history."""

from types import SimpleNamespace

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxRender
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.weekly_models import WeeklyDigestRecipient

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_daily_digest_dispatch_postgresql import revoke_admin
from .test_delivery_resolution_postgresql import resolve
from .test_family_mail_dispatch_postgresql import claim
from .test_policy_postgresql import user
from .test_taskrun_postgresql import act
from .test_weekly_capture_postgresql import INSTANT
from .test_weekly_dispatch_postgresql import allocated, begin
from .test_weekly_fanout_postgresql import messages

pytestmark = pytest.mark.django_db(transaction=True)


def failed(harness, status, *, additional_admins=()):
    """Allocate one retained report and drain its unsuccessful provider task."""
    snapshot = allocated(harness, additional_admins=additional_admins)
    recipient = WeeklyDigestRecipient.objects.get(
        snapshot=snapshot, address="admin@example.org"
    )
    message = messages().get(pk=recipient.outbox_id)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        if status is not None:
            begin(message, execution)
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(status, 1)
            )
    act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
    message.refresh_from_db()
    return snapshot, message


@pytest.mark.parametrize(
    "action,status",
    [
        ("accept", Status.UNKNOWN),
        ("resend", Status.UNKNOWN),
        ("retry_failed", Status.PERMANENT),
        ("retry_unsent", None),
    ],
)
def test_admin_weekly_resolution_preserves_original_subset(
    live_response_service, sql_plan_mode, action, status
):
    """A keyless Admin intent is scrubbed; only MAIL can restore frozen content."""
    harness = live_response_service
    principal = user("admin@example.org")
    with campaign_clock(INSTANT):
        snapshot, message = failed(harness, status)
        recipient = WeeklyDigestRecipient.objects.get(outbox=message)
        version, attempt = message.version, message.attempt
        command = resolve(
            harness, principal, message, action, general=None, public=None
        )
        message.refresh_from_db()
        assert command.preparation is None
        assert message.sealed_substitutions is None
        assert message.token_generation_id is None
        assert TaskRun.objects.get(pk=message.task_id).state == "failed"
        assert (
            resolve(
                harness,
                principal,
                message,
                action,
                command_id=command.pk,
                expected_version=version,
                general=None,
                public=None,
            ).pk
            == command.pk
        )
        if action == "accept":
            assert message.state == "delivered" and command.retry_task_id is None
            assert (
                ScheduleOccurrence.objects.get(
                    pk=snapshot.preparation.occurrence_id
                ).state
                == "pending"
            )
            return
        assert message.state == "pending" and message.attempt == attempt
        assert (
            "PARISHKIT_PENDING_WEEKLY_DIGEST"
            in OutboxRender.objects.get(pk=message.render_id).text
        )
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            with connection.cursor() as cursor:
                cursor.execute("SHOW plan_cache_mode")
                assert cursor.fetchone()[0] == sql_plan_mode
            execution = claim(SimpleNamespace(task_id=command.retry_task_id))
            mail, _, _, number = begin(message, execution)
            assert number == attempt + 1
            assert mail.html.endswith(recipient.html)
            assert mail.text.endswith(recipient.text)
            assert mail.recipients == ("admin@example.org",)
            assert "PARISHKIT_PENDING_WEEKLY_DIGEST" not in mail.text
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
            )
        assert (
            ScheduleOccurrence.objects.get(pk=snapshot.preparation.occurrence_id).state
            == "succeeded"
        )


def test_web_cannot_author_weekly_content_or_call_private_seed(
    live_response_service, monkeypatch
):
    """Even a forged service request cannot turn retry into report modification."""
    from parishkit.stewardship.jobs import delivery_resolution

    with campaign_clock(INSTANT):
        _, message = failed(live_response_service, None)
        before = TaskRun.objects.count()
        monkeypatch.setattr(
            delivery_resolution,
            "_prepare_digest",
            lambda message: {"weekly_digest": True, "render": {"text": "Forged"}},
        )
        with pytest.raises(DatabaseError, match="fresh scoped preparation"):
            resolve(
                live_response_service,
                user("admin@example.org"),
                message,
                "retry_unsent",
                general=None,
                public=None,
            )
        assert TaskRun.objects.count() == before
        with task_login(ServiceRole.WEB, exact=True):
            for sql in (
                "SELECT stewardship_weekly_digest_seed_v1(%s,NULL)",
                "SELECT html FROM stewardship_weekly_digest_recipient WHERE id=%s",
                "SELECT recipients FROM stewardship_weekly_digest_snapshot WHERE id=%s",
                "UPDATE stewardship_outbox_message SET state='delivered' WHERE id=%s",
            ):
                with (
                    pytest.raises(DatabaseError, match="permission denied") as error,
                    transaction.atomic(),
                    connection.cursor() as cursor,
                ):
                    cursor.execute(sql, [message.pk])
                assert error.value.__cause__.sqlstate == "42501"


def test_revoked_weekly_recipient_cannot_be_retried(live_response_service):
    """An uncertain former Admin's message needs reconciliation, not a resend."""
    with campaign_clock(INSTANT):
        _, message = failed(
            live_response_service,
            Status.UNKNOWN,
            additional_admins=("second@example.org",),
        )
        revoke_admin(
            live_response_service,
            WeeklyDigestRecipient.objects.get(outbox=message),
        )
        before = TaskRun.objects.count()
        with pytest.raises(PermissionError, match="retry is not currently admitted"):
            resolve(
                live_response_service,
                user("second@example.org"),
                message,
                "resend",
                general=None,
                public=None,
            )
        assert TaskRun.objects.count() == before
        message.refresh_from_db()
        assert message.state == "delivery_unknown"


@pytest.mark.parametrize(
    "action,status", [("resend", Status.UNKNOWN), ("retry_failed", Status.PERMANENT)]
)
def test_paused_weekly_resend_is_held_until_resume(
    live_response_service, action, status
):
    """An unknown weekly report can be resent while paused, so the pause can resume.

    The weekly gate carries its own copy of the pause predicate, so it earns the
    same check as the daily one: only the unknown state gets the exception.
    """
    from parishkit.stewardship.jobs.delivery_resolution_models import (
        DeliveryResolution,
    )

    from .test_delivery_resolution_postgresql import unknown_inventory
    from .test_outbox_boundaries_postgresql import control

    harness = live_response_service
    principal = user("admin@example.org")
    with campaign_clock(INSTANT):
        _, message = failed(harness, status)
        control(harness.campaign, "pause")
        if action == "retry_failed":
            with pytest.raises((PermissionError, DatabaseError)):
                resolve(harness, principal, message, action, general=None, public=None)
            assert not DeliveryResolution.objects.exists()
            assert TaskRun.objects.filter(root_id=message.task_id).count() == 1
            return
        # The unresolved delivery is what the Admin resume guard refuses over;
        # authorizing the resend is what clears it.
        assert unknown_inventory(harness.campaign) == 1
        command = resolve(
            harness, principal, message, action, general=None, public=None
        )
        message.refresh_from_db()
        assert message.state == "pending" and message.pause_hold_id is not None
        assert unknown_inventory(harness.campaign) == 0
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(SimpleNamespace(task_id=command.retry_task_id))
            assert begin(message, execution) is None
        control(harness.campaign, "resume")
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            assert begin(message, execution) is not None
