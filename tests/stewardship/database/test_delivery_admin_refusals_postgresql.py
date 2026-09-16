"""Verified Admin clearance retains refusal history and its real SQL boundary."""

from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_admin import clear_recipient_refusal
from parishkit.stewardship.jobs.recipient_models import (
    RecipientRefusal,
    RecipientRefusalResolution,
)
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.storage import StaleRecordError

from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_policy_postgresql import user
from .test_recipient_suppressions_postgresql import refused, remember

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def scenario(response_service):
    """One genuine refusal of the only currently eligible head address."""
    harness = activate_response_service(response_service)
    refusal = remember(refused(harness))
    principal = user("admin@example.org")
    current = SourceCurrent.objects.get()
    values = dict(
        refusal_id=refusal.pk,
        command_id=uuid4(),
        source_snapshot_id=current.snapshot_id,
        source_generation=current.generation,
        note="Verified contact with the Family; the receiving mailbox is working.",
        verified=True,
    )
    return harness, principal, refusal, values


def clear(setup, **overrides):
    """Call the service under the actual closed Web permission set."""
    harness, principal, _, values = setup
    with task_login(ServiceRole.WEB, exact=True):
        return clear_recipient_refusal(
            harness.service.store, principal.pk, **(values | overrides)
        )


def test_verified_clearance_restores_only_eligibility_and_keeps_history(scenario):
    """A false-to-true edge is durable; clearing is not itself a delivery."""
    _, principal, refusal, values = scenario
    family = FamilyCampaign.objects.get(pk=refusal.family_id)
    assert not family.email_deliverable
    version = family.version
    receipt = clear(scenario)
    family.refresh_from_db()
    assert family.email_deliverable and family.version == version + 1
    assert receipt.reason == "verified_admin"
    assert receipt.actor_id == principal.pk
    assert RecipientRefusal.objects.filter(pk=refusal.pk).exists()
    event = AuditEvent.objects.get(event_type="recipient_refusal_cleared")
    assert event.subject_id == receipt.pk and event.actor_id == principal.pk
    assert clear(scenario).pk == values["command_id"]
    family.refresh_from_db()
    assert family.version == version + 1
    assert (
        AuditEvent.objects.filter(event_type="recipient_refusal_cleared").count() == 1
    )


@pytest.mark.parametrize(
    "override", [dict(verified=False), dict(note=" "), dict(note="x" * 2001)]
)
def test_verification_is_explicit_and_bounded(scenario, override):
    """An empty note or absent attestation cannot silently lift suppression."""
    with pytest.raises(ValueError):
        clear(scenario, **override)
    assert not RecipientRefusalResolution.objects.exists()
    assert not FamilyCampaign.objects.get(family_duid=1).email_deliverable


def test_changed_source_and_conflicting_replay_are_not_accepted(scenario):
    """UI source version and immutable command intent survive repeated requests."""
    with pytest.raises(StaleRecordError):
        clear(scenario, source_generation=scenario[3]["source_generation"] + 1)
    clear(scenario)
    with pytest.raises(ValueError, match="already bound"):
        clear(scenario, note="A different verification")
    with pytest.raises(StaleRecordError):
        clear(scenario, command_id=uuid4())


def test_nonadmin_and_revoked_actor_cannot_clear_or_replay(scenario):
    """A known Google identity and an earlier successful command grant no power."""
    harness, principal, refusal, values = scenario
    outsider = user("outsider@example.net")
    with pytest.raises(PermissionError):
        clear((harness, outsider, refusal, values))
    clear(scenario)
    principal.disabled = True
    principal.version += 1
    principal.save()
    with pytest.raises(PermissionError):
        clear(scenario)


@pytest.mark.parametrize("service", [ServiceRole.WEB, ServiceRole.WORKER])
def test_raw_sql_cannot_forge_verification_authority(scenario, service):
    """SQL independently rejects a web outsider and worker-forged Admin evidence."""
    _, principal, refusal, values = scenario
    actor = user("outsider@example.net") if service is ServiceRole.WEB else principal
    with (
        task_login(service, exact=True),
        pytest.raises(DatabaseError) as error,
        transaction.atomic(),
    ):
        RecipientRefusalResolution.objects.create(
            refusal_id=refusal.pk,
            source_snapshot_id=values["source_snapshot_id"],
            source_generation=values["source_generation"],
            reason="verified_admin",
            evidence_note="Forged attestation",
            actor_id=actor.pk,
        )
    assert error.value.__cause__.sqlstate == "23514"
    assert not RecipientRefusalResolution.objects.exists()


def test_clearance_effect_and_audit_roll_back_together(scenario):
    """The UI cannot show a cleared address after its enclosing command aborts."""
    with pytest.raises(RuntimeError), transaction.atomic():
        clear(scenario)
        raise RuntimeError("Injected rollback")
    assert not RecipientRefusalResolution.objects.exists()
    assert not FamilyCampaign.objects.get(family_duid=1).email_deliverable
    assert not AuditEvent.objects.filter(
        event_type="recipient_refusal_cleared"
    ).exists()


def test_web_cannot_impersonate_source_owned_refusal_correction(scenario):
    """The new Web INSERT grant admits verified evidence, never source authority."""
    _, principal, refusal, values = scenario
    with (
        task_login(ServiceRole.WEB, exact=True),
        pytest.raises(DatabaseError, match="Web cannot claim source-owned") as error,
        transaction.atomic(),
    ):
        RecipientRefusalResolution.objects.create(
            refusal_id=refusal.pk,
            source_snapshot_id=values["source_snapshot_id"],
            source_generation=values["source_generation"],
            reason="source_changed",
            evidence_note="",
            actor_id=principal.pk,
        )
    assert error.value.__cause__.sqlstate == "23514"
    assert not RecipientRefusalResolution.objects.exists()
    assert not FamilyCampaign.objects.get(family_duid=1).email_deliverable
