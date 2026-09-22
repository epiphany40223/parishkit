"""Explicit Admin digest decisions preserve frozen facts and provider history."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage, OutboxRender
from parishkit.stewardship.jobs.storage import _status

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_daily_digest_dispatch_postgresql import allocated, begin
from .test_daily_digest_planning_postgresql import INSTANT
from .test_delivery_resolution_postgresql import resolve
from .test_family_mail_dispatch_postgresql import claim
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_policy_postgresql import user
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def failed(harness, status):
    """Record a real restricted delivery outcome, then drain the failed task."""
    ready = allocated(harness)
    message = OutboxMessage.objects.get()
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        if status is not None:
            begin(message, execution)
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(status, 1)
            )
    act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
    message.refresh_from_db()
    return ready, message


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize(
    "action,status",
    [
        ("accept", Status.UNKNOWN),
        ("resend", Status.UNKNOWN),
        ("retry_failed", Status.PERMANENT),
        ("retry_unsent", None),
    ],
)
def test_admin_resolution_is_keyless_and_retry_uses_original_report(
    family_mail,  # noqa: F811
    sql_plan_mode,
    production,
    action,
    status,
):
    """Acceptance records evidence; retries rerender frozen content only in MAIL."""
    if production:
        family_mail = activate_response_service(family_mail)
        complete_empty_catchup(family_mail.campaign, uuid4())
    principal = user("admin@example.org")
    with campaign_clock(INSTANT):
        ready, message = failed(family_mail, status)
        version, attempt = message.version, message.attempt
        command = resolve(
            family_mail, principal, message, action, general=None, public=None
        )
        message.refresh_from_db()
        assert command.preparation is None
        assert message.sealed_substitutions is None
        assert message.token_generation_id is None
        assert TaskRun.objects.get(pk=message.task_id).state == "failed"
        assert (
            resolve(
                family_mail,
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
            # Provider task history stays failed; the separately tested metadata
            # finalizer obtains a fresh claim before settling this occurrence.
            assert (
                ScheduleOccurrence.objects.get(
                    pk=ready.snapshot.preparation.occurrence_id
                ).state
                == "pending"
            )
            return
        assert message.state == "pending" and message.attempt == attempt
        assert (
            "PARISHKIT_PENDING_DAILY_DIGEST"
            in OutboxRender.objects.get(pk=message.render_id).text
        )
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            with connection.cursor() as cursor:
                cursor.execute("SHOW plan_cache_mode")
                assert cursor.fetchone()[0] == sql_plan_mode
            with (
                pytest.raises(DatabaseError, match="permission denied"),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute("SELECT id FROM stewardship_production_request")
            execution = claim(SimpleNamespace(task_id=command.retry_task_id))
            mail, _, _, number = begin(message, execution)
            assert number == attempt + 1
            assert mail.chart == bytes(ready.chart)
            assert mail.html.endswith(ready.html)
            assert mail.recipients == (
                ("admin@example.org",) if production else ("test@example.org",)
            )
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
            )
        assert (
            ScheduleOccurrence.objects.get(
                pk=ready.snapshot.preparation.occurrence_id
            ).state
            == "succeeded"
        )


def test_web_cannot_supply_report_payload_or_invoke_private_seed(
    family_mail,  # noqa: F811
    monkeypatch,
):
    """The command journal is not a private-report read/write capability."""
    from parishkit.stewardship.jobs import delivery_resolution

    with campaign_clock(INSTANT):
        _, message = failed(family_mail, None)
        before = TaskRun.objects.count()
        monkeypatch.setattr(
            delivery_resolution,
            "_prepare_digest",
            lambda message: {"daily_digest": True, "render": {"text": "Forged report"}},
        )
        with pytest.raises(DatabaseError, match="fresh scoped preparation"):
            resolve(
                family_mail,
                user("admin@example.org"),
                message,
                "retry_unsent",
                general=None,
                public=None,
            )
        assert TaskRun.objects.count() == before
        with task_login(ServiceRole.WEB, exact=True):
            for sql in (
                "SELECT stewardship_daily_digest_seed_v1(%s,NULL)",
                "SELECT html FROM stewardship_daily_digest_ready WHERE id=%s",
                "UPDATE stewardship_outbox_message SET state='delivered' WHERE id=%s",
            ):
                with (
                    pytest.raises(DatabaseError, match="permission denied"),
                    transaction.atomic(),
                    connection.cursor() as cursor,
                ):
                    cursor.execute(sql, [message.pk])


def test_paused_report_resend_is_held_until_resume(family_mail):  # noqa: F811
    """An unknown Admin report can be resent while paused, so the pause can resume."""
    from .test_outbox_boundaries_postgresql import control

    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(INSTANT):
        _, message = failed(harness, Status.UNKNOWN)
        control(harness.campaign, "pause")
        command = resolve(
            harness,
            user("admin@example.org"),
            message,
            "resend",
            general=None,
            public=None,
        )
        message.refresh_from_db()
        assert message.state == "pending" and message.pause_hold_id is not None
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(SimpleNamespace(task_id=command.retry_task_id))
            assert begin(message, execution) is None
        control(harness.campaign, "resume")
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            assert begin(message, execution) is not None
