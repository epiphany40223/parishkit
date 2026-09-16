"""Partial recipient evidence survives accepted, retryable and unknown outcomes."""

import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.jobs.family_mail_results import event_result, result_evidence
from parishkit.stewardship.jobs.outbox_models import OutboxEvent
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity
from parishkit.stewardship.jobs.recipient_models import RecipientRefusal

from ..test_outbox_validation import rendering
from .campaign_builders import campaign_clock
from .response_builders import activate_response_service
from .test_outbox_postgresql import change, permit, submit
from .test_recipient_suppressions_postgresql import remember

pytestmark = pytest.mark.django_db(transaction=True)


def observed(
    harness, result, *, mode="production", corrupt=False, health_override=None
):
    """Use real fenced Task/outbox history with synthetic provider observations."""
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    semantic_key = uuid4()
    recipients = ("valid@example.org", "zother@example.org")
    status = create_message(
        identity=DeliveryIdentity(
            scope_id=harness.campaign.pk,
            campaign_id=harness.campaign.pk,
            family_id=family.pk,
            semantic_key=semantic_key,
            mode=mode,
            routing="production" if mode == "production" else "testing_override",
            purpose="initial",
        ),
        render=rendering(
            configuration_id=harness.campaign.active_configuration.configuration_id,
            intended_recipients=recipients,
            routed_recipients=recipients,
        ),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        command_id=uuid4(),
        admit=permit,
    )
    evidence = result_evidence(result, semantic_key=semantic_key)
    if health_override is not None:
        payload = json.loads(evidence.evidence_note) | {"health": health_override}
        note = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        evidence = replace(
            evidence,
            evidence_note=note,
            evidence_digest=hashlib.sha256(note.encode()).hexdigest(),
        )
    if corrupt:
        evidence = replace(evidence, evidence_digest="0" * 64)
    action = {
        Status.ACCEPTED: DeliveryAction.ACCEPT,
        Status.TRANSIENT: DeliveryAction.RETRY_UNACCEPTED,
        Status.UNAVAILABLE: DeliveryAction.RETRY_UNACCEPTED,
        Status.PERMANENT: DeliveryAction.FAIL_UNACCEPTED,
        Status.SYSTEMIC: DeliveryAction.FAIL_UNACCEPTED,
        Status.UNKNOWN: DeliveryAction.MARK_UNKNOWN,
    }[result.status]
    current = change(
        submit(status),
        action,
        evidence=evidence,
        **({"retry_seconds": 30} if action is DeliveryAction.RETRY_UNACCEPTED else {}),
    )
    return OutboxEvent.objects.select_related("message", "render").get(
        message_id=current.message_id, version=current.version
    )


@pytest.mark.parametrize(
    "status",
    [
        Status.ACCEPTED,
        Status.TRANSIENT,
        Status.UNKNOWN,
        Status.PERMANENT,
        Status.UNAVAILABLE,
        Status.SYSTEMIC,
    ],
)
def test_partial_refusal_has_independent_immutable_evidence(response_service, status):
    """A definitive RCPT refusal is retained even if DATA succeeded or is unknown."""
    harness = activate_response_service(response_service)
    with campaign_clock(harness.campaign.active_configuration.starts_at):
        result = FamilyDeliveryResult(status, 2, permanent=(0,))
        event = observed(harness, result)
        assert event_result(event) == result
        with connection.cursor() as cursor:
            cursor.execute("SELECT stewardship_family_smtp_result_v1(%s)", [event.pk])
            assert cursor.fetchone()[0] is not None
        assert remember(event).address == "valid@example.org"
        assert remember(event).event_id == event.pk
        with pytest.raises(PermissionError, match="definitive delivery"):
            remember(event, "zother@example.org")
        with (
            pytest.raises(IntegrityError, match="Production recipient"),
            transaction.atomic(),
        ):
            previous = RecipientRefusal.objects.get(event_id=event.pk)
            RecipientRefusal.objects.create(
                family_id=previous.family_id,
                organization_id=previous.organization_id,
                family_duid=previous.family_duid,
                event_id=event.pk,
                address="zother@example.org",
                actor_id=event.actor_id,
                correlation_id=uuid4(),
            )


def test_corrupt_partial_evidence_is_not_a_refusal(response_service):
    """A reason label alone cannot stand in for the exact numbered outcome."""
    harness = activate_response_service(response_service)
    with campaign_clock(harness.campaign.active_configuration.starts_at):
        event = observed(
            harness,
            FamilyDeliveryResult(Status.ACCEPTED, 2, permanent=(0,)),
            corrupt=True,
        )
        with pytest.raises(PermissionError, match="evidence"):
            remember(event)
        with connection.cursor() as cursor:
            cursor.execute("SELECT stewardship_family_smtp_result_v1(%s)", [event.pk])
            assert cursor.fetchone()[0] is None


def test_transient_address_refusal_never_suppresses(response_service):
    """A usable address remains eligible for a definitive-unaccepted retry."""
    harness = activate_response_service(response_service)
    with campaign_clock(harness.campaign.active_configuration.starts_at):
        event = observed(
            harness, FamilyDeliveryResult(Status.TRANSIENT, 2, transient=(0, 1))
        )
        with pytest.raises(PermissionError, match="definitive delivery"):
            remember(event)
        assert not RecipientRefusal.objects.exists()


def test_shared_outage_evidence_roundtrips_without_recipient_refusal(response_service):
    """Python and the SQL evidence decoder agree on known shared non-acceptance."""
    harness = activate_response_service(response_service)
    with campaign_clock(harness.campaign.active_configuration.starts_at):
        result = FamilyDeliveryResult(Status.UNAVAILABLE, 2)
        event = observed(harness, result)
        assert event.state == "retry_wait" and event_result(event) == result
        with connection.cursor() as cursor:
            cursor.execute("SELECT stewardship_family_smtp_result_v1(%s)", [event.pk])
            assert cursor.fetchone()[0] is not None
        with pytest.raises(PermissionError, match="definitive delivery"):
            remember(event)
        assert not RecipientRefusal.objects.exists()


@pytest.mark.parametrize(
    "status,health",
    [
        (Status.UNKNOWN, "healthy"),
        (Status.ACCEPTED, "systemic"),
        (Status.PERMANENT, "unobserved"),
        (Status.PERMANENT, "invented"),
    ],
)
def test_sql_and_python_reject_forged_health_evidence(response_service, status, health):
    """A recomputed digest does not validate contradictory health/refusal facts."""
    harness = activate_response_service(response_service)
    with campaign_clock(harness.campaign.active_configuration.starts_at):
        event = observed(
            harness,
            FamilyDeliveryResult(status, 2, permanent=(0,)),
            health_override=health,
        )
        with pytest.raises(PermissionError, match="evidence"):
            event_result(event)
        with connection.cursor() as cursor:
            cursor.execute("SELECT stewardship_family_smtp_result_v1(%s)", [event.pk])
            assert cursor.fetchone()[0] is None
        with pytest.raises(PermissionError):
            remember(event)
