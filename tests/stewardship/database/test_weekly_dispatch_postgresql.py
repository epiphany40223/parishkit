"""Weekly SMTP boundaries run with exact MAIL authority and synthetic outcomes."""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.campaigns.boundaries import apply_due_boundaries
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.jobs.family_mail_dispatch import (
    begin_submission,
    bound_dispatch,
    cancel_unsent,
    finish_submission,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_storage import prepare_message
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.jobs.weekly_dispatch import current_weekly_content
from parishkit.stewardship.reports.weekly_models import WeeklyDigestRecipient
from parishkit.stewardship.weekly_delivery import WeeklyDeliveryMail

from ..policy_factory import address
from .campaign_builders import admit_test_work, campaign_clock, change, claimed_task
from .test_background_grants_postgresql import task_login
from .test_daily_digest_dispatch_postgresql import revoke_admin
from .test_family_mail_dispatch_postgresql import claim
from .test_outbox_boundaries_postgresql import control
from .test_weekly_capture_postgresql import INSTANT
from .test_weekly_coverage_postgresql import publish
from .test_weekly_fanout_postgresql import captured, messages
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def allocated(harness, *, additional_admins=()):
    """Prepare one frozen live report without invoking a real email provider."""
    respond(harness, "PRIVATE-WEEKLY-DISPATCH-CANARY")
    owner, snapshot = captured(harness, additional_admins=additional_admins)
    publish(owner)
    return snapshot


def begin(message, execution):
    """Weekly sends have no household key, code, or portal token substitution."""
    return begin_submission(
        message.pk,
        execution.claim,
        private=None,
        public_origin="https://parish.example",
    )


def test_weekly_report_survives_pause_and_campaign_close(live_response_service):
    """A held due report resumes with frozen content even after the portal closes."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        allocated(harness)
        message = messages().get()
        recipient = WeeklyDigestRecipient.objects.get(outbox=message)
        control(harness.campaign, "pause")
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            assert begin(message, execution) is None
        message.refresh_from_db()
        assert message.pause_hold_id and message.attempt == 0
        control(harness.campaign, "resume")
    actor = uuid4()
    boundary = claimed_task("campaign_boundary", harness.campaign.pk, actor)
    with campaign_clock(harness.campaign.active_configuration.ends_at):
        apply_due_boundaries(
            campaign_id=harness.campaign.pk,
            task_id=boundary.run_id,
            fence=boundary.fence,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    with (
        campaign_clock(harness.campaign.active_configuration.ends_at),
        task_login(ServiceRole.MAIL_DISPATCH, exact=True),
    ):
        mail, *_ = begin(message, execution)
        assert mail.html.endswith(recipient.html)
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )
    message.refresh_from_db()
    assert message.state == "delivered" and message.pause_hold_id is None


@pytest.mark.parametrize("status", list(Status))
def test_exact_mail_role_records_weekly_provider_outcomes(
    live_response_service, status
):
    with campaign_clock(INSTANT):
        snapshot = allocated(live_response_service)
        message = messages().get()
        with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
            assert (
                bound_dispatch(_status(TaskRun.objects.get(pk=message.task_id))).pk
                == message.pk
            )
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            mail, _, _, attempt = begin(message, execution)
            assert type(mail) is WeeklyDeliveryMail and attempt == 1
            assert mail.recipients == ("admin@example.org",)
            assert "PRIVATE-WEEKLY-DISPATCH-CANARY" in mail.text
            assert live_response_service.code not in mail.text
            result = finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(status, 1)
            )
        message.refresh_from_db()
        assert message.state == result.state.value
        assert message.sealed_substitutions is None
        occurrence = ScheduleOccurrence.objects.get(
            pk=snapshot.preparation.occurrence_id
        )
        assert (occurrence.state == "succeeded") is (status is Status.ACCEPTED)
        assert ScheduleFulfillment.objects.filter(
            occurrence=occurrence, disposition="delivered"
        ).exists() is (status is Status.ACCEPTED)


@pytest.mark.parametrize("accepted_first", [False, True])
def test_revoked_admin_withdrawal_does_not_claim_acceptance(
    live_response_service, accepted_first
):
    harness = live_response_service
    with campaign_clock(INSTANT):
        snapshot = allocated(harness, additional_admins=("second@example.org",))
        revoked = WeeklyDigestRecipient.objects.get(
            snapshot=snapshot, address="second@example.org"
        )
        remaining = WeeklyDigestRecipient.objects.get(
            snapshot=snapshot, address="admin@example.org"
        ).outbox

        def deliver():
            """Settle only the retained Admin's own exact provider observation."""
            with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
                execution = claim(remaining)
                begin(remaining, execution)
                finish_submission(
                    remaining.pk,
                    execution.claim,
                    FamilyDeliveryResult(Status.ACCEPTED, 1),
                )

        if accepted_first:
            deliver()
        revoke_admin(harness, revoked)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(revoked.outbox)
            assert begin(revoked.outbox, execution) is None
        revoked.outbox.refresh_from_db()
        assert (
            revoked.outbox.state == "cancelled"
            and revoked.outbox.reason == "recipient_revoked"
        )
        assert revoked.outbox.attempt == 0
        if not accepted_first:
            deliver()
        assert ScheduleFulfillment.objects.get(
            occurrence_id=snapshot.preparation.occurrence_id, disposition="delivered"
        )
        assert messages().filter(state="delivered").count() == 1


def test_entire_revoked_cohort_is_empty_not_delivered(live_response_service):
    harness = live_response_service
    with campaign_clock(INSTANT):
        snapshot = allocated(harness)
        recipient = WeeklyDigestRecipient.objects.get(snapshot=snapshot)
        store = harness.service.store
        assert (
            change(
                store,
                store.active(),
                uuid4(),
                [
                    {
                        "operation": "add",
                        "section": "login_rules",
                        **address("new@example.org"),
                    }
                ],
            ).state
            == "applied"
        )
        revoke_admin(harness, recipient)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(recipient.outbox)
            assert begin(recipient.outbox, execution) is None
        occurrence = ScheduleOccurrence.objects.get(
            pk=snapshot.preparation.occurrence_id
        )
        assert (
            occurrence.state == "succeeded"
            and occurrence.reason == "weekly_digest_no_current_recipients"
        )
        assert (
            ScheduleFulfillment.objects.get(
                occurrence=occurrence, slot=occurrence.slot
            ).disposition
            == "empty"
        )
        assert not messages().filter(state="delivered").exists()
        assert WeeklyDigestRecipient.objects.count() == 1


def test_one_weekly_claim_cannot_cancel_another_admin(live_response_service):
    with campaign_clock(INSTANT):
        snapshot = allocated(
            live_response_service, additional_admins=("second@example.org",)
        )
        rows = list(
            WeeklyDigestRecipient.objects.filter(snapshot=snapshot).order_by("address")
        )
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(rows[0].outbox)
            with (
                work_transaction(),
                pytest.raises(PermissionError, match="another Admin"),
            ):
                cancel_unsent(
                    rows[1].outbox_id, execution.claim, reason="recipient_revoked"
                )


@pytest.mark.parametrize("field", ["html", "routed_recipients"])
def test_sql_rejects_private_body_or_recipient_rebinding(live_response_service, field):
    with campaign_clock(INSTANT):
        allocated(live_response_service)
        message = messages().get()
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            with (
                pytest.raises(DatabaseError, match="Weekly dispatch"),
                work_transaction(),
            ):
                render, _, _ = current_weekly_content(
                    message, _scope(message.campaign_id)
                )
                render = replace(
                    render,
                    **{
                        field: "<p>Foreign content</p>"
                        if field == "html"
                        else ("outsider@example.org",)
                    },
                )
                prepare_message(
                    message_id=message.pk,
                    expected_version=message.version,
                    command_id=uuid4(),
                    actor_id=execution.claim.worker_id,
                    correlation_id=execution.claim.run_id,
                    render=render,
                    admit=lambda *args: True,
                )


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT observation FROM stewardship_weekly_digest_snapshot",
        "SELECT text FROM stewardship_additional_information",
        "SELECT answers FROM stewardship_submission",
    ],
)
def test_mail_role_cannot_read_live_requests_or_full_observation(statement):
    with (
        task_login(ServiceRole.MAIL_DISPATCH, exact=True),
        pytest.raises(DatabaseError) as error,
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)
    assert error.value.__cause__.sqlstate == "42501"
