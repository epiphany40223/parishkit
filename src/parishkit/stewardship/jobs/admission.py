"""Fresh transactional campaign gates for compiled-in owning task handlers.

These functions are not user authorization, domain-completion proof or a generic
maintenance bypass. A handler loads its immutable campaign/mode/epoch binding
from its own durable request, then checks here at creation, claim and each effect.
Restore/purge/cleanup/operational exception handlers require their owning later
workflows; no caller-supplied boolean or queue name can enable an exemption.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    RehearsalEpoch,
)
from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.campaigns.lifecycle import (
    CampaignWorkKind,
    campaign_work_admitted,
)
from parishkit.stewardship.campaigns.models import Campaign, CampaignWorkGate
from parishkit.stewardship.campaigns.runtime import _now, campaign_facts
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.errors import SourceScopeChanged


@dataclass(frozen=True)
class WorkScope:
    """Locked ORM evidence belongs only to this transaction, not a reusable permit."""

    runtime: SystemConfiguration = field(repr=False)
    campaign: Campaign | None = field(repr=False)
    instant: datetime


def _scope(campaign_id):
    """Read runtime then campaign after the shared order lock; missing state denies."""
    if campaign_id is not None and not isinstance(campaign_id, UUID):
        raise TypeError("Work scope requires a canonical campaign identity.")
    require_work_order()
    runtime = SystemConfiguration.objects.select_for_update().first()
    if runtime is None or runtime.active_configuration_id is None:
        raise PermissionError("Background work requires applied configuration.")
    campaign = None
    if campaign_id is not None:
        campaign = (
            Campaign.objects.select_for_update(of=("self",))
            .select_related("active_configuration")
            .filter(pk=campaign_id)
            .first()
        )
        if campaign is None:
            raise PermissionError("The campaign work scope is unavailable.")
    return WorkScope(runtime, campaign, _now())


def require_campaign_work(*, campaign_id, kind, mode, rehearsal_epoch_id=None):
    """Repeat lifecycle/restore/purge/go-live/mode/pause and optional epoch checks.

    The existing pure lifecycle policy remains authoritative for date boundaries,
    catch-up and delivery pause. A delayed Testing hint may not silently become
    Production work, nor may an old rehearsal resume under a newly created epoch.
    Domain handlers still verify their revision, recipient, request and outcome.
    """
    if (
        not isinstance(campaign_id, UUID)
        or not isinstance(kind, CampaignWorkKind)
        or not isinstance(mode, SystemMode)
        or (rehearsal_epoch_id is not None and not isinstance(rehearsal_epoch_id, UUID))
    ):
        raise TypeError("Campaign work requires canonical immutable scope bindings.")
    scope = _scope(campaign_id)
    campaign, runtime = scope.campaign, scope.runtime
    if runtime.mode != mode.value or not campaign_work_admitted(
        campaign_facts(campaign, runtime), scope.instant, kind
    ):
        raise PermissionError("Campaign work is not currently admitted.")
    if (
        CampaignWorkGate.objects.filter(campaign=campaign)
        .exclude(state="released")
        .exists()
    ):
        raise PermissionError("Campaign work is held for purge.")
    credentials = (
        CampaignCredentialState.objects.select_for_update()
        .filter(campaign=campaign)
        .first()
    )
    if credentials is None or credentials.go_live_gate:
        raise PermissionError("Campaign work is held for readiness cleanup.")
    if kind is CampaignWorkKind.REHEARSAL and rehearsal_epoch_id is None:
        raise PermissionError("Rehearsal work requires its original epoch.")
    if rehearsal_epoch_id is not None and (
        credentials.rehearsal_epoch_id != rehearsal_epoch_id
        or not RehearsalEpoch.objects.filter(
            pk=rehearsal_epoch_id, campaign=campaign, state="active"
        ).exists()
    ):
        raise PermissionError("The rehearsal work epoch is no longer current.")
    return scope


def require_source_refresh(*, campaign_id):
    """Source refresh alone may continue during go-live cleanup and delivery pause.

    Its immutable parent binds the sole current campaign/giving window, or None
    when no campaign exists. A pointer change requires the owning refresh service
    to supersede/replan the request rather than load a different period silently.
    Restore and purge keep source mutations held; no maintenance flag bypasses
    them. The source mutation lease and corpus validation remain independent.
    """
    try:
        scope = _scope(campaign_id)
    except PermissionError:
        raise SourceScopeChanged("Source refresh scope is unavailable.") from None
    if (
        scope.runtime.restore_review_required
        or scope.runtime.current_campaign_id != campaign_id
        or (
            scope.campaign is not None
            and scope.campaign.state
            not in {"draft", "scheduled", "active", "closed", "archived"}
        )
        or CampaignWorkGate.objects.filter(state__in=["preparing", "running"]).exists()
    ):
        raise SourceScopeChanged("Source refresh is not currently admitted.")
    return scope
