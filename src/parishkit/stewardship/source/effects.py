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

    ``suppressions(scope)`` returns the exact canonical Family-scoped refusals
    consumed by Family reconciliation (or an explicit uniform frozenset for
    isolated storage tests). No implicit empty default can ignore provider
    refusal evidence. The compiled owner resolves corrected source values in
    the same promotion transaction.
    """
    if not callable(suppressions):
        raise TypeError("Refresh effects require an explicit suppression owner.")
    return partial(
        _apply, general=general, mac=mac, public=public, suppressions=suppressions
    )


def refresh_unchanged(*, suppressions):
    """Bind the check that lets an identical quick update skip promotion (#630).

    ``suppressions`` is the same owner ``refresh_reconciler`` receives, so the
    check computes Family statuses exactly as promotion would.
    """
    if not callable(suppressions):
        raise TypeError("The unchanged check requires an explicit suppression owner.")
    return partial(_unchanged, suppressions=suppressions)


def _unchanged(snapshot, corpus, execution, claim, *, suppressions):
    """Whether promoting ``corpus`` again would change no owning-domain state.

    ``snapshot`` is the quick update's staging snapshot and ``corpus`` equals
    the current snapshot's. Promotion would then only restamp every Family
    row with a new generation, but its effects also apply what changed
    outside ParishSoft, so each is checked against what is stored, with the
    same decisions the effects make:

    - chairs: the active configuration already reconciled the current
      snapshot. After a configuration activation that skipped its own chair
      reconciliation (no chair seeds and no open review), the next quick
      update promotes once more than strictly needed;
    - Families (``population_current``): statuses with today's mail
      suppressions, so a new bounce counts as a change and promotes;
      codes, links and clean population evidence. A stale active link
      generation raises here exactly as promotion would;
    - proposals and Ministry requests: reconciliation would change none
      (``proposals_current``, ``ministry_requests_current``), so a new
      submission or a staff edit does not force a promotion.

    Runs in the caller's work-order transaction. Anything unexpected answers
    False, and the quick update stages and promotes as before.
    """
    from parishkit.stewardship.accounts.chair_models import ChairReconciliation
    from parishkit.stewardship.campaigns.family_identity import population_current
    from parishkit.stewardship.campaigns.link_tokens import (
        require_current_generation,
    )
    from parishkit.stewardship.responses.ministry_reconciliation import (
        ministry_requests_current,
    )
    from parishkit.stewardship.responses.reconciliation import proposals_current

    from .families import family_statuses
    from .snapshot_models import SourceCurrent

    require_work_order()
    attempt_id = SourceRefreshAttempt.objects.get(snapshot_id=snapshot.pk).pk
    attempt = verify_refresh_attempt(attempt_id, execution, claim)
    current = SourceCurrent.objects.get(singleton=True)
    if current.snapshot_id is None or current.snapshot_id != attempt.snapshot.base_id:
        return False
    scope = _scope(attempt.request, attempt.credential_fingerprint)
    if not ChairReconciliation.objects.filter(
        configuration_id=scope.runtime.active_configuration_id,
        snapshot_id=current.snapshot_id,
    ).exists():
        return False
    campaign = scope.campaign
    if campaign is None or campaign.state == "archived":
        # Promotion would apply only the chair effects checked above.
        return True
    if campaign.active_token_generation_id is not None:
        require_current_generation(campaign)
    return (
        proposals_current(corpus, campaign_id=campaign.pk)
        and ministry_requests_current(corpus, campaign_id=campaign.pk)
        and population_current(
            campaign,
            source_snapshot_id=current.snapshot_id,
            source_generation=current.generation,
            statuses=family_statuses(corpus, suppressed_addresses=suppressions(scope)),
        )
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
