"""Actual MAIL authority and synthetic daily SMTP outcomes without provider IO."""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.models import AddressRule
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.schedule_preview import work_summary
from parishkit.stewardship.campaigns.boundaries import apply_due_boundaries
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.digest_delivery import DigestDeliveryMail
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.jobs.digest_dispatch import current_digest_content
from parishkit.stewardship.jobs.family_mail_dispatch import (
    begin_submission,
    bound_dispatch,
    finish_submission,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.outbox_storage import prepare_message
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.digest_fanout import fanout_daily
from parishkit.stewardship.reports.digest_models import DailyDigestRecipient

from ..policy_factory import address
from .campaign_builders import (
    admit_test_work,
    campaign_clock,
    change,
    claimed_task,
    complete_empty_catchup,
)
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_daily_digest_fanout_postgresql import ready_report
from .test_daily_digest_planning_postgresql import INSTANT
from .test_family_mail_dispatch_postgresql import claim
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_outbox_boundaries_postgresql import control

pytestmark = pytest.mark.django_db(transaction=True)


def allocated(harness, *, additional_admins=()):
    """Allocate real compiled content and individual, immutable recipient intents."""
    owner, ready = ready_report(harness, additional_admins=additional_admins)
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        assert fanout_daily(owner).phase == "complete"
    return ready


def begin(message, execution):
    """Daily delivery has no Family decryption key or portal-link substitution."""
    return begin_submission(
        message.pk,
        execution.claim,
        private=None,
        public_origin="https://parish.example",
    )


@pytest.mark.parametrize("status", list(Status))
@pytest.mark.parametrize("production", [False, True])
def test_daily_exact_mail_role_records_provider_outcomes(
    family_mail,  # noqa: F811
    status,
    production,
):
    if production:
        family_mail = activate_response_service(family_mail)
        complete_empty_catchup(family_mail.campaign, uuid4())
    with campaign_clock(INSTANT):
        ready = allocated(family_mail)
        message = OutboxMessage.objects.get()
        with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
            assert (
                bound_dispatch(_status(TaskRun.objects.get(pk=message.task_id))).pk
                == message.pk
            )
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            mail, _, _, attempt = begin(message, execution)
            assert isinstance(mail, DigestDeliveryMail) and attempt == 1
            assert mail.recipients == (
                ("admin@example.org",) if production else ("test@example.org",)
            )
            assert mail.chart == bytes(ready.chart) and mail.html.endswith(ready.html)
            assert family_mail.code not in mail.text
            outcome = finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(status, 1)
            )
        message.refresh_from_db()
        assert message.state == outcome.state.value
        assert message.sealed_substitutions is None
        occurrence = ScheduleOccurrence.objects.get(
            pk=ready.snapshot.preparation.occurrence_id
        )
        assert (occurrence.state == "succeeded") is (status is Status.ACCEPTED)
        assert ScheduleFulfillment.objects.filter(
            occurrence=occurrence, disposition="delivered"
        ).exists() is (status is Status.ACCEPTED)


def revoke_admin(harness, recipient):
    """Apply a real login-policy replacement rather than mutating projection rows."""
    store = harness.service.store
    rule = AddressRule.objects.get(
        email=recipient.address,
        configuration_id=SystemConfiguration.objects.get().active_configuration_id,
    )
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "remove",
                    "section": "login_rules",
                    "id": str(rule.record_id),
                }
            ],
        ).state
        == "applied"
    )


@pytest.mark.parametrize("accepted_first", [False, True])
def test_revoked_admin_is_cancelled_without_opening_provider_attempt(
    family_mail,  # noqa: F811
    accepted_first,
):
    with campaign_clock(INSTANT):
        ready = allocated(family_mail, additional_admins=("second@example.org",))
        recipient = DailyDigestRecipient.objects.get(address="second@example.org")
        message = recipient.outbox
        remaining = DailyDigestRecipient.objects.get(address="admin@example.org").outbox

        def deliver_remaining():
            """Keep accepted evidence distinct from the revoked recipient's intent."""
            with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
                execution = claim(remaining)
                begin(remaining, execution)
                finish_submission(
                    remaining.pk,
                    execution.claim,
                    FamilyDeliveryResult(Status.ACCEPTED, 1),
                )

        if accepted_first:
            deliver_remaining()
        revoke_admin(family_mail, recipient)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            assert begin(message, execution) is None
        message.refresh_from_db()
        assert message.state == "cancelled" and message.reason == "recipient_revoked"
        assert message.attempt == 0
        if not accepted_first:
            deliver_remaining()
        occurrence = ScheduleOccurrence.objects.get(
            pk=ready.snapshot.preparation.occurrence_id
        )
        assert occurrence.state == "succeeded"
        assert (
            ScheduleFulfillment.objects.get(
                occurrence=occurrence, slot=occurrence.slot
            ).disposition
            == "delivered"
        )
        assert OutboxMessage.objects.filter(state="delivered").count() == 1


def test_entirely_revoked_cohort_has_empty_not_delivered_fulfillment(family_mail):  # noqa: F811
    """A changed Admin roster never leaves a frozen, undeliverable cohort pending."""
    with campaign_clock(INSTANT):
        ready = allocated(family_mail)
        recipient = DailyDigestRecipient.objects.get()
        store = family_mail.service.store
        assert (
            change(
                store,
                store.active(),
                uuid4(),
                [
                    {
                        "operation": "add",
                        "section": "login_rules",
                        **address("replacement@example.org", roles=("administrator",)),
                    }
                ],
            ).state
            == "applied"
        )
        revoke_admin(family_mail, recipient)
        message = recipient.outbox
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            assert begin(message, execution) is None
        occurrence = ScheduleOccurrence.objects.get(
            pk=ready.snapshot.preparation.occurrence_id
        )
        assert occurrence.state == "succeeded"
        assert occurrence.reason == "daily_digest_no_current_recipients"
        assert (
            ScheduleFulfillment.objects.get(
                occurrence=occurrence, slot=occurrence.slot
            ).disposition
            == "empty"
        )
        assert not OutboxMessage.objects.filter(state="delivered").exists()
        assert OutboxMessage.objects.get().attempt == 0


def test_raw_mail_cannot_waive_a_current_admin_obligation(family_mail):  # noqa: F811
    """Even the restricted MAIL role must prove revocation to record withdrawal."""
    from parishkit.stewardship.jobs.family_mail_dispatch import cancel_unsent

    with campaign_clock(INSTANT):
        ready = allocated(family_mail)
        message = OutboxMessage.objects.get()
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            with work_transaction(), pytest.raises(DatabaseError), transaction.atomic():
                cancel_unsent(message.pk, execution.claim, reason="recipient_revoked")
        message.refresh_from_db()
        assert message.state == "pending"
        assert (
            ScheduleOccurrence.objects.get(
                pk=ready.snapshot.preparation.occurrence_id
            ).state
            == "pending"
        )


def test_production_daily_report_survives_pause_and_campaign_close(family_mail):  # noqa: F811
    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(INSTANT):
        ready = allocated(harness)
        message = OutboxMessage.objects.get()
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
        assert mail.chart == bytes(ready.chart)
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )
    message.refresh_from_db()
    assert message.state == "delivered" and message.pause_hold_id is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("sender", "forged@example.org"),
        ("routed_recipients", ("forged@example.org",)),
        ("html", "<p>Omitted report</p>"),
        ("text", "Omitted report"),
    ],
)
def test_raw_daily_render_cannot_change_envelope_or_drop_report(
    family_mail,  # noqa: F811
    field,
    value,
):
    with campaign_clock(INSTANT):
        allocated(family_mail)
        message = OutboxMessage.objects.get()
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            with work_transaction():
                rendering, _, _ = current_digest_content(
                    message, _scope(message.campaign_id)
                )
                with pytest.raises(DatabaseError), transaction.atomic():
                    prepare_message(
                        message_id=message.pk,
                        expected_version=message.version,
                        command_id=uuid4(),
                        actor_id=execution.claim.worker_id,
                        correlation_id=execution.claim.run_id,
                        render=replace(rendering, **{field: value}),
                        sealed=None,
                        admit=lambda *args: True,
                    )
        message.refresh_from_db()
        assert message.state == "pending" and message.attempt == 0


@pytest.mark.parametrize(
    "service,statement",
    [
        (
            ServiceRole.SCHEDULER,
            "SELECT address FROM stewardship_daily_digest_recipient",
        ),
        (ServiceRole.SCHEDULER, "SELECT chart FROM stewardship_daily_digest_ready"),
        (
            ServiceRole.MAIL_DISPATCH,
            "SELECT statistics_inputs FROM stewardship_daily_digest_snapshot",
        ),
        (
            ServiceRole.MAIL_DISPATCH,
            "UPDATE stewardship_daily_digest_ready SET subject='forged'",
        ),
    ],
)
def test_daily_dispatch_roles_do_not_gain_unrelated_authority(service, statement):
    with (
        task_login(service, exact=True),
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)


def test_daily_cohort_is_not_successful_until_every_admin_is_accepted(family_mail):  # noqa: F811
    with campaign_clock(INSTANT):
        ready = allocated(family_mail, additional_admins=("second@example.org",))
        occurrence = ScheduleOccurrence.objects.get(
            pk=ready.snapshot.preparation.occurrence_id
        )
        messages = list(OutboxMessage.objects.order_by("pk"))
        for index, message in enumerate(messages):
            with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
                execution = claim(message)
                begin(message, execution)
                finish_submission(
                    message.pk,
                    execution.claim,
                    FamilyDeliveryResult(Status.ACCEPTED, 1),
                )
            occurrence.refresh_from_db()
            assert occurrence.state == ("pending" if index == 0 else "succeeded")
        assert (
            ScheduleFulfillment.objects.filter(
                occurrence=occurrence, disposition="delivered"
            ).count()
            == 1
        )
        with task_login(ServiceRole.WEB, exact=True):
            assert (
                work_summary(family_mail.campaign.pk)[str(occurrence.definition_id)][
                    "blocking"
                ]
                == 0
            )
