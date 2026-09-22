"""Compiled Admin decisions preserve provider history, scope and fresh credentials."""

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
)
from parishkit.stewardship.jobs.delivery_resolution import resolve_delivery
from parishkit.stewardship.jobs.delivery_resolution_models import DeliveryResolution
from parishkit.stewardship.jobs.family_mail_dispatch import (
    begin_submission,
    finish_submission,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxEvent
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StaleRecordError

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import claim, prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_policy_postgresql import user
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def failed_delivery(harness, status=FamilyDeliveryStatus.UNKNOWN):
    """Drain a real claimed synthetic delivery, including pre-SUBMIT failure."""
    message = prepare(harness)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        if status is not None:
            begin_submission(
                message.pk,
                execution.claim,
                private=harness.rings.private,
                public_origin="http://localhost:8000",
            )
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(status, 1)
            )
    act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
    message.refresh_from_db()
    return message


def unknown_inventory(campaign):
    """Read the unknown count the Admin resume guard refuses over."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT inventory->>'unknown' FROM stewardship_delivery_control_inventory"
            " WHERE campaign_id=%s",
            [campaign.pk],
        )
        return int(cursor.fetchone()[0])


def retry_admitted(message):
    """Read the authoritative SQL resolution admission, beneath Web preparation."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_delivery_retry_admitted_v1(%s)", [message.pk]
        )
        return cursor.fetchone()[0]


def resolve(harness, principal, message, action, **options):
    """Only public/general keys and restricted Web SQL reach the command service."""
    values = dict(
        message_id=message.pk,
        command_id=uuid4(),
        expected_version=message.version,
        action=action,
        note="Verified external evidence for this exact attempt.",
        duplicate_acknowledged=action == "resend",
        general=harness.rings.general,
        public=harness.rings.public,
        public_origin="http://localhost:8000",
    )
    with task_login(ServiceRole.WEB, exact=True):
        return resolve_delivery(
            harness.service.store, principal.pk, **(values | options)
        )


def test_external_acceptance_fulfills_once_without_rewriting_failed_task(family_mail):  # noqa: F811
    """Provider success requires a retained explicit Admin attestation."""
    principal = user("admin@example.org")
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        old_version = message.version
        command = resolve(family_mail, principal, message, "accept")
        message.refresh_from_db()
        assert message.state == "delivered" and message.sealed_substitutions is None
        assert (
            ScheduleOccurrence.objects.get(pk=message.semantic_key).state == "succeeded"
        )
        assert ScheduleFulfillment.objects.count() == 1
        assert TaskRun.objects.get(pk=message.task_id).state == "failed"
        assert (
            OutboxEvent.objects.filter(message=message, action="mark_unknown").count()
            == 1
        )
        replay = resolve(
            family_mail,
            principal,
            message,
            "accept",
            command_id=command.pk,
            expected_version=old_version,
        )
        assert replay.pk == command.pk and ScheduleFulfillment.objects.count() == 1


@pytest.mark.parametrize(
    "action,status",
    [
        ("resend", FamilyDeliveryStatus.UNKNOWN),
        ("retry_failed", FamilyDeliveryStatus.PERMANENT),
        ("retry_unsent", None),
    ],
)
@pytest.mark.parametrize("production", [False, True])
def test_explicit_retry_reseals_and_retains_numbered_history(
    family_mail,  # noqa: F811
    action,
    status,
    production,
):
    """An authorized retry queues one new immutable Task, never an immediate send."""
    principal = user("admin@example.org")
    if production:
        family_mail = activate_response_service(family_mail)
        complete_empty_catchup(family_mail.campaign, uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail, status)
        render_id, attempt, prior_version = (
            message.render_id,
            message.attempt,
            message.version,
        )
        receipt = resolve(family_mail, principal, message, action)
        message.refresh_from_db()
        receipt.refresh_from_db()
        assert receipt.preparation is None
        assert message.state == "pending" and message.render_id != render_id
        assert message.sealed_substitutions and message.attempt == attempt
        assert TaskRun.objects.get(pk=message.task_id).state == "failed"
        retried = TaskRun.objects.get(pk=receipt.retry_task_id)
        assert retried.parent_id == message.task_id and retried.state == "queued"
        assert not ScheduleFulfillment.objects.exists()
        assert (
            resolve(
                family_mail,
                principal,
                message,
                action,
                command_id=receipt.pk,
                expected_version=prior_version,
            ).pk
            == receipt.pk
        )
        assert TaskRun.objects.filter(root_id=message.task_id).count() == 2
        # The retry is consumable by the real restricted dispatcher and keeps
        # the immutable outbox root instead of allocating a second delivery.
        from types import SimpleNamespace

        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(SimpleNamespace(task_id=retried.pk))
            mail, _, _, number = begin_submission(
                message.pk,
                execution.claim,
                private=family_mail.rings.private,
                public_origin="http://localhost:8000",
            )
            assert number == attempt + 1
            assert mail.recipients == (
                ("valid@example.org",) if production else ("test@example.org",)
            )
            finish_submission(
                message.pk,
                execution.claim,
                FamilyDeliveryResult(
                    FamilyDeliveryStatus.ACCEPTED, len(mail.recipients)
                ),
            )
        assert ScheduleFulfillment.objects.count() == 1


def test_note_and_duplicate_acknowledgement_are_separate_intents(family_mail):  # noqa: F811
    """Recording evidence alone cannot resolve uncertainty or schedule a resend."""
    principal = user("admin@example.org")
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        receipt = resolve(family_mail, principal, message, "note")
        message.refresh_from_db()
        assert message.state == "delivery_unknown" and receipt.retry_task_id is None
        with pytest.raises(ValueError):
            resolve(
                family_mail, principal, message, "resend", duplicate_acknowledged=False
            )
        with pytest.raises(StaleRecordError):
            resolve(
                family_mail,
                principal,
                message,
                "accept",
                expected_version=message.version + 1,
            )


def test_web_still_cannot_mutate_outbox_or_call_definer(family_mail):  # noqa: F811
    """The command table is not a general worker authority escalation."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        with task_login(ServiceRole.WEB, exact=True):
            for sql in (
                "UPDATE stewardship_outbox_message SET state='delivered' WHERE id=%s",
                "SELECT stewardship_delivery_retry_render_v1(%s,'{}'::jsonb,NULL,NULL)",
            ):
                with (
                    pytest.raises(DatabaseError) as error,
                    transaction.atomic(),
                    connection.cursor() as cursor,
                ):
                    cursor.execute(sql, (message.pk,))
                assert error.value.__cause__.sqlstate == "42501"
        assert not DeliveryResolution.objects.exists()


@pytest.mark.parametrize("action", ["accept", "resend"])
def test_campaign_end_allows_evidence_but_not_a_new_send(family_mail, action):  # noqa: F811
    """Truthful past acceptance is separate from admission to future delivery."""
    principal = user("admin@example.org")
    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(harness)
    harness.campaign.refresh_from_db()
    with campaign_clock(harness.campaign.active_configuration.ends_at):
        if action == "accept":
            resolve(harness, principal, message, action)
            assert ScheduleFulfillment.objects.count() == 1
        else:
            with pytest.raises((PermissionError, DatabaseError)):
                resolve(harness, principal, message, action)
            assert not DeliveryResolution.objects.exists()
            assert TaskRun.objects.filter(root_id=message.task_id).count() == 1


@pytest.mark.parametrize(
    "action,status",
    [
        ("accept", FamilyDeliveryStatus.UNKNOWN),
        ("resend", FamilyDeliveryStatus.UNKNOWN),
        ("retry_failed", FamilyDeliveryStatus.PERMANENT),
        ("retry_unsent", None),
    ],
)
def test_pause_resolves_uncertainty_but_fences_new_sends(family_mail, action, status):  # noqa: F811
    """A pause keeps unknown deliveries resolvable, but no provider call crosses it.

    Resume refuses while any delivery is unknown, so a resend is admitted and held
    by the pause rather than refused; retries of failed or unsent mail wait for
    resume.
    """
    from types import SimpleNamespace

    from .test_outbox_boundaries_postgresql import control

    principal = user("admin@example.org")
    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(harness, status)
        control(harness.campaign, "pause")
        if action == "accept":
            resolve(harness, principal, message, action)
            assert ScheduleFulfillment.objects.count() == 1
        elif action == "resend":
            # The unresolved delivery is what the Admin resume guard refuses
            # over; authorizing the resend is what clears it.
            assert unknown_inventory(harness.campaign) == 1
            receipt = resolve(harness, principal, message, action)
            message.refresh_from_db()
            assert message.state == "pending" and message.pause_hold_id is not None
            assert unknown_inventory(harness.campaign) == 0
            with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
                execution = claim(SimpleNamespace(task_id=receipt.retry_task_id))
                begin = dict(
                    private=harness.rings.private,
                    public_origin="http://localhost:8000",
                )
                assert begin_submission(message.pk, execution.claim, **begin) is None
            # The resolved uncertainty no longer blocks resume, which releases
            # the held resend to the provider.
            control(harness.campaign, "resume")
            with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
                assert begin_submission(message.pk, execution.claim, **begin)
            message.refresh_from_db()
            assert message.state == "submitting" and message.pause_hold_id is None
        else:
            with pytest.raises((PermissionError, DatabaseError)):
                resolve(harness, principal, message, action)
            assert not DeliveryResolution.objects.exists()
            assert TaskRun.objects.filter(root_id=message.task_id).count() == 1


def test_failed_resolution_rolls_back_retry_and_all_journals(family_mail, monkeypatch):  # noqa: F811
    """A final command rejection leaves neither orphan retry nor fresh rendering."""
    from parishkit.stewardship.jobs.outbox_models import OutboxRender

    principal = user("admin@example.org")
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        renders, events = OutboxRender.objects.count(), OutboxEvent.objects.count()
        original = DeliveryResolution.objects.create

        def mismatched(**values):
            """Reject after retry allocation, using the actual SQL actor fence."""
            return original(**(values | {"actor_id": uuid4()}))

        monkeypatch.setattr(DeliveryResolution.objects, "create", mismatched)
        with pytest.raises(DatabaseError):
            resolve(family_mail, principal, message, "resend")
        assert TaskRun.objects.filter(root_id=message.task_id).count() == 1
        assert OutboxRender.objects.count() == renders
        assert OutboxEvent.objects.count() == events
        assert not DeliveryResolution.objects.exists()


def test_replay_rechecks_authority_and_exact_evidence(family_mail):  # noqa: F811
    """Knowing a command ID is neither Admin authority nor permission to edit it."""
    from django.db.models import F

    from parishkit.stewardship.accounts.models import PortalUser

    principal = user("admin@example.org")
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        receipt = resolve(family_mail, principal, message, "note")
        with pytest.raises(ValueError, match="already bound"):
            resolve(
                family_mail,
                principal,
                message,
                "note",
                command_id=receipt.pk,
                note="Different evidence",
            )
        PortalUser.objects.filter(pk=principal.pk).update(
            disabled=True,
            version=F("version") + 1,
        )
        with pytest.raises(PermissionError):
            resolve(family_mail, principal, message, "note", command_id=receipt.pk)
        assert DeliveryResolution.objects.count() == 1


@pytest.mark.parametrize("same_command", [False, True])
def test_concurrent_resend_creates_one_retry_and_one_resolution(
    family_mail,  # noqa: F811
    same_command,
):
    """Independent restricted connections serialize on the shared work boundary."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import connections

    principal = user("admin@example.org")
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        barrier, command = Barrier(2), uuid4()

        def run(command_id):
            """Each contender gets a distinct actual Web session and transaction."""
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                try:
                    return resolve_delivery(
                        family_mail.service.store,
                        principal.pk,
                        message_id=message.pk,
                        command_id=command_id,
                        expected_version=message.version,
                        action="resend",
                        note="Verified exact delivery",
                        duplicate_acknowledged=True,
                        general=family_mail.rings.general,
                        public=family_mail.rings.public,
                        public_origin="http://localhost:8000",
                    ).pk
                except StaleRecordError:
                    return None
            finally:
                connections.close_all()

        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            results = list(
                pool.map(run, [command, command if same_command else uuid4()])
            )
        assert sum(result is not None for result in results) == (
            2 if same_command else 1
        )
        assert DeliveryResolution.objects.count() == 1
        assert TaskRun.objects.filter(root_id=message.task_id).count() == 2


def test_retry_uses_current_source_and_public_encryption_key(family_mail):  # noqa: F811
    """A retry does not copy the failed render's recipient or retired key choice."""
    from parishkit.stewardship.accounts.cryptography import Key, TokenPrivateKeyring
    from parishkit.stewardship.campaigns.credential_keys import add_rotation_key
    from parishkit.stewardship.jobs.outbox_models import OutboxRender

    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh

    principal = user("admin@example.org")
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        old_render = message.render_id
        source = response_source()
        source.members[3]["emailAddress"] = "updated@example.org"
        refresh(family_mail, source)
        rotated = TokenPrivateKeyring(
            [
                Key("t1", "decrypt-only", b"t" * 32),
                Key("t2", "active", b"u" * 32),
            ]
        )
        add_rotation_key(family_mail.rings.public, rotated.public())
        resolve(family_mail, principal, message, "resend", public=rotated.public())
        message.refresh_from_db()
        assert message.sealed_key_id == "t2"
        assert OutboxRender.objects.get(pk=message.render_id).intended_recipients == [
            "updated@example.org"
        ]
        assert OutboxRender.objects.get(pk=old_render).intended_recipients == [
            "valid@example.org"
        ]


def test_refreshed_ineligible_family_cannot_retry(family_mail):  # noqa: F811
    """Current source eligibility is required even though the old message exists."""
    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh

    principal = user("admin@example.org")
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
        source = response_source()
        source.member_contactinfos.clear()
        source.members[3]["emailAddress"] = ""
        refresh(family_mail, source)
        with pytest.raises(PermissionError):
            resolve(family_mail, principal, message, "resend")
        assert not DeliveryResolution.objects.exists()
        assert TaskRun.objects.filter(root_id=message.task_id).count() == 1
