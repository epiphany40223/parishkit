"""Concrete Phase 2 Family/Chair effects for the compiled refresh handler.

Keyrings and suppression reads are startup-owned dependencies, never broker
payloads. Later submission/publication owners must extend this composition
before enabling those features; a placeholder success callback is not an effect.
"""

from functools import partial

from parishkit.stewardship.accounts.chair_reconciliation import reconcile_source_chairs
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.storage import StorageInvariantError

from .attempts import _scope, verify_refresh_attempt
from .families import reconcile_source_families
from .refresh_models import SourceRefreshAttempt


def refresh_reconciler(*, general, mac, public, suppressions):
    """Bind explicit keys and a transactionally fresh suppression-set provider.

    ``suppressions(scope)`` returns the exact canonical frozenset consumed by
    Family reconciliation. There is intentionally no implicit empty default that
    could ignore a subsequently implemented provider-suppression owner.
    """
    if not callable(suppressions):
        raise TypeError("Refresh effects require an explicit suppression owner.")
    return partial(
        _apply, general=general, mac=mac, public=public, suppressions=suppressions
    )


def _apply(snapshot, execution, claim, *, general, mac, public, suppressions):
    """Commit real effects only for the exact live attempt and just-promoted source."""
    require_work_order()
    attempt_id = SourceRefreshAttempt.objects.get(snapshot_id=snapshot.pk).pk
    attempt = verify_refresh_attempt(attempt_id, execution, claim)
    if attempt.snapshot.state != "promoted":
        raise StorageInvariantError("Refresh effects require promoted source truth.")
    scope = _scope(attempt.request, attempt.credential_fingerprint)
    reconcile_source_chairs(snapshot.pk, claim, campaign_id=attempt.request.campaign_id)
    # No campaign exists during pre-campaign imports. Archived populations are
    # historical; neither case creates a new Family code/cohort opportunistically.
    if scope.campaign is not None and scope.campaign.state != "archived":

        def admit(campaign):
            """Recheck the original attempt under Family allocation's own locks."""
            current = verify_refresh_attempt(attempt_id, execution, claim)
            return current.request.campaign_id == campaign.pk

        reconcile_source_families(
            snapshot.pk,
            claim,
            campaign_id=scope.campaign.pk,
            general=general,
            mac=mac,
            public=public,
            suppressed_addresses=suppressions(scope),
            admit=admit,
        )
    verify_refresh_attempt(attempt_id, execution, claim)
    return True
