"""Fresh transactional campaign gates for compiled-in owning task handlers.

These functions are not user authorization, domain-completion proof or a generic
maintenance bypass. A handler loads its immutable campaign/mode/epoch binding
from its own durable request, then checks here at creation, claim and each effect.
Restore/purge/cleanup/operational exception handlers require their owning later
workflows; no caller-supplied boolean or queue name can enable an exemption.
"""

from contextlib import contextmanager
from contextvars import ContextVar
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


# Scopes already read in the current bulk item (#430), or None outside one.
_REMEMBERED = ContextVar("stewardship_remembered_scopes", default=None)


@contextmanager
def remembered_scopes():
    """Read each campaign scope once for one bulk Family item (#430).

    One Family message's preparation or submission checks its campaign
    scope (runtime, campaign, credential and gate rows) about eight times,
    through each admission callback, inside one transaction that holds the
    work-order lock. Every writer of those rows takes that lock first, and
    the item itself writes none of them, so within the item every read
    returns the same rows. Inside this context ``_scope`` and
    ``family_schedule_planning._planning_scope`` return the first result
    for the same arguments instead of reading again. A refusal is not
    remembered (it is raised again on the next call), and the context is
    entered per item, never across items or transactions. Only the bulk
    send uses it; the one-at-a-time path is unchanged.
    """
    token = _REMEMBERED.set({})
    try:
        yield
    finally:
        _REMEMBERED.reset(token)


def remembered(key, read):
    """Return ``read()``, once per key inside remembered_scopes()."""
    memo = _REMEMBERED.get()
    if memo is None:
        return read()
    if key not in memo:
        memo[key] = read()
    return memo[key]


def _scope(campaign_id, *, share=False, lock=True):
    """Read runtime and campaign scope; see _read_scope (remembered per bulk item)."""
    return remembered(
        ("scope", campaign_id, share, lock),
        lambda: _read_scope(campaign_id, share=share, lock=lock),
    )


def _read_scope(campaign_id, *, share=False, lock=True):
    """Read runtime then campaign after the shared order lock; missing state denies.

    The runtime row is normally locked FOR UPDATE. ``share`` locks it FOR
    SHARE instead, for admission-only callers (Family mail dispatch) that
    never write it, nor any row a Family login locks, later in the same
    transaction. Every writer of the row still takes FOR UPDATE after the
    work-order lock, so either lock keeps it unchanged until commit; but a
    Family login takes FOR SHARE on it too, and an admission's FOR UPDATE
    made each login wait for that admission's whole transaction (#147).
    A caller that might later update the row, or lock a credential or
    session row first, must keep the default: upgrading a shared lock while
    a login holds one could deadlock with that login.

    ``lock=False`` reads both rows without locks and outside the work order,
    for a source refresh's fetch admission and staging batches (#147). Those
    steps publish nothing: promotion re-reads this scope under the work
    order with the default locks before anything becomes source truth, so
    the unlocked read only stops a stale attempt early. Taking the rows
    FOR SHARE instead could deadlock: such a step also locks its task rows,
    and transitions lock task rows and the runtime row in both orders.
    """
    if campaign_id is not None and not isinstance(campaign_id, UUID):
        raise TypeError("Work scope requires a canonical campaign identity.")
    if not lock:
        runtime = SystemConfiguration.objects.first()
        if runtime is None or runtime.active_configuration_id is None:
            raise PermissionError("Background work requires applied configuration.")
        campaign = None
        if campaign_id is not None:
            campaign = (
                Campaign.objects.select_related("active_configuration")
                .filter(pk=campaign_id)
                .first()
            )
            if campaign is None:
                raise PermissionError("The campaign work scope is unavailable.")
        return WorkScope(runtime, campaign, _now())
    require_work_order()
    if share:
        # Django has no FOR SHARE, so one raw statement locks and reads the
        # row: what is read is exactly what is locked.
        runtime = next(
            iter(
                SystemConfiguration.objects.raw(
                    "SELECT * FROM stewardship_system_configuration"
                    " ORDER BY id LIMIT 1 FOR SHARE"
                )
            ),
            None,
        )
    else:
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


def require_source_refresh(*, campaign_id, lock=True):
    """Source refresh alone may continue during go-live cleanup and delivery pause.

    Its immutable parent binds the sole current campaign/giving window, or None
    when no campaign exists. A pointer change requires the owning refresh service
    to supersede/replan the request rather than load a different period silently.
    Restore and purge keep source mutations held; no maintenance flag bypasses
    them. The source mutation lease and corpus validation remain independent.
    ``lock=False`` is the unlocked read of _read_scope, for source steps
    that publish nothing (#147).
    """
    try:
        scope = _scope(campaign_id, lock=lock)
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
