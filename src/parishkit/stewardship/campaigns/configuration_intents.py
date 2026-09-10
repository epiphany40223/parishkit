"""Exceptional YAML edits retain ordinary installation recovery and fresh proof."""

from uuid import UUID

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .models import Campaign, CampaignConfigurationAbort, CampaignConfigurationIntent
from .runtime import campaign_transaction


def bind_configuration_intent(
    *,
    campaign_id,
    request_id,
    action,
    expected_version,
    expected_runtime_version,
    token_generation_id=None,
    actor_id,
    correlation_id,
    admit,
):
    """Bind one reviewed candidate; this does not select YAML or grant readiness."""
    if action not in {"edit_end", "reopen"} or not callable(admit):
        raise TypeError("An exceptional edit needs its owning admission callback.")
    if any(not isinstance(value, UUID) for value in (request_id, actor_id)):
        raise TypeError("Exceptional edit identifiers must be UUIDs.")
    with campaign_transaction(campaign_id, correlation_id=correlation_id) as (
        campaign,
        runtime,
    ):
        admit(action, campaign, runtime, None)
        existing = CampaignConfigurationIntent.objects.filter(
            request_id=request_id
        ).first()
        expected = (
            campaign_id,
            action,
            expected_version,
            expected_runtime_version,
            token_generation_id,
            actor_id,
        )
        if existing:
            if (
                existing.campaign_id,
                existing.action,
                existing.expected_version,
                existing.expected_runtime_version,
                existing.token_generation_id,
                existing.actor_id,
            ) != expected:
                raise StorageInvariantError(
                    "Exceptional edit request is already bound."
                )
            return existing
        if (campaign.version, runtime.version) != (
            expected_version,
            expected_runtime_version,
        ):
            raise StaleRecordError(
                "Exceptional campaign inputs changed; refresh readiness."
            )
        return CampaignConfigurationIntent.objects.create(
            campaign=campaign,
            request_id=request_id,
            action=action,
            prior_projection_id=campaign.active_configuration_id,
            expected_version=expected_version,
            expected_runtime_version=expected_runtime_version,
            token_generation_id=token_generation_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )


def verify_intent(request_id, admit):
    """Recheck the exact binding on every preparation/finalization/recovery attempt."""
    intent = CampaignConfigurationIntent.objects.filter(request_id=request_id).first()
    if intent is None:
        return
    if CampaignConfigurationAbort.objects.filter(intent=intent).exists():
        raise StorageInvariantError("Exceptional campaign candidate was aborted.")
    if not callable(admit):
        raise StorageInvariantError(
            "Exceptional campaign installation requires current admission."
        )
    campaign = Campaign.objects.get(pk=intent.campaign_id)
    runtime = SystemConfiguration.objects.get()
    if (campaign.version, runtime.version, campaign.active_configuration_id) != (
        intent.expected_version,
        intent.expected_runtime_version,
        intent.prior_projection_id,
    ):
        raise StaleRecordError(
            "Exceptional campaign inputs changed; refresh readiness."
        )
    admit(intent.action, campaign, runtime, intent)


def verify_intent_receipt(request_id, admit):
    """Authorize a terminal receipt without rerunning obsolete activation readiness."""
    intent = CampaignConfigurationIntent.objects.filter(request_id=request_id).first()
    if intent is None:
        return
    if not callable(admit):
        raise StorageInvariantError(
            "Exceptional campaign receipt requires current admission."
        )
    admit(
        "configuration_receipt",
        Campaign.objects.get(pk=intent.campaign_id),
        SystemConfiguration.objects.get(),
        intent,
    )


def abort_configuration_intent(
    store, *, request_id, actor_id, correlation_id, reason, admit
):
    """Cancel only an unapplied exceptional candidate, with a crash-safe journal.

    Never rewinds a committed configuration. The predecessor remains the active
    database truth throughout; this restores agreement after a failed end-edit
    confirmation whose date/readiness can no longer be recovered successfully.
    """
    from django.db import connection, transaction

    from parishkit.stewardship.accounts.configuration_installation import (
        DatabaseMaterializer,
    )
    from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest

    if not callable(admit) or not isinstance(actor_id, UUID) or not reason.strip():
        raise TypeError("Exceptional cancellation requires fresh attributed admission.")
    request = ConfigurationChangeRequest.objects.get(pk=request_id, authority="admin")
    materializer = DatabaseMaterializer(
        store,
        actor_id=request.actor_id,
        correlation_id=correlation_id,
        request=request,
        admit_campaign=admit,
    )
    with materializer.lock():
        if not materializer.is_prepared(request.candidate_digest):
            raise StorageInvariantError(
                "Exceptional cancellation requires exact prepared data."
            )
        with transaction.atomic(durable=True):
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s,%s)", [736220, 1])
            runtime = SystemConfiguration.objects.select_for_update().get()
            intent = CampaignConfigurationIntent.objects.get(request=request)
            campaign = Campaign.objects.select_for_update().get(pk=intent.campaign_id)
            admit("abort_configuration", campaign, runtime, intent)
            abort = CampaignConfigurationAbort.objects.filter(intent=intent).first()
            if abort is None:
                CampaignConfigurationAbort.objects.create(
                    intent=intent,
                    reason=reason,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                )
            elif abort.reason != reason:
                raise StorageInvariantError(
                    "Exceptional cancellation has different intent."
                )
        return recover_configuration_abort(materializer)


def recover_configuration_abort(materializer):
    """Resume a durable abort before ordinary selected-candidate recovery runs."""
    request = materializer.request
    abort = (
        CampaignConfigurationAbort.objects.select_related("intent")
        .filter(intent__request=request)
        .first()
    )
    if abort is None:
        return None
    if not callable(materializer.admit_campaign):
        raise StorageInvariantError(
            "Exceptional cancellation recovery requires current admission."
        )
    runtime = SystemConfiguration.objects.get()
    campaign = Campaign.objects.get(pk=abort.intent.campaign_id)
    materializer.admit_campaign("abort_configuration", campaign, runtime, abort.intent)
    return materializer.restore_aborted_candidate()
