"""Collect current durable readiness inputs under one authenticated work order.

This read-only layer neither checks external DNS nor authorizes cleanup. The web
workflow combines these inputs with its bounded public-origin check and explicit
irreversible acknowledgement. Activation remains a separate, closed owner.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

from django.db.models import OuterRef, Subquery

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.cleanup_preview import (
    CleanupPreview,
    cleanup_preview,
)
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.models import Campaign, CampaignWorkGate
from parishkit.stewardship.campaigns.readiness_families import (
    FamilyImpactEvidence,
    family_impact,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.source.models import SourceCurrent
from parishkit.stewardship.source.readiness import SourceReadiness, source_readiness
from parishkit.stewardship.source.version_models import SnapshotFund, SnapshotMinistry
from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import editable_configuration, principal
from .campaign_mail_models import CampaignMailTest
from .go_live_configuration import ConfigurationReadiness, configuration_readiness
from .integration_selection import current_receipt, integration_records
from .request_models import ConfigurationChangeRequest, ConfigurationRequestCheckpoint


@dataclass(frozen=True)
class GoLiveInputs:
    """Exact pre-cleanup inputs, never a reusable authorization permit."""

    campaign: Campaign
    observed_at: datetime
    target_state: str
    configuration: ConfigurationReadiness
    source: SourceReadiness
    families: FamilyImpactEvidence
    cleanup: CleanupPreview
    mail_test_id: UUID | None
    problems: tuple[str, ...]
    digest: str


def collect_inputs(request, service, campaign_id):
    """Reload current Admin authority, configuration and complete impact together.

    The digest deliberately omits wall-clock sampling time. It includes the
    computed target, current source/full proof and due-group binding, so start,
    close, source expiry and newly due slots can still invalidate a preview.
    A final response must independently repeat current authorization.
    """
    with work_transaction():
        principal(request, service, passive=True)
        configuration = editable_configuration(service)
        scope = _scope(campaign_id)
        campaign = scope.campaign
        if (
            scope.runtime.current_campaign_id != campaign_id
            or scope.runtime.mode != "testing"
            or campaign.state != "draft"
            or scope.runtime.active_configuration_id
            != configuration.active_configuration_id
        ):
            raise StaleRecordError(
                "Go-live readiness requires the current Testing draft."
            )
        document = configuration.active_configuration.canonical_document
        source = source_readiness(scope)
        ministries, funds = _catalogs(source)
        config = configuration_readiness(
            document, campaign_id, ministries=ministries, funds=funds
        )
        impact = family_impact(campaign, cutoff=scope.instant)
        cleanup = cleanup_preview(campaign_id)
        records = integration_records(document)
        problems = list(config.problems)
        if not source.ready:
            problems.append(source.reason)
        if cleanup.unresolved:
            problems.append("testing_delivery_unresolved")
        if impact.counts.blocked_families:
            problems.append("production_delivery_unresolved")
        credentials = CampaignCredentialState.objects.filter(campaign=campaign).first()
        current = SourceCurrent.objects.first()
        if (
            credentials is None
            or credentials.population_dirty
            or current is None
            or current.snapshot_id is None
            or credentials.source_snapshot_id != current.snapshot_id
            or credentials.source_generation != current.generation
        ):
            problems.append("family_population_unavailable")
        if credentials is not None and credentials.go_live_gate:
            problems.append("cleanup_already_started")
        if CampaignWorkGate.objects.exclude(state="released").exists():
            problems.append("campaign_work_held")
        if (
            Campaign.objects.exclude(pk=campaign_id)
            .filter(state__in=("scheduled", "active"))
            .exists()
        ):
            problems.append("other_campaign_live")
        if _configuration_pending(configuration.active_configuration_id):
            problems.append("configuration_pending")
        receipts = _receipts(records, problems)
        mail = _mail_test(configuration, campaign_id, config, records)
        if mail is None:
            problems.append("family_test_mail_required")
        if CampaignMailTest.objects.filter(
            campaign_id=campaign_id, state__in=("queued", "submitting")
        ).exists():
            problems.append("family_test_mail_pending")
        interval = campaign.active_configuration
        target = "scheduled" if scope.instant < interval.starts_at else "active"
        if scope.instant >= interval.ends_at:
            target = "closed"
            problems.append("campaign_closed")
        binding = {
            "configuration": configuration.active_configuration.digest,
            "runtime": scope.runtime.version,
            "campaign": (
                str(campaign.pk),
                campaign.version,
                campaign.readiness_revision,
            ),
            "credentials": (str(credentials.pk), credentials.version)
            if credentials
            else None,
            "source": asdict(source),
            "families": impact.digest,
            "inventory": cleanup.inventory.digest,
            "submissions": (cleanup.submissions, cleanup.families),
            "messages": cleanup.message_states,
            "mail_test": mail,
            "receipts": receipts,
            "target": target,
            "problems": problems,
        }
        digest = hashlib.sha256(
            json.dumps(binding, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return GoLiveInputs(
            campaign,
            scope.instant,
            target,
            config,
            source,
            impact,
            cleanup,
            mail,
            tuple(problems),
            digest,
        )


def _catalogs(source):
    """Never borrow another tenant/window's catalog to satisfy selected mappings."""
    if source.current_id is None or source.reason in {
        "tenant_unavailable",
        "source_scope_changed",
        "full_refresh_required",
    }:
        return set(), set()
    return tuple(
        {
            int(key)
            for key in model.objects.filter(snapshot_id=source.current_id).values_list(
                "source_key", flat=True
            )
        }
        for model in (SnapshotMinistry, SnapshotFund)
    )


def _configuration_pending(configuration_id):
    """Only still-applicable current-base edits can change this preview's inputs."""
    latest = ConfigurationRequestCheckpoint.objects.filter(
        request_id=OuterRef("pk")
    ).order_by("-sequence")
    return (
        ConfigurationChangeRequest.objects.filter(base_id=configuration_id)
        .annotate(latest_state=Subquery(latest.values("state")[:1]))
        .exclude(latest_state__in=("applied", "failed", "cancelled"))
        .exists()
    )


def _receipts(records, problems):
    """Consumer-acknowledged provider validation is separate from test delivery."""
    receipts = {}
    for target in ("parishsoft", "google_workspace", "slack"):
        if target == "slack" and target not in records:
            continue
        fingerprint = (
            records.get(target, {}).get("values", {}).get("credential_fingerprint")
        )
        try:
            receipts[target] = current_receipt(target, fingerprint, records).pk
        except ConfigError:
            problems.append(target + "_check_required")
    return receipts


def _mail_test(configuration, campaign_id, config, records):
    """Only successful current selected Family mail counts, never a generic receipt."""
    fingerprint = (
        records.get("google_workspace", {})
        .get("values", {})
        .get("credential_fingerprint")
    )
    return (
        CampaignMailTest.objects.filter(
            campaign_id=campaign_id,
            configuration_id=configuration.active_configuration_id,
            template__record_id__in=config.family_templates,
            fingerprint=fingerprint,
            state="accepted",
        )
        .order_by("-finished_at", "-id")
        .values_list("id", flat=True)
        .first()
    )
