"""Concrete Phase 2 Family effects for the compiled refresh handler.

Ministry leaders come from ParishSoft roles (#922), so a promotion no longer
reconciles Chairperson-seeded assignments.

Keyrings and suppression reads are startup-owned dependencies, never broker
payloads. Later submission/publication owners must extend this composition
before enabling those features; a placeholder success callback is not an effect.
"""

from functools import partial

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.storage import StorageInvariantError

from .attempts import _scope, verify_refresh_attempt
from .families import prepare_promotion, reconcile_source_families
from .refresh_models import SourceRefreshAttempt


def refresh_reconciler(*, general, mac, public, suppressions):
    """Bind explicit keys and a transactionally fresh suppression-set provider.

    ``suppressions(scope)`` returns the exact canonical Family-scoped refusals
    consumed by Family reconciliation (or an explicit uniform frozenset for
    isolated storage tests). No implicit empty default can ignore provider
    refusal evidence. The compiled owner resolves corrected source values in
    the same promotion transaction.
    """
    if not callable(suppressions):
        raise TypeError("Refresh effects require an explicit suppression owner.")
    return RefreshReconciler(
        partial(
            _apply, general=general, mac=mac, public=public, suppressions=suppressions
        )
    )


class RefreshReconciler:
    """The compiled promotion effects, plus their precompute (#895).

    Calling it applies the effects inside promotion, as a plain reconcile
    callable does. ``prepare`` derives the read-only Family inputs before
    the work order; execution passes its result back as ``prepared``.
    Callers that pass a plain callable never prepare, and keep the old path.
    """

    def __init__(self, apply):
        self._apply = apply

    def __call__(self, snapshot, execution, claim, *, prepared=None):
        """Apply the effects for the just-promoted ``snapshot``."""
        return self._apply(snapshot, execution, claim, prepared=prepared)

    @staticmethod
    def prepare(snapshot_id, claim):
        """Promotion's read-only Family inputs, or None (``prepare_promotion``)."""
        return prepare_promotion(snapshot_id, claim)


def _apply(
    snapshot, execution, claim, *, general, mac, public, suppressions, prepared=None
):
    """Commit real effects only for the exact live attempt and just-promoted source.

    ``prepared`` is the precomputed Family input (``prepare_promotion``) or
    None; ``reconcile_source_families`` uses it only for this snapshot.
    """
    require_work_order()
    attempt_id = SourceRefreshAttempt.objects.get(snapshot_id=snapshot.pk).pk
    attempt = verify_refresh_attempt(attempt_id, execution, claim)
    if attempt.snapshot.state != "promoted":
        raise StorageInvariantError("Refresh effects require promoted source truth.")
    scope = _scope(attempt.request, attempt.credential_fingerprint)
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
            prepared=prepared,
        )
        from parishkit.stewardship.reports.export_services import admit_campaign
        from parishkit.stewardship.reports.fact_production import hint_current_facts

        try:
            admit_campaign(scope.campaign.pk, mutating=True)
        except PermissionError:
            # This verified refresh owns the current campaign. Report writes
            # have narrower admission (notably during go-live cleanup); the
            # scheduler reconciles current inputs once report admission opens.
            # Do not swallow failures from an admitted hint's actual effects.
            pass
        else:
            hint_current_facts(scope.campaign.pk, source_id=snapshot.pk)
    verify_refresh_attempt(attempt_id, execution, claim)
    return True
