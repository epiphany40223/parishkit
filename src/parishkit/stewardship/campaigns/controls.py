"""Internal campaign controls; owning services verify external evidence under lock."""

from uuid import UUID

from django.db.models import F

from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .models import CampaignControlChange, CampaignWorkGate
from .runtime import _now, campaign_transaction


def change_control(
    *,
    campaign_id,
    request_id,
    action,
    expected_version,
    expected_runtime_version,
    actor_id,
    correlation_id,
    admit,
    reason="",
    evidence_id=None,
    occurred_at=None,
):
    """Recheck pause recovery/live-effect proof before recording one exact command."""
    if not callable(admit) or action not in {
        "pause",
        "resume",
        "first_delivery",
        "first_submission",
    }:
        raise TypeError(
            "Campaign control requires an owning verifier and known action."
        )
    if any(not isinstance(value, UUID) for value in (request_id, actor_id)):
        raise TypeError("Campaign control identifiers must be UUIDs.")
    with campaign_transaction(campaign_id, correlation_id=correlation_id) as (
        campaign,
        runtime,
    ):
        admit(action, campaign, runtime)
        existing = CampaignControlChange.objects.filter(request_id=request_id).first()
        intent = (
            campaign_id,
            action,
            expected_version,
            expected_runtime_version,
            actor_id,
            reason,
            evidence_id,
        )
        if existing:
            if (
                existing.campaign_id,
                existing.action,
                existing.expected_version,
                existing.expected_runtime_version,
                existing.actor_id,
                existing.reason,
                existing.evidence_id,
            ) != intent or (
                occurred_at is not None and existing.occurred_at != occurred_at
            ):
                raise StorageInvariantError(
                    "Campaign control command has different intent."
                )
            return existing
        if (
            campaign.version != expected_version
            or runtime.version != expected_runtime_version
        ):
            raise StaleRecordError("Campaign control inputs changed.")
        return CampaignControlChange.objects.create(
            campaign=campaign,
            request_id=request_id,
            action=action,
            expected_version=expected_version,
            expected_runtime_version=expected_runtime_version,
            actor_id=actor_id,
            correlation_id=correlation_id,
            reason=reason,
            evidence_id=evidence_id,
            occurred_at=_now() if occurred_at is None else occurred_at,
        )


def reserve_work_gate(*, campaign_id, request_id, actor_id, correlation_id, admit):
    """Reserve the single global purge-preparation admission gate, without deleting."""
    if (
        not callable(admit)
        or not isinstance(request_id, UUID)
        or not isinstance(actor_id, UUID)
    ):
        raise TypeError("Work gate requires attributed owning admission.")
    with campaign_transaction(campaign_id, correlation_id=correlation_id) as (
        campaign,
        runtime,
    ):
        admit("reserve_work_gate", campaign, runtime)
        existing = CampaignWorkGate.objects.filter(request_id=request_id).first()
        if existing:
            if existing.campaign_id != campaign_id:
                raise StorageInvariantError("Work gate request is already bound.")
            return existing
        return CampaignWorkGate.objects.create(
            campaign=campaign,
            request_id=request_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )


def release_work_gate(*, gate_id, expected_version, actor_id, correlation_id, admit):
    """Release only aborted preparation; running/tombstoned deletion never reopens."""
    if not callable(admit):
        raise TypeError("Work gate release requires owning quiescence proof.")
    gate = CampaignWorkGate.objects.get(pk=gate_id)
    with campaign_transaction(gate.campaign_id, correlation_id=correlation_id) as (
        campaign,
        runtime,
    ):
        gate.refresh_from_db()
        admit("release_work_gate", campaign, runtime)
        if gate.version != expected_version:
            raise StaleRecordError("Work gate changed.")
        CampaignWorkGate.objects.filter(pk=gate.pk).update(
            state="released",
            version=F("version") + 1,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        gate.refresh_from_db()
        return gate
