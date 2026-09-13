"""Exact initial-finalization input, separate from ordinary runtime authority.

The installer receipt authorizes only this original setup's remaining work. It
does not make selected-but-unapplied YAML current for any ordinary task or route.
No target's private credential context is needed by this read-only verifier.
"""

from dataclasses import dataclass
from uuid import UUID
from zoneinfo import ZoneInfo

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.integration_selection import integration_records
from parishkit.stewardship.accounts.request_admission import intake_base
from parishkit.stewardship.accounts.request_models import ConfigurationRequestCheckpoint
from parishkit.stewardship.accounts.setup_credential_installation import ready_live
from parishkit.stewardship.accounts.setup_install_models import SetupPreparationReceipt
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.errors import SourceScopeChanged

from .snapshot_models import SourceCurrent
from .windows import RefreshWindow, refresh_window


@dataclass(frozen=True)
class FinalSetupScope:
    """Safe immutable inputs for one final load; no caller-supplied financial scope."""

    preparation_id: UUID
    readiness_id: UUID
    attempt_id: UUID
    owner_id: UUID
    request_id: UUID
    configuration_id: UUID
    configuration_digest: str
    organization_id: int
    fingerprint: str
    timezone: ZoneInfo
    window: RefreshWindow


def require_prepared_setup(store, preparation_id):
    """Repeat original-login, immutable preparation and exact selected-file proof.

    Call under work serialization before every external request and durable
    effect. The separate final commit owner must check this before changing
    either SQL pointer; afterwards this pre-activation scope deliberately closes.
    """
    require_work_order()
    receipt = (
        SetupPreparationReceipt.objects.select_related(
            "readiness__intent__attempt", "readiness__intent__request"
        )
        .filter(pk=preparation_id)
        .first()
    )
    if receipt is None or not ready_live(receipt.readiness_id):
        raise SourceScopeChanged("Original prepared setup is no longer live.")
    intent = receipt.readiness.intent
    request, attempt = intent.request, intent.attempt
    selected = store.active()
    if (
        request.request_schema != "initial-setup-patch-v7"
        or receipt.configuration_id != request.candidate_version_id
        or selected is None
        or (selected.version_id, selected.digest)
        != (request.candidate_version_id, request.candidate_digest)
        or ConfigurationRequestCheckpoint.objects.filter(request=request)
        .order_by("-sequence")
        .values_list("state", flat=True)
        .first()
        != "yaml_activated"
        or SourceCurrent.objects.get(singleton=True).snapshot_id is not None
    ):
        raise PermissionError("Setup finalization has different installation evidence.")
    _, projected = intake_base(selected.digest)
    if projected != selected or store.manifest_reference() != (
        selected.version_id,
        selected.digest,
    ):
        raise ConfigError("Setup finalization requires exact prepared YAML.")
    document = selected.document()
    campaigns = document["sections"].get("campaigns", [])
    if len(campaigns) != 1 or campaigns[0]["id"] != str(attempt.pk):
        raise ConfigError("Setup finalization requires its first campaign.")
    source = integration_records(document)["parishsoft"]["values"]
    return FinalSetupScope(
        receipt.pk,
        receipt.readiness_id,
        attempt.pk,
        attempt.owner_id,
        request.pk,
        selected.version_id,
        selected.digest,
        int(source["settings"]["organization_id"]),
        source["credential_fingerprint"],
        ZoneInfo(document["sections"]["parish"][0]["values"]["timezone"]),
        refresh_window(
            campaign_id=attempt.pk, state="draft", values=campaigns[0]["values"]
        ),
    )
