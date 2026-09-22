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
from .test_delivery_resolution_postgresql import (
    resolve,
    retry_admitted,
    unknown_inventory,
)
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


@pytest.mark.parametrize(
    "action,status", [("resend", Status.UNKNOWN), ("retry_failed", Status.PERMANENT)]
)
def test_paused_report_resend_is_held_until_resume(family_mail, action, status):  # noqa: F811
    """An unknown Admin report can be resent while paused, so the pause can resume.

    Only the unknown state earns the pause exception: a failed report's retry
    still waits for resume.
    """
    from parishkit.stewardship.jobs.delivery_resolution_models import (
        DeliveryResolution,
    )

    from .test_outbox_boundaries_postgresql import control

    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(INSTANT):
        _, message = failed(harness, status)
        control(harness.campaign, "pause")
        principal = user("admin@example.org")
        # The SQL admission, not only the Web preparation that refuses first,
        # grants the pause exception to the unknown state alone.
        assert retry_admitted(message) is (action == "resend")
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


# How a report to admin@example.org ends, relative to the other Admin's copy:
# removed before confirm_unsent while paused; confirmed while still an
# Administrator; confirmed and only then removed; a provider permanent failure
# and then removal; the other copy delivered first so confirm_unsent is the
# last settlement; and admin@ as the report's only recipient.
SCENARIOS = (
    "removed_first",
    "admitted",
    "removed_after_confirm",
    "provider_failure_then_removed",
    "other_first",
    "only_recipient",
)


def add_admin(harness, email):
    """Grant a real Administrator login rule through the configuration owner."""
    from ..policy_factory import address
    from .campaign_builders import change

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
                    **address(email, roles=("administrator",)),
                }
            ],
        ).state
        == "applied"
    )


def finalize(*, weekly=False):
    """Run the real finalizer producer and task, as after any late settlement."""
    from parishkit.stewardship.jobs.dispatch import execute_hint
    from parishkit.stewardship.jobs.queues import WorkQueue
    from parishkit.stewardship.jobs.scheduler import scheduler_session
    from parishkit.stewardship.reports.digest_finalization import (
        TASK_TYPE,
        WEEKLY_TASK_TYPE,
        DailyDigestFinalizeProducer,
        WeeklyDigestFinalizeProducer,
        finalization_handler,
    )

    kind = WEEKLY_TASK_TYPE if weekly else TASK_TYPE
    producer = (
        WeeklyDigestFinalizeProducer if weekly else DailyDigestFinalizeProducer
    )(uuid4())
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        (task,) = producer(guard)
    with task_login(ServiceRole.WORKER, exact=True):
        assert execute_hint(
            task.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={kind: finalization_handler()},
        )


def deliver(message):
    """Accept one frozen report copy through the real restricted dispatcher."""
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        assert begin(message, execution) is not None
        finish_submission(
            message.pk, execution.claim, FamilyDeliveryResult(Status.ACCEPTED, 1)
        )


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_report_confirmed_unsent_settles_only_for_a_removed_recipient(
    family_mail,  # noqa: F811
    scenario,
):
    """A failed report settles its cohort once its recipient is not an Admin.

    Whatever ended it (the provider or confirm_unsent) and in whichever order,
    a failed report to a removed Administrator can never be retried, so it
    settles like a recipient_revoked cancellation: the other Admin's delivery,
    or else the metadata finalizer, completes the occurrence. A current
    recipient's failed report keeps the cohort open for an admitted retry.
    """
    from parishkit.stewardship.campaigns.schedule_models import ScheduleFulfillment
    from parishkit.stewardship.reports.digest_models import DailyDigestRecipient

    from .test_daily_digest_dispatch_postgresql import revoke_admin
    from .test_delivery_resolution_postgresql import confirmed_unsent
    from .test_outbox_boundaries_postgresql import control

    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    principal = user("second@example.org")
    only = scenario == "only_recipient"
    with campaign_clock(INSTANT):
        ready = allocated(
            harness, additional_admins=() if only else ("second@example.org",)
        )
        recipient = DailyDigestRecipient.objects.get(address="admin@example.org")
        message = recipient.outbox
        other = (
            None
            if only
            else DailyDigestRecipient.objects.get(address="second@example.org").outbox
        )
        provider_failure = scenario == "provider_failure_then_removed"
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            begin(message, execution)
            finish_submission(
                message.pk,
                execution.claim,
                FamilyDeliveryResult(
                    Status.PERMANENT if provider_failure else Status.UNKNOWN, 1
                ),
            )
        act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
        message.refresh_from_db()
        occurrence = ScheduleOccurrence.objects.get(
            pk=ready.snapshot.preparation.occurrence_id
        )

        def confirm():
            """Record the Admin's evidence that the provider did not send it."""
            command = resolve(
                harness, principal, message, "confirm_unsent", general=None, public=None
            )
            confirmed_unsent(message, command)

        if only:
            add_admin(harness, "second@example.org")
            revoke_admin(harness, recipient)
            confirm()
        elif scenario in {"removed_first", "admitted"}:
            control(harness.campaign, "pause")
            if scenario == "removed_first":
                revoke_admin(harness, recipient)
                assert retry_admitted(message) is False
                with pytest.raises(PermissionError, match="retry is not currently"):
                    resolve(
                        harness, principal, message, "resend", general=None, public=None
                    )
            assert unknown_inventory(harness.campaign) == 1
            confirm()
            assert unknown_inventory(harness.campaign) == 0
            # Lift only the pause flag so the remaining Admin's report can go.
            control(harness.campaign, "resume")
            deliver(other)
        elif scenario == "other_first":
            deliver(other)
            revoke_admin(harness, recipient)
            occurrence.refresh_from_db()
            assert occurrence.state == "pending"
            confirm()
        else:
            if not provider_failure:
                confirm()
            deliver(other)
            occurrence.refresh_from_db()
            # Still an Administrator: an ordinary failure holds the report open.
            assert occurrence.state == "pending"
            revoke_admin(harness, recipient)
        if scenario not in {"removed_first", "admitted"}:
            # The last settlement had no live worker (an Admin command or a
            # roster change), so only the metadata finalizer completes it.
            occurrence.refresh_from_db()
            assert occurrence.state == "pending"
            finalize()
        occurrence.refresh_from_db()
        # Overdue slots coalesced into this occurrence have their own rows.
        fulfilled = ScheduleFulfillment.objects.filter(
            occurrence=occurrence, slot=occurrence.slot
        )
        if scenario == "admitted":
            assert occurrence.state == "pending" and not fulfilled.exists()
            assert retry_admitted(message) is True
        else:
            assert occurrence.state == "succeeded"
            assert [row.disposition for row in fulfilled] == [
                "empty" if only else "delivered"
            ]


def test_readded_administrator_reopens_a_pending_cohort(family_mail):  # noqa: F811
    """The current roster decides settlement until the occurrence completes.

    Removing the recipient makes the failed report settled; re-adding them
    before completion reopens the cohort and admits the retry again. A
    finalizer allocated in the removed window then refuses its claim until the
    cohort resolves again, which is the documented, harmless noise.
    """
    from parishkit.stewardship.jobs.dispatch import execute_hint
    from parishkit.stewardship.jobs.queues import WorkQueue
    from parishkit.stewardship.jobs.scheduler import scheduler_session
    from parishkit.stewardship.reports.digest_finalization import (
        TASK_TYPE,
        DailyDigestFinalizeProducer,
        finalization_handler,
    )
    from parishkit.stewardship.reports.digest_models import DailyDigestRecipient

    from .test_daily_digest_dispatch_postgresql import revoke_admin

    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(INSTANT):
        ready = allocated(harness, additional_admins=("second@example.org",))
        recipient = DailyDigestRecipient.objects.get(address="admin@example.org")
        message = recipient.outbox
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            begin(message, execution)
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(Status.PERMANENT, 1)
            )
        act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
        deliver(DailyDigestRecipient.objects.get(address="second@example.org").outbox)
        occurrence = ScheduleOccurrence.objects.get(
            pk=ready.snapshot.preparation.occurrence_id
        )
        revoke_admin(harness, recipient)
        assert retry_admitted(message) is False
        producer = DailyDigestFinalizeProducer(uuid4())
        with (
            task_login(ServiceRole.SCHEDULER, exact=True),
            scheduler_session() as guard,
        ):
            (task,) = producer(guard)
        add_admin(harness, "admin@example.org")
        with (
            task_login(ServiceRole.WORKER, exact=True),
            pytest.raises(PermissionError, match="entire resolved cohort"),
        ):
            execute_hint(
                task.run_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={TASK_TYPE: finalization_handler()},
            )
        occurrence.refresh_from_db()
        assert occurrence.state == "pending"
        assert retry_admitted(message) is True
