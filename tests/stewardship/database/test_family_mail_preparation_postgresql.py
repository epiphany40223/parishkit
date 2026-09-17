"""Real scheduler/worker preparation, without provider credentials or mail sends."""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.schedule_production import FamilyScheduleProducer
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.family_mail_content import (
    CODE_PLACEHOLDER,
    open_family_credentials,
)
from parishkit.stewardship.jobs.family_mail_inputs import load_family_mail_source
from parishkit.stewardship.jobs.family_mail_models import FamilyMailPreparation
from parishkit.stewardship.jobs.family_mail_tasks import TASK_TYPE, preparation_handler
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.outbox_storage import _status as delivery_status
from parishkit.stewardship.jobs.outbox_validation import (
    RenderInput,
    SealedSubstitutions,
)
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session

from ..content_factory import content
from . import campaign_builders
from .campaign_builders import campaign_clock, change, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def family_mail(request, monkeypatch):
    """Prepare real source-backed credentials and applied personalized content."""
    original = campaign_builders.configuration_document

    def document():
        """The root has public sender settings; no provider credential is needed."""
        value = original()
        value["sections"]["integrations"].append(
            {
                "id": str(uuid4()),
                "values": {
                    "kind": "email",
                    "settings": {
                        "sender": "sender@example.org",
                        "reply_to": "reply@example.org",
                    },
                    "credential_fingerprint": None,
                },
            }
        )
        return value

    monkeypatch.setattr(campaign_builders, "configuration_document", document)
    harness = request.getfixturevalue("response_service")
    definition = ScheduleDefinition.objects.get()
    template = content(
        str(harness.campaign.pk),
        kind="email",
        slot="initial",
        html="<p>Hello {{ family_name }}: {{ family_code }} {{ family_url }}</p>",
        text="Hello {{ family_name }}: {{ family_code }} {{ family_url }}",
    )
    assert (
        change(
            harness.service.store,
            harness.service.store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "content", **template},
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": str(definition.pk),
                    "values": {
                        "template_version": template["id"],
                        "subject": template["values"]["subject"],
                    },
                },
            ],
        ).state
        == "applied"
    )
    return harness


def allocate():
    """Use the real exact-name scheduler, including the session-owned producer."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        FamilyScheduleProducer(uuid4())(guard)
    return FamilyMailPreparation.objects.get()


def handler(harness):
    """The general worker receives no private token key or provider transport."""
    return preparation_handler(
        general=harness.rings.general,
        mac=harness.rings.mac,
        public=harness.rings.public,
        public_origin="http://localhost:8000",
    )


@pytest.mark.parametrize("production", [False, True])
def test_real_restricted_worker_prepares_one_redacted_message(family_mail, production):
    """The durable receipt follows real source, credential and SQL role boundaries."""
    if production:
        family_mail = activate_response_service(family_mail)
        complete_empty_catchup(family_mail.campaign, uuid4())
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket = allocate()
        assert allocate().pk == ticket.pk
        owner = handler(family_mail)
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(
                ticket.task_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={TASK_TYPE: owner},
            )
            with maintain_execution(execution):
                owner.execute(execution)
    row = ScheduleOccurrence.objects.get(pk=ticket.occurrence_id)
    message = OutboxMessage.objects.select_related("render").get(pk=row.outbox_id)
    assert row.state == "pending" and row.task_id == ticket.task_id
    assert TaskRun.objects.get(pk=ticket.task_id).state == "succeeded"
    assert message.state == "pending" and message.attempt == 0
    assert message.render.reply_to == "reply@example.org"
    assert message.render.routed_recipients == (
        ["valid@example.org"] if production else ["test@example.org"]
    )
    assert message.render.intended_recipients == ["valid@example.org"]
    assert CODE_PLACEHOLDER in message.render.html
    assert family_mail.code not in message.render.html
    assert OutboxMessage.objects.count() == 1
    assert TaskRun.objects.get(pk=message.task_id).state == "queued"
    retained = message.render
    rendering = RenderInput(
        **{
            field: getattr(retained, field)
            for field in (
                "configuration_id",
                "template_id",
                "sender",
                "reply_to",
                "intended_recipients",
                "routed_recipients",
                "subject",
                "html",
                "text",
            )
        }
    )
    reference = open_family_credentials(
        identity=delivery_status(message).identity,
        render=rendering,
        sealed=SealedSubstitutions(
            message.sealed_substitutions,
            message.token_generation_id,
            message.credential_epoch_id,
        ),
        private=family_mail.rings.private,
    )
    assert reference.code == family_mail.code


def test_mail_source_is_current_and_household_scoped(response_service):
    """Read head contacts, not census edits or unrelated parish households."""
    family = FamilyCampaign.objects.get(portal_eligible=True)
    with work_transaction():
        result = load_family_mail_source(family)
    assert result.snapshot_id == response_service.snapshot.pk
    assert result.recipients.deliverable == ("valid@example.org",)
    assert result.active_members >= 1
    assert "valid@example.org" not in repr(result)


def claim(ticket, owner):
    """Use transport metadata admission to obtain a real bounded worker claim."""
    return claim_hint(
        ticket.task_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: owner},
    )


def test_response_after_enqueue_cancels_invitation_but_preserves_receipt(family_mail):
    """The last local preparation boundary rechecks a newly submitted response."""
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(family_mail)
        form, answers = form_and_answers(family_mail)
        assert submit(family_mail, form, answers).submission is not None
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim(ticket, owner)
            with maintain_execution(execution):
                owner.execute(execution)
    assert TaskRun.objects.get(pk=ticket.task_id).state == "cancelled"
    row = ScheduleOccurrence.objects.get(pk=ticket.occurrence_id)
    assert row.state == "skipped" and row.reason == "family_responded"
    assert not OutboxMessage.objects.filter(
        purpose__in=("initial", "reminder")
    ).exists()
    assert OutboxMessage.objects.get(purpose="receipt").state == "pending"


def test_invalidated_epoch_never_rebinds_or_decrypts(family_mail, monkeypatch):
    """Old work drains as cancelled even while the go-live gate holds new work."""
    from parishkit.stewardship.campaigns.rehearsals import invalidate_rehearsal

    def forbidden(*args, **kwargs):
        """A stale ticket must not reach any code or primary-token access."""
        raise AssertionError("Stale preparation attempted decryption")

    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(family_mail)
        invalidate_rehearsal(campaign_id=family_mail.campaign.pk, admit=lambda _: True)
        monkeypatch.setattr(family_mail.rings.general, "decrypt", forbidden)
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim(ticket, owner)
            with maintain_execution(execution):
                owner.execute(execution)
    assert TaskRun.objects.get(pk=ticket.task_id).state == "cancelled"
    assert not OutboxMessage.objects.exists()


def test_committed_preparation_recovers_lost_ack_without_rerender(
    family_mail, monkeypatch
):
    """A repeated execution acknowledges its retained receipt, not today's inputs."""
    from parishkit.stewardship.jobs import family_mail_preparation as preparation

    def forbidden(*args, **kwargs):
        """Re-rendering could change content or manufacture another message."""
        raise AssertionError("Completed preparation was repeated")

    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(family_mail)
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim(ticket, owner)
            with maintain_execution(execution):
                with execution.effect():
                    assert (
                        preparation.prepare_occurrence(
                            ticket,
                            execution.claim,
                            general=family_mail.rings.general,
                            mac=family_mail.rings.mac,
                            public=family_mail.rings.public,
                            public_origin="http://localhost:8000",
                        )
                        == "complete"
                    )
                monkeypatch.setattr(preparation, "prepare_occurrence", forbidden)
                owner.execute(execution)
    assert TaskRun.objects.get(pk=ticket.task_id).state == "succeeded"
    assert OutboxMessage.objects.count() == 1


def test_failed_preparation_rolls_back_outbox_and_occurrence(family_mail, monkeypatch):
    """The ticket remains recoverable, but no half-prepared message survives."""
    from parishkit.stewardship.jobs import family_mail_preparation as preparation

    original = preparation.create_message

    def fail_after_allocation(**kwargs):
        """Crash after the real root/render/event writes but before the receipt."""
        original(**kwargs)
        raise RuntimeError("Synthetic preparation failure")

    monkeypatch.setattr(preparation, "create_message", fail_after_allocation)
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(family_mail)
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim(ticket, owner)
            with (
                maintain_execution(execution),
                pytest.raises(RuntimeError, match="Synthetic"),
            ):
                owner.execute(execution)
    row = ScheduleOccurrence.objects.get(pk=ticket.occurrence_id)
    assert (row.state, row.task_id, row.outbox_id, row.attempts) == (
        "pending",
        None,
        None,
        0,
    )
    assert TaskRun.objects.get(pk=ticket.task_id).state == "running"
    assert not OutboxMessage.objects.exists()
    assert not TaskRun.objects.filter(task_type="outbox_delivery").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("intended_recipients", ("other-family@example.org",)),
        ("routed_recipients", ("other-family@example.org",)),
        ("reply_to", "other@example.org"),
        ("sender", "other@example.org"),
    ],
)
def test_sql_rejects_forged_source_recipient_or_mail_setting(
    family_mail, monkeypatch, field, value
):
    """Worker SQL independently binds routing/settings, not just Python callbacks."""
    from parishkit.stewardship.jobs import family_mail_rendering as rendering

    original = rendering.render_family_mail

    def forged(**kwargs):
        """Inject a shape-valid rendering after normal source-aware construction."""
        return replace(original(**kwargs), **{field: value})

    monkeypatch.setattr(rendering, "render_family_mail", forged)
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(family_mail)
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim(ticket, owner)
            with (
                maintain_execution(execution),
                pytest.raises(IntegrityError, match="preparation ownership"),
            ):
                owner.execute(execution)
    assert not OutboxMessage.objects.exists()


def test_worker_cannot_read_primary_tokens_or_edit_outbox(response_service):
    """Preparation gets manual-code access, never reusable-token or dispatch writes."""
    with task_login(ServiceRole.WORKER, exact=True):
        for statement in (
            "SELECT ciphertext FROM stewardship_family_token",
            "SELECT token_ciphertext FROM stewardship_rehearsal_credential",
            "UPDATE stewardship_outbox_message SET state='delivered'",
            "DELETE FROM stewardship_outbox_render",
            "INSERT INTO stewardship_family_mail_preparation DEFAULT VALUES",
        ):
            with (
                pytest.raises(ProgrammingError, match="permission denied"),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)


def test_scheduler_and_worker_create_missing_rehearsal_without_fixture_credentials(
    family_mail,
):
    """A fresh empty epoch works through real roles, not pre-seeded test secrets."""
    from parishkit.stewardship.campaigns.credential_models import (
        CampaignCredentialState,
        RehearsalCredential,
    )
    from parishkit.stewardship.campaigns.rehearsals import (
        cleanup_rehearsal,
        invalidate_rehearsal,
        release_rehearsal_gate,
    )

    previous = invalidate_rehearsal(
        campaign_id=family_mail.campaign.pk, admit=lambda _: True
    )
    while cleanup_rehearsal(previous):
        pass
    release_rehearsal_gate(campaign_id=family_mail.campaign.pk, admit=lambda _: True)
    assert not RehearsalCredential.objects.exists()
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(family_mail)
        assert ticket.rehearsal_epoch_id != previous
        assert (
            CampaignCredentialState.objects.get().rehearsal_epoch_id
            == ticket.rehearsal_epoch_id
        )
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim(ticket, owner)
            with maintain_execution(execution):
                owner.execute(execution)
    assert RehearsalCredential.objects.get().epoch_id == ticket.rehearsal_epoch_id
    assert OutboxMessage.objects.get().rehearsal_epoch_id == ticket.rehearsal_epoch_id
